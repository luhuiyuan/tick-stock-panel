#!/usr/bin/env bash
# 轻量保活探针：供定时任务（automation）调用，用于把沙箱从空闲休眠中唤醒。
# 设计原则：
#   1) 幂等 —— 只在守护进程缺失时拉起，绝不 kill / 重启正在跑的导出；
#   2) 轻量 —— 只做一次 ps 检查 + 一次目录计数，秒级返回；
#   3) 安全 —— 导出已完成（marker 存在）则什么也不做。
#
# 用法：
#   bash keepalive.sh            # 默认 YEAR=2022
#   YEAR=2022 bash keepalive.sh
set -u

YEAR="${YEAR:-2022}"
OUT_DIR="${OUT_DIR:-/workspace/tick-data/backup_export}"
TICKS_DIR="${TICKS_DIR:-/workspace/tick-data/ticks}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="$OUT_DIR/minute/kline_minute_1m/.year-$YEAR.done"

if [ -f "$MARKER" ]; then
  echo "DONE: $YEAR 已导出完成（$MARKER 存在），本次不动作"
  exit 0
fi

parts=$(find "$TICKS_DIR/$YEAR" -maxdepth 1 -name 'part-*.csv.gz' 2>/dev/null | wc -l | tr -d ' ')

if pgrep -f "tick_export_daemon.sh" >/dev/null 2>&1; then
  echo "ALIVE: daemon 在运行，$YEAR 已完成分片 $parts，本次仅唤醒沙箱不动作"
  exit 0
fi

echo "RESTART: daemon 不在，拉起守护（$YEAR 当前分片 $parts）"
cd "$LOCAL_DIR" || exit 1
YEAR="$YEAR" OUT_DIR="$OUT_DIR" TICKS_DIR="$TICKS_DIR" \
  setsid nohup bash "$LOCAL_DIR/tick_export_daemon.sh" >>"$OUT_DIR/../daemon_console.log" 2>&1 &
sleep 2
echo "RESTARTED: $(pgrep -f 'tick_export_daemon.sh' | tr '\n' ' ')"
