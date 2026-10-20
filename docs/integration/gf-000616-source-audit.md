# GF-000616-A 竞价路径原始采集与 QA 审计

- 审计日期：2026-10-20
- 功能编号：GF-000616-A
- 源仓库：`JackTheRipper1998/tickflow-stock-panel`
- 固定分支：`main`
- 固定快照：`f2a939243ee0ed76db16eb1a99b9d4072efcfcc0`
- 源代码目录：`/tmp/tick-stock-panel-fork-migration/JackTheRipper1998__tickflow-stock-panel`
- 核心提交：`b95b91bdefbe12b23f28cefb74d5efb5c61233de`
- 核心提交主题：`feat(prereg): auction 因子扩展预注册清单 + 竞价路径采集器 (F3/F6/F7 前瞻数据)`
- 证据：源提交 diff、文件清单和固定快照均位于仓库外迁移临时目录；未将源代码复制到目标仓库分析目录。

## 1. 功能意义

GF-000616-A 不是 bug 修复，而是为后续竞价因子 F3/F6/F7 建立前瞻原始数据资产：

- F3：竞价匹配价格路径形态；
- F6：未匹配量不平衡度；
- F7：可撤单阶段与不可撤单阶段的盘口变化/疑似撤单行为。

没有 09:16:15–09:25:15 的逐分钟原始快照，后续只能使用终值，无法区分竞价路径和终局结果。该能力不应直接宣称收益提升，也不应在 QA 前作为交易信号启用。

## 2. 源实现溯源

核心提交新增或修改：

- `backend/app/services/auction_path_service.py`：每分钟采集最多 200 只趋势候选股的五档盘口和实时累计量/额；按交易日写入 `data/user_data/auction_path/date=YYYY-MM-DD/part.parquet`。
- `backend/app/jobs/daily_pipeline.py`：通过 APScheduler 在 09:16:15–09:25:15 每分钟触发一次，共 10 拍。
- `backend/app/main.py`：创建服务并注入 `repo` 与 `app.state`。
- `backend/scripts/verify_auction_path.py`：盘后独立 QA，包含五项检查：拍完整性、09:25 终值与日线开盘价、累计量与 09:30 竞价分钟 K、买一/卖一相等比例、可撤单/不可撤单阶段结构。
- `docs/prereg-2026-07-19-auction-factors.md`：预注册 F1–F11 定义、参数网格、数据状态和首日 QA 追加规则。

源仓库没有为该采集器提供独立自动化测试；验收依赖真实交易日数据和 QA 脚本。

## 3. 目标侧已有能力

目标仓库已经具备可复用基础设施：

- `backend/app/services/depth_service.py`：五档批量取数、Provider 路由、批量限速、失败批隔离、能力检查和独立 `depth5` 落盘。
- `backend/app/data_providers/base.py`：统一 `get_depth_batch()` Provider 契约。
- `backend/app/tickflow/capabilities.py` / `policy.py`：`DEPTH5_BATCH` 能力矩阵和限额解析。
- `backend/app/services/fs_utils.py`：原子 Parquet 写入。
- `backend/app/services/trading_day.py` 与 `daily_pipeline._holiday_skip()`：交易日/休市日门控。
- `backend/app/services/quote_service.py`：实时 enriched 快照，可提供累计成交量和成交额。
- `backend/app/indicators/pipeline.py`：日线指标计算和 enriched 数据。
- GF-000617-A 已接入竞价量两腿快照，但它只提供竞价量比，不保存逐分钟五档路径。

因此，GF-000616-A 应作为独立 `auction_path` 原始数据旁路，不修改 `auction` 标准长表、不写回 enriched 14 列、不复用 `depth5` sealed 语义。

## 4. 源实现不能直接复制的原因

1. **取数路由不同**：源代码直接调用 `tf.depth.batch`；目标必须使用统一 Provider 的 `get_depth_batch()`，复用当前生效数据源、能力矩阵、批量限制和失败语义。
2. **候选池依赖源策略**：源实现按 `strong_momentum_auction` 的趋势层规则计算候选池；目标没有同名策略实现，必须在采集服务内复用目标已有指标/维表，或建立明确的只读候选池函数，不能静默假设源策略存在。
3. **交易日门控不同**：源调度只使用工作日 Cron；目标必须先通过交易日 oracle，避免节假日和临时休市写入空数据或误标记采集日。
4. **落盘安全不同**：源每拍直接 `write_parquet` 重写文件；目标应使用原子写，且要处理 reload/手动任务/调度任务并发，避免读到半文件。
5. **重启恢复缺失**：源服务只把当日行保存在内存，进程重启会丢失此前拍次；目标应在服务初始化或每拍前加载当日已有分区，做到追加/幂等写，至少不能覆盖既有拍次。
6. **数据目录不可硬编码**：源 QA 脚本把数据目录固定为仓库相对路径；目标必须读取 `settings.data_dir` 或命令行参数，适配本机服务数据目录。
7. **QA 语义需保守**：源文档对买一/卖一和未匹配量有待实证说明；目标不得在首日 QA 前把这些字段命名为已验证因子或直接进入策略执行。

