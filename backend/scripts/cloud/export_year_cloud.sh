#!/usr/bin/env bash
# =============================================================================
# 云端按年导出 A 股 1 分钟 + 逐笔数据（一键封装）
#
# 目标：在一台干净的 Linux 云主机上，用尽量少的命令跑完 `--years <year>`，
#       并且断点续跑、日志按天切分、跑完打印取回命令。
#
# 子命令：
#   check    环境自检（CPU/内存/磁盘/7709 出网/日历文件/依赖），不通过就不让跑
#   setup    装 uv、同步后端依赖、建目录（幂等）
#   plan     调 --dry-run 打印分片计划（不连网、不写数据）
#   run      正式跑，失败自动重试（断点续跑靠脚本自己的 shard_done 表）
#   status   看一眼当前进度（不动任何文件、不影响进程）
#   collect  打印两个成品 zip 的体积与本机取回命令
#   all      setup -> check -> plan -> run -> collect（默认）
#
# 路径都可用环境变量覆盖，默认值：
#   BASE_DIR=/data            数据盘根
#   OUT_DIR=$BASE_DIR/backup_export
#   TICKS_DIR=$BASE_DIR/ticks
#   YEAR=2022  WORKERS=4  ENDPOINTS=8  MAX_RETRY=50
#
# 典型用法：
#   ./export_year_cloud.sh all --year 2022
#   tmux new -s exp 'bash export_year_cloud.sh run --year 2022'
#
# 重要：本脚本只新增文件，从不删除任何已经产出的数据。清理是人工步骤。
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
BACKEND_DIR="$REPO_DIR/backend"
EXPORT_PY="$BACKEND_DIR/scripts/export_history_minute.py"

BASE_DIR="${BASE_DIR:-/data}"
OUT_DIR="${OUT_DIR:-$BASE_DIR/backup_export}"
TICKS_DIR="${TICKS_DIR:-$BASE_DIR/ticks}"
YEAR="${YEAR:-2022}"
WORKERS="${WORKERS:-4}"
ENDPOINTS="${ENDPOINTS:-8}"
MAX_RETRY="${MAX_RETRY:-50}"
RETRY_WAIT="${RETRY_WAIT:-120}"
MONITOR_INTERVAL="${MONITOR_INTERVAL:-600}"

# 每片 512 MiB 原始估算；一片约 2 分钟，据此给 ETA 用的经验值
SHARDS_PER_HOUR=27

export PATH="$HOME/.local/bin:$PATH"

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_DIM=$'\033[2m'

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
log()  { printf '%s [%s] %s\n' "$(ts)" "${1:-info}" "${2:-}"; }
ok()   { printf '%s %s[ OK ]%s %s\n' "$(ts)" "$C_GRN" "$C_RESET" "$*"; }
warn() { printf '%s %s[WARN]%s %s\n' "$(ts)" "$C_YEL" "$C_RESET" "$*"; }
bad()  { printf '%s %s[FAIL]%s %s\n' "$(ts)" "$C_RED" "$C_RESET" "$*"; }
die()  { bad "$*"; exit 1; }

free_gb() {  # free_gb <path> -> 整数 GB
  df -Pk "$1" 2>/dev/null | awk 'NR==2{print int($4/1048576)}'
}

marker_path() { echo "$OUT_DIR/minute/kline_minute_1m/.year-$YEAR.done"; }
log_dir()     { echo "$OUT_DIR/logs/$YEAR"; }
parts_count() { find "$TICKS_DIR/$YEAR" -maxdepth 1 -name 'part-*.csv.gz' 2>/dev/null | wc -l; }
py_running()  {
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -af 'export_history_minute\.py' 2>/dev/null | grep -c .
  else
    ps -ef 2>/dev/null | grep -c '[e]xport_history_minute\.py'
  fi
}

uv_bin() {
  command -v uv 2>/dev/null || { [ -x "$HOME/.local/bin/uv" ] && echo "$HOME/.local/bin/uv"; }
}

run_export() {  # run_export <额外参数...>
  cd "$BACKEND_DIR" || die "进不去 $BACKEND_DIR"
  # env -u PYTHONPATH：云端一般没有这个变量，但本机跑过的人 shell 里可能残留，
  # 它的 sitecustomize 钩子会干扰，去掉最稳。
  env -u PYTHONPATH "$UV_BIN" run --no-sync python "$EXPORT_PY" \
      --years "$YEAR" --out "$OUT_DIR" --ticks-roots "$TICKS_DIR" \
      --workers "$WORKERS" --endpoints "$ENDPOINTS" "$@"
}

