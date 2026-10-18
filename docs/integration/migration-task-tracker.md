# Fork 功能迁移任务台账

- **迁移标识**：`fork-feature-migration`
- **当前分析轮次**：`r2`（保留 `r1` 历史）
- **目标仓库**：`luhuiyuan/tick-stock-panel`
- **目标分支**：`fork-integration`
- **目标基线**：`7708bec3`
- **源仓库基线**：`shy3130/tick-stock-panel:main@df2da2dc8a87e6f0d9eb1611f3ba660f3632de47`
- **总体状态**：规划中
- **当前阶段**：Phase 1：数据底座与数据源优先
- **当前任务**：opentdx 主仓库主体已固定并完成初步审计；基础行情能力继续单独评估，openkph 涨停/板块附加能力已标记暂不接入；free_public 已验证，free_market_data 与现有公开源重复、无需迁移；zzshare/Tushare 暂不接入
- **最后完成步骤**：完成 r2 前 80 Fork 独有提交及特征分析；汇总规范化为 80 条唯一记录（79 ok / 1 error）。AkShare 与 free_public 已人工确认；free_market_data 已完成固定源码、原始 diff、后续修正和网络链路只读审计，结论为与 free_public 重复、无需迁移；opentdx 主仓库与 `luhuiyuan/opentdx` 基础库源码已固定并完成主体核对；`openkph` 仅作为涨停/板块附加能力，因源码、协议和许可证证据不足已标记暂不接入；eltdx 真实链路仍阻塞。
- **下一步**：等待用户人工确认 `opentdx` 自动验证结果；确认前不部署/重启、不切换默认路由；`openkph` 附加能力保持暂不接入，保持 free_public 不启用全局路由，Longbridge 不纳入免费源范围，eltdx 真实链路仍待后续处理
- **更新时间**：2026-10-18T08:35:00+08:00

## 1. 使用规则

本台账是迁移执行状态的唯一进度入口；总体原则、阶段目标和验收标准以 [`fork-feature-migration-plan.md`](fork-feature-migration-plan.md) 为准，源仓库和轮次索引以 [`migration-registry.md`](migration-registry.md) 为准。

每次开始、中断或完成迁移任务时，必须更新本文件的：

- `status`；
- `last_completed_step`；
- `next_step`；
- `source_snapshot_path` 和 `source_sha`；
- `compatibility_changes`；
- `validation_commands` 和 `validation_result`；
- `updated_at`。

不得把计划执行的命令、预计结果或未执行的验证写成已完成。

## 2. 状态定义

| 状态 | 含义 |
|---|---|
| `待处理` | 已登记但尚未开始 |
| `源码下载中` | 正在获取并固定源仓库源码 |
| `待源码审计` | 尚未完成源码、原始 diff 或配套测试核对 |
| `待迁移前确认` | 已完成源码审计，等待向用户说明意义、收益、范围和风险 |
| `待确认` | 已完成分析，但本批迁移范围尚未确认 |
| `迁移中` | 已获得用户确认，正在按原始源码实施 |
| `待验证` | 源码已落地，但验证尚未完成 |
| `待人工确认` | 自动验证已完成，等待人工审核，期间不得开始下一功能 |
| `已人工确认` | 人工确认通过，允许申请下一功能 |
| `已验证` | 通过目标模块、兼容和失败路径验证 |
| `已完成` | 迁移、验证和人工确认完成，证据已归档 |
| `阻塞` | 被依赖、数据口径、许可、资源或兼容问题阻塞 |
| `无需迁移` | 主线已有等价能力，或确认不属于业务迁移范围 |
| `延后迁移` | 用户明确暂缓；保留已有审计和断点，恢复实施前重新确认 |

未完成本地源码快照、原始 diff 或配套测试审计的任务，不得标记为 `待迁移前确认`、`迁移中`、`待验证`、`待人工确认`、`已人工确认`、`已验证` 或 `已完成`。

完成源码审计后必须先进入 `待迁移前确认`，向用户说明迁移意义、预期收益、范围和风险；只有用户明确确认后才能进入 `迁移中`。自动验证完成后，任务必须进入 `待人工确认`；人工确认通过后才能进入 `已人工确认`，并且前一功能未进入 `已人工确认` 前，不得开始下一功能迁移。

## 3. 任务清单

### 3.1 Phase 0：功能去重与基线审计

| task_id | 任务 | 范围/来源 | status | priority | last_completed_step | next_step | evidence |
|---|---|---|---|---|---|---|---|
| `P0-001` | 建立全局功能去重表和 `global_feature_id` | r1 前 15 + r2 前 80 的合并 `feature_updates.csv`、`feature_commits.csv` | `待确认` | P0 | 已完成启发式初步去重：2,538 条来源记录映射到 765 个全局 `feature_id` | 人工复核全局分组并登记最终迁移范围 | `analysis/fork-analysis/feature-updates/` |
| `P0-002` | 排除非业务迁移项 | 文档、截图、生成 CSV、任务归档、纯工程维护 | `待处理` | P0 | 尚未开始 | 标记 `无需迁移` 或 `待复核` | `feature_updates.csv`、原始独有提交 CSV |
| `P0-003` | 复核高影响数据、策略和回测候选 | Phase 1–3 候选功能 | `待处理` | P1 | 尚未开始 | 按原始 diff 建立依赖和风险记录 | `feature_commits.csv` |
| `P0-004` | 固定第一批迁移用源码快照 | 相关 Fork 固定 SHA | `待处理` | P0 | 尚未开始 | 下载到仓库外临时目录并记录快照 manifest | `${TMPDIR:-/tmp}/tick-stock-panel-fork-migration/` |

### 3.2 Phase 1：数据底座与数据源

| task_id | 任务 | 范围/来源 | status | priority | last_completed_step | next_step | source_snapshot_path | validation_result |
|---|---|---|---|---|---|---|---|---|
| `P1-001` | eltdx完整数据源插件、竞价与财务降级 | mygx-xx固定源码，保留tdxquant | `延后迁移` | P1（当前优先） | 固定源码及配套测试已移植；后端定向验证、能力矩阵回归、前端构建和差异校验通过；独立网关安装及隔离检查完成，但真实主站握手阻塞 | 延后处理；重新开始前确认主站握手/网络诊断和真实行情验证方案；P1-002 仅按审计门禁例外推进 | main@29273e7321e8a939002a716475c76c44118a7eba | `uv run --project backend pytest -q backend/tests/test_eltdx_provider.py backend/tests/test_auction_api.py backend/tests/test_auction_service.py backend/tests/test_financial_backup_routing.py backend/tests/test_fuyao_client.py`：204 passed in 8.34s；`uv run --project backend pytest -q backend/tests/test_capability_matrix.py`：13 passed in 0.80s；pnpm 9.10.0 `build`（tsc -b + vite build）：成功；`git diff --check`：通过；Vite仅有大chunk警告；真实网关未上线 |
| `P1-002` | K 线、分钟数据、交易日和历史回补 | 多个 Fork 的数据契约候选 | `延后迁移` | P1 | 已完成候选去重、仓库外固定源码快照、原始 diff/配套测试与目标调用链只读审计；用户确认暂不实施 | 保留审计报告和快照；恢复前重新确认 P1-002-A/P1-002-B 范围，不自动迁移 | `/tmp/tick-stock-panel-fork-migration/p1-002-audit-20261016/manifest.json`；`docs/integration/p1-002-source-audit.md` | 本轮不实施；未运行测试/构建 |
| `P1-003` | ETF、资金流、复权和扩展数据 | 多个 Fork 的数据能力候选 | `延后迁移` | P1 | 已完成候选只读查看；用户确认继续跳过 | 保留候选查看记录；恢复前重新确认是否启动源码审计，不自动下载源码或迁移 | `analysis/fork-analysis/feature-updates/` | 本轮不实施；AkShare 已单独完成只读审计，未迁移 |
| `P1-003-AkShare` | AkShare 免费数据源适配与回退 | `CJXkeep/tickflow-stock-panel`：`f63a3646`、`b947c28e` | `已人工确认` | P1 | A1、A2 均已实施并经用户确认；A2 为仅 daily + 单股 minute 的 AkShare Provider，股票/指数日K固定 `adjust=""`，分钟固定 1m 不复权并具备主接口/旧接口回退；自动验证和前端分钟能力可见性验证通过 | 保持 A1/A2；不实施 full_minute、实时、深度、竞价、财务或除权因子，除非另行确认 | `/tmp/tick-stock-panel-fork-migration/cjxkeep__tickflow-stock-panel-akshare-20261016/`；`docs/integration/p1-003-akshare-source-audit.md` | `py_compile` 通过；Provider+能力矩阵 25 passed；日K真实读取已通过；分钟真实读取主接口 `RemoteDisconnected` 后旧接口返回 238 行；字段和北京时间墙钟契约通过；用户登录后确认前端 AkShare 日K及分钟K能力可见；A2 未切换默认源


### 3.2.1 r2 前 80 候选发现记录

