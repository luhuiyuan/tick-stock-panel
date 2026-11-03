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
# 单实例保证：优先用「端口 8099 内核级互斥」——健康检查服务只能有一个进程绑住该端口，
# 其余实例在 bind 失败时立即退出；flock 仅作兜底。这样无论平台一次拉起几个实例，
# 全局永远只有 1 个 daemon 真正运行，避免竞争重复拉起导出。
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
HEALTH_LOG="/workspace/tick-data/daemon_health.log"

mkdir -p "$(dirname "$HEALTH_LOG")"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$HEALTH_LOG"; }

# ---- 单实例：唯一手段 = 端口 8099 归属判定 ----
# 说明：曾用 flock 做兜底，但实测本机 overlay 文件系统上 flock 并非可靠互斥
#      （多个实例都能「拿到锁」），且 exec 9>锁文件 在脚本里会莫名阻塞，
#      故彻底移除 flock，只保留下面的端口预检 + 绑定归属校验。
# ---- 端口预检（已被占用说明另一 daemon 在跑） ----
if ss -ltn 2>/dev/null | grep -q ":$DAEMON_PORT "; then
  log "端口 $DAEMON_PORT 已被占用，已有 daemon 在跑，退出"
  exit 0
fi

# 保证导出监管在跑（检测到已在运行则不重复拉起）
ensure_supervisor() {
  if ! pgrep -f "run_export_supervisor.sh" >/dev/null 2>&1; then
    ( cd "$ROOT" && YEAR="$YEAR" BASE_DIR="$BASE_DIR" REPO_DIR="$REPO_DIR" \
        setsid nohup bash "$SUPERVISOR" "$YEAR" >> /workspace/tick-data/export_supervisor.log 2>&1 & )
    log "拉起导出监管 run_export_supervisor.sh YEAR=$YEAR"
  fi
}

# 保证本地监控在跑
ensure_monitor() {
  if ! pgrep -f "monitor_export.sh" >/dev/null 2>&1; then
    ( cd "$ROOT" && setsid nohup bash "$MONITOR" >> /workspace/tick-data/monitor_export_nohup.log 2>&1 & )
    log "拉起监控 monitor_export.sh"
  fi
}

# 健康检查 HTTP 服务：本进程持有 8099 监听套接字。若端口已被占用（另一 daemon 先绑住），
# 这里 bind 会失败、python 以非 0 退出，守护据此退出，从而保证全局唯一实例。
#
# 注意：这里【不能】用 HPID=$(start_health) 这种命令替换！后台 python 会继承
# 命令替换的管道 stdout 并一直持有，导致 $(...) 永远等不到 EOF，守护卡死在
# anon_pipe_read，主循环永远不执行。所以改为「直接调用 + 全局变量收 PID」，
# 并把 python 自身的 stdout/stderr 重定向掉，避免污染管道/终端。
start_health() {
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
  # 给一点时间让 bind 完成，然后确认「8099 的监听套接字」确实属于我们自己的 HPID。
  # 不能用 kill -0 判断：bind 失败退出的 python 会短暂处于僵尸态，kill -0 仍返回成功，
  # 会让多余实例误判而继续运行（曾导致 3 个 daemon 并存）。
  sleep 1
  if ! ss -ltnp 2>/dev/null | grep -q "pid=$HPID,"; then
    log "健康检查端口 $DAEMON_PORT 绑定失败（已被另一 daemon 占用），本实例退出"
    exit 1
  fi
}

log "心跳守护启动, 健康检查端口 $DAEMON_PORT"
start_health

ensure_supervisor
ensure_monitor

# 主循环：每 30s 巡检一次，保证导出链路（监管 + 监控）始终存活。
# 本脚本自身由平台自启保活，故永不退出，端口持续监听。
while :; do
  sleep 30
  # 健康检查服务一旦退出（端口被抢/异常），守护也退出，避免成为空转孤儿。
  # 同样用 ss 判归属，避免僵尸进程让 kill -0 误判为存活。
  if ! ss -ltnp 2>/dev/null | grep -q "pid=$HPID,"; then
    log "健康检查服务异常退出，守护退出"
    exit 1
  fi
  ensure_supervisor
  ensure_monitor
done