## 5. 建议实施范围：GF-000616-A-Foundation

第一阶段只建设原始采集与可验证数据资产：

- 新增独立 `AuctionPathService`；
- 候选池最多 200 只，候选数量、截断和来源写入日志/采集元信息；
- 09:16:15–09:25:15 每分钟采集 10 拍；
- 复用目标 Provider 的 `get_depth_batch()` 和能力限额；
- 采集五档原始价格/量、源时间戳、实时累计成交量/额；
- 按日独立目录落盘，使用原子写和进程内锁；
- 交易日门控、缺能力跳过、单批失败隔离、空数据不伪造；
- 服务注入 `app.state`，调度器注册单独 job；
- 不改 enriched、auction、depth5 sealed、策略执行和前端页面。

## 6. 建议实施范围：GF-000616-A-QA

第二阶段在至少一个真实交易日产生数据后执行：

- Q1：09:25 终值匹配价与日线开盘价，不一致率门槛暂按源脚本 1%，结果只作语义验证；
- Q2：终值累计量与当天 09:30 分钟 K 竞价量的分布比较；
- Q3：09:20–09:25 买一/卖一相等比例，判断是否为竞价撮合视图；
- Q4：09:16–09:19 与 09:20–09:24 的盘口非空率、价差和结构差异；
- Q5：10 拍完整性、每拍覆盖率、空值率和标的去重；
- QA 结果无论 PASS/FAIL 都追加到预注册文档，不修改原始定义。

在 QA 通过前，不实施 F3/F6/F7 正式计算、策略接入、回测收益结论或前端交易提示。

## 7. 风险与验收门禁

- **数据源风险**：盘前时段 Provider 可能不返回盘口；缺数据应记录原因并保持空值，不回退到普通盘中五档冒充竞价路径。
- **资源风险**：最多 200 只 × 10 拍，需按 Provider batch 限额串行执行；不能与其他重型同步任务并发启动。
- **时间风险**：竞价路径是不可补拍数据，调度错过后只能记录缺拍，不能用盘后数据伪造。
- **语义风险**：买卖一、匹配价、未匹配量字段含义必须由首日 QA 证实。
- **一致性风险**：每拍落盘需避免覆盖已有拍次，进程重启后应能恢复当日文件。
- **前瞻性风险**：采集数据只能用于未来样本，不能回填历史或把采集日之前的数据当作已知因子。

验收门禁：

1. 自动测试覆盖候选池上限、能力缺失、Provider 批次失败、空返回、交易日门控、原子落盘和重启恢复；
2. 至少一个真实交易日生成 10 拍或明确记录缺拍原因；
3. QA 脚本完成 Q1–Q5 并追加结果；
4. 用户人工确认通过后，才可评估是否进入 F3/F6/F7 因子实施。

## 8. 审计结论

- GF-000616-A 是真实新增的数据底座能力，建议迁移。
- 不建议直接复制源仓库 458 行提交；目标侧应以现有 Provider、DepthService、交易日门控和原子写为基础做最小适配。
- 建议拆为 `GF-000616-A-Foundation` 和 `GF-000616-A-QA` 两个实施阶段；当前仅完成审计，未进入实施。
- 当前状态：`待迁移前确认`。未修改业务代码、未安装依赖、未部署、未重启、未提交、未推送。

## 9. GF-000616-A-Foundation 实施记录（2026-10-20）

已按确认范围完成目标侧 Foundation：

- 新增 `backend/app/services/auction_path_service.py`；
- 复用目标 `DepthService._call_depth_batch()`，间接使用当前 Provider、能力矩阵、批量限速和失败隔离；
- 接入目标 `app.state` 和 `daily_pipeline`，交易日 09:16:15–09:25:15 每分钟触发一次；
- 候选池最多 200 只，按目标日线指标和 instruments 股本字段筛选；
- 按 `data/user_data/auction_path/date=YYYY-MM-DD/part.parquet` 独立落盘；
- 使用 `atomic_write_parquet`，按拍以 `date/ts/symbol` 幂等去重，并从已有当日文件恢复，避免 reload/重启覆盖此前拍次；
- 能力缺失、候选池为空、盘口空返回和累计量读取失败均采用跳过/null 降级；
- 未修改 enriched、auction 标准表、depth5 sealed、策略执行或 F3/F6/F7 因子。

自动验证：

- `uv run pytest -q tests/test_auction_path_service.py tests/test_auction_strength_service.py tests/test_auction_service.py` → `17 passed`；
- `python -m compileall` 与 `git diff --check` 通过；
- import/scheduler contract check 通过。