# ---------------------------------------------------------------------------
# check：环境自检
# ---------------------------------------------------------------------------
cmd_check() {
  local fails=0

  echo "──────────────────────────────────────────────────────────"
  echo " 环境自检  year=$YEAR  base=$BASE_DIR"
  echo "──────────────────────────────────────────────────────────"

  # 1) Python / uv
  if command -v python3 >/dev/null 2>&1; then
    local pv; pv="$(python3 -V 2>&1)"
    local pnum; pnum="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
    if python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)'; then
      ok "Python $pv"
    else
      bad "Python $pv 版本过低（需 >= 3.11）"; fails=$((fails+1))
    fi
    : "$pnum"
  else
    bad "没有 python3（先 apt install -y python3 python3-venv 或 yum install -y python3）"; fails=$((fails+1))
  fi

  UV_BIN="$(uv_bin)"
  if [ -n "$UV_BIN" ]; then ok "uv $("$UV_BIN" --version 2>/dev/null)"
  else warn "uv 未安装（setup 会装）"; fi

  # 2) CPU / 内存
  local ncpu memgb
  ncpu="$(nproc 2>/dev/null || echo '?')"
  memgb="$(awk '/MemTotal/{printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null || echo '?')"
  ok "CPU ${ncpu} 核 / 内存 ${memgb} GB（建议 >=4 核 8 GB，workers=$WORKERS）"
  if [ "$ncpu" != "?" ] && [ "$ncpu" -lt "$WORKERS" ]; then
    warn "核数少于 workers=$WORKERS，实际吞吐会低于预期，可把 WORKERS 调小"
  fi

  # 3) 磁盘：脚本自身要求逐笔根目录 >= est_gz*1.4+20 GB；2022 约需 50 GB
  if [ -d "$BASE_DIR" ]; then
    local fg; fg="$(free_gb "$BASE_DIR")"
    if [ "${fg:-0}" -ge 70 ]; then ok "数据盘 $BASE_DIR 剩余 ${fg} GB"
    else bad "数据盘 $BASE_DIR 只剩 ${fg} GB（建议 >=70，脚本硬性要求 >=50）"; fails=$((fails+1)); fi
  else
    bad "数据盘 $BASE_DIR 不存在（挂载没做或路径不对）"; fails=$((fails+1))
  fi

  # 4) 日历文件（唯一的数据前置）
  local cal="$OUT_DIR/daily/kline_daily/$YEAR.csv.gz"
  if [ -f "$cal" ]; then ok "日历 $cal ($(du -h "$cal" | cut -f1))"
  else bad "缺日历 $cal —— 从本机 scp 过去，见 README「第 2 步」"; fails=$((fails+1)); fi

  # 5) 7709 出网（不通就别开始，否则白跑十几个小时）
  local probe
  probe="$(python3 - <<'PY' 2>/dev/null
import socket
ips = ["124.71.187.122","119.97.185.59","47.107.64.168","123.60.84.66",
       "124.70.75.113","124.71.9.153","120.46.186.223","116.205.163.254"]
okn = 0
for ip in ips:
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((ip, 7709)); okn += 1
    except Exception:
        pass
    finally:
        s.close()
print(okn)
PY
)"
  if [ "${probe:-0}" -ge 3 ]; then ok "通达信主站 TCP 7709 出网正常（${probe}/8 可达）"
  else bad "TCP 7709 出网几乎全不通（${probe:-0}/8）—— 换机房或换安全组，不要往下跑"; fails=$((fails+1)); fi

  echo "──────────────────────────────────────────────────────────"
  if [ "$fails" -gt 0 ]; then
    bad "自检未通过（$fails 项），先修完再跑"
    return 1
  fi
  ok "自检全部通过"
  return 0
}