| 范围 | 结果 | 状态 | 下一步 | 证据 |
|---|---|---|---|---|
| 排名前 80 的 Fork 独有提交分析 | 80 条唯一 Fork 记录；79 个 `ok`、1 个 `error`；`leoyb/tick-stock-panel` 不可访问 | `已完成分析` | 不将候选直接转为迁移任务 | `analysis/fork-analysis/fork_unique_summary.csv` |
| 前 80 候选特征识别 | 1,643 条候选功能，关联 4,224 个独有提交 | `已合并，待人工复核` | 已写回统一 `feature_updates.csv`/`feature_commits.csv`，全局 ID 共 765 个 | 人工复核跨 Fork 语义分组 | `analysis/fork-analysis/feature-updates/` |
| `zzshare` 数据源候选 | 第 7 名 Fork 出现内置 Provider 接入及 minute 能力声明 | `暂不接入` | 保留审计报告与固定快照；不安装依赖、不迁移、不部署；如重新评估须重新确认范围 | `feature_updates.csv`；`/tmp/tick-stock-panel-fork-migration/zzshare-liuwei1989-20261017/`；本次只读审计结论 |
| Tushare/自定义 Tushare 候选 | 第 3、20、29 名 Fork 出现多种接入、限流、移除和自定义源实现 | `暂不接入` | 保留候选记录；不启动源码审计、依赖安装或迁移；后续仅评估新的免费数据源 | `feature_updates.csv` |
| Stock SDK | 多个 Fork 高频出现，但用户已明确暂不考虑接入 | `延后迁移` | 不启动源码审计或实施 | `feature_updates.csv` |
| `free_market_data` 候选 | 第 52 名 Fork `zengwenliang416/tickflow-stock-panel` 的东方财富公开行情适配 | `无需迁移` | 与现有 `free_public` 同源且能力重复；保留审计报告，不迁移全局 Free 模式；Longbridge 后续提交另行评估 | `feature_updates.csv`；[`free-market-data-source-audit.md`](free-market-data-source-audit.md)；`/tmp/tick-stock-panel-fork-migration/free-market-data-zengwenliang-20261017/` |
| `opentdx` 候选 | 第 28 名 Fork `LisonEvf/tickflow-stock-panel`；基础行情 Provider 改造 | `已完成` | 代码迁移、部署重启和前端卡片验收已完成；默认 TickFlow 和现有路由不变；真实 TDX 单股日 K 读取仍被远端重置、返回 0 行，保留为已知链路风险，不宣称真实行情链路已验证 | `feature_updates.csv`；[`opentdx-source-audit.md`](opentdx-source-audit.md)；`/tmp/tick-stock-panel-fork-migration/opentdx-lisonevf-20261017/`；`/tmp/tick-stock-panel-fork-migration/luhuiyuan-opentdx-20261018-review/`；`backend/opentdx/` |

### 3.3 Phase 2-A：现成策略集成

以下任务来自 `AINiuNiuCollection/tick-stock-panel` 的独有提交。策略源码必须先下载到本机并完成原始 diff、配套测试和主线契约审计，当前均未开始迁移。

| task_id | global_feature_id | 策略候选 | source_repository | source_branch | source_sha | source_commit_ids | source_files | status | priority | source_snapshot_path | last_completed_step | next_step | validation_result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `P2A-001` | `FU-000010`/`FU-000011` | 二板龙头·绝望弱转强（炸板回封型） | `AINiuNiuCollection/tick-stock-panel` | `main` | `bc96ad5cc4370af8851fcfadd697c53f25263cfa` | `f0512f96e88c170f58768f5bc86e1313269e2555`; `602d14642b27f37d17789beee0768b8c408c3692` | `data/strategies/custom/custom_despair_reversal_leader.py` | `已人工确认` | P1 | `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa` | 已按固定快照迁移；仅修正 `_rolling_any_2d` 完整窗口边界漏算；新增目标侧定向测试；用户人工确认通过 | 允许申请 P2A-002；修改前仍需单功能迁移前确认 | 自动验证完成；源 SHA-256 `2a60dca353b9cdbb1bd62d28ff8afda7f2c1f2933c2533b362e270cdf7bcb768`；策略目标文件与源文件仅有预期兼容差异；`py_compile` 通过；定向测试 `2 passed`；项目策略加载/安全校验通过；3018 健康 200、策略接口 200、27 个策略、0 加载错误；已注册 `custom_despair_reversal_leader` |
| `P2A-002` | `FU-000012`/`FU-000014` | 连板股改进版 | `AINiuNiuCollection/tick-stock-panel` | `main` | `bc96ad5cc4370af8851fcfadd697c53f25263cfa` | `7a061b19c56762040add8089c8dcd717eb879a6c`; `f7c8587d5608af615ac12b55424a20d70499699d`; `7a31b2b2fcdf2011160932e070bb65ff467caf3c`; `fedabb80531f493fc88671d93fb26ecb67b439b6` | `data/strategies/custom/custom_consecutive_limit_ups_v2.py` | `已人工确认` | P1 | `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa` | 固定快照原样移植、57 项测试及热重载验证完成；用户确认 Web 可见并人工验收通过 | 允许申请 P2A-003 源码审计；代码修改前仍需确认 | 沿用已记录结果：57 passed；策略 API 200、28 个策略、0 加载错误；本次未重跑测试 |
| `P2A-003` | `FU-000042` | 2 连板特殊配置 | `AINiuNiuCollection/tick-stock-panel` | `main` | `bc96ad5cc4370af8851fcfadd697c53f25263cfa` | `2c370bb7214006c8829193783095a90a498b914b` | `data/strategies/custom/custom_two_board_special.py` | `已人工确认` | P1 | `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa` | 策略及评分方向支持迁移、96 项测试及部署验证完成；用户人工确认通过 | 允许申请 P2B-001 源码审计；修改前须确认 | 24 + 72 = 96 passed；29 策略、0 加载错误；六个评分方向均 low；健康/策略接口 200 |
| `P2A-004` | `FU-000052` | MA75 + KDJ 超卖 | `AINiuNiuCollection/tick-stock-panel` | `main` | `bc96ad5cc4370af8851fcfadd697c53f25263cfa` | `ed79ece02ad32bc7b538481b5b481c50939b2a82`; `4158e4f2fd775b5fdb9bf8054113b57afb80ed76` | `data/strategies/custom/custom_ma75_kdj_oversold.py` | `已人工确认` | P1 | `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa` | 已获得用户确认；按固定快照原样复制，源/目标 SHA-256 一致；自动验证和 3018 Web 部署验证完成；人工确认通过 | `py_compile`、策略安全校验/加载、主线定向测试 49 passed、矩阵信号/缺失 volume fail-closed/前缀不变性验证通过；3018 首页/健康检查/策略接口通过；Web 可见 `MA75+KDJ超卖`；合成样本 0 入场/0 退出，不作为收益结论 | 已人工确认 |

### 3.4 Phase 2-B：策略框架增强

| task_id | 任务 | 范围/来源 | source_commit_ids | status | priority | last_completed_step | next_step | validation_result |
|---|---|---|---|---|---|---|---|---|
| `P2B-001` | cooldown 冷却参数和连续亏损暂停 | `AINiuNiuCollection/tick-stock-panel` | `e96cb375efe00cc0a01f3643c470e3d776f52296`; `cb67dd928e7ea4411d251e3b31f8b167503a58a9` | `已人工确认` | P2 | 迁移、验证、3018部署完成，用户人工确认通过 | 允许申请P2B-002源码审计，实施前须确认 | 后端79 passed；tsc/Vite通过；3018 health/页面/资源200；29策略0加载错误；默认冷却2笔/3交易日 |
| `P2B-002` | 策略生命周期、发布、归档和绩效淘汰 | `mygx-xx/tickflow-stock-panel` | `17d916770beb465260228a3f8dd86340fa3737e1`; `150eaa288a05e234309177d5daa2c48c7f3c53fe`; `caff443a58abae4ebd77c91bf0f23d4e5030c0d7` | `延后迁移` | P3 | 固定快照及静态审计保留；用户要求先做数据源 | 数据源阶段后重新评估并确认，暂不实施 | 静态审计完成，未运行测试或构建；自动降级不宜直接启用 |
| `P2B-003` | overlay、自定义信号和组合策略增强 | 多个 Fork 的策略候选 | 已完成只读源码审计，已拆分 P2B-003-A/B | `待迁移前确认` | P2 | 候选去重、仓库外固定快照、原始 diff/配套测试和目标调用链审计完成；监控信号、单候选中性分、北京日期判定为已有等价能力 | 等待用户分别确认 P2B-003-A 或 P2B-003-B 的最小实施范围；不自动迁移 | `/tmp/tick-stock-panel-fork-migration/p2b003-audit-20261016/manifest.json`；`docs/integration/p2b-003-source-audit.md` |

### 3.5 单功能迁移前用户确认记录

每个任务开始代码迁移前，必须先向用户提交迁移说明并等待明确回复。用户未确认、暂不迁移或要求调整范围时，不得修改目标代码、配置或数据。

