#!/usr/bin/env bash
# tick_export_daemon.sh —— 心跳守护（方案 A 核心）
# 作用：
#   1) 监听本地端口做健康检查，使平台自启机制能保活本进程；
#   2) 周期巡检，保证「导出监管 run_export_supervisor.sh」与「本地监控 monitor_export.sh」
#      始终在运行；一旦被容器重启/孤儿清理杀掉，自动重拉。
# 由平台自启（replaceCloudStudioConfig）拉起 -> 容器重启后自动恢复导出续跑。
#
# 年份 / 数据盘等全部走环境变量（默认 2022，与官方脚本一致）；要导出别的年份，
# 把自启命令写成 `YEAR=2023 bash .../tick_export_daemon.sh` 即可。
#
# 单实例保证：
#   主手段 = 原子 mkdir 选主（overlayfs 上也可靠）；
#   辅助   = 端口 8099 绑定 + 归属校验（平台也需要一个监听端口才认这个自启服务）。
#   已放弃 flock：实测本机 overlay 上 flock 并非可靠互斥，且 exec 9>锁文件 会莫名阻塞。
#
# 注意：本脚本只负责「拉起 + 保活」，不碰 3000 端口的下载服务。

set -u

DAEMON_PORT="${DAEMON_PORT:-8099}"
ROOT="${ROOT:-/workspace/tick-stock-panel/backend/scripts/local}"
YEAR="${YEAR:-2022}"
BASE_DIR="${BASE_DIR:-/workspace/tick-data}"
REPO_DIR="${REPO_DIR:-/workspace/tick-stock-panel}"
SUPERVISOR="$ROOT/run_export_supervisor.sh"
MONITOR="$ROOT/monitor_export.sh"
HEALTH_LOG="$BASE_DIR/daemon_health.log"

mkdir -p "$(dirname "$HEALTH_LOG")"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$HEALTH_LOG"; }

# ---- 单实例：原子 mkdir 选主 ----
# 平台自启可能同时拉起多个实例（实测一次起 3 个）。mkdir 是原子操作：
# 谁 mkdir 成功谁是唯一 leader；失败方读 pid 判断持有者是否存活，存活则立即退出；
# 持有者已死（容器重启/被回收留下的陈旧标记）则清理后重试。
LEADER_DIR="$BASE_DIR/.export_daemon.leader"
LEADER_ACQUIRED=0

acquire_leader() {
  local i old
  for i in 1 2 3; do
    if mkdir "$LEADER_DIR" 2>/dev/null; then
      echo $$ > "$LEADER_DIR/pid"
      LEADER_ACQUIRED=1
      return 0
    fi
    # 读取持有者 pid 时必须去掉换行/空白，否则 kill -0 拿到 "123\n" 会失败，
    # 导致本应退出的非 leader 误判为「可夺权」，多个实例互相抢、谁都退不掉。
    old="$(cat "$LEADER_DIR/pid" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$old" ] && [ "$old" != "$$" ]; then
      # 持有者存活 -> 本实例直接退出，绝不删除存活 leader 的目录（防夺权）
      if kill -0 "$old" 2>/dev/null; then
        return 1
      fi
      # 持有者已死 -> 仅此时才清理并由本实例接管
      rm -rf "$LEADER_DIR" 2>/dev/null
    fi
    sleep 1
  done
  return 1
}

cleanup_leader() {
  if [ "${LEADER_ACQUIRED:-0}" = "1" ]; then
    rm -rf "$LEADER_DIR" 2>/dev/null
  fi
}
trap cleanup_leader EXIT

if ! acquire_leader; then
  log "已有存活的 daemon（pid=$(cat "$LEADER_DIR/pid" 2>/dev/null)），本实例退出"
  exit 0
fi

# 保证导出监管在跑（检测到已在运行则不重复拉起）
ensure_supervisor() {
  if ! pgrep -f "run_export_supervisor.sh" >/dev/null 2>&1; then
    ( cd "$ROOT" && YEAR="$YEAR" BASE_DIR="$BASE_DIR" REPO_DIR="$REPO_DIR" \
        setsid nohup bash "$SUPERVISOR" "$YEAR" >> "$BASE_DIR/export_supervisor.log" 2>&1 & )
    log "拉起导出监管 run_export_supervisor.sh YEAR=$YEAR"
  fi
}

# 保证本地监控在跑
ensure_monitor() {
  if ! pgrep -f "monitor_export.sh" >/dev/null 2>&1; then
    ( cd "$ROOT" && YEAR="$YEAR" TSP_DATA="$BASE_DIR" REPO_DIR="$REPO_DIR" \
        setsid nohup bash "$MONITOR" >> "$BASE_DIR/monitor_export_nohup.log" 2>&1 & )
    log "拉起监控 monitor_export.sh"
  fi
}