# ---------------------------------------------------------------------------
# setup：装依赖、建目录（幂等，可重复执行）
# ---------------------------------------------------------------------------
cmd_setup() {
  UV_BIN="$(uv_bin)"
  if [ -z "$UV_BIN" ]; then
    log info "安装 uv…"
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh || die "uv 安装失败"
    else
      die "没有 curl，先 apt install -y curl 再执行 setup"
    fi
    export PATH="$HOME/.local/bin:$PATH"
    UV_BIN="$(uv_bin)"
    [ -n "$UV_BIN" ] || die "uv 装完仍找不到（看 ~/.local/bin 是否在 PATH）"
  fi
  ok "uv: $("$UV_BIN" --version)"

  mkdir -p "$OUT_DIR/daily/kline_daily" \
           "$OUT_DIR/minute/kline_minute_1m" \
           "$TICKS_DIR" \
           "$(log_dir)" || die "建目录失败"

  log info "同步后端依赖（首次几分钟，之后秒级）…"
  ( cd "$BACKEND_DIR" && "$UV_BIN" sync --frozen ) || {
    warn "--frozen 失败（uv.lock 与 pyproject 不一致？），改用普通 sync"
    ( cd "$BACKEND_DIR" && "$UV_BIN" sync ) || die "uv sync 失败"
  }
  ok "依赖就绪"

  local cal="$OUT_DIR/daily/kline_daily/$YEAR.csv.gz"
  if [ ! -f "$cal" ]; then
    warn "还缺日历文件，在本机（不是云主机）执行："
    echo "    scp \"D:/tsp/backup_export/daily/kline_daily/$YEAR.csv.gz\" \\"
    echo "        root@<云主机IP>:$OUT_DIR/daily/kline_daily/"
  fi
  echo
  ok "setup 完成。下一步：$0 check --year $YEAR"
}

# ---------------------------------------------------------------------------
# plan：dry-run
# ---------------------------------------------------------------------------
cmd_plan() {
  UV_BIN="$(uv_bin)"; [ -n "$UV_BIN" ] || die "先执行 setup"
  log info "打印 $YEAR 年分片计划（不连网、不写数据）"
  run_export --dry-run
}

# ---------------------------------------------------------------------------
# run：正式跑 + 自动重试
# ---------------------------------------------------------------------------
_snapshot() {  # 输出一行进度快照，给进度日志用
  local logf="$1" parts last free
  parts="$(parts_count)"
  last="$(grep -E '^  片 ' "$logf" 2>/dev/null | tail -1 | sed 's/^ *//')"
  free="$(free_gb "$TICKS_DIR")"
  printf '%s parts=%s free=%sGB last=[%s]\n' "$(ts)" "$parts" "${free:-?}" "${last:-暂无}"
}

cmd_run() {
  UV_BIN="$(uv_bin)"; [ -n "$UV_BIN" ] || die "先执行 setup"
  local cal="$OUT_DIR/daily/kline_daily/$YEAR.csv.gz"
  [ -f "$cal" ] || die "缺日历 $cal（先 scp 过去）"
  mkdir -p "$(log_dir)" "$TICKS_DIR"

  local marker; marker="$(marker_path)"
  if [ -f "$marker" ]; then
    ok "$YEAR 年已完成（$marker 存在），无需重跑"
    return 0
  fi

  local stamp; stamp="$(date '+%Y%m%d-%H%M%S')"
  local logf="$OUT_DIR/logs/$YEAR/run-$stamp.log"
  local mon_pid=""

  # 进度快照：每 MONITOR_INTERVAL 秒一行，按自然日切文件，便于「天粒度」回看
  _monitor() {
    while :; do
      sleep "$MONITOR_INTERVAL"
      local pfile
      pfile="$(log_dir)/progress-$(date '+%Y-%m-%d').log"
      _snapshot "$logf" >> "$pfile" 2>/dev/null
    done
  }
  _monitor &
  mon_pid=$!
  trap '[ -n "$mon_pid" ] && kill "$mon_pid" 2>/dev/null' EXIT INT TERM

  local attempt=0 rc=0
  while :; do
    attempt=$((attempt+1))
    log info "第 $attempt/$MAX_RETRY 次尝试 → 日志 $logf"
    run_export 2>&1 | tee -a "$logf"
    rc="${PIPESTATUS[0]}"

    if [ -f "$marker" ]; then
      ok "$YEAR 年导出完成 → $marker"
      break
    fi
    if [ "$rc" -eq 2 ]; then
      die "脚本判定无法继续（退出码 2：缺日历/无可跑年份），不重试。看 $logf"
    fi
    if [ "$attempt" -ge "$MAX_RETRY" ]; then
      die "已重试 $MAX_RETRY 次仍未完成，最后一次退出码 $rc。看 $logf"
    fi
    warn "本次退出码 $rc，已完成 $(parts_count) 片保留在中转库；${RETRY_WAIT}s 后从断点继续"
    sleep "$RETRY_WAIT"
  done

  cmd_collect
}