| task_id | status | migration_significance | expected_outcome | scope_and_risks | source_snapshot_path | user_confirmation | confirmed_at | target_baseline |
|---|---|---|---|---|---|---|---|---|
| `P2A-003` | `迁移前已确认` | 两板候选低值优先评分 | 新增 2连板特别版并正确读取 META 评分方向 | 策略原样移植，配套共享评分默认方向支持及回归、部署；原研究统计未独立复现 | `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa` | 用户回复“确认”，批准上一轮完整方案 | 2026-10-16T11:05:48+08:00（补录时间；原确认见会话） | `0a68bf46eed4a721c102864979344a1eb658d03f` |
| `P2A-002` | `迁移中` | 连板筛选增加上限及可选 MA10 确认退出 | 独立策略默认 2～4 板、5% 止损、20 交易日上限 | 原样移植固定快照，补充测试及运行验证；亏损冷却常量不生效，框架能力留待 P2B-001；不证明收益 | `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa` | 用户回复“开始吧”，确认上一轮迁移方案 | 2026-10-16T10:38:05+08:00 | `0a68bf46eed4a721c102864979344a1eb658d03f` |
| `P2B-001` | `迁移前已确认` | 连续亏损后暂停新开仓 | 组合回测可配置冷却，接通连板改进版默认2笔/3交易日 | 源sell_first口径；仅组合资金回测；前后端、测试及3018部署；回测结果可能变化 | `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa` | 用户回复“开始吧”，确认上一轮完整方案 | 2026-10-16T11:22:15+08:00（补录，原确认见会话） | `0a68bf46eed4a721c102864979344a1eb658d03f` |
| 待填写 | `待迁移前确认` | 待填写迁移意义 | 待填写用户将获得的能力 | 待填写范围和风险 | 待建立 | 待确认 | 待确认 | 待填写 |

**迁移说明和确认步骤**：

1. 完成源代码下载、固定 SHA、原始 diff 和配套测试审计；
2. 向用户说明迁移意义、预期收益、目标文件、风险、验证方案和回滚边界；
3. 等待用户明确回复 `确认开始`；
4. 将用户确认摘要、确认时间、确认范围和目标基线写入本台账；
5. 只有该任务进入 `迁移中` 后，才允许修改目标代码；
6. 用户回复 `暂不迁移` 或 `需要调整范围` 时，记录意见并保持任务未开始。

### 3.6 单功能人工确认记录

每个迁移任务在自动验证完成后，必须新增一条人工确认记录，并将任务状态改为 `待人工确认`。没有确认记录，不得将任务标记为 `已人工确认` 或 `已完成`。

| task_id | status | evidence_package | validation_result | reviewer | reviewed_at | decision | review_notes | target_baseline | next_task_allowed |
|---|---|---|---|---|---|---|---|---|---|
| `P2B-001` | `已人工确认` | 本台账P2B-001实施、自动验证与部署记录；`backend/tests/backtest/test_cooldown.py` | 沿用此前79 passed、前端构建成功、3018接口及资源200、29策略0加载错误；本次未重跑 | 用户 | 2026-10-16T11:29:32+08:00 | 通过 | 用户明确回复“通过 P2B-001”；仅组合资金回测执行冷却，无收益结论 | `0a68bf46eed4a721c102864979344a1eb658d03f`（含未提交变更） | 是 |
| `P2A-003` | `已人工确认` | 本台账 P2A-003 溯源与验证记录；`backend/tests/test_custom_two_board_special.py` | 96 passed；线上 29 策略、0 加载错误，六个评分方向为 low | 用户 | 2026-10-16T11:07:55+08:00 | 通过 | 用户明确回复“通过 P2A-003”；未证明源研究收益；本次未重跑测试 | `0a68bf46eed4a721c102864979344a1eb658d03f`（含未提交及运行时文件） | 是 |
| `P2A-002` | `已人工确认` | 本台账 P2A-002 溯源与验证记录；`backend/tests/test_custom_consecutive_limit_ups_v2.py` | 沿用已记录结果：57 passed；28 个策略、0 加载错误；健康与策略接口 200；用户确认 Web 可见；本次未重跑测试 | 用户 | 2026-10-16T10:46:06+08:00 | 通过 | 用户明确回复“通过 P2A-002”；亏损冷却常量未接入目标引擎；无收益结论 | `0a68bf46eed4a721c102864979344a1eb658d03f`（含未提交及运行时文件） | 是 |
| `P2A-001` | `已人工确认` | 本台账 P2A-001 任务记录及更新历史；固定快照与目标策略路径见任务行；`backend/tests/test_custom_despair_reversal_leader.py` | 沿用已记录结果：定向测试 2 passed、语法编译、加载及安全校验通过；3018 健康和策略接口 200；27 个策略、0 加载错误；本次未重跑测试 | 用户 | 2026-10-16T10:22:00+08:00 | 通过 | 用户明确回复“通过 P2A-001”；保留滚动窗口兼容修正；本次验收不构成收益证明 | `0a68bf46eed4a721c102864979344a1eb658d03f`（工作区含未提交变更，运行时策略位于 data/） | 是 |
| `P2A-004` | `已人工确认` | 固定快照 `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa`；目标策略 `data/strategies/custom/custom_ma75_kdj_oversold.py`；3018 Web 运行验证 | 自动验证：49 passed，安全校验/加载、信号矩阵、缺失 volume fail-closed、前缀不变性通过；部署后首页、健康接口、策略接口通过；Web 已看到 `MA75+KDJ超卖`；合成样本 0 入场/0 退出不作收益结论 | 用户 | 2026-10-16T09:51:36+08:00 | 通过 | 用户确认公网 Web 可访问且策略可见；无收益结论；允许申请下一功能 | `7708bec3`（当前目标分支基线记录） | 是 |

**确认执行步骤**：

1. 执行者停止当前任务后的下一功能，不并行扩展迁移范围；
2. 汇总证据包并链接到 `evidence_package`；
3. 提交人工确认摘要，明确源码来源、目标改动、验证结果和剩余风险；
4. 确认人记录 `通过`、`退回修改` 或 `暂不通过`；
5. 只有 `通过` 时，将状态改为 `已人工确认`，填写确认人、时间、意见和目标基线；
6. 其他结论保持当前任务未完成，记录修复项后重新进入 `待人工确认`；
7. 下一任务只能在上一任务的 `next_task_allowed` 为 `是` 后启动。

**证据包最低内容**：

- 固定源仓库、分支和完整 SHA；
- 本地源码快照路径及 manifest；
- 原始提交 diff、原始测试和目标变更 diff；
- 兼容修改说明；
- 实际执行的验证命令和结果；
- 已知风险、未迁移内容和回滚边界；
- 目标分支确认时的基线 commit。

### 3.6 Phase 3–6：后续任务占位

| task_id | 阶段 | 任务范围 | status | 进入条件 | 下一步 |
|---|---|---|---|---|---|
| `P3-001` | Phase 3 | Walk-forward、因子回测、回测缓存和内存控制 | `待处理` | Phase 0 完成且 Phase 1/2 相关契约明确 | 建立原始提交和源码快照 |
| `P4-001` | Phase 4 | 持仓、模拟盘、订单幂等和风险约束 | `待处理` | Phase 3 回测口径通过验证 | 建立原始提交和源码快照 |
| `P5-001` | Phase 5 | 监控、AI 和前端集成 | `待处理` | Phase 4 权限和风险边界明确 | 建立原始提交和源码快照 |
| `P6-001` | Phase 6 | 部署、升级与发布 | `待处理` | 业务功能批次稳定 | 建立原始提交和源码快照 |

## 4. 每个任务的溯源记录

任务开始后，应在对应表格中补充以下信息：

- `global_feature_id`；
- 源仓库 URL、分支和完整 `source_sha`；
- 本地源码快照路径；
- 原始提交 SHA、父提交和完整 message；
- 原始变更文件、配套测试和依赖文件；
- 目标主线文件和调用链；
- 迁移方式：直接移植、最小兼容修改、阻塞或无需迁移；
- 兼容修改的原因、范围和风险；
- 原测试、目标测试、验证命令和实际结果；
- 任务最后完成步骤、下一步和更新时间。

源码快照必须放在本仓库之外的临时目录，推荐使用：

```text
${TMPDIR:-/tmp}/tick-stock-panel-fork-migration/<owner>__<repo>/<source_sha>/
```

不得将源仓库源码下载到当前项目的 `analysis/`、`docs/` 或其他目录。任务未完成或未人工确认前不得清理临时快照；若临时目录因系统清理丢失，必须按台账中的同一 `source_sha` 重新下载并重新执行源码核对。

每个临时快照目录至少保留：

```text
snapshot-manifest.json
git-status.txt
git-log.txt
source/  # 固定 SHA 的工作树或裸仓库工作树
```

### P2A-002 溯源与验证记录

