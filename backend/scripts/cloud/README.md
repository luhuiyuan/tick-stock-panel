# 云端按年导出 1 分钟 + 逐笔数据

在 Linux 云主机上跑 `backend/scripts/export_history_minute.py`，一次产出一年的两个包：

| 产物 | 路径 | 内容 |
|---|---|---|
| 分钟包 | `<OUT>/minute/kline_minute_1m/<year>.zip` | 一只股票一个 csv，含 OHLCV + 主动买/卖量 |
| 逐笔包 | `<TICKS>/<year>_ticks.zip` | 一只股票一个 csv，3 秒快照原层 |

`export_year_cloud.sh` 只是把这套流程包成几条命令：自检 → 装依赖 → 看计划 → 跑 → 取回。
**它不删除任何数据**，只新增文件。

---

## 1. 云主机要求

| 项 | 要求 | 说明 |
|---|---|---|
| 系统 | Linux（Ubuntu 22.04 / Debian 12 / CentOS 8 均可） | 脚本用 bash + coreutils |
| CPU | **4 核** | 对应 `--workers 4`；2 核能跑但吞吐下降 |
| 内存 | **8 GB** | polars + DuckDB 处理一年 2.8 亿行 |
| 数据盘 | **≥70 GB** 空闲，挂在 `/data` | 脚本对逐笔目录硬性要求 **≥50 GB**（`est_gz×1.4+20`），不满足直接拒绝启动 |
| 出站 | **TCP 7709** 可达公网 | 通达信行情主站端口。这一步不通，后面全白搭 |
| 入站 | 只需 SSH | 不需要开放任何业务端口 |

> 磁盘别只算成品体积。2022 年成品约 15 GB，但过程中还要放逐笔分片（21 GB）、
> 分钟包（4.4 GB）、DuckDB 中转库（约 5.5 GB），峰值约 31 GB，留 70 GB 是安全余量。

---

## 2. 三步走

### 第 1 步：云主机上拉代码

```bash
git clone https://github.com/luhuiyuan/tick-stock-panel.git
cd tick-stock-panel
git checkout fork-integration
```

### 第 2 步：把日历文件传上去（只需一个文件）

脚本靠日K 备份里的 `<year>.csv.gz` 确定「哪些交易日、哪些股票在市」，这是**唯一**的数据前置。
整个备份包不用搬，**只要一年的 csv.gz，约 23 MB**。

在**本机**（Windows / Git Bash）执行：

```bash
ssh root@<云IP> 'mkdir -p /data/backup_export/daily/kline_daily'
scp "D:/tsp/backup_export/daily/kline_daily/2022.csv.gz" \
    root@<云IP>:/data/backup_export/daily/kline_daily/
```

> 没有这个文件也能自己生成，但要在云上重跑一遍日K 导出（`export_history_backup.py`），
> 为了 23 MB 去跑那个不划算——直接传。

### 第 3 步：一键跑

```bash
cd backend/scripts/cloud
chmod +x export_year_cloud.sh
./export_year_cloud.sh all --year 2022
```

`all` = `setup → check → plan → run → collect`。建议放进 tmux，断 SSH 不影响：

```bash
tmux new -s exp
./export_year_cloud.sh all --year 2022
# 按 Ctrl+B 再按 D 脱离；回来 tmux attach -t exp
```

另开一个窗口看进度：

```bash
./export_year_cloud.sh status --year 2022
```

---

## 3. 子命令

| 命令 | 作用 | 会改数据吗 |
|---|---|---|
| `check` | 自检：Python 版本、CPU/内存、磁盘、7709 出网、日历文件 | 否 |
| `setup` | 装 uv、`uv sync` 同步依赖、建目录（可重复执行） | 只建空目录 |
| `plan` | `--dry-run` 打印分片计划 | 只建空目录 |
| `run` | 正式导出，失败自动重试（断点续跑） | 是 |
| `status` | 只读查进度：片数、体积、磁盘、最后进度行 | 否 |
| `collect` | 核对成品 + 打印本机取回命令 | 否 |
| `all` | 上述串联 | 是 |