Foundation 已有部分自动测试通过，但验收覆盖尚不完整，不能等同于完整自动验证通过；真实交易日路径数据及 Q1–Q5 QA 尚未验证。当前待补齐验证与用户确认，不解锁下一阶段。未主动重启、未部署、未提交、未推送；运行中的 reload 服务是否已自动加载需另行核对。

## 10. Foundation 验收说明与覆盖纠正（2026-10-20）

### 入口与前置条件

本功能没有新增前端页面、按钮或专用查询 API，不能通过 3018 页面是否可访问判断采集成功。

真实采集需确认：运行进程已加载采集服务与调度任务；交易日 oracle 未判休市；当前生效五档源可用；趋势候选池非空；服务在北京时间 09:16:15–09:25:15 期间持续运行。不得在非竞价时段直接调用 `collect_tick()` 伪造验收数据。运行态调度注册尚未核验，不能把函数可导入等同于实际 job 已注册。

### 已有证据与缺口

此前 `17 passed` 是三份测试文件合计，并非 17 项新增采集器测试；采集器文件目前只有 3 个测试函数。

已验证：缺少 DEPTH5_BATCH 时不写文件；盘口空返回时不写文件；临时目录中成功写入约定 26 列、同拍同股去重，以及新服务实例读取已有文件后重复同一拍仍只有一行。

尚未验证：候选池真实筛选/200 只上限；Provider 路由和失败批隔离在采集链路中的集成；交易日门控；实际调度注册与完整 10 拍；重启后追加不同拍次；写入失败时旧文件保留及临时文件清理；并发一致性；真实盘前字段时效、覆盖率和数值语义。成功调用原子写工具不等于已做失败注入测试。

### 可执行的只读文件检查

以下命令作为代理技术验收的可复现检查步骤保留，不要求用户 SSH 登录或执行。由代理在必要执行授权具备后，于真实交易日 09:25 后在服务器上检查，将日期替换为实际待验收日期。不安装依赖、不触发采集、不修改项目数据：

```bash
cd /home/tick-stock-panel/backend
uv run --no-sync python - 2026-10-20 <<'PY'
import sys
from pathlib import Path
import polars as pl
from app.config import settings
from app.services.auction_path_service import PATH_COLUMNS

day = sys.argv[1]
path = Path(settings.data_dir) / 'user_data' / 'auction_path' / f'date={day}' / 'part.parquet'
print('读取路径:', path)
if not path.exists():
    raise SystemExit('无采集文件：待核对服务、调度、交易日、候选池和数据源；不能判定通过。')
df = pl.read_parquet(path)
missing = sorted(set(PATH_COLUMNS) - set(df.columns))
print('行数:', df.height, '缺少字段:', missing)
if missing:
    raise SystemExit('字段不完整，不能判定通过。')
print('日期:', df['date'].unique().to_list())
print('重复键数量:', df.height - df.unique(subset=['date', 'ts', 'symbol']).height)
print(df.group_by('ts').agg(
    pl.len().alias('rows'),
    pl.col('symbol').n_unique().alias('symbols'),
    pl.col('bid_p1').null_count().alias('bid_p1_null'),
    pl.col('quote_cum_vol').null_count().alias('quote_cum_vol_null'),
).sort('ts'))
PY
```

命令读取 `.env`/当前环境下的 `settings.data_dir`，须与运行服务的数据目录一致。此命令仅作为验收说明，本轮文档修订未执行。

### 预期结果、判定与限制

- 所有约定字段存在、日期与验收日一致、重复键为 0，每拍唯一股票数不超过 200。
- 候选池非空且数据源正常时，应覆盖 09:16 至 09:25 的 10 个分钟拍次；`ts` 是实际开始采集时间，不能仅凭秒数恰好为 15 判断成败，还需核对迟到和耗时。
- 累计量/额允许为空，但必须保留缺失事实；盘口和源时间戳即使非空，也不自动证明其新鲜度或竞价语义。
- 无文件、缺拍、重复键、字段缺失或单拍超过上限时，不判通过；记录日期、逐拍统计及采集/调度日志，排查原因。安全跳过可作为降级证据，不能替代成功采集证据。
- 没有文件时，当前实现未必有足够的逐拍原因日志，原因可观测性也须核验，不能保证现有日志已完整记录。
- 测试/落盘/调度基础验证属于 Foundation；Q1–Q5 的价格量能交叉验证、首日语义实证属于后续 QA，不能提前宣称通过。

当前状态：**已有部分自动测试通过，待补齐 Foundation 验证与用户人工确认**。本轮用户仅确认记录验收要求及修正文档，不构成 Foundation 验收通过，也不授权补测、代码修改、部署或重启。

## 11. 验收责任分工确认（2026-10-20）