- source_repository：`AINiuNiuCollection/tick-stock-panel`，分支 `main`；source_sha：`bc96ad5cc4370af8851fcfadd697c53f25263cfa`。
- source_snapshot_path：`/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa`。
- 原始提交：`7a061b19c56762040add8089c8dcd717eb879a6c`（新增无扩展名策略）、`f7c8587d5608af615ac12b55424a20d70499699d`（重命名为 .py）、`7a31b2b2fcdf2011160932e070bb65ff467caf3c`（冷却常量）、`fedabb80531f493fc88671d93fb26ecb67b439b6`（退出开关与默认风控优化）。
- source_files / target_files：`data/strategies/custom/custom_consecutive_limit_ups_v2.py`；目标源码与固定快照 `cmp` 一致，SHA-256：`dfa8c774c89e5d4272324c863a078b11071a0a69a9385bb4f45c3cbe68d6848d`。
- compatibility_changes：无；保留源文件常量，但目标引擎未消费 `COOLDOWN_LOSS_STREAK` / `COOLDOWN_DAYS`，不得视作连续亏损暂停能力已实现。
- 调用链：StrategyEngine 自定义策略加载 → MatrixStrategyPipeline → 矩阵字段与信号校验 → API 策略列表；已有 `/api/strategies/reload` 完成热重载，无需重启服务。
- 原提交未提供策略专属测试。新增目标测试 `backend/tests/test_custom_consecutive_limit_ups_v2.py`，覆盖上下限、自定义区间、退出开关、MA10 连续确认、缺字段拒绝、NaN、历史前缀不变性和字段计划；通过真实矩阵构建及管线执行。
- validation_commands：`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 POLARS_MAX_THREADS=1 backend/.venv/bin/python -m pytest -q backend/tests/test_custom_consecutive_limit_ups_v2.py backend/tests/test_strategy_registry.py backend/tests/test_strategy_safety_validation.py -p no:xdist`；实际结果 `57 passed in 1.73s`。首轮缺字段测试仅错误文本不匹配，修正断言后通过；策略源码未改写。
- validation_result：登录/热重载/策略列表/健康接口均 200，28 个策略且 0 加载错误；P2A-002、P2A-001、P2A-004 均存在。验证未运行选股或收益回测，未触发交易。
- 回滚边界：移除此新增运行时策略并热重载；不影响既有策略。该策略位于 Git 忽略的 data/ 中，尚未形成版本化发布包。

### P2A-003 溯源与验证记录

- source_repository：`AINiuNiuCollection/tick-stock-panel`；source_branch：`main`；source_sha：`bc96ad5cc4370af8851fcfadd697c53f25263cfa`。
- source_snapshot_path：`/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa`。
- 策略原始提交：`2c370bb7214006c8829193783095a90a498b914b`；配套共享评分提交：`47657596c942762fb9ed2fbcd6687b4d3779d932`（默认方向支持）、`78fa36e6512920751cc74c80cacba39bb02cd403`（引擎调用）、`777fb256cdcfec19f2a65eb234a4f02ddc3dc99d`（API 调用）。
- 策略 source_files / target_files：`data/strategies/custom/custom_two_board_special.py`；原样移植，cmp 一致，SHA-256 `524e12b907893a2e6eccc5b81de6b54f16ee20541eda72913a834ff9991b1820`。
- compatibility_changes：评分函数采用固定源快照实现；目标 `engine.py` 两个调用点、`api/strategy.py` 详情调用点及 `backtest/strategy.py` 单策略/组合子策略/旧评分路径传入对应策略 META。用户覆盖映射优先（包括空映射），无覆盖时读取默认方向；无声明策略保持原行为。
- 目标侧测试：`backend/tests/test_custom_two_board_special.py`，覆盖评分覆盖优先级、空配置、非法方向、旧策略默认行为、API 六个低值方向、真实矩阵两板/封板过滤、低值评分排序及历史前缀不变性。源提交未提供该策略专属测试。
- validation_commands：`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 POLARS_MAX_THREADS=1 backend/.venv/bin/python -m pytest -q backend/tests/test_custom_two_board_special.py backend/tests/test_strategy_scoring.py backend/tests/test_custom_consecutive_limit_ups_v2.py backend/tests/test_custom_despair_reversal_leader.py -p no:xdist`，实际 `24 passed in 2.18s`。
- 补充回归：同样线程限制下执行 `backend/tests/test_composite_strategy.py backend/tests/backtest/test_composite_backtest_e2e.py backend/tests/test_strategy_detail_signals.py backend/tests/test_strategy_safety_validation.py`，实际 `72 passed in 2.49s`；本轮合计 96 passed。
- 部署：优雅停止原 3018 后端，用现有虚拟环境直接启动单进程 uvicorn，未同步依赖。登录、健康、策略接口均 200；29 个策略、0 加载错误；新策略六个方向均为 low，前 3 个迁移策略均存在。未执行收益回测或交易。
- 已知限制：原策略引用的 analysis_buyable 系列脚本未在固定快照中找到，其样本相关性和收益描述未独立验证；策略不保证次日可成交。策略位于 Git 忽略的 data/，尚无版本化发布包。
- 回滚边界：移除新增策略，撤回本功能四个共享源码文件的定向改动并重启后端；保留此前迁移和用户数据。当前等待人工确认，不自动启动下一功能。

### 2026-10-16T18:00:00+08:00：P1-002 延后，P1-003 候选只读查看

- 用户确认“P1-002 暂不实施，跳过”，P1-002 标记为 `延后迁移`；保留 `/tmp/tick-stock-panel-fork-migration/p1-002-audit-20261016/`、审计报告和恢复前重新确认门禁。
- 按用户要求只读查看 P1-003。分析 CSV 显示主要候选簇包括：`FU-000342/454/638`（ETF 支持，多 Fork 重复）、`FU-000237/243/249/257/265`（资金流及数据源/回补，多提交）、`FU-000259/714`（复权/除权口径）、`FU-000371/411/483/530/671/706`（扩展数据与原子写，多 Fork 重复）。
- 目标仓库已有 ETF 相关选股/回测/监控测试、`adj_factor`/分钟复权、`ext_data` 上传与分区测试；不能仅凭候选标题判断缺失。资金流候选对应的 `backend/app/fund_flow/` 当前未见于目标实现路径，需源码审计确认是否属于目标当前版本的真实缺口。
- 本轮未下载源码、未运行测试/构建、未修改业务代码、未部署或重启。
- 下一步：等待用户明确是否启动 P1-003 源码审计；启动后仍须先说明拆簇范围和风险。

### 2026-10-16T18:10:00+08:00：P1-003 延后，Phase 1 暂停

- 用户确认“继续跳过 P1-003”。
- P1-003 状态由 `待源码审计` 调整为 `延后迁移`；保留前一轮候选只读查看记录，不下载源码、不固定新快照、不进入实施。
- 当前 Phase 1 中 P1-001、P1-002、P1-003 均为 `延后迁移`；等待用户重新指定下一项并单独确认范围。
- 本次仅更新任务台账，未修改业务代码、未运行测试/构建、未安装依赖、未部署或重启、未提交或推送。

## 5. 中断与恢复流程

恢复任务时按以下顺序执行：

1. 读取本台账，找到状态为 `源码下载中`、`待源码审计`、`待迁移前确认`、`迁移中`、`待验证` 或 `待人工确认` 的任务；
2. 检查 `source_snapshot_path`、`source_sha` 和目标分支基线是否仍与记录一致；
3. 查看 `last_completed_step`、`next_step`、`compatibility_changes` 和最近验证结果；
4. 若本地快照不存在、SHA 不一致或目标工作区发生未记录变化，先暂停并重新核对，不覆盖现有改动；
5. 只从 `next_step` 继续，不重复执行已完成的源码复制或兼容修改；
6. 完成一个可验证步骤后立即更新台账；
7. 若任务处于 `待迁移前确认`，只提交迁移说明并等待用户明确确认，不得修改目标代码；
8. 若任务处于 `待人工确认`，只补充证据或处理确认意见，不得启动下一功能；
9. 若遇到主线不兼容、源代码缺失、测试失败、许可或资源问题，将任务标记为 `阻塞` 并记录原因。

## 6. 批次完成门禁

一个迁移任务只有在以下条件全部满足后，才能标记为 `已完成`：

- 源仓库、分支和完整 SHA 已固定；
- 迁移前用户确认已记录，且确认范围与实际变更一致；
- 本地源码快照和 manifest 已保存；
- 原始提交 diff、源码和配套测试已审阅；
- 目标主线差异和兼容修改已记录；
- 原测试和目标主线定向测试已实际执行并记录结果；
- 人工确认结论为 `通过`，且确认人、确认时间、确认意见和目标基线已记录；
- `git diff --check` 已通过；
- 未引入未批准的无关变更；
- 相关文档、证据和失败路径已归档。

## 7. 当前进度摘要

| 项目 | 当前值 |
|---|---|
| 已完成任务 | 0 |
| 进行中任务 | 0 |
| 待源码审计任务 | 4 |
| 待迁移前确认任务 | 0 |
| 延后迁移任务 | 1 |
| 待验证任务 | 0 |
| 待人工确认任务 | 1 |
| 已人工确认任务 | 5 |
| 已验证任务 | 0 |
| 阻塞任务 | 0 |
| 已下载源码快照 | 2 |
| 已迁移功能 | 5（已人工确认） |
| 已执行验证 | 6（Phase 2-A 四项、P2B-001 及 P1-001） |

以上统计由本台账任务表维护；没有实际记录的完成项不得计入统计。

