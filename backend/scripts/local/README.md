# 另一台云主机跑数手册（本地辅助工具 + 跨机器全流程）

本目录（`backend/scripts/local/`）下的一组工具，配合上游的 `backend/scripts/cloud/export_year_cloud.sh`，
解决**在一台全新的云主机（而非原开发机）上，从零把 A 股历史分钟 + 逐笔数据导出跑通**这件事。

> 与官方 `cloud/README.md` 的最大区别：**官方要求把「日历文件」从本机 `scp` 到云主机**，
> 但你在一台新云主机上往往没有那份本地日历备份。本手册改用 `gen_calendar_all.py`
> **直接在云上现生成全量日历**，从而摆脱对本地备份的依赖，真正做到「一台干净机器就能跑」。

---

## 0. 一句话总览

```
1. 准备机器（clone + 切分支 + 装依赖）
2. 准备日K日历（优先从已生成云主机拷贝，否则现生成）
3. 跑导出（export_year_cloud.sh all）
4. 进度监控（monitor_export.sh，每小时记日志）
5. 完成后自动校验（verify_export_result.py）
6. 取回成果（download-server 断点续传下载）
```

---

## 1. 机器要求

与官方一致，重点两条硬性门槛：

| 项 | 要求 | 说明 |
|---|---|---|
| 数据盘 | **≥70 GB** 空闲 | 导出脚本对逐笔目录硬性要求 ≥50 GB，否则拒绝启动 |
| 出站 | **TCP 7709** 可达公网 | 通达信行情主站端口，3/8 以上可达才行；不通就别硬跑 |
| 系统 | Linux（Ubuntu 22.04 / Debian 12 / CentOS 8） | bash + coreutils + Python 3.11+ |
| CPU / 内存 | ≥4 核 / 8 GB | 对应 `--workers 4`；当前沙箱 32 核 123 GB 远超标 |

> 磁盘峰值占用约 31 GB（逐笔分片 21 + 分钟包 4.4 + DuckDB 中转 5.5），留 70 GB 是安全余量。

---

## 2. 环境变量约定（务必看这一节）

两套脚本用了**不同的环境变量**指代数据盘，必须让它们指向**同一个目录**，否则日历生成的位置和导出处期望的位置对不上：

| 变量 | 被谁使用 | 默认 | 含义 |
|---|---|---|---|
| `BASE_DIR` | `export_year_cloud.sh` | `/data` | 数据盘根；内部 `OUT_DIR=$BASE_DIR/backup_export`、`TICKS_DIR=$BASE_DIR/ticks` |
| `TSP_DATA` | 本目录全部 `local/` 工具 | `/workspace/tick-data` | 数据盘根 |

**关键对齐关系**：`gen_calendar_all.py` 把日历写到 `$TSP_DATA/backup_export/daily/kline_daily/<YEAR>.csv.gz`，
而 `export_year_cloud.sh` 期望日历在 `$OUT_DIR/daily/kline_daily/<YEAR>.csv.gz`（`OUT_DIR=$BASE_DIR/backup_export`）。
因此只要 **`TSP_DATA == BASE_DIR`**，日历路径就天然对齐。

推荐做法：把数据盘挂到 `/data`，然后**两个变量都显式设成 `/data`**（或在沙箱这种 `/workspace/tick-data` 场景两者都设成 `/workspace/tick-data`）。

```bash
export BASE_DIR=/data          # 给云端导出脚本
export TSP_DATA=/data          # 给本目录 local 工具
```

---

## 3. 步骤 1：准备代码与依赖

```bash
git clone https://github.com/luhuiyuan/tick-stock-panel.git
cd tick-stock-panel
git checkout fork-integration

# 让 export_year_cloud.sh 能直接找到脚本
chmod +x backend/scripts/cloud/export_year_cloud.sh
```

依赖由 `export_year_cloud.sh setup` 自动安装（uv + 后端 Python 依赖）。也可以单独先装 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

---

