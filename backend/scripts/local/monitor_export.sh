#!/usr/bin/env bash
# 每小时汇报一次 2022 全量导出进展，写入 hourly_report.log
BASE=${TSP_DATA:-/workspace/tick-data}
LOG=${BASE}/hourly_report.log
TOTAL=353

mkdir -p "$BASE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 监控启动 (total shards=$TOTAL)" >> "$LOG"

while true; do
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  parts=$(find "$BASE/ticks/2022" -name 'part-*.csv.gz' 2>/dev/null | wc -l)
  size=$(du -sh "$BASE/ticks/2022" 2>/dev/null | cut -f1)
  pct=$(awk "BEGIN{printf \"%.1f\", $parts/$TOTAL*100}")
  running=$(pgrep -f "export_history_minute.py" >/dev/null && echo YES || echo NO)
  err=$(grep -ricE "fail|error|traceback" "$BASE/backup_export/logs/2022/run-"*.log 2>/dev/null || echo 0)
  done_min=$(ls "$BASE/backup_export/minute/kline_minute_1m/.year-2022.done" 2>/dev/null >/dev/null && echo YES || echo NO)
  zip_ticks=$(ls "$BASE/ticks/2022_ticks.zip" 2>/dev/null >/dev/null && echo YES || echo NO)
  zip_min=$(ls "$BASE/backup_export/minute/kline_minute_1m/2022.zip" 2>/dev/null >/dev/null && echo YES || echo NO)

  if [ "$done_min" = "YES" ] || [ "$zip_ticks" = "YES" ]; then
    echo "[$ts] parts=$parts/$TOTAL ($pct%) ticks=$size proc=$running minute_done=$done_min zip(min/ticks)=$zip_min/$zip_ticks errors=$err" >> "$LOG"
    echo "[$ts] *** 2022 导出已完成, 启动自动校验 ***" >> "$LOG"
    ( cd /workspace/tick-stock-panel/backend && uv run --no-sync python /workspace/tick-stock-panel/backend/scripts/local/verify_export_result.py --year 2022 >> "$LOG" 2>&1 )
    echo "[$ts] *** 校验完成 (报告见 ${BASE}/verify_report_2022.txt) ***" >> "$LOG"
    break
  fi

  if [ "$running" = "NO" ]; then
    echo "[$ts] parts=$parts/$TOTAL ($pct%) ticks=$size proc=$running minute_done=$done_min zip(min/ticks)=$zip_min/$zip_ticks errors=$err" >> "$LOG"
    echo "[$ts] !! 进程未运行且未完成, 可能中断或处于重试等待, 需检查" >> "$LOG"
  else
    echo "[$ts] parts=$parts/$TOTAL ($pct%) ticks=$size proc=$running zip(min/ticks)=$zip_min/$zip_ticks errors=$err" >> "$LOG"
  fi

  sleep 3600
done
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 监控结束" >> "$LOG"