用户确认：技术验收由代理完成，后续仅告知用户确实需要本人手动操作的项目。

- 本 Foundation **无需用户手动操作**；后台测试、运行服务/调度核对、采集文件检查、逐拍统计和恢复验证由代理承担。
- 第 10 节命令保留为代理执行方案和复现依据，不作为用户待办。
- 当前未补齐的技术验证和真实交易日数据检查仍是代理待办；未到交易窗口、执行未获授权或数据不足时明确标记待验证，不以用户回复通过替代技术证据。
- 用户是否最终接受阶段交付仍需单独确认；此次仅确认验收分工，不表示 Foundation 已通过，也不解锁 QA 或下一功能。
- 本轮只更新文档，未运行测试、采集、部署或重启，未提交或推送。未来补测、代码变更及系统状态变化继续按方案确认门禁执行。

## 12. Foundation 验收补测与最小修复（2026-10-20）

经用户确认实施后，补齐并执行 Foundation 隔离验收：

- 候选池仅读取 T-1 及更早日线分区，排除当日/未来分区，并锁定最多 200 只；
- 采集器自身增加 09:16:15–09:25:15 窗口门控，避免被手工调用或误调度时在竞价窗口外写入；
- 当日历史 Parquet 读取失败时拒绝从空表继续，保留原文件并返回 `history_read_failed`；
- 补测不同拍次重启追加、同拍去重、空盘口不落盘、能力不足不落盘、写入失败旧文件保护和交易日调度门控；
- 未切换数据源、未触发真实采集、未重启服务。

验证结果：

```text
37 passed in 2.25s
`git diff --check` passed
```

当前结论：Foundation 的隔离行为和安全降级测试通过；真实五档数据源、真实交易窗口 10 拍采集和真实文件字段/时效验证仍待运行条件满足后由代理完成。当前不记为用户最终通过，也不解锁后续 QA/下一功能。

### 2026-10-20：用户确认 Foundation 隔离验收结果

用户确认接受上述 Foundation 隔离验收结果。此确认仅覆盖自动化隔离测试与对应最小修复，不等同于真实五档数据源/竞价窗口验收，也不代表 GF-000616-A 整体最终交付通过；真实运行验收仍待具备 DEPTH5_BATCH 能力并在真实交易窗口完成。未解锁下一迁移功能。

## 13. 数据源完整性复核与源码证据优先规则（2026-10-20）

用户要求：迁移遇到问题时，优先从对应 Fork 仓库寻找答案，不要自行修正代码逻辑或另写平行实现。该要求已记录到根 `AGENTS.md`。

### 源仓库数据源结论

固定源仓库：`JackTheRipper1998/tickflow-stock-panel@f2a939243ee0ed76db16eb1a99b9d4072efcfcc0`；功能提交：`b95b91bdefbe12b23f28cefb74d5efb5c61233de`。

源采集器在 `backend/app/services/auction_path_service.py` 直接调用：

```python
from app.tickflow.client import get_client
depth_data = get_client().depth.batch(symbols)
```

源仓库 `backend/app/tickflow/policy.py` 明确将该能力登记为 `DEPTH5_BATCH`，注释说明使用 TickFlow SDK `0.1.23+` 的 `depth.batch`，对应官方 `/v1/depth/batch` 端点。源仓库没有为该迁移功能另行实现竞价专用五档 Provider；竞价路径使用 TickFlow 批量五档数据源。

### 目标侧差异

- 目标 `backend/pyproject.toml` 声明 `tickflow[all]>=0.1.23`，`backend/uv.lock` 当前锁定 `tickflow 0.1.25`；
- 目标已有 `TickFlowProvider`、`DepthService._call_depth_batch()`、`DEPTH5_BATCH` 能力探测和批量分片/限速路由；
- 目标竞价路径当前通过 `DepthService` 路由获取五档，兼容目标既有 Provider 抽象；这属于架构适配，不能据此宣称已获得真实数据源；
- 当前运行环境的 `capabilities.json` 没有 `DEPTH5_BATCH`，日志显示 TickFlow 五档能力不可用；因此尚无真实五档数据和 10 拍采集证据。

### 完整迁移判定

GF-000616-A 只有在以下各项均有证据时，才能称为完整迁移：

1. TickFlow SDK/Provider 与源 `depth.batch` 调用契约一致；
2. `DEPTH5_BATCH` 能力探测、权限/套餐和端点配置可用；
3. 竞价路径按源定义在 09:16:15–09:25:15 完成 10 拍采集；
4. 每拍数据字段、候选池上限、分片限速、失败隔离和落盘结果可核验；
5. 源仓库 QA 脚本对应的真实数据验证完成。

当前仅证明第 1 项的目标代码链和隔离行为基础存在；第 2–5 项仍未完成。不得通过自行编写另一套数据源逻辑绕过该缺口。