# ---------------------------------------------------------------------------
# status：只读查看，可以随时在另一个 tmux 窗口敲
# ---------------------------------------------------------------------------
cmd_status() {
  local marker; marker="$(marker_path)"
  local lg; lg="$(ls -1t "$(log_dir)"/run-*.log 2>/dev/null | head -1)"
  # 兜底：用 .bat / 手工命令起的进程，日志落在 $OUT_DIR/export-<year>.log
  [ -z "$lg" ] && [ -f "$OUT_DIR/export-$YEAR.log" ] && lg="$OUT_DIR/export-$YEAR.log"

  echo "── $YEAR 年导出状态 ─────────────────────────────────────"
  echo "进程数        : $(py_running)"
  echo "已完成分片    : $(parts_count)"
  echo "逐笔目录      : $TICKS_DIR/$YEAR ($(du -sh "$TICKS_DIR/$YEAR" 2>/dev/null | cut -f1))"
  echo "数据盘剩余    : $(free_gb "$TICKS_DIR") GB"
  echo "完成标记      : $([ -f "$marker" ] && cat "$marker" || echo '未完成')"
  echo "全部完成      : $([ -f "$marker" ] && echo '是' || echo '否')"
  if [ -n "$lg" ]; then
    echo "当前日志      : $lg"
    echo "最后一行进度  :"
    grep -E '^  片 |打包|全部完成' "$lg" 2>/dev/null | tail -3 | sed 's/^/    /'
    local p; p="$(parts_count)"
    if [ "$p" -gt 0 ]; then
      local total; total="$(grep -oE '片 [0-9]+/[0-9]+' "$lg" 2>/dev/null | tail -1 | cut -d/ -f2)"
      if [ -n "${total:-}" ]; then
        awk -v p="$p" -v t="$total" -v r="$SHARDS_PER_HOUR" \
          'BEGIN{printf "进度          : %d/%d (%.1f%%), 剩余约 %.1f 小时抓取 + 1.5 小时打包\n", p, t, 100*p/t, (t-p)/r}'
      fi
    fi
  else
    echo "当前日志      : （还没有，未开跑）"
  fi
  local pf
  pf="$(ls -1t "$(log_dir)"/progress-*.log 2>/dev/null | head -1)"
  [ -n "$pf" ] && { echo "最近快照 ($(basename "$pf")):"; tail -3 "$pf" | sed 's/^/    /'; }
  echo "──────────────────────────────────────────────────────────"
}

# ---------------------------------------------------------------------------
# collect：成品核对 + 取回命令
# ---------------------------------------------------------------------------
cmd_collect() {
  local mzip="$OUT_DIR/minute/kline_minute_1m/$YEAR.zip"
  local tzip="$TICKS_DIR/${YEAR}_ticks.zip"
  echo "── $YEAR 年成品 ────────────────────────────────────────"
  for f in "$mzip" "$tzip"; do
    if [ -f "$f" ]; then ok "$f ($(du -h "$f" | cut -f1))"
    else bad "缺 $f"; fi
  done
  local gaps="$OUT_DIR/gaps-$YEAR.jsonl" fails="$OUT_DIR/failures-$YEAR.jsonl"
  [ -f "$gaps" ]  && warn "有缺口记录 $gaps（$(wc -l < "$gaps") 条），个别股票日数据不全"
  [ -f "$fails" ] && warn "有失败记录 $fails（$(wc -l < "$fails") 条），本机用 patch_symbol.py 单独补"
  cat <<EOF

在本机（Windows / Git Bash）取回：
  scp root@<云IP>:$mzip "D:/tsp/backup_export/minute/kline_minute_1m/"
  scp root@<云IP>:$tzip "E:/tsp_backup_ticks/"

拿回来后本机复核：
  cd backend && PYTHONPATH= .venv/Scripts/python.exe ../backup_export/_verify_minute_full.py $YEAR

EOF
}

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
CMD="all"
if [ $# -gt 0 ] && [[ "$1" != --* ]]; then CMD="$1"; shift; fi
while [ $# -gt 0 ]; do
  case "$1" in
    --year)      YEAR="$2"; shift 2;;
    --workers)   WORKERS="$2"; shift 2;;
    --endpoints) ENDPOINTS="$2"; shift 2;;
    --max-retry) MAX_RETRY="$2"; shift 2;;
    -h|--help)   sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    *) die "未知参数 $1";;
  esac
done

case "$CMD" in
  check)   cmd_check ;;
  setup)   cmd_setup ;;
  plan)    cmd_plan ;;
  run)     cmd_run ;;
  status)  cmd_status ;;
  collect) cmd_collect ;;
  all)     cmd_setup && cmd_check && cmd_plan && cmd_run ;;
  *)       die "未知子命令 $CMD（可用：check setup plan run status collect all）" ;;
esac
