#!/usr/bin/env bash
# =============================================================================
# 导出监管脚本：在 export_year_cloud.sh run 之上再套一层 supervisor。
#
# 背景：export_year_cloud.sh run 自带 50 次内部重试 + 120s 等待，能扛住
#       通达信主站瞬时空结果/网络抖动；但它扛不住「整个进程组被沙箱回收/
#       休眠杀掉」这种情况。本脚本在外层循环，进程一旦退出(且未完成)就重拉，
#       直到出现完成标记 .year-<YEAR>.done 才退出。
#
# 全部参数走环境变量（默认 2022，与官方脚本一致），部署新机器 / 换年份
# 无需改脚本，只要传环境变量或位置参数：
#   YEAR        导出年份（默认 2022，也可作第 1 个位置参数）
#   BASE_DIR    数据盘根（默认 /workspace/tick-data；官方脚本默认 /data）
#   REPO_DIR    仓库根（默认 /workspace/tick-stock-panel）
#   TSP_DATA    下载服务根目录（默认同 BASE_DIR）
#   MAX_RESTART 最大重启次数（默认 0 = 不限；设正整数可限制）
#
# 用法：
#   setsid nohup bash run_export_supervisor.sh >> /workspace/tick-data/export_supervisor.log 2>&1 &
#   YEAR=2023 BASE_DIR=/data setsid nohup bash run_export_supervisor.sh >> log 2>&1 &
# =============================================================================
set -uo pipefail

# ---- 参数（环境变量 / 位置参数驱动，通用化，默认 2022） ----
YEAR="${1:-${YEAR:-2022}}"
BASE_DIR="${BASE_DIR:-/workspace/tick-data}"
REPO_DIR="${REPO_DIR:-/workspace/tick-stock-panel}"
TSP_DATA="${TSP_DATA:-$BASE_DIR}"
OUT_DIR="${OUT_DIR:-$BASE_DIR/backup_export}"
TICKS_DIR="${TICKS_DIR:-$BASE_DIR/ticks}"
MAX_RESTART="${MAX_RESTART:-0}"

# uv 用原生 TLS 实现连网更稳（部分环境 rustls 握手会失败）
export UV_NATIVE_TLS=1
export PATH="$HOME/.local/bin:$PATH"

# 关键：必须把路径变量导出给子进程 export_year_cloud.sh，
# 否则它会回落到自己的默认 BASE_DIR=/data，导致「缺日历」误判。
export YEAR BASE_DIR OUT_DIR TICKS_DIR TSP_DATA REPO_DIR

SCRIPT="$REPO_DIR/backend/scripts/cloud/export_year_cloud.sh"
MARKER="$OUT_DIR/minute/kline_minute_1m/.year-$YEAR.done"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(ts)] 监管启动: YEAR=$YEAR BASE_DIR=$BASE_DIR REPO_DIR=$REPO_DIR MAX_RESTART=$MAX_RESTART"

if [ -f "$MARKER" ]; then
  echo "[$(ts)] 已完成($MARKER)，无需启动，监管退出"; exit 0
fi

n=0
while :; do
  if [ -f "$MARKER" ]; then
    echo "[$(ts)] 检测到完成标记($MARKER)，监管退出"; break
  fi
  if [ "$MAX_RESTART" -gt 0 ] && [ "$n" -ge "$MAX_RESTART" ]; then
    echo "[$(ts)] 已达最大重启次数 $MAX_RESTART，监管退出（需人工排查）"; break
  fi
  echo "[$(ts)] >>> 启动/恢复 $YEAR 导出 (外层 supervisor 第 $((++n)) 次) ..."
  bash "$SCRIPT" run --year "$YEAR"
  rc=$?
  echo "[$(ts)] <<< export_year_cloud.sh run 退出码=$rc"

  if [ -f "$MARKER" ]; then
    echo "[$(ts)] 检测到完成标记($MARKER)，监管退出"; break
  fi
  # 脚本判定「无法继续」(缺日历/无可跑年份) 时不重试，直接退出
  if [ "$rc" -eq 2 ]; then
    echo "[$(ts)] 退出码 2：任务无法继续，监管退出（看日志排查）"; break
  fi
  echo "[$(ts)] 导出进程退出但任务未完成(退出码=$rc)，30s 后由监管重拉 ..."
  sleep 30
done

echo "[$(ts)] 监管结束"