## 4. 参数

命令行参数：

```bash
--year 2022        # 目标年份
--workers 4        # 并发股票数，默认 4
--endpoints 8      # 主站连接数，默认 8
--max-retry 50     # 失败重试次数上限
```

路径用环境变量覆盖（默认值写在脚本头部）：

```bash
BASE_DIR=/data                              # 数据盘根
OUT_DIR=$BASE_DIR/backup_export             # 分钟表 + 日志 + manifest
TICKS_DIR=$BASE_DIR/ticks                   # 逐笔分片与成品
WORKERS=4 ENDPOINTS=8 YEAR=2022
```

例：数据盘挂在 `/mnt/ssd`：

```bash
BASE_DIR=/mnt/ssd ./export_year_cloud.sh all --year 2022
```

---

## 5. 断点续跑怎么生效

不需要你做什么，重跑同一条命令即可。两层保护：

1. **分片级**：`<OUT>/_staging_minute/minute-<year>.duckdb` 里有 `shard_done` 表。崩在第 N 片，
   重跑会跳过已完成的片，从断点继续。分片是**整只股票对齐**的原子单位，所以不会出现
   「逐笔写了、标记没落」的重复行。
2. **年份级**：全部跑完才写 `<OUT>/minute/kline_minute_1m/.year-<year>.done`。
   有这个标记，脚本会打印「已完成，跳过」。

包装脚本本身还会在进程异常退出后**自动重试**（默认等 120 秒、最多 50 次），所以
「网抖一下」「SSH 断了」都不用手动接。

> 唯一会拒绝重试的情况是退出码 2（缺日历 / 无可跑年份），那属于配置错误，重试没意义。

---

## 6. 日志在哪

```
<OUT>/logs/<year>/
├── run-20261103-071500.log          # 每次尝试一份，原始完整输出
├── progress-2026-11-03.log          # 每 10 分钟一行快照，按自然日切文件
└── progress-2026-11-04.log
```

`progress-*.log` 每行形如：

```
2026-11-03 08:10:00 parts=31 free=41GB last=[片 11/353 累计 8,153,021 行分钟 / 93.9M 笔逐笔 / 已跑 0.4h]
```

跨零点的长跑会自然分成两个文件，按天回看就行。

---

## 7. 跑完之后

### 取回（本机执行）

```bash
scp root@<云IP>:/data/backup_export/minute/kline_minute_1m/2022.zip  D:/tsp/backup_export/minute/kline_minute_1m/
scp root@<云IP>:/data/ticks/2022_ticks.zip                          E:/tsp_backup_ticks/
```

### 本地复核

```bash
cd backend
PYTHONPATH= .venv/Scripts/python.exe ../backup_export/_verify_minute_full.py 2022
```

检查四件事：股票日与日K **1:1**、15:00 那根 `O=H=L=C`、每日 bar 上限 **239**、
成交量差落在 **0 / +1 手**。

### manifest 怎么合

云上会生成一份只含 `<year>` 的 `minute_manifest.json`。**不要直接覆盖本地那份**，
把云上这条的数字手工抄进本地 `D:/tsp/backup_export/README.md` 的表格即可。

### 云主机清理

确认网盘传完、本地复核通过后，再决定是否释放云主机。**只要还可能需要重跑，就别删
`/data/backup_export/_staging_minute/`**——那是断点续跑的依据。

---

## 8. 2022 年的预期数字（用来判断跑得对不对）

| 项 | 值 |
|---|---|
| 股票 / 交易日 | 4,757 只 / 242 天 |
| 股·天 | 1,107,440 |
| 分片 | **353 片** |
| 逐笔估算 | 50.9 亿笔 |
| 逐笔包 gz | 约 21.4 GB |
| 分钟包 | 约 4.4 GB（2.6 亿行左右） |
| 耗时 | **14~16 小时**（13 小时抓取 + 1.5 小时打包） |

