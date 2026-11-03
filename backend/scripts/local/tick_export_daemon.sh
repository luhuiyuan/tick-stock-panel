#!/usr/bin/env bash
# tick_export_daemon.sh —— 心跳守护（方案 A 核心）
# 作用：
#   1) 监听本地端口做健康检查，使平台自启机制能保活本进程；
#   2) 周期巡检，保证「导出监管 run_export_supervisor.sh」与「本地监控 monitor_export.sh」
#      始终在运行；一旦被容器重启/孤儿清理杀掉，自动重拉。
# 由平台自启（replaceCloudStudioConfig）拉起 -> 容器重启后自动恢复 2022 导出续跑。
#
# 注意：本脚本只负责「拉起 + 保活」，不碰 3000 端口的下载服务。

set -u

DAEMON_PORT="${DAEMON_PORT:-8099}"
ROOT="/workspace/tick-stock-panel/backend/scripts/local"
SUPERVISOR="$ROOT/run_export_supervisor.sh"
MONITOR="$ROOT/monitor_export.sh"
HEALTH_LOG="/workspace/tick-data/daemon_health.log"

mkdir -p "$(dirname "$HEALTH_LOG")"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$HEALTH_LOG"; }

# 保证导出监管在跑（检测到已在运行则不重复拉起）
ensure_supervisor() {
  if ! pgrep -f "run_export_supervisor.sh" >/dev/null 2>&1; then
    ( cd "$ROOT" && setsid nohup bash "$SUPERVISOR" >> /workspace/tick-data/export_supervisor.log 2>&1 & )
    log "拉起导出监管 run_export_supervisor.sh"
  fi
}

# 保证本地监控在跑
ensure_monitor() {
  if ! pgrep -f "monitor_export.sh" >/dev/null 2>&1; then
    ( cd "$ROOT" && setsid nohup bash "$MONITOR" >> /workspace/tick-data/monitor_export_nohup.log 2>&1 & )
    log "拉起监控 monitor_export.sh"
  fi
}

# 健康检查 HTTP 服务（监听 DAEMON_PORT，返回 200 即视为就绪）
start_health() {
  python3 - "$DAEMON_PORT" <<'PYEOF' &
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
srv = socketserver.TCPServer(("0.0.0.0", port), H)
srv.serve_forever()
PYEOF
}

log "心跳守护启动, 健康检查端口 $DAEMON_PORT"

start_health
ensure_supervisor
ensure_monitor

# 主循环：每 30s 巡检一次，保证导出链路（监管 + 监控）始终存活。
# 本脚本自身由平台自启保活，故永不退出，端口持续监听。
while :; do
  sleep 30
  ensure_supervisor
  ensure_monitor
done