## 4. 步骤 2：准备日K日历（首选拷贝，兜底现生成）

官方 `cloud/README.md` 第 2 步要求把 `<year>.csv.gz` 从本机 `scp` 上来。
但日历是公开的 A 股历史数据，**更合理的做法是第一台云主机生成一次，之后所有新机器直接从它拷贝**，
避免每台都重抓（省 25–35 分钟 + 降低主站负载 + 避免漏抓补跑）。

当前这台已生成日历的云主机上，文件位于 `$TSP_DATA/backup_export/daily/kline_daily/`
（`1990.csv.gz`–`2026.csv.gz`，共 37 个，约 308 MB）。

### 方式 A（首选）：从已生成日历的云主机拷贝

**途径 1 — scp（两台机器 SSH 可达时最快最稳）**，在新云主机上执行：

```bash
ssh root@<源云IP> 'mkdir -p /data/backup_export/daily/kline_daily'
scp "root@<源云IP>:/data/backup_export/daily/kline_daily/*.csv.gz" \
    /data/backup_export/daily/kline_daily/
# 若源机数据盘是 /workspace/tick-data，路径相应改成该前缀
```

**途径 2 — HTTP 下载链接（更通用，无需 SSH 互通）**。源机若已启动 `tools/download-server`
（暴露 `$TSP_DATA` 整目录、支持 Range 断点续传），新机器直接拉：

```bash
# 单年（如 2022，约 22 MB）
wget -c "https://<源机域名或IP>:8088/backup_export/daily/kline_daily/2022.csv.gz"
# 或全量 37 个年份（约 308 MB）
for y in $(seq 1990 2026); do
  wget -c "https://<源机域名或IP>:8088/backup_export/daily/kline_daily/$y.csv.gz"
done
```

> 源机没有现成下载服务时，起一个即可：`cd tools/download-server && PORT=8088 TSP_DATA=/data node server.js`。
> 若要把 8088 暴露为公网链接，用各平台的「发布为应用 / 端口转发」即可。

拷贝完成后**自检**对齐到导出脚本期望的位置：

```bash
ls -lh "$TSP_DATA/backup_export/daily/kline_daily/2022.csv.gz"   # 应存在且约 22 MB
```

### 方式 B（兜底）：云上现生成

只有当**没有任何已生成日历的来源**时，才在新机器上用本目录脚本直接连通达信主站抓取并生成：

```bash
cd tick-stock-panel/backend
# 全量：1990-2026，写到 $TSP_DATA/backup_export/daily/kline_daily/<YEAR>.csv.gz
nohup uv run --no-sync python scripts/local/gen_calendar_all.py \
  > "$TSP_DATA/gen_calendar.log" 2>&1 &
```

- 可选参数 `LIMIT`：`./gen_calendar_all.py 20` 只抓前 20 只（调试用）。
- 耗时：约 4756 只股票、每只抓全部历史日K，实测 **25–35 分钟**。
- 产出：37 个年份文件，约 **308 MB / 1676 万行**。2022 = **1,107,198 行 / 4,756 只**（与官方 2022 数字一致）。

生成完成后**自检**是否对齐到导出脚本期望的位置：

```bash
ls -lh "$TSP_DATA/backup_export/daily/kline_daily/2022.csv.gz"   # 应存在且约 22 MB
```

> **为什么不用官方的「本机日K导出」来造日历？** 官方 `export_history_backup.py` 不支持按年过滤，
> 会导出全历史、跑几天且不现实；且依赖项目 `data/` 维表（新机器上没有）。
> `gen_calendar_all.py` 直接用 `app/plugins/qatdx/client.py` 取数，不依赖维表、按年拆分，更适合此场景。

#### 补漏（仅方式 B 需要时）

若生成时个别主站抽风导致某只股票返回空（实测最多约 2105 只瞬时空结果），用补跑脚本补齐：

```bash
cd tick-stock-panel/backend
nohup uv run --no-sync python scripts/local/gen_calendar_complement.py \
  > "$TSP_DATA/gen_complement.log" 2>&1 &
```