## 8. 更新历史
### 2026-10-16T12:08:00+08:00：P1-001 自动验证完成，等待人工确认

- 用户明确确认开始 P1-001 受控验证；未安装依赖、未启动网关、未部署、未重启服务，未提交或推送。
- 资源门禁：开始前可用内存约 1.7 GiB、free swap 598 MiB、根盘可用 6.4 GiB；构建期间最低可用内存约 1.0 GiB，free swap 565 MiB；结束时可用内存约 1.8 GiB、free swap 514 MiB。
- 后端定向测试：`uv run --project backend pytest -q backend/tests/test_eltdx_provider.py backend/tests/test_auction_api.py backend/tests/test_auction_service.py backend/tests/test_financial_backup_routing.py backend/tests/test_fuyao_client.py`，`204 passed in 8.34s`。
- 能力矩阵回归：`uv run --project backend pytest -q backend/tests/test_capability_matrix.py`，`13 passed in 0.80s`；此前组合命令中两个不存在的历史测试路径无测试收集，回退到实际存在的能力矩阵测试，未影响结果。
- 前端构建：使用缓存的 pnpm 9.10.0，`NODE_OPTIONS=--max-old-space-size=1536 ... pnpm ... --dir frontend run build`，`tsc -b` 与 Vite 均成功；仅有大 chunk 警告。
- `git diff --check` 通过；未覆盖或清理工作区已有改动。P1-001 状态进入 `待人工确认`，暂停下一功能迁移。


### 2026-10-16T11:07:55+08:00：P2A-003 人工确认通过

- 用户明确回复“通过 P2A-003”，已记录确认人、时间、结论和目标 HEAD。
- Phase 2-A 四个策略均为 `已人工确认`；允许申请 P2B-001 源码审计，实施前仍需迁移前确认。
- 本次仅更新台账，未重跑测试、重启服务、启动下一功能或创建 Git 提交。

### 2026-10-16T11:05:48+08:00：P2A-003 自动验证及部署完成

- 用户确认后原样移植策略，并按源实现接通评分方向默认值，目标回测路径补齐调用。
- 本轮 96 项测试通过；3018 部署验证为 29 策略、0 加载错误，新策略六项评分方向为 low。
- 状态进入 `待人工确认`，未开始下一功能。

### 2026-10-16T10:46:06+08:00：P2A-002 人工确认通过

- 用户确认 Web 可见“连板股·改进版”，并明确回复“通过 P2A-002”；状态更新为 `已人工确认`，允许申请下一功能。
- 记录确认人、时间及当前目标 HEAD，修正此前任务行的列位置错误。
- 本次仅更新台账，未重跑测试、重启服务、启动下一功能或创建 Git 提交。

### 2026-10-16：P2A-002 自动验证完成

- 用户确认后原样移植固定快照策略，新增 7 项真实矩阵管线测试；注册与安全回归合计 57 passed。
- 通过已有接口热重载至 3018，策略列表包含“连板股·改进版”；进入 `待人工确认`。

### 2026-10-16：P2A-001 人工确认通过

- 用户明确回复“通过 P2A-001”，状态更新为 `已人工确认`；
- 已记录确认人、记录时间、当前目标 HEAD 和允许申请下一功能；
- 本次仅更新台账，未重跑测试、启动下一功能或创建 Git 提交。

### 2026-10-16：P2A-001 迁移和自动验证完成

- 按固定源快照落地 `data/strategies/custom/custom_despair_reversal_leader.py`；
- 仅对 `_rolling_any_2d` 完整窗口边界做最小兼容修正；
- 新增 `backend/tests/test_custom_despair_reversal_leader.py`，定向测试 `2 passed`；
- 项目 venv 导入、策略安全校验、策略引擎加载均通过；
- 3018 服务重启后策略引擎加载 27 个策略，接口返回 0 个加载错误并包含 P2A-001；
- 任务进入 `待人工确认`，未自动标记通过或完成；


- 固定快照：`/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa`；
- 已审阅提交 `f0512f96e88c170f58768f5bc86e1313269e2555` 和 `602d14642b27f37d17789beee0768b8c408c3692`；
- 已完成项目 venv 导入、策略 ID 去重和矩阵字段契约审计；
- 发现源策略 `_rolling_any_2d` 在完整窗口边界漏掉首日，迁移前需确认最小兼容修正；
- 源仓库未提供该策略专属测试，目标侧需补充定向边界与失败路径验证；

### 2026-10-16：P2A-004 人工确认通过

- 用户确认 3018 公网部署可访问，策略页面可见 `MA75+KDJ超卖`；
- P2A-004 状态由 `待人工确认` 更新为 `已人工确认`；
- 记录人工确认结论为 `通过`，允许申请下一功能；
- 保留自动验证、固定源码快照和部署验证证据；

### 2026-10-16：建立迁移任务台账

- 新增任务台账，支持按任务记录状态和断点；
- 登记 Phase 0、Phase 1、Phase 2-A、Phase 2-B 及 Phase 3–6 占位任务；
- 登记 4 个现成策略候选，当前均为 `待源码审计`；
- 已下载 1 个源仓库快照到仓库外临时目录；
- 尚未迁移业务代码，未执行迁移验证；
- 后续源码只允许下载到仓库外临时目录，不得写入本项目目录；
- P2A-004 已进入迁移后人工确认阶段；在确认通过前不得启动下一功能。

### P2B-001 实施记录

- 用户确认：“开始吧”；目标 HEAD `0a68bf46eed4a721c102864979344a1eb658d03f`。
- 固定外部快照 `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa`，main 完整 SHA 同目录名。
- 来源：原登记参数提交及 `283b1097af54ba70988cd901a786291fa646636b` 撮合逻辑、`5dba1ea59937e9e800e2f372b187b725e7efb0dd` 禁用与边界修复、`0013fe7a243a23d496d7d99b4745c1d1db73846c` 前端覆盖字段。
- 兼容：当前主线仅先卖后买，保留源 sell_first 边界；不引入 buy_first 或源选股日志框架；仅组合资金回测有效，独立全量模拟不执行冷却。
- 参数经现有 overrides 序列化和缓存键传递；StrategyDef 新字段使用默认 None 兼容已有构造器。
- 原快照未找到冷却专属测试，目标补充真实撮合测试；尚待执行。

### P2B-001 自动验证与部署完成

- status: `待人工确认`；last_completed_step: 后端验证、前端构建及3018部署通过；next_step: 等待用户验收。
- validation_commands: `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 POLARS_MAX_THREADS=1 backend/.venv/bin/python -m pytest backend/tests/backtest/test_cooldown.py backend/tests/backtest/test_engine_portfolio.py backend/tests/test_strategy_detail_signals.py -q -p no:xdist` → 29 passed。
- 回归：同环境运行 `test_strategy_backtest_correctness.py`、`test_minute_backtest.py`、`test_composite_backtest_e2e.py`、`test_worker_process.py`、`test_custom_consecutive_limit_ups_v2.py`、`test_custom_two_board_special.py` → 50 passed，5条 Polars streaming 弃用警告。
- 新冷却测试11项：触发日与到期边界、四类禁用、四种非亏损重置序列、费用净亏损、真实策略默认值及显式null覆盖；回归合计79 passed。
- 前端首次类型检查发现策略池测试fixture缺少新增字段，补齐后 `NODE_OPTIONS=--max-old-space-size=1536 npm_config_registry=https://registry.npmjs.org pnpm run build` 成功；Vite仅有大chunk提示。
- 构建前检查内存、swap、根盘；首次构建后free swap低于512MiB，先停止本次3018进程再复查：可用RAM2.5GiB、free swap1.1GiB、磁盘6.5GiB后串行重建；未改依赖源或运行Docker构建。
- 旧服务TERM后停止监听但未退出，随后INT退出；新服务PID2667019，工具会话50804，监听0.0.0.0:3018。日志 `/tmp/tick-stock-panel-p2b001-3018.log`；未配置开机自启。
- 使用已有密码在内存中登录核验：login/health/页面/新Backtest资源均200；策略列表29项、load_errors=[]；`custom_consecutive_limit_ups_v2` detail返回cooldown_loss_streak=2、cooldown_days=3。
- 前端增加适用范围提示、配置和结果参数、冷却拦截计数。尚未执行浏览器交互和真实行情回测收益复核，交由人工确认；源码统计不代表收益保证。
- `git diff --check` 通过；既有改动及 backend/uv.lock 保留；未创建提交或推送，未开始P2B-002。
- updated_at: 2026-10-16T11:22:15+08:00

### 2026-10-16T11:29:32+08:00：P2B-001 人工确认通过

- 用户明确回复“通过 P2B-001”，确认人、时间、结论、目标HEAD及next_task_allowed已记录。
- 当前状态为 `已人工确认`；允许申请P2B-002源码审计，尚未启动下一功能。
- 本次仅更新台账，未重跑测试、构建、重启、提交或推送。

### 2026-10-16T11:39:29+08:00：P2B-002 静态源码审计完成

