# P1-002 源码审计报告

- **任务**：P1-002（K 线、分钟数据、交易日和历史回补）
- **审计日期**：2026-10-16
- **目标分支**：`fork-integration`
- **目标工作区 HEAD（审计时）**：`0a68bf46eed4a721c102864979344a1eb658d03f`
- **计划记录的目标基线**：`7708bec3`（本报告不改写该历史基线）
- **审计范围**：候选去重、仓库外固定源码快照、原始 diff、配套测试、目标调用链只读对照
- **明确不包含**：业务代码修改、测试/构建执行、依赖安装、数据修复、部署、重启

## 1. 门禁结论

P1-002 源码审计完成，任务进入 `待迁移前确认`。这不是迁移批准，也不代表 P1-001 已通过人工确认；P1-001 继续保持 `延后迁移`，真实行情链路仍未验收。

候选提交经 stable patch-id 去重后形成以下审计簇：

| 审计簇 | 结论 | 建议 |
|---|---|---|
| 分钟单股补拉、路由和进度回调（FU-000007、FU-000433） | **大部分已有**。目标已有 `sync_minute_single`、自定义分钟路由、2→3 参数回调适配和分钟路由测试；未发现应直接复制的完整增量。 | 不迁移；仅在后续定向验证中补看失败回调和插件权限边界。 |
| 分钟分区修复脚本（FU-000231） | **未纳入当前迁移**。候选主要是运维脚本和可选修复工具，目标没有同名脚本；它不是主数据链路的最小补丁。 | 另立运维工具评审，不随 P1-002 迁移。 |
| 复权日价格基准与脏成交额清洗（FU-000259、FU-000262） | **实质增量但范围过宽**。目标已有分钟原始基准、日 K 因子投影和金额字段规范，但未证明完全等价于候选中的跨回测/因子/行情全链路 `price_basis` 与 `amount_guard`。 | 不整批复制；拆成“价格基准契约”和“成交额单位/异常值”两个后续小审计。 |
| Fuyao 历史日 K 流式同步（FU-000380 及其审查修正） | **目标已具备等价能力**。目标存在 `iter_daily`、私有 `.daily_sync_staging`、完整拉取后提交、失败清理、陈旧 staging 清扫及相应用例。 | 不迁移；保留源提交作为等价性证据。 |
| 美西主机北京日期（FU-000418） | **目标已有**。`daily_pipeline` 使用 `cn_today()`，并有 `test_pipeline_beijing_today.py`。 | 不迁移。 |
| 分钟复权缺日 K 锚点保护（FU-000429） | **目标已有**。`migrate_minute_to_raw()` 对缺日 K 分区计数并不创建 `.raw_basis`，允许日 K 补齐后续跑；已有测试。 | 不迁移。 |
| 统一交易日历、持久化和调度门控（FU-000730、FU-000734） | **实质增量，建议优先迁移**。目标目前是 fuyao 日历/行情时间戳探针及 TTL 缓存，未见候选所述统一持久化日历、任意日期查询、所有调度复用和完整门禁测试。 | 仅迁移源实现中与交易日历契约直接相关的最小文件和测试；先人工确认范围。 |
| 休市日不伪造交易日数据（FU-000728） | **部分已有，仍有缺口**。目标已有交易日探针、实时轮询门控、最终行情定版/完整性机制和若干 `as_of` 读取；但候选覆盖的时间戳归属、落盘边界、最新交易日选择和读取侧全路径尚未证明完全覆盖。 | 与 FU-000730 合并成“交易日/休市日数据契约”小批迁移审计，禁止整批复制。 |
| 停牌残留与交易日窗口完整性（FU-000668、FU-000709） | **部分已有**。目标有停牌过滤、完整性扫描和实时聚合窗口逻辑，但候选新增的交易日计数、K 线根数核对和停牌残留防误判尚未逐项闭合。 | 作为交易日契约批次的配套差异，不单独迁移。 |
| Parquet 分区并发写保护（FU-000757 两个提交） | **实质增量，建议优先迁移**。目标已有乐观 upsert 和原子替换，但 `repository.py` 仍按“路径读文件后再 stat”取得基底与指纹，`fs_utils.atomic_write_parquet()` 仍使用固定 `<name>.tmp`；未达到源提交的同源快照和暂存名独占语义。 | 仅迁移快照读、唯一暂存文件名、锁内版本校验及对应测试；重点审查所有调用路径。 |

## 2. 固定源码与证据

所有源代码均在目标仓库之外固定，未写入 `analysis/`、`docs/` 或目标代码目录：

- `/tmp/tick-stock-panel-fork-migration/mygx-xx__tickflow-stock-panel/29273e7321e8a939002a716475c76c44118a7eba`
- `/tmp/tick-stock-panel-fork-migration/jie429__tick-stock-panel/cb807863f70d54603a6cf6082b82ecc90a699fb4`
- `/tmp/tick-stock-panel-fork-migration/kuku-26__tick-stock-panel/72b4d30fbffd9043c160b09368fe70e0eb8dda56`
- `/tmp/tick-stock-panel-fork-migration/m01i0ng__tick-stock-panel/d4be6141db48d1caee4145ee6172fc820213a5c4`
- `/tmp/tick-stock-panel-fork-migration/flyme8899__tick-stock-panel/6fb3915a22225ae1e317f6d3e319c8b5a81a184f`
- 既存快照：`/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa`（保留其既有未跟踪文件，不覆盖）