补跑逻辑：找出「日历文件里缺失的股票」重新抓取（带空结果重试换主站），追加写入已有年份文件。

---

## 5. 步骤 3：跑导出

```bash
cd tick-stock-panel/backend/scripts/cloud

# 推荐放 tmux，断 SSH 不影响
tmux new -s exp
BASE_DIR=/data ./export_year_cloud.sh all --year 2022
# Ctrl+B 再按 D 脱离；回来 tmux attach -t exp
```

`all` = `setup → check → plan → run → collect`。`check` 会确认 Python / 磁盘 / 7709 出网 / 日历齐全，
`plan` 会打印分片数（2022 应为 **353 片**，对不上先查日历）。

另开窗口看实时进度：

```bash
./export_year_cloud.sh status --year 2022
```

### 断点续跑

崩了 / SSH 断了 / 机器休眠后恢复，**重跑同一条 `all` 命令即可**：

- 分片级：`shard_done` 表记录已完成的分片，重跑跳过已完成片。
- 年份级：全跑完才写 `.year-2022.done`，有它则打印「已完成，跳过」。
- 包装脚本还会在异常退出后**自动重试**（默认等 120s、最多 50 次）。

### 抗沙箱回收 / 容器重启：监管脚本 + 心跳守护自启

在 workbuddy 这类会话型沙箱里，`export_year_cloud.sh run` 即便用 `tmux` 也扛不住两件事：
- **会话结束 / 空闲回收**：AI 对话结束或机器空闲后，非平台托管进程会被清理；
- **容器整机重启**：沙箱 runtime 重建，所有进程随之一并消失。

这两类都会让导出静默停掉（连 monitor 也一起死，hourly_report 不再更新）。本目录提供三层防护：

**第 1 层 · 监管脚本 `run_export_supervisor.sh`（抗会话/沙箱回收）**

在官方 `export_year_cloud.sh run` 之外再套一层 supervisor：进程一旦退出且任务未完成（无 `.year-2022.done`），30s 后自动重拉，直到出现完成标记才退出。必须**脱离会话**启动，否则仍会被回收：

```bash
cd tick-stock-panel/backend/scripts/local
setsid nohup bash run_export_supervisor.sh >> /workspace/tick-data/export_supervisor.log 2>&1 &
```

> **参数全走环境变量，换年份/换机器无需改脚本**：`YEAR`（默认 2022，也可作第 1 个位置参数）、`BASE_DIR`（默认 `/workspace/tick-data`）、`REPO_DIR`、`OUT_DIR`、`TICKS_DIR`、`MAX_RESTART`（默认 0 = 不限）。例如导出 2023 年：
>
> ```bash
> YEAR=2023 setsid nohup bash run_export_supervisor.sh >> /workspace/tick-data/export_supervisor.log 2>&1 &
> ```
>
> ⚠️ **坑 1（务必注意）**：`BASE_DIR` 等变量在脚本内已 `export` 给子进程 `export_year_cloud.sh`。如果不导出，官方脚本会回落到它自己的默认 `BASE_DIR=/data`，于是报「缺日历 `/data/backup_export/daily/kline_daily/<YEAR>.csv.gz`」并陷入 30s 重试死循环——**看到这个 `/data` 路径，基本就是环境变量没传下去**。

**第 2 层 · 心跳守护 `tick_export_daemon.sh`（抗容器重启，注册平台自启）**

仅靠 `setsid nohup` 仍扛不住容器整机重启。把「导出监管 + 监控」注册成**平台自启**即可：平台只自动保活注册了自启且监听端口的服务。守护脚本监听本地端口做健康检查，每 30s 巡检、保证 `run_export_supervisor.sh` 与 `monitor_export.sh` 始终在跑；它被杀后平台立刻重拉，从而自动续跑导出。

注册（一次性，双 app 同时保活下载服务 3000 与守护 8099）：