- 用户授权：“开始 P2B-002 源码审计”；仅授权审计，不代表批准代码迁移。
- source_snapshot_path：`/tmp/tick-stock-panel-fork-migration/mygx-xx__tickflow-stock-panel/29273e7321e8a939002a716475c76c44118a7eba`；main，source_sha：`29273e7321e8a939002a716475c76c44118a7eba`。
- 原始diff及manifest位于同父目录 `p2b002-audit-evidence/`；审计报告：[p2b-002-source-audit.md](p2b-002-source-audit.md)。
- last_completed_step：审阅状态机、判定、缓存、状态API、调度及配套测试；next_step：等待首批方案确认。
- compatibility_changes：尚未实施；建议保留draft/watch运行兼容、归档拦截补齐、人工来源及状态写入审计修正；自动降级保持关闭。
- validation_commands/result：git rev-parse、git show、git grep、源码/测试静态阅读、快照SHA256核对；未执行测试、构建、部署或重启。前序验证不视为本功能验证。
- 未修改业务代码、提交或推送，保留所有前序未提交改动；updated_at：2026-10-16T11:39:29+08:00。

### 2026-10-16T11:50:34+08:00：用户调整优先级，数据源优先

- 用户要求P2B-002标记延后，保留源码审计及证据，未批准该功能实施。
- 后续优先顺序：数据源接入与能力/降级 → 日K完整性/交易日/复权/回补 → 分钟数据闭环 → 五档盘口 → 财务/ETF等扩展；之后再评估策略框架。
- 首选审计eltdx日K/实时（FU-000725，与FU-000663去重），随后depth5（FU-000726）；是否实施取决于固定diff与目标差异。
- 现有fuyao已包含record-batch流式读取及财务schema推断相关实现，不按候选标题重复移植；先核对源码差异。
- 本次仅更新规划文档，无业务代码修改、构建、测试、部署或提交。

### 2026-10-15T23:17:32-07:00：P1-001 完整插件源码落地，待自动验证

- status: `待验证`；用户授权：“确认接入eltdx”“按完整插件范围开始迁移”。
- source_snapshot_path: `/tmp/tick-stock-panel-fork-migration/mygx-xx__tickflow-stock-panel/29273e7321e8a939002a716475c76c44118a7eba`；source_sha: `29273e7321e8a939002a716475c76c44118a7eba`；分支main。
- 来源提交：07112dd、4fbe377、a4463ed、50490c3、843c1a3、70464a5、d9234f1；快照HEAD已核对。
- last_completed_step: 插件client/http gateway/provider/manifest、auction契约/落盘/API/调度、财务缺失标的备源填充及Fuyao重连、源配套测试、前端API类型/查询键/竞价图/榜单入口落地。
- compatibility_changes: 保留tdxquant和既有daily/realtime/minute/depth5/adj-factor路由；复用现有request、QK与custom/abnormal扩展，不创建平行页面；竞价量为手、金额为元、涨幅为小数制。
- validation_commands/result: git rev-parse核对固定快照；git diff --check通过；尚未运行测试、类型检查或构建，不引用前序验证为本功能结果。
- next_step: 受控单进程验证并记录真实结果；未安装eltdx依赖、未启动网关、未部署或重启3018；这些操作另行确认。
- 风险：插件声明Research-Only License；真实网关及行情未验证；income/metrics依赖备源；历史竞价窗口有限；保留前序未提交修改，未commit/push。

### 2026-10-16T15:45:49+08:00：P1-001 部署至3018，仍待人工确认

- 用户授权：“确认，按方案重启部署”；本次未修改业务代码、安装依赖、启动eltdx网关或更改数据源配置，未commit/push。
- 旧服务PID2667019收到TERM后正常退出；首次nohup子进程PID2734160短暂健康后消失，日志无关机堆栈，原因尚未确认，不能断言OOM或代码故障。随后通过工具长会话前台执行相同命令。
- 当前命令：在`/home/tick-stock-panel/backend`执行`.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 3018`；PID2734596，工具会话99998，日志`/tmp/tick-stock-panel-p1-001-3018.log`；未配置systemd或开机自启，常驻保障仍有限。
- 实际检查：`/health`为JSON 200、status=ok、version=0.3.7、mode=none；首页200；未登录业务接口401；已有密码仅在内存中用于登录核验，login 200，未输出密码或会话。
- 登录后数据源列表200：eltdx插件已发现，但默认HTTP网关`http://127.0.0.1:8000`拒绝连接，available=false；fuyao可用；tdxquant未配置API Key。`/api/settings/data-sources/eltdx`返回404，未作为插件状态验证通过。
- `/api/auction/status`、`/api/auction/series?symbol=600519.SH`、`/api/auction/board?limit=2`均JSON 200，state=source_unavailable、无竞价数据；正确降级不等于真实竞价链路验收通过。`/api/strategies`200，29策略，load_errors=[]。
- 启动后台任务有fuyao扩展拉取429；回测矩阵预热出现PyArrow `OSError: Unexpected end of stream: Page was smaller (16268) than expected (16549)`，疑似Parquet读取完整性问题，具体文件及成因未定位，未做数据修复，回测不能宣称已验收。服务健康接口仍正常。
- 资源检查：可用RAM约1.6GiB、free swap833MiB、根盘可用6.3GiB；本次未重跑构建。
- status仍为`待人工确认`；next_step：用户前端确认入口、错误/降级状态及既有功能。若需真实eltdx行情，另行确认网关/依赖接入；如需修复预热数据异常或设置开机自启，也需另行确认。未启动P1-002。

### 2026-10-16T15:58:08+08:00：用户确认后仅安装 eltdx 依赖（3.2.2）

- 用户明确授权“仅安装 eltdx 3.2.2 依赖”。使用官方 PyPI，先以 `uv pip install --dry-run --only-binary=:all: eltdx==3.2.2` 检查，计划仅新增此包；安装前 `uv pip check` 通过。
- 实际命令：`UV_DEFAULT_INDEX=https://pypi.org/simple uv pip install --python backend/.venv/bin/python --only-binary=:all: eltdx==3.2.2`；仅下载约1.9 MiB wheel，安装成功，未源码编译、未升级/替换其它包。
- 安装后确认：版本3.2.2、模块可导入、`uv pip check` 检查97个包通过、`eltdx-http --help` 正常；`python -m eltdx --help` 不适用（无`__main__`），改用真实console entry point核验。
- 此普通安装仅提供eltdx Python包，**不包含HTTP网关的`http`可选依赖**；插件 availability 仍因127.0.0.1:8000未启动返回不可用。未安装`eltdx[http]`、未启动网关、未重启3018、未改配置或源码。
- 当前3018仍为PID2734596，健康200；前端此前验收通过。安装后不需要立即重启HTTP面板来让本机venv包可见，但真实插件能力需网关接入后重新检测/重启应用验证。
- 台账状态保持`待人工确认`（真实eltdx行情尚未接通）；未开始P1-002，未commit/push。updated_at：2026-10-16T15:58:08+08:00。

### 2026-10-16T16:20:43+08:00：独立 eltdx HTTP 网关安装完成，启动受主站连接阻塞

- 授权：用户明确批准“创建独立虚拟环境并部署 eltdx HTTP 网关”；未授权共享后端依赖升级、源码修复、网络配置修改或开机自启。
- 独立环境：`/tmp/tick-stock-panel-eltdx-gateway-20261016-01`，Python 3.11.13；使用官方PyPI和预编译wheel，命令为 `UV_DEFAULT_INDEX=https://pypi.org/simple uv pip install --python /tmp/tick-stock-panel-eltdx-gateway-20261016-01/bin/python --only-binary=:all: 'eltdx[http]==3.2.2'`。安装21包，eltdx 3.2.2、FastAPI 0.143.2、Uvicorn 0.54.0；独立环境 `python -m pip check` 通过。临时目录可能被系统清理，不作为持久化部署。
- 后端环境仍为eltdx 3.2.2、FastAPI 0.136.1、Uvicorn 0.47.0，未替换或升级；未改业务源码、配置或锁文件，未重启3018。
- 启动尝试：`eltdx-http --host 127.0.0.1 --port 8000 --server-count 1 --log-level info`，PID2742517（会话84407）；启动初始化连接主站时 `ResponseTimeoutError: 7709 response timed out during connect`，退出码3，未成功监听。
- 只读诊断：逐个短超时探测eltdx自带43个fallback候选，36个TCP可连接、7个超时；这不证明协议握手或行情可用。显式主站101.33.225.16、43.139.18.171、101.35.121.35、111.230.186.52的单连接烟测均在握手阶段被重置（os error104）；最后一次自动选站单连接烟测连接超时，未得到真实行情。具体根因未定位，不能断言是IP封锁、防火墙或上游协议兼容故障。
- 最终检查：8000无监听，gateway /health连接拒绝；3018原PID2734596保持运行，`/health` JSON200/status=ok/version=0.3.7/mode=none。网关未上线，前端真实数据验证仍不可进行。
- 首次含清理命令的创建脚本被工具安全规则整体拒绝，未执行删除；改用全新路径安装。未删除缓存或已有环境。
- 资源检查：可用RAM约1.3GiB、free swap828MiB、根盘可用6.3GiB；无编译、无构建。
- last_completed_step：独立环境安装和隔离验证完成；网关启动失败已复核。next_step：等待用户确认进一步诊断主站握手/网络，不自行升级版本、改源码或设置自启。真实数据接入阻塞；不将前端降级检查通过视为真实行情验收，不开始P1-002；未commit/push。