# 8099 上可能残留「上个 daemon 的健康检查 python」孤儿（父进程已死、被 init 收养）。
# 它会让新 daemon 的 bind 失败；这里只清理父进程为 1 的孤儿，绝不误杀别的服务。
kill_orphan_health() {
  local lp pp
  lp="$(ss -ltnp 2>/dev/null | grep ":$DAEMON_PORT " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)"
  [ -z "$lp" ] && return 1
  [ "$lp" = "${HPID:-}" ] && return 1
  pp="$(awk '/^PPid:/{print $2}' "/proc/$lp/status" 2>/dev/null)"
  if [ "$pp" = "1" ]; then
    kill "$lp" 2>/dev/null
    sleep 1
    log "清理 8099 孤儿健康检查 pid=$lp"
    return 0
  fi
  return 1
}

# 健康检查 HTTP 服务：本进程持有 8099 监听套接字。
#
# 注意：这里【不能】用 HPID=$(start_health) 这种命令替换！后台 python 会继承
# 命令替换的管道 stdout 并一直持有，导致 $(...) 永远等不到 EOF，守护卡死在
# anon_pipe_read，主循环永远不执行。所以改为「直接调用 + 全局变量收 PID」，
# 并把 python 自身的 stdout/stderr 重定向掉，避免污染管道/终端。
#
# 判定「是否绑定成功」也不能用 kill -0：bind 失败退出的 python 会短暂处于僵尸态，
# kill -0 仍返回成功，会让多余实例误判而继续运行。统一用 ss 校验套接字归属。
spawn_health() {
  python3 - "$DAEMON_PORT" >/dev/null 2>>"$HEALTH_LOG" <<'PYEOF' &
import sys, http.server, socketserver
port = int(sys.argv[1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"tick-export-daemon OK\n")
    def log_message(self, *a):
        pass
try:
    srv = socketserver.TCPServer(("0.0.0.0", port), H)
except OSError as e:
    sys.stderr.write("bind %d failed: %s\n" % (port, e))
    sys.exit(3)
srv.serve_forever()
PYEOF
  HPID=$!
  sleep 1
  ss -ltnp 2>/dev/null | grep -q "pid=$HPID,"
}

start_health() {
  if spawn_health; then
    return 0
  fi
  # 首次失败：可能是上个 daemon 留下的孤儿占着端口，清掉后重试一次
  if kill_orphan_health; then
    if spawn_health; then
      return 0
    fi
  fi
  log "健康检查端口 $DAEMON_PORT 绑定失败（已被占用），本实例退出"
  exit 1
}

log "心跳守护启动, 健康检查端口 $DAEMON_PORT, leader=$$"
start_health

# 主循环每轮复核领导权：平台自启常一次拉起多个实例，启动时选举有竞态，
# 这里兜底——只要 leader 不是自己且对方存活，本实例立即退出，杜绝多链抢锁。
check_leader() {
  local cur
  cur="$(cat "$LEADER_DIR/pid" 2>/dev/null | tr -d '[:space:]')"
  if [ "$cur" = "$$" ]; then
    return 0
  fi
  if [ -n "$cur" ] && kill -0 "$cur" 2>/dev/null; then
    log "领导权已由 pid=$cur 持有，本实例退出"
    exit 0
  fi
  # 持有者不存在或已死，尝试重新夺权（仅在此时清理目录）
  rm -rf "$LEADER_DIR" 2>/dev/null
  if mkdir "$LEADER_DIR" 2>/dev/null; then
    echo $$ > "$LEADER_DIR/pid"
    LEADER_ACQUIRED=1
    return 0
  fi
  log "重新夺权失败，本实例退出"
  exit 0
}

check_leader

# 是否本实例就是 leader（pid 文件里写的就是自己）。
# 只有 leader 才拉起/保活导出链，也只有 leader 才清理兄弟实例，
# 避免多个实例都自认 leader 时互相误杀、或都去拉导出链抢 DuckDB 锁。
am_leader() {
  local cur="$(cat "$LEADER_DIR/pid" 2>/dev/null | tr -d '[:space:]')"
  [ "$cur" = "$$" ]
}

# leader 主动清理其它 tick_export_daemon.sh 实例：平台自启常一次拉起多个，
# 且个别实例在竞态下未能按预期自行退出（实测如此），由 leader 兜底清掉。
prune_siblings() {
  local me=$$
  local cur="$(cat "$LEADER_DIR/pid" 2>/dev/null | tr -d '[:space:]')"
  [ "$cur" != "$me" ] && return 0
  local p
  for p in $(pgrep -f "[t]ick_export_daemon\.sh" 2>/dev/null); do
    if [ "$p" != "$me" ]; then
      kill -9 "$p" 2>/dev/null
      log "清理重复守护实例 pid=$p"
    fi
  done
}

if am_leader; then
  ensure_supervisor
  ensure_monitor
fi

# 主循环：每 30s 巡检一次，保证导出链路（监管 + 监控）始终存活。
# 本脚本自身由平台自启保活，故永不退出，端口持续监听。
while :; do
  check_leader
  prune_siblings
  sleep 30
  # 健康检查服务一旦退出（端口被抢/异常），守护也退出，避免成为空转孤儿。
  if ! ss -ltnp 2>/dev/null | grep -q "pid=$HPID,"; then
    log "健康检查服务异常退出，守护退出"
    exit 1
  fi
  if am_leader; then
    ensure_supervisor
    ensure_monitor
  fi
done