```bash
cat > /tmp/reg_autostart_dual.js <<'EOF'
const http = require('http');
const cfg = {
  app: [
    { name: 'download', port: 3000,
      cmd: 'cd /workspace/tick-stock-panel/tools/download-server && TSP_DATA=/workspace/tick-data PORT=3000 node server.js' },
    { name: 'export-daemon', port: 8099,
      cmd: 'bash /workspace/tick-stock-panel/backend/scripts/local/tick_export_daemon.sh' }
  ],
  restart: true
};
const body = JSON.stringify(cfg);
const req = http.request(
  { hostname: '127.0.0.1', port: 65310, path: '/replaceCloudStudioConfig', method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } },
  (res) => { let d = ''; res.on('data', c => d += c); res.on('end', () => console.log('HTTP', res.statusCode, d)); }
);
req.on('error', e => { console.log('ERR', e.message); process.exit(1); });
req.write(body); req.end();
EOF
node /tmp/reg_autostart_dual.js
```

- 注册后 `8099` 端口即被监听、守护进程拉起；`3000` 下载服务不受影响（注册时特意带上）。
- 此后**任何容器重启 / 会话回收**，平台都会自动拉起守护 → 自动恢复导出，无需人工干预。
- 验证守护在线：`curl -s http://127.0.0.1:8099/` 返回 `tick-export-daemon OK`。

> ⚠️ **坑 2（单实例：用 mkdir 选主，别用 flock）**：平台自启有可能会同时拉起多个实例（实测一次起了 3 个），必须保证全局只有 1 个 daemon（多个 daemon 同时 `ensure` 会拉出多条监管/导出链，抢 DuckDB 锁导致崩溃）。
> 本脚本用**原子 `mkdir` 选主**：`mkdir $BASE_DIR/.export_daemon.leader` 成功者为唯一 leader，失败方读 `pid` 判断持有者存活则退出，陈旧标记（容器重启留下的）会被清理后重试。端口 8099 绑定 + `ss` 归属校验作为第二道闸（平台也需要它监听端口才认这个自启服务），并对**父进程为 1 的孤儿健康检查 python** 做一次清理后重试。
> **不要**改回 `flock`：实测本机 overlay 文件系统上它并非可靠互斥（多个实例都能"拿到锁"），而且 `exec 9>锁文件` 在本环境脚本里会莫名阻塞在 `exec` 不返回。
> 也不要用 `kill -0` 判断绑定是否成功——bind 失败的 python 会短暂处于僵尸态，`kill -0` 仍返回成功，导致误判。
>
> 另外健康检查的启动**不能用 `HPID=$(start_health)`**：后台 python 会继承命令替换的管道 stdout 并一直持有，`$(...)` 永远等不到 EOF，daemon 会卡死在 `anon_pipe_read`、主循环永不执行。当前实现是"直接调用 + 全局变量收 PID"。

**第 3 层 · 定时任务保活（实测不可行，已废弃）**

沙箱进入**空闲休眠**时，整个容器不执行任何进程——daemon、supervisor、monitor 全部冻结，本地 `crontab` 也不会触发。直觉上应该用 workbuddy 的自动化定时任务定时唤醒，但**实测这条路走不通**：

> 2026-11-04 实测结果：`sandbox_agent` 类型的自动化任务**跑在一个全新的临时沙箱里**，与正在跑数的沙箱不共享 `/workspace`、不共享进程、不共享数据盘。任务执行时 `/workspace` 只有 `.codebuddy`，`tick-stock-panel`、`keepalive.sh` 全都不存在：
>
> ```
> bash: /workspace/tick-stock-panel/backend/scripts/local/keepalive.sh: No such file or directory
> （退出码 127）
> ```
>
> 也就是说定时任务既**唤醒不了原沙箱**，也**续跑不了导出**，只会每小时空跑一次白烧额度。已删除该任务，不要在沙箱场景重复尝试。

因此第 3 层作废，可用的只剩两个方向：