### 2026-10-16T17:02:42+08:00：用户确认 P1-001 延后处理

- 用户明确确认“标记为延后迁移”。
- P1-001 状态由 `待人工确认` 调整为 `延后迁移`；保留已完成的源码迁移、自动化验证、前端验证、依赖安装和网关诊断记录。
- 延后原因：独立 eltdx HTTP 网关在连接 TDX 7709 主站时握手超时/连接重置，真实行情链路尚未接通。
- 后续重新开始前需再次确认主站握手/网络诊断及真实行情验证方案；期间不开始 P1-002，不修改代码、配置或服务状态。
- 本次仅更新任务台账，未执行测试、构建、部署、重启、提交或推送。

### 2026-10-16T17:15:42+08:00：用户明确批准 P1-002 源码审计门禁例外

- 授权原文：“明确调整门禁，先做 P1-002 源码审计”。
- P1-001 保持 `延后迁移`，不视为真实行情验收通过；允许 P1-002 候选去重、固定源码快照与只读源码/测试审计。
- 允许更新本计划、台账和审计文档；不授权业务代码修改、测试/构建、依赖安装、数据修复、部署或重启。
- 审计完成后另行说明迁移意义、范围、风险和验证方案，并等待实施确认。


### 2026-10-16T18:45:00+08:00：AkShare 候选源码审计完成

### 2026-10-16T19:05:00+08:00：P1-003-A1 不复权日K回退适配器实施完成

### 2026-10-17T07:31:00+08:00：P1-003-A1 部署与前端数据源入口验证

### 2026-10-17T07:35:00+08:00：用户确认 AkShare 前端卡片可见

- 用户登录后人工确认前端“设置 → 数据源”页面可见 AkShare 卡片。
- 本次确认覆盖前端入口/卡片展示和日K能力入口，不等同于真实行情长期稳定性、全量同步幂等性或收益口径验收。
- P1-003-A1 仍等待用户对整体实施结果作最终“通过/暂不通过”结论。


- 按用户要求重启 3018 单进程 Uvicorn，使用完全脱离会话方式启动，当前 PID `3007279`，父进程为 `1`，健康接口 200，进程稳定存活。
- 启动日志确认 `akshare` 内置插件已注册；运行时插件状态为 `available=True`、`datasets=['daily']`、无 API Key 配置，`fuyao` 保持可用，`eltdx` 因 HTTP 网关未启动保持不可用。
- 前端入口 `/` 返回 200，静态 JS 资源返回 200；未重新构建前端，沿用现有 `frontend/dist`。
- `/api/settings/data-sources` 受访问密码保护，未登录返回 401；用户随后登录前端并人工确认 AkShare 卡片可见，且“日K”能力入口可见。
- 首次后台启动在 enriched 预热阶段退出，未见应用异常或内核 OOM 记录；随后使用脱离会话方式重启成功。
- 未安装依赖、未写入行情数据、未切换默认源、未提交或推送；P1-003-A1 状态保持 `待人工确认`。

### 2026-10-17：P1-003-A2 人工确认通过

- 用户登录后确认前端“设置 → 数据源”页面可见 AkShare 分钟 K 能力。
- P1-003-A2 标记为 `已人工确认`；AkShare 仍不声明 `full_minute`，不切换默认数据源。
- A1/A2 当前批次完成；本次仅更新迁移台账和审计状态，未修改业务代码、未写入行情数据、未提交或推送。

### 2026-10-17：P1-003-A2 单股分钟回退实施完成

- 用户确认按最小范围实施 P1-003-A2。
- 插件声明 `minute`，仅支持单只股票 1 分钟请求；不声明 `full_minute`，不支持指数/ETF/多标的/全市场同步。
- 主接口 `stock_zh_a_hist_min_em` 使用 `adjust=""`；失败或空结果回退 `stock_zh_a_minute`；输出标准字段并转换为北京时间墙钟。
- 自动验证：`py_compile` 通过；Provider+能力矩阵 `25 passed`。
- 真实读取 `000001.SZ`（2026-10-16 09:25–15:05）主接口 `RemoteDisconnected`，旧接口成功返回 238 行；未写入行情数据、未部署、未切换默认源。
- A2 当前等待用户人工确认。

### 2026-10-17：P1-003-A1 人工确认通过

- 用户登录后确认前端“设置 → 数据源”页面可见 AkShare 卡片及日 K 能力入口。
- P1-003-A1 标记为 `已人工确认`；P1-003-A2 单股分钟回退仍不实施。
- 本次仅更新迁移台账，未修改代码、未切换默认数据源、未提交或推送。

### 2026-10-17T07:24:15+08:00：P1-003-A1 指数接口诊断与修复完成

- 用户确认继续诊断后，确认 `stock_zh_index_daily_em`/`index_zh_a_hist` 失败，而 `stock_zh_index_daily` 对七个核心指数均成功。
- 已将 Provider 指数分支切换为 `stock_zh_index_daily`，本地按日期过滤；旧接口缺少成交额时保留 nullable `amount`。
- 真实读取 `000001.SH` 在 `2026-10-01` 至 `2026-10-16` 返回 7 行；`py_compile` 通过；定向测试与能力矩阵共 19 passed。
- 未安装依赖、未写入 Parquet、未切换默认源、未部署/重启；P1-003-A1 保持 `待人工确认`。

### 2026-10-16T19:20:00+08:00：P1-003-A1 真实 AkShare 网络读取验证

- 只读真实请求股票 `000001.SZ` 与指数 `000001.SH`，窗口 `2026-10-01` 至 `2026-10-16`；未写入 Parquet、未切换配置。
- 股票成功返回 7 行，字段完整，日期覆盖 `2026-10-08` 至 `2026-10-16`。
- 指数连续 2 次被远端 `RemoteDisconnected`，Provider 正常 fail-closed 返回空表；指数上游接口可用性未通过。
- P1-003-A1 保持 `待人工确认`，不将部分真实验证写成全部通过；待确认是否接受指数源暂时不可用风险或另行处理。


- 用户确认“按不复权 adjust="" 实施 P1-003-A1”。本轮仅新增 AkShare 内置插件及定向契约测试，不改变默认数据源，不接入分钟/实时/深度/竞价/财务/除权因子。
- 股票与指数日K调用固定 `adjust=""`；插件只声明 `daily`，无 API Key 配置；依赖延迟导入，后端虚拟环境已有 AkShare 1.19.1，未重复安装。
- 验证：`py_compile` 通过；`backend/tests/test_akshare_provider.py backend/tests/test_capability_matrix.py` 共 18 passed；仅有既有 Polars 日期 cast 弃用警告。未执行真实网络请求、构建、部署或重启。
- 状态进入 `待人工确认`；A2 单股分钟回退不自动启动。


- 用户确认审计 AkShare 数据源候选。本轮固定 `CJXkeep/tickflow-stock-panel:main@40d4ce0f206345f540edb7d9740636f4dc5b16ed`，核对候选提交 `f63a364606d3153f7c27ab2b83c31eb49ea258ac` 和 `b947c28e11b3aa05e7f5d4c0612259cae0f23e9e`。
- 证据目录：`/tmp/tick-stock-panel-fork-migration/cjxkeep__tickflow-stock-panel-akshare-20261016/`；报告：[`p1-003-akshare-source-audit.md`](p1-003-akshare-source-audit.md)。
- 结论：AkShare 是真实新增候选，但源实现采用独立 `backend/app/datasource` 架构，日K默认前复权、分钟仅单股回退、默认八线程同步，不能直接复制到目标插件契约。首个提交混入大量无关工作流/产品文件。
- 拆分为 P1-003-A1（股票/指数日K最小回退适配器）和 P1-003-A2（单股分钟图表回退）；当前均等待用户实施确认。
- 本轮未修改业务代码、未安装 AkShare、未运行测试/构建、未部署/重启、未提交/推送。

### 2026-10-16T18:20:00+08:00：P2B-003 源码审计完成

- 用户明确确认“按上述范围开展 P2B-003 源码审计”。本轮仅进行仓库外固定源码快照、原始 diff、patch-id 去重、目标调用链和现有测试/契约的只读核对。
- 证据目录：`/tmp/tick-stock-panel-fork-migration/p2b003-audit-20261016/`；报告：[`p2b-003-source-audit.md`](p2b-003-source-audit.md)。
- 监控自定义信号选择补丁稳定 patch-id 为 `7abd1c1dcc2d1b28fe62cef1d5b3539c5665d3c2`，目标已有等价实现；组合策略单候选中性分/北京日期也已有等价实现，不迁移。
- 发现两个可独立评估的真实增量：P2B-003-A enriched 存储列/叠加条件缺列闭环；P2B-003-B 评分 null 兜底与 `nulls_last` 排序防御。
- P2B-003 状态进入 `待迁移前确认`；等待用户分别确认 A 或 B 的最小实施范围。未修改业务代码、未运行测试/构建、未部署/重启、未提交/推送。