`plan` 子命令打印出来的分片数如果是 353，说明日历文件正确、代码正确，可以放心跑。
对不上就先停下查日历。

---

## 9. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `check` 报 7709 全不通 | 机房封非标端口 / 境外机房 | 换境内机房，或换安全组；**别硬跑** |
| `check` 报磁盘不足 | 数据盘没挂上或太小 | 挂盘到 `/data`，或 `BASE_DIR=/其他路径` |
| `plan` 打印「无日K日历，跳过」 | 日历文件路径/名称不对 | 确认 `<OUT>/daily/kline_daily/2022.csv.gz` 存在 |
| 日志出现 `IncompleteDayError` / 大量 gaps | 主板某天数据不全 | 正常现象，记在 `gaps-<year>.jsonl`，不影响整年 |
| 某只股票被记进 `failures-<year>.jsonl` | 单只票数据源与日K 对不上 | 本机用 `scripts/patch_symbol.py` 单独补进已打包的 zip |
| 跑着跑着速率掉一半 | 与本地/别的任务抢同一批主站 | 把 `--endpoints` 降到 4 再观察 |
| 进程被 OOM kill | 内存不足 | 加内存，或 `--workers 2` |

---

## 10. 给 AI 会话用的开场白

在云主机上开一个 AI 会话（或把这段贴给任意能执行命令的 agent），直接复制下面这段：

```
我在一台 Linux 云主机上导出 A 股历史分钟/逐笔数据，你负责把这件事跑完并盯着。

仓库：/root/tick-stock-panel （分支 fork-integration）
脚本：backend/scripts/cloud/export_year_cloud.sh
目标年份：2022

数据盘 /data，路径约定：
  分钟表与日志  /data/backup_export
  逐笔分片与成品 /data/ticks
  日历前置文件  /data/backup_export/daily/kline_daily/2022.csv.gz

执行顺序：
1. 先跑 ./export_year_cloud.sh check --year 2022，把每一项结果告我。
   任何一项 FAIL 就停下来说明原因，不要自己想办法绕过，尤其 7709 出网不通不许硬跑。
2. check 全过再跑 ./export_year_cloud.sh all --year 2022，放 tmux 里。
3. 之后每半小时跑一次 ./export_year_cloud.sh status --year 2022，把进度简报给我：
   已完成片数 / 353、已跑时长、速率、剩余 ETA、数据盘剩余空间、有无 gaps 或 failures 记录。
4. 日志出现「全部完成」或 .year-2022.done 出现时，跑 ./export_year_cloud.sh collect --year 2022，
   把两个 zip 的行数、只数、体积和 scp 命令给我。

约束：
- 只允许新增文件，不许删除 /data 下任何已有数据，不许清理 _staging_minute。
- 不要改 workers/endpoints 参数，也不要因为进度慢就调参或改脚本。
- 不要动仓库里 backend/app 下的任何代码。
- 进程异常退出时包装脚本会自动重试，你不要手动 kill 或重启它，先看日志判断原因再动作。
- 报进度必须用 date 取真实时间，不要沿用上次播报的时间戳。
```

---

## 附：与本机导出的差异

本机（Windows）那套流程有几个坑在云端**不存在**，别照搬：

| 本机做法 | 云端 |
|---|---|
| 命令前加 `PYTHONPATH=` | 不需要（脚本内部已 `env -u PYTHONPATH` 兜底） |
| `rm` 走回收站、删了不释放空间 | Linux 正常的 `rm`，立即释放 |
| `--ticks-roots E:/...,D:/...` 多盘挑空间最大的 | 单目录 `/data/ticks` 即可 |
| 用 `.bat` 起后台 | 用 tmux |