1. **保持会话活跃**（唯一确定有效）：导出没跑完时别关页面/断开会话，让平台判定为空闲的时间窗口不要出现。
2. **外部探活**（未验证）：从真实云主机（不受沙箱休眠策略约束）定时请求沙箱公网地址，例如另一台机器上加：
   ```bash
   # crontab -e，每 5 分钟一次
   */5 * * * * curl -s -o /dev/null -m 10 https://<沙箱域名>/
   ```
   是否被平台计为"活跃"取决于其空闲判定实现，需实测；不生效也不会造成损害。

`keepalive.sh` 仍然保留，它本身没问题——在**有持久 workspace 的真实云主机**上（例如另一台自己 cron 的场景）依然可用：

```bash
bash backend/scripts/local/keepalive.sh     # YEAR 默认 2022；变量 YEAR / OUT_DIR / TICKS_DIR 可覆盖
```

它只做三件事，**不含任何 kill / 重启逻辑**，绝不干扰正在跑的导出：

1. `.year-$YEAR.done` 存在 → 直接退出，不动作；
2. daemon 在跑 → 只回报当前分片数（`ALIVE: ...`）；
3. daemon 不在 → `setsid nohup` 拉起守护（`RESTART: ...`）。

### 实测 2022 数字（用于判断跑得对不对）

| 项 | 值 |
|---|---|
| 股票 / 交易日 | 4,756 只 / 242 天 |
| 股·天 | 1,107,198 |
| 分片 | 353 片 |
| 逐笔估算 | 50.9 亿笔 |
| 逐笔包 gz | 约 21.4 GB |
| 分钟包 | 约 4.4 GB（2.6 亿行左右） |
| 耗时 | 约 10–14 小时（实测 2 分钟/片 × 353） |

---

## 6. 步骤 4：进度监控（每小时记日志）

导出要跑大半天，本目录的 `monitor_export.sh` 可后台跑，每小时把进度写进 `hourly_report.log`：

```bash
# 默认 TSP_DATA=/workspace/tick-data；换数据盘时先 export TSP_DATA=/data
nohup bash scripts/local/monitor_export.sh >/dev/null 2>&1 &
```

日志样例：

```
[2026-11-03 09:32:12] 监控启动 (total shards=353)
[2026-11-03 09:32:12] parts=12/353 (3.4%) ticks=350M proc=YES zip(min/ticks)=NO/NO errors=0
[2026-11-03 10:32:12] parts=42/353 (11.9%) ticks=1.3G proc=YES zip(min/ticks)=NO/NO errors=0
```

> **部署到新机器时需改的硬编码点**（脚本头部）：
> - `TOTAL=353`：按实际年份分片数调整（`plan` 子命令可查）。
> - 第 24 行校验脚本路径 `/workspace/tick-stock-panel/backend/scripts/local/verify_export_result.py`：改成新机器的仓库绝对路径。
> - 年份 `2022` 与 `.year-2022.done`、`2022_ticks.zip` 等固定字符串：换年份时一并替换。

---

## 7. 步骤 5：成果校验（导出完成后）

官方 `collect` 只检查文件存在 + 体积 + 打印 scp 命令，**不校验数据正确性**。本目录的
`verify_export_result.py` 做深度校验，`monitor_export.sh` 会在检测到导出完成时**自动调用**它。

手动运行：

```bash
cd tick-stock-panel/backend
uv run --no-sync python scripts/local/verify_export_result.py --year 2022
# 可选：
#   --sample 50     抽样深检股票数（默认 50，含茅台/工行等知名股 + 随机）
#   --full          精确统计逐笔总笔数（较慢，否则抽样外推）
#   --base /data    覆盖 TSP_DATA
```

校验维度：

1. 两个成品 zip 存在 + 体积合理（分钟 ≈4.4GB、逐笔 ≈21GB，过小告警）
2. zip 完整性（分钟全量 `testzip`；逐笔抽样 60 个成员实际读取）
3. 股票覆盖：导出股票集 vs 日历股票集，算覆盖率 + 缺失清单
4. 抽样深检（抽 50 只）：行数 >0、日期全部落在 2022、价格列 >0、成交量 ≥0、列齐全；分钟包还比对每只交易日数与日历一致
5. 规模对账：逐笔总笔数 vs 脚本估计 50.9 亿