### 2026-10-17：zzshare 数据源候选标记为暂不接入

- 已完成 zzshare 固定源码、vendor 客户端、Provider、日 K/分钟 K、复权因子、token 轮询、并发模型、回退语义和测试覆盖的只读审计。
- 结论：zzshare 依赖公开 SDK 与 `api.zizizaizai.com` 服务，但不属于交易所官方直连数据源；候选实现存在 token/限流错误语义、instruments 返回类型兼容、复权因子推导和真实网络验证不足等风险。
- 用户确认暂不接入：不安装依赖、不迁移代码、不切换数据源、不部署/重启；保留固定源码快照供后续重新评估。
- 证据目录：`/tmp/tick-stock-panel-fork-migration/zzshare-liuwei1989-20261017/`。

### 2026-10-17：free_public 独立 Provider 接入、部署并验证完成

- 按用户确认范围接入 `free_public` 独立内置 Provider，显示名为“免费公开源（新浪/东方财富/腾讯）”。
- 已接入能力矩阵和显式按能力路由：标的列表、日 K、分钟 K、实时行情、五档盘口；未声明复权因子、财务、竞价或全量分钟能力。
- 未修改 `tickflow.client.py`、`tickflow.policy.py`，未启用全局 `data_backend=free_source`，未切换默认数据源。
- 为适配项目契约，日 K默认不复权，新浪成交额缺失保留为空；复权因子返回空表，不伪造官方因子；免费源调用沿用失败回退路径。
- 新增文件：`backend/app/data_providers/free_public_adapter.py`、`backend/app/data_providers/free_public_provider.py`、`backend/tests/test_free_public_provider.py`；修改 Provider 注册、能力矩阵、偏好白名单及日 K/分钟/标的/实时/五档路由。
- 验证：新增/修改 Python 文件 `py_compile` 通过，`git diff --check` 通过，uv 环境 mock contract smoke 通过；因可用 swap 约 260 MiB 低于仓库要求的 512 MiB，本轮未运行 pytest、未运行构建。
- 当前状态：`已验证`；未安装新依赖、未进行真实免费源读取、未写入行情数据；已完成 3018 服务重启部署。`free_public` 未启用，未切换全局 `data_backend`。
- 部署验证：3018 Uvicorn 进程已恢复监听，首页 HTTP 200；启动日志显示 `mode=none`，现有能力路由保持为日K/除权/实时 `fuyao`、分钟K `AkShare`、五档 `TickFlow`，未切换到 `free_public`。
- 注意：当前持久化配置本来就是上述分能力路由，并非所有能力均指向 TickFlow；本次未修改现有路由配置。
- 后续修复：补登记 `/api/settings/data-sources` 的内置源列表，重启 3018 后首页 HTTP 200；用户已登录确认“免费公开源（新浪/东方财富/腾讯）”卡片可见，能力路由与数据源列表显示一致。


### 2026-10-17：free_market_data 候选源码审计完成

- 用户确认开展 `zengwenliang416/tickflow-stock-panel` 的 `free_market_data` 候选只读源码审计。
- 固定候选提交 `138811095821da293206ac9af15b8c7c4946de3b`，父提交 `24252cf77ceb2d7b426ba01279cf06c2bc6c4a46`；源码快照位于仓库外 `/tmp/tick-stock-panel-fork-migration/free-market-data-zengwenliang-20261017/`。
- 结论：候选核心使用东方财富公开接口，与当前 `free_public` 同源且能力重复；候选还修改全局 Free 模式、policy、pool、repository 和同步链路，侵入范围过大，不迁移。
- 后续 Longbridge 提交需要 OAuth/凭证，不属于本轮新的免费数据源范围；不纳入本次迁移。
- 本机 2026-10-17 低频网络探测出现 302/502/远端断开，未写入数据；该结果仅记录链路不稳定，不作为永久可用性结论。
- 审计报告：[`free-market-data-source-audit.md`](free-market-data-source-audit.md)。本轮未修改业务代码、未安装依赖、未部署/重启、未提交/推送。

### 2026-10-17：opentdx 候选源码审计被源仓库访问阻塞

- 用户确认审计第 28 名 Fork `LisonEvf/tick-stock-panel` 的 opentdx 候选。
- 已从候选分析表固定提交元数据：`2a06c5bec8fe737ef9d697c8893a63859a8ca800`、`873c2ce9d301f444edfefc3a1144724e9f4f0a47`、`969a2b370b866d9527df3f6c5f154d749870f95d`、`faee04e8b66ce324e79f4aed54099a917bab47c9`、`586a1c8700cb214b8ac0406df202b0fc57dcb71d`。
- 主仓库网页/API/ls-remote 已恢复可访问；已通过官方 commit archive 固定主仓库并核对 Provider、测试和全局路由。两个 `.gitmodules` 子模块地址当前返回 404，无法取得子模块 SHA、传输实现和许可证。
- 主仓库主体审计完成，确认 opentdx 是独立 TDX 数据源候选；因子模块源码门禁未满足，本轮仍不安装依赖、不迁移代码、不部署/重启。
- 记录：[`opentdx-source-audit.md`](opentdx-source-audit.md)。

### 2026-10-18：opentdx 基础 Provider 最小范围实施

- 用户确认按最小范围实施 `opentdx`，不接入 `openkph`，不替换全局 TickFlow。
- 固定上游源码：`luhuiyuan/opentdx@4e90696afac1d5f8094624fe559887924d5c0437`；上游 MIT 许可证随 `backend/opentdx/LICENSE` 保留。
- 当前运行时为 Python 3.11，上游声明 `>=3.12`；采用项目内 vendoring，并通过 `backend/opentdx/compat.py` 兼容 `typing.override`，未安装 PyPI `opentdx` 包。
- 新增独立 Provider：基础标的、日 K、分钟 K、实时行情、五档盘口；不声明复权因子、财务、竞价或 `openkph` 附加能力。
- 接入 Provider 注册表、设置页数据源列表、能力矩阵和合法源白名单；默认路由与现有偏好未改变。
- 自动验证：`test_opentdx_provider.py` 3 passed；固定源码 `compileall`、目标文件 `py_compile` 和 `git diff --check` 通过。
- 真实读取：单股 `000001.SZ` 日 K 探测连接被远端以 `Connection reset by peer` 重置，返回 0 行；因此真实链路仍未验证通过。
- 当前状态：`已完成`；代码迁移、部署重启和前端卡片验收均已完成；真实 TDX 主站链路仍阻塞，作为已知风险保留；未切换默认路由；不提交、不推送。

### 2026-10-18：opentdx 前端卡片人工确认通过

- 用户确认登录后可在设置页数据源列表看到 `OpenTDX（通达信协议）` 卡片。
- OpenTDX 最小范围 Provider 的前端可见性验收通过；默认 TickFlow、现有能力路由和 `openkph` 暂不接入边界保持不变。
- 真实 TDX 主站连接仍返回 `Connection reset by peer`，因此仅标记为 `已人工确认`，不标记为真实行情 `已验证` 或 `已完成`。

### 2026-10-18：opentdx 迁移标记为已完成

- 用户确认将 `opentdx` 迁移任务标记为 `已完成`。
- 完成范围为：固定源码 vendoring、独立 Provider 接入、注册表/能力矩阵/设置页接线、后端部署重启和前端数据源卡片人工确认。
- 真实 TDX 主站读取仍出现 `Connection reset by peer`，该风险保留在台账中；“已完成”不代表真实主站行情链路已验证通过。

### 2026-10-18：opentdx 后端部署完成，等待前端人工确认

- 用户确认部署 `opentdx`，已复用现有 Uvicorn 后端进程重启，无独立 HTTP 网关或容器。
- 3018 后端稳定监听，首页返回 HTTP 200；`/api/settings/data-sources` 和 `/api/settings/capability-matrix` 未登录返回 HTTP 401，认证门禁正常。
- 启动日志显示 `mode=none`，现有能力路由保持不变；未切换默认 TickFlow 路由、未启用 OpenTDX 全局路由、未接入 `openkph`。
- 当前状态仍为 `待人工确认`：请登录前端确认 OpenTDX 数据源卡片可见；真实 TDX 主站读取仍因远端重置未通过。

### 2026-10-18：openkph 附加能力标记为暂不接入

- 用户确认将原 `opentdx` 候选中的 `openkph` 附加能力标记为暂不接入。
- `openkph` 仅涉及涨停/跌停梯队、炸板/连板、概念/行业板块排名及板块成分股分析，不影响 `opentdx` 的基础实时行情、日 K 和分钟 K 能力评估。
- 当前不安装 `openkph` 依赖、不迁移相关服务、不部署或重启；后续如需恢复，必须重新提供可核验源码、固定提交 SHA、协议和许可证证据并重新确认范围。

### 2026-10-17：Tushare 数据源候选标记为暂不接入

- 用户确认 Tushare/自定义 Tushare 候选暂不接入，不启动其源码审计、依赖安装、迁移或部署。
- 后续数据源评估范围收窄为：只评估新的免费数据源；已有候选 zzshare 同样保持暂不接入。
- 当前不切换默认数据源，不修改业务代码。