原始 diff、patch-id、SHA-256 和目标祖先关系清单：

`/tmp/tick-stock-panel-fork-migration/p1-002-audit-20261016/manifest.json`

该清单记录 19 条候选提交记录；其中 FU-000380 和 FU-000757 的多次提交分别按 stable patch-id 归并为演进链/同一功能簇，不将重复提交当作多个独立功能。

## 3. 关键目标侧证据

- 分钟单股入口：`backend/app/api/kline.py::sync_minute_single`；自定义源回调适配：`backend/app/services/kline_sync.py::_try_custom_minute`。
- 北京日期：`backend/app/jobs/daily_pipeline.py` 使用 `cn_today()`；目标已有 `backend/tests/test_pipeline_beijing_today.py`。
- 交易日探针：`backend/app/services/trading_day.py::is_trading_day()`，当前语义是“今天探针 + TTL 缓存”，不是持久化历史日历。
- 分钟复权锚点：`backend/app/services/minute_adjust.py::migrate_minute_to_raw()`，缺日 K 时增加 `skipped_no_daily` 且不创建 `.raw_basis`。
- Fuyao 流式同步：`backend/app/services/kline_sync.py` 的 `.daily_sync_staging` 提交流程，以及 `backend/tests/test_fuyao_daily_streaming_sync.py`。
- 并发分区写：`backend/app/tickflow/repository.py::_optimistic_upsert_partition()` 和 `backend/app/services/fs_utils.py::atomic_write_parquet()`。当前目标仍可见固定 `.tmp` 暂存和路径读/后 stat 的窗口，构成 FU-000757 的保留风险。
- 调度风险：`backend/app/jobs/daily_pipeline.py::_scheduled_auction_sweep()` 调用了 `_holiday_skip("auction_sweep")`，但当前文件未找到该函数定义。该风险不在本轮修复范围，需作为独立缺陷在实施前处理或明确豁免，不能假定源提交已自动解决。

## 4. 建议的最小迁移批次

若用户确认实施，建议拆成两个相互独立、可回滚的小批次：

### P1-002-A：交易日与休市日数据契约

范围：FU-000730、FU-000734、FU-000728，以及 FU-000668/FU-000709 中直接支撑交易日边界的最小代码和测试。

目标能力：统一可查询交易日口径；工作日但休市时不轮询、不伪造当日数据；读取侧默认使用最近有效交易日；完整性和指标窗口不把长假/停牌残留误判为缺数。

风险：日历来源、缓存失效、历史数据重算边界和现有实时探针可能出现口径冲突；必须保留未知状态降级，不能把日历不可用当成交易日或休市日的确定结论。

### P1-002-B：分区并发写安全

范围：FU-000757 的最终演进实现及其 `test_write_lock_scope.py` 相关用例，并逐个核对 `fs_utils`、`KlineRepository`、分钟刷新、深度和竞价写路径。

目标能力：基底内容与版本指纹来自同一个不可变快照；暂存文件名在并发写下独占；锁外做重计算，锁内只做版本校验与原子发布，避免丢行、Parquet 混读和临时文件互相覆盖。

风险：引入唯一临时文件后需要处理异常清理和跨平台替换；回退到锁内全量模式的正确性必须保留；不能把其他未授权的性能重构混入本批。

## 5. 实施前置条件与验证建议

1. P1-001 仍延后；不以本报告替代真实 eltdx 主站握手和行情验收。
2. 用户必须分别确认 P1-002-A 或 P1-002-B 的实施范围；确认前不得改业务代码。
3. 实施后按低内存约束单进程运行最小定向测试，先测新增契约，再测受影响的分钟/日 K/写入路径；本轮未执行测试或构建。
4. 需要人工检查：休市日、长假首日/末日、跨北京日期边界、并发写冲突、写入进程异常退出、缺日 K 锚点续跑。
5. `git diff --check` 仅检查本轮文档改动和工作区格式；不把既有未提交业务改动视为本轮成果。

## 6. 本轮只读审计命令

- `git status --short`、`git branch --show-current`、`git rev-parse HEAD`
- 阅读 `CONTRIBUTING.md`、`docs/secondary-development.md`、迁移计划/索引/台账
- 读取仓库外快照及 `manifest.json`
- 对候选 patch 执行 `git apply --stat`、逐文件 diff 对照、stable patch-id/文件路径/目标祖先关系核对
- `rg`/`sed` 对照目标的分钟路由、交易日、完整性、Fuyao staging、分钟复权和 Parquet 写入调用链

未执行：`pytest`、`pnpm build`、`uv run` 编译性命令、依赖安装、部署、重启、数据写入。
