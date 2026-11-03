#!/usr/bin/env bash
# 每小时汇报一次 <YEAR> 全量导出进展，写入 hourly_report.log
# 参数全部走环境变量（默认 2022）；换年份只需传 YEAR=2023。
YEAR="${YEAR:-2022}"
BASE="${TSP_DATA:-/workspace/tick-data}"
REPO_DIR="${REPO_DIR:-/workspace/tick-stock-panel}"
LOG=${BASE}/hourly_report.log

# 分片总数：优先从运行日志里解析（片 X/总数），解析不到再回退默认 353
total_shards() {
  local lg
  lg="$(ls -1t "$BASE/backup_export/logs/$YEAR"/run-*.log 2>/dev/null | head -1)"
  local t
  t="$(grep -oE "片 [0-9]+/[0-9]+" "$lg" 2>/dev/null | tail -1 | cut -d/ -f2)"
  echo "${t:-353}"
}

mkdir -p "$BASE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 监控启动 (year=$YEAR)" >> "$LOG"

while true; do
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  TOTAL=$(total_shards)
  parts=$(find "$BASE/ticks/$YEAR" -name 'part-*.csv.gz' 2>/dev/null | wc -l)
  size=$(du -sh "$BASE/ticks/$YEAR" 2>/dev/null | cut -f1)
  pct=$(awk "BEGIN{printf \"%.1f\", $parts/$TOTAL*100}")
  running=$(pgrep -f "export_history_minute.py" >/dev/null && echo YES || echo NO)
  err=$(grep -ricE "fail|error|traceback" "$BASE/backup_export/logs/$YEAR/run-"*.log 2>/dev/null || echo 0)
  done_min=$(ls "$BASE/backup_export/minute/kline_minute_1m/.year-$YEAR.done" 2>/dev/null >/dev/null && echo YES || echo NO)
  zip_ticks=$(ls "$BASE/ticks/${YEAR}_ticks.zip" 2>/dev/null >/dev/null && echo YES || echo NO)
  zip_min=$(ls "$BASE/backup_export/minute/kline_minute_1m/$YEAR.zip" 2>/dev/null >/dev/null && echo YES || echo NO)

  if [ "$done_min" = "YES" ] || [ "$zip_ticks" = "YES" ]; then
    echo "[$ts] parts=$parts/$TOTAL ($pct%) ticks=$size proc=$running minute_done=$done_min zip(min/ticks)=$zip_min/$zip_ticks errors=$err" >> "$LOG"
    echo "[$ts] *** $YEAR 导出已完成, 启动自动校验 ***" >> "$LOG"
    ( cd "$REPO_DIR/backend" && uv run --no-sync python "$REPO_DIR/backend/scripts/local/verify_export_result.py" --year "$YEAR" >> "$LOG" 2>&1 )
    echo "[$ts] *** 校验完成 (报告见 ${BASE}/verify_report_${YEAR}.txt) ***" >> "$LOG"
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