报告写到 `$TSP_DATA/verify_report_<YEAR>.txt` 并打印。

---

## 8. 步骤 6：取回 / 下载成果

### 成果文件位置

| 成果 | 路径 | 预计大小 |
|---|---|---|
| 分钟 K 线包 | `$BASE_DIR/backup_export/minute/kline_minute_1m/2022.zip` | ~4.4 GB |
| 逐笔(tick)包 | `$BASE_DIR/ticks/2022_ticks.zip` | ~21 GB |

### 方式 A：SSH scp（官方推荐，最稳）

```bash
# 在本机执行
scp root@<云IP>:/data/backup_export/minute/kline_minute_1m/2022.zip  ./backup_export/minute/kline_minute_1m/
scp root@<云IP>:/data/ticks/2022_ticks.zip                          ./tsp_backup_ticks/
```

### 方式 B：公网下载服务（断点续传）

本目录的 `tools/download-server/server.js` 是一个只读静态服务，暴露 `$TSP_DATA` 整个目录，
支持目录浏览 + **HTTP Range 断点续传**，适合超大文件：

```bash
cd tick-stock-panel/tools/download-server
PORT=8088 TSP_DATA=/data node server.js
# 监听 0.0.0.0:8088，访问 http://<云IP>:8088/ 即可看到目录
```

- 发布/转发：用你所在平台的「发布为应用 / 端口转发 / 反向代理」把 `8088` 暴露为公网链接即可。
- 下载建议：逐笔包 ~21GB，**用支持断点续传的下载器**更稳：

```bash
# 浏览器直下可能不稳，推荐：
aria2c "http://<域名或IP>:8088/ticks/2022_ticks.zip"
wget -c "http://<域名或IP>:8088/ticks/2022_ticks.zip"
```

- 只取上面两个 `2022.zip`，`ticks/2022/part-*.csv.gz` 等中转分片是过程文件，不必下载。

---

## 9. 目录结构（以 `BASE_DIR=TSP_DATA=/data` 为例）

```
/data
├── backup_export/
│   ├── daily/kline_daily/        # 日历：1990.csv.gz … 2026.csv.gz（gen_calendar_all.py 产出）
│   ├── minute/kline_minute_1m/
│   │   ├── 2022.zip              # ★ 分钟成果
│   │   └── .year-2022.done      # 年份完成标记
│   ├── logs/2022/                # run-*.log / progress-*.log
│   ├── gaps-2022.jsonl           # 个别股票日数据不全记录（正常）
│   └── failures-2022.jsonl       # 单只失败记录（用 patch_symbol.py 补）
└── ticks/
    ├── 2022/part-*.csv.gz        # 逐笔中转分片（过程文件）
    └── 2022_ticks.zip            # ★ 逐笔成果
```

---

## 10. 给新机器 AI 会话的开场白（模板）

在另一台云主机的 AI 会话里，可直接粘贴这段，让它把全流程跑完并盯着：

