#!/usr/bin/env bash
# =============================================================================
# 导出监管脚本：在 export_year_cloud.sh run 之上再套一层 supervisor。
#
# 背景：export_year_cloud.sh run 自带 50 次内部重试 + 120s 等待，能扛住
#       通达信主站瞬时空结果/网络抖动；但它扛不住「整个进程组被沙箱回收/
#       休眠杀掉」这种情况。本脚本在外层循环，进程一旦退出(且未完成)就重拉，
#       直到出现完成标记 .year-2022.done 才退出。
#
# 用法：setsid nohup bash run_export_supervisor.sh >/workspace/tick-data/export_supervisor.log 2>&1 &
# =============================================================================
set -uo pipefail

export BASE_DIR=/workspace/tick-data
export TSP_DATA=/workspace/tick-data
export PATH="$HOME/.local/bin:$PATH"

SCRIPT=/workspace/tick-stock-panel/backend/scripts/cloud/export_year_cloud.sh
MARKER=/workspace/tick-data/backup_export/minute/kline_minute_1m/.year-2022.done

ts() { date '+%Y-%m-%d %H:%M:%S'; }

if [ -f "$MARKER" ]; then
  echo "[$(ts)] 已完成($MARKER)，无需启动，监管退出"; exit 0
fi

n=0
while :; do
  if [ -f "$MARKER" ]; then
    echo "[$(ts)] 检测到完成标记($MARKER)，监管退出"; break
  fi
  echo "[$(ts)] >>> 启动/恢复 2022 导出 (外层 supervisor 第 $((++n)) 次) ..."
  bash "$SCRIPT" run --year 2022
  rc=$?
  echo "[$(ts)] <<< export_year_cloud.sh run 退出码=$rc"

  if [ -f "$MARKER" ]; then
    echo "[$(ts)] 检测到完成标记($MARKER)，监管退出"; break
  fi
  echo "[$(ts)] 导出进程退出但任务未完成(退出码=$rc)，30s 后由监管重拉 ..."
  sleep 30
done

echo "[$(ts)] 监管结束"