```
我在一台全新的 Linux 云主机上导出 A 股历史分钟/逐笔数据，你负责把这件事跑完并盯着。

仓库：/root/tick-stock-panel（分支 fork-integration，已 clone 并 checkout）
数据盘：/data（BASE_DIR 与 TSP_DATA 都指向它）
目标年份：2022

流程（按顺序）：
1. 先跑 backend/scripts/cloud/export_year_cloud.sh check --year 2022，把每一项结果告我。
   任何 FAIL 停下说明原因，尤其 7709 出网不通不许硬跑。
2. 日历优先从已生成日历的云主机拷贝（不要每台都重抓）：
   - 源机可 SSH：scp root@<源云IP>:/data/backup_export/daily/kline_daily/*.csv.gz /data/backup_export/daily/kline_daily/
   - 否则用源机下载链接：wget -c "https://<源机域名或IP>:8088/backup_export/daily/kline_daily/2022.csv.gz"（年份按需替换）
   - 没有任何来源时再现生成：cd backend && uv run --no-sync python scripts/local/gen_calendar_all.py（约 30 分钟）
3. check 全过 + 日历生成完后，BASE_DIR=/data ./export_year_cloud.sh all --year 2022，放 tmux。
4. 同时后台跑 bash scripts/local/monitor_export.sh（每小时记 hourly_report.log，完成自动校验）。
5. 导出完成后，脚本会自动调用 scripts/local/verify_export_result.py 校验，把报告结论给我。

约束：
- 只允许新增文件，不许删除 /data 下任何已有数据，不许清理 _staging_minute。
- 不要改 workers/endpoints 参数，也不要因为进度慢就调参或改脚本。
- 进程异常退出时包装脚本会自动重试，先看日志判断原因再动作，不要手动 kill。
- 报进度用 date 取真实时间，不要沿用上次播报的时间戳。
- 导出用 `setsid nohup bash scripts/local/run_export_supervisor.sh` 起（抗回收）；若在 workbuddy 沙箱，把 `scripts/local/tick_export_daemon.sh` 注册为平台自启，容器重启后自动续跑。
```

---

## 11. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `check` 报 7709 全不通 | 机房封非标端口 / 境外机房 | 换境内机房或安全组，**别硬跑** |
| `check` 报磁盘不足 | 数据盘没挂上或太小 | 挂盘到 `/data`，或 `BASE_DIR=/其他路径` 且同步设 `TSP_DATA` |
| 日历生成某只返回 0 行 | 选中抽风/无数据主站 | 跑 `gen_calendar_complement.py` 补缺 |
| `plan` 分片数不是 353 | 日历路径/年份不对 | 确认 `$TSP_DATA/backup_export/daily/kline_daily/2022.csv.gz` 存在 |
| 日志出现 `IncompleteDayError` / gaps | 主板某天数据不全 | 正常现象，记在 `gaps-2022.jsonl`，不影响整年 |
| 进度速率掉一半 | 与别的任务抢同一批主站 | 把 `--endpoints` 降到 4 再观察 |
| 进程被 OOM kill | 内存不足 | 加内存，或 `--workers 2` |
| 下载 21GB 中断 | 公网隧道不稳 | 用 `aria2c` / `wget -c` 断点续传；或改用 scp |
| 导出/监控静默停掉、hourly_report 不再更新 | 会话被回收或容器重启杀掉非平台托管进程 | 用 `setsid nohup` 起 `run_export_supervisor.sh`；并注册 `tick_export_daemon.sh` 平台自启（见第 5 节） |

---

## 附：本目录工具清单

| 文件 | 作用 |
|---|---|
| `gen_calendar_all.py` | （兜底）云上现生成 1990–2026 全量日K日历（无现成拷贝来源时） |
| `gen_calendar_complement.py` | 日历补漏：补齐缺失股票（空结果重试换主站） |
| `monitor_export.sh` | 每小时记录导出进度，完成时自动触发校验 |
| `verify_export_result.py` | 导出成果的深度学习正确性校验 |
| `../tools/download-server/server.js` | 公网静态下载服务（目录浏览 + Range 断点续传） |
| `run_export_supervisor.sh` | 导出监管：在 `export_year_cloud.sh run` 外再套 supervisor，进程退出且未完成时自动重拉（抗会话/沙箱回收） |
| `tick_export_daemon.sh` | 心跳守护：监听本地端口 + 平台自启，容器重启/会话回收后自动拉起监管与监控，实现导出自动续跑 |
| `keepalive.sh` | 轻量保活探针：唤醒 + 按需拉起守护，幂等不干扰导出。**仅适用于真实云主机 cron**；沙箱里的自动化定时任务跑在新沙箱、读不到本文件，实测无效 |
