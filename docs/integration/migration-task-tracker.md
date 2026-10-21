# Fork 功能迁移任务台账

- **迁移标识**：`fork-feature-migration`
- **当前分析轮次**：`r2`（保留 `r1` 历史）
- **目标仓库**：`luhuiyuan/tick-stock-panel`
- **目标分支**：`fork-integration`
- **目标基线**：`7708bec3`
- **源仓库基线**：`shy3130/tick-stock-panel:main@df2da2dc8a87e6f0d9eb1611f3ba660f3632de47`
- **总体状态**：规划中
- **当前阶段**：新增策略/数据源/因子优先；Bug 修复类候选延后处理
- **当前任务**：`YYD-CENTER` 已人工确认。没有正在实施的下一项。
- **最后完成步骤**：用户要求在下一功能前提交当前 `YYD-CENTER` 代码和台账。本提交包含该功能，未推送。
- **下一步**：不开始 `GF-000749` 或其他候选。
- **更新时间**：2026-10-21（YYD-CENTER 已人工确认，并按用户要求提交，未推送）

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
| `P1-002` | K 线、分钟数据、交易日和历史回补 | 多个 Fork 的数据契约候选；子批次 P1-002-A/B | `已验证` | P1 | P1-002-B 已完成并发写安全实施并自动验证通过；P1-002-A 已人工确认通过 | P1-002-B 已自动验证；下一功能需另行提出并确认范围 | `/tmp/tick-stock-panel-fork-migration/p1-002-audit-20261016/manifest.json`；`docs/integration/p1-002-source-audit.md`；`mygx-xx/tickflow-stock-panel@29273e7321e8a939002a716475c76c44118a7eba` | P1-002-A 定向 69 passed；P1-002-B 定向 103 passed；`py_compile` 通过；`git diff --check` 通过；未部署/重启/提交 |
| `P1-003` | ETF、资金流、复权和扩展数据 | 多个 Fork 的数据能力候选 | `延后迁移` | P1 | 已完成候选只读查看；用户确认继续跳过 | 保留候选查看记录；恢复前重新确认是否启动源码审计，不自动下载源码或迁移 | `analysis/fork-analysis/feature-updates/` | 本轮不实施；AkShare 已单独完成只读审计，未迁移 |
| `P1-003-AkShare` | AkShare 免费数据源适配与回退 | `CJXkeep/tickflow-stock-panel`：`f63a3646`、`b947c28e` | `已人工确认` | P1 | A1、A2 均已实施并经用户确认；A2 为仅 daily + 单股 minute 的 AkShare Provider，股票/指数日K固定 `adjust=""`，分钟固定 1m 不复权并具备主接口/旧接口回退；自动验证和前端分钟能力可见性验证通过 | 保持 A1/A2；不实施 full_minute、实时、深度、竞价、财务或除权因子，除非另行确认 | `/tmp/tick-stock-panel-fork-migration/cjxkeep__tickflow-stock-panel-akshare-20261016/`；`docs/integration/p1-003-akshare-source-audit.md` | `py_compile` 通过；Provider+能力矩阵 25 passed；日K真实读取已通过；分钟真实读取主接口 `RemoteDisconnected` 后旧接口返回 238 行；字段和北京时间墙钟契约通过；用户登录后确认前端 AkShare 日K及分钟K能力可见；A2 未切换默认源



### 3.2.0 已审计候选去重结论

| feature_id | 功能 | status | 结论与证据 | 更新时间 |
|---|---|---|---|---|
| `GF-000077` | 真 Walk-Forward 优化：滚动窗口 IS 优化 + OOS 验证 | `无需迁移` | 目标仓库已存在 `backend/app/backtest/walkforward.py`、`backend/app/api/backtest.py`、`backend/app/backtest/worker.py`、前端 `StrategyWalkForward.tsx` 以及 `backend/tests/backtest/test_walkforward.py`；已覆盖训练/OOS 区间隔离、IS position 模式、退化统计、缓存遥测、取消和 SSE 进度。源候选核心提交 `586d9a469934bd72942994064b5ff8bbbe31d707`、`4c40c6b1bf67bbf1bb625a8fa5bc9bb8ff0e9814`、`32d27a0a6e1dca501137eca29973dcf3cc515f77`、`db535c8004edd860f2fd17d75e173a3b24887e33` 与目标现有提交/实现重复或已被目标吸收；直接迁移会造成重复实现并可能覆盖目标兼容修复。 | 2026-10-19 |

| `GF-000003` | Alpha158 实验因子组 | `已人工确认` | Phase A+B 已完成：158 个股票实验因子、必要 DSL 算子、注册表默认隐藏/显式开启、因子回测依赖接入、因子库与因子发现开关；未迁移一次性评估脚本和自动策略接入 | `/tmp/tick-stock-panel-fork-migration/flyme8899__tick-stock-panel-gf000003`；源 SHA `4d2d7e9e00b126a29d40d4860a1b4284aefe7162` | 2026-10-19 |
| `GF-000590-A` | 情绪阶段策略择时 `REGIME_PHASES` | `待迁移前确认` | 新增策略契约；目标已有情绪阶段历史和 state/score T-1 回测过滤，但缺策略阶段白名单、phase 过滤传递及开仓门控 | [`gf-000590-source-audit.md`](gf-000590-source-audit.md)；`YuYang019/tick-stock-panel@f0ee647c05a0a8db372e8c66a0da371accfd65b0`；候选提交 `16d21f4e0a9306eb2f6208dcfbf8277a909ec161` | 2026-10-19 |
| `GF-000590-B` | 完整 RPS / 相对强度因子族迁移（19 个因子） | `已人工确认` | 目标缺少底层 RS 模块；需先审计更早提交 `3296ac8fe80984e0f493906b83ed9ddd76dcf6e6` 的最小依赖，不与 A 并行实施 | [`gf-000590-source-audit.md`](gf-000590-source-audit.md)；固定快照同上 | 2026-10-19 |
| `GF-000590-FIX` | 长回看缓存覆盖/评分白名单修复 | `已并入 GF-000590-B` | RPS 长窗口执行所需依赖；已随 B-Execution 迁移白名单与长回看缓存修复 | `YuYang019/tick-stock-panel@f0ee647c05a0a8db372e8c66a0da371accfd65b0`；提交 `712f4c1def0380c3cdbde638e8d6e5e3c1086d83` | 2026-10-19 |

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
| `P1-002-A` | `已人工确认` | 固定快照 `/tmp/tick-stock-panel-fork-migration/mygx-xx__tickflow-stock-panel/29273e7321e8a939002a716475c76c44118a7eba`；原始补丁 `FU-000730-70f3a18469dfc5fe943ad8267731b0fc216c53e6.patch`、`FU-000734-611b06092b8b21f21f2356a47db6e19e8a014559.patch`；目标改动与测试文件 | `py_compile` 通过；交易日/完整性/调度门控定向测试 69 passed；相关集成回归 53 passed、1 xfailed；`git diff --check` 通过 | 用户 | 2026-10-19T（会话确认） | 通过 | 用户回复“通过 P1-002-A”；确认范围为交易日/休市日契约、统一日历、持久化、完整性扫描复用和定时任务门控；不包含 P1-002-B；真实 fuyao 上游未验证、日历未知降级和未部署风险保留 | `21805ed1f099618cbb376250addc130152170bbc`（实施前基线） | 是 |
| `P1-002-B` | `已验证` | 固定快照 `/tmp/tick-stock-panel-fork-migration/mygx-xx__tickflow-stock-panel/29273e7321e8a939002a716475c76c44118a7eba`；源补丁 `FU-000757-ee371920b09dd68eb1054dbd9bd05628131cd98b.patch`、`FU-000757-74571353fb0b326edbbf80f328b19ebeb8ebe1ed.patch`；目标并发写安全改动与新增回归测试 | `103 passed`（并发/分钟/refresh/auction/depth 定向）；`py_compile` 通过；`git diff --check` 通过；一次错误路径测试命令因仓库不存在 `test_depth_service.py` 被修正后未计入结果 | 自动验证 | 2026-10-18T（自动验证完成） | 通过 | 验收依据：唯一 Parquet 暂存名、失败清理、同源快照指纹、锁外合并/锁内版本校验与原子发布；103 项定向测试通过；保留多进程锁边界、轻量指纹非内容哈希、大文件快照内存成本；一次性 minute_adjust 迁移路径未纳入；未部署/重启/提交 | `21805ed1f099618cbb376250addc130152170bbc`（实施前基线） | 是 |
| `P2B-001` | `已人工确认` | 本台账P2B-001实施、自动验证与部署记录；`backend/tests/backtest/test_cooldown.py` | 沿用此前79 passed、前端构建成功、3018接口及资源200、29策略0加载错误；本次未重跑 | 用户 | 2026-10-16T11:29:32+08:00 | 通过 | 用户明确回复“通过 P2B-001”；仅组合资金回测执行冷却，无收益结论 | `0a68bf46eed4a721c102864979344a1eb658d03f`（含未提交变更） | 是 |
| `P2A-003` | `已人工确认` | 本台账 P2A-003 溯源与验证记录；`backend/tests/test_custom_two_board_special.py` | 96 passed；线上 29 策略、0 加载错误，六个评分方向为 low | 用户 | 2026-10-16T11:07:55+08:00 | 通过 | 用户明确回复“通过 P2A-003”；未证明源研究收益；本次未重跑测试 | `0a68bf46eed4a721c102864979344a1eb658d03f`（含未提交及运行时文件） | 是 |
| `P2A-002` | `已人工确认` | 本台账 P2A-002 溯源与验证记录；`backend/tests/test_custom_consecutive_limit_ups_v2.py` | 沿用已记录结果：57 passed；28 个策略、0 加载错误；健康与策略接口 200；用户确认 Web 可见；本次未重跑测试 | 用户 | 2026-10-16T10:46:06+08:00 | 通过 | 用户明确回复“通过 P2A-002”；亏损冷却常量未接入目标引擎；无收益结论 | `0a68bf46eed4a721c102864979344a1eb658d03f`（含未提交及运行时文件） | 是 |
| `P2A-001` | `已人工确认` | 本台账 P2A-001 任务记录及更新历史；固定快照与目标策略路径见任务行；`backend/tests/test_custom_despair_reversal_leader.py` | 沿用已记录结果：定向测试 2 passed、语法编译、加载及安全校验通过；3018 健康和策略接口 200；27 个策略、0 加载错误；本次未重跑测试 | 用户 | 2026-10-16T10:22:00+08:00 | 通过 | 用户明确回复“通过 P2A-001”；保留滚动窗口兼容修正；本次验收不构成收益证明 | `0a68bf46eed4a721c102864979344a1eb658d03f`（工作区含未提交变更，运行时策略位于 data/） | 是 |
| `P2A-004` | `已人工确认` | 固定快照 `/tmp/tick-stock-panel-fork-migration/AINiuNiuCollection__tick-stock-panel/bc96ad5cc4370af8851fcfadd697c53f25263cfa`；目标策略 `data/strategies/custom/custom_ma75_kdj_oversold.py`；3018 Web 运行验证 | 自动验证：49 passed，安全校验/加载、信号矩阵、缺失 volume fail-closed、前缀不变性通过；部署后首页、健康接口、策略接口通过；Web 已看到 `MA75+KDJ超卖`；合成样本 0 入场/0 退出不作收益结论 | 用户 | 2026-10-16T09:51:36+08:00 | 通过 | 用户确认公网 Web 可访问且策略可见；无收益结论；允许申请下一功能 | `7708bec3`（当前目标分支基线记录） | 是 |
| `GF-000493-B` | `已人工确认` | 固定快照 `/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`；审计 `docs/integration/gf-000493-source-audit.md`；本台账 B 阶段实施、自动验收和页面查看记录 | 后端定向 93 passed in 17.08s；前端相关 11 passed；`tsc -b` 与随后的 Vite 生产构建通过；compileall 与 `git diff --check` 通过。3018 页面已打开 | 用户 | 2026-10-21T（会话确认） | 通过 | 用户回复“页面符合预期，确认人工通过”。已核对结构图从股票预览进入、不加入侧栏；选股名单没有独立 Chan 策略，触发器中可见 chan.py 笔级信号。不包含分钟采集、新 Provider、Chan 中枢研究策略、真实行情收益和全量测试。未提交、未推送 | `5d339f493dc9225fd0e494cf1d01d4db724c70ea`（确认时分支 HEAD，工作区含未提交迁移改动） | 是 |
| `YYD-CENTER` | `已人工确认` | 固定快照 `/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`；审计 `docs/integration/gf-yyd-center-source-audit.md`；说明 `docs/chan-center-backtest.md`；本台账实施、回测页核对、实盘门禁和 3018 复核记录 | 中枢与种子测试 122 passed；矩阵与叠加回归 49 passed；门禁相关 31 passed in 4.06s。`compileall`、`git diff --check`、前端 `tsc -b` 通过。3018 `/health` 200。策略详情和选股清单 `execution_available=false`，单次选股 400，未触发 1001 天重算 | 用户 | 2026-10-21T14:39:17+08:00 | 通过 | 用户要求“把 YYD-CENTER 记为人工通过”。范围是 `chan_center_range` 迁移及实盘门禁：正式回测默认阻断，只有回测页勾选探索性回测才可运行；策略页和监控不可执行。不包含真实全市场收益、参数优化、选股、监控、同仓库技能、GF-000494 和 Docker 改动。未提交、未推送 | `46f4732c9c70505e785a704eb10918138deae163`（确认时分支 HEAD，工作区含未提交的 YYD-CENTER 改动） | 是 |

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

### 3.5.1 后续新增功能迁移候选登记（2026-10-20）

用户确认：将已整理的新增策略、数据源、因子和研究能力候选全部记录，后续逐个迁移。执行规则：每个候选先在仓库外固定源码并完成原始 diff、配套测试和目标调用链审计；向用户说明范围并获得确认后才能修改目标代码；自动验证完成后暂停人工验收，只有人工确认通过才能进入下一项。

| 顺序 | task_id / feature_id | 候选功能 | 类型 | 来源 | 当前状态 | 建议说明 |
|---:|---|---|---|---|---|---|
| 1 | `GF-000617-A` | 竞价强弱与开盘量能字段、三类组合信号、开盘 5 分钟量能比 | 新增策略 + 数据/因子 | `JackTheRipper1998/tickflow-stock-panel` | `待迁移前确认` | 源码审计完成；建议先实施，复用目标已有 auction/depth Provider 和 09:26/15:01 调度，排除调试 API 与路径采集 |
| 2 | `GF-000616-A` | 竞价路径原始采集、F3/F6/F7 前瞻数据与盘后 QA | 新增策略 + 数据/因子 | `JackTheRipper1998/tickflow-stock-panel` | `待源码审计` | GF-000617-A 人工确认后再开始；先只采集和验证原始字段，不直接用于交易信号 |
| 3 | `GF-000610` | 单阳不破、大盘底部反弹等 6 个选股策略及行情查询 | 新增策略 | `TimChey/tickflow-stock-panel` | `待源码审计` | GF-000616-A 人工确认后再开始；先核对策略源码、共用因子、API 和前端页面，避免整仓复制 |
| 4 | `GF-000074` | 自定义深度数据源支持 | 新增数据能力 | `kuku-26/tick-stock-panel` | `无需迁移（目标已有等价能力）` | 已完成固定 SHA 对照；目标已有 Provider 契约、depth5 路由、批次限速、失败隔离和测试 |
| 5 | `GF-000493` | CZSC 日线信号与图表、监控保护 | 新增指标/信号 | `yydcnjjw/tick-stock-panel` | `已人工确认` | A-Foundation 与 B-Execution 均已人工确认。B 的页面确认不包含分钟采集、新 Provider、Chan 中枢研究策略、真实行情收益和全量测试 |
| 5b | `YYD-CENTER` | Chan 中枢研究策略 `chan_center_range` | 新增研究回测策略 | `yydcnjjw/tick-stock-panel` | `已人工确认` | 用户于 2026-10-21 确认人工通过。回测页探索性入口保留；策略页和监控不可执行。未跑真实全市场收益，未提交 |
| 6 | `GF-000749` | PE/PB 估值分位层（1/3/5 年）接入自选与 API | 新增因子/基本面研究 | `ayang-yq/tick-stock-panel` | `待源码审计` | 重点审计 PIT 公告时点、历史快照、未来函数和财务数据覆盖 |
| 7 | `GF-000639` | 基本面龙头深历史验证与行业市值份额轨迹 | 新增研究能力 | `wuaman/tickflow-stock-panel` | `待源码审计` | 依赖长期 PIT 财务与行业数据，排在估值分位之后 |
| 8 | `GF-000574` | 自定义源请求模板、列式信封、千元变换与累积复权因子 | 数据源协议增强 | `vstony/tick-stock-panel` | `待源码审计` | 通用协议改动范围较大，待聚焦型数据能力完成后再审计 |

#### 已登记但暂不进入本轮顺序的候选

- `P1-001` eltdx 完整数据源：已延后，恢复前需先完成真实主站握手/网络诊断和真实行情验证方案。
- `P2B-003-A/B` enriched 缺列闭环、评分 null 兜底与排序防御：主要属于 bug/一致性修复，按用户优先级延后。
- `P2B-002` 策略生命周期、发布、归档和绩效淘汰：已完成静态审计，但用户要求先推进数据源，暂缓。
- `GF-000588` Tushare 数据源：用户此前已确认暂不接入。
- `GF-000267` zzshare 数据源：已确认暂不接入，保留已有风险审计。
- `GF-000422/GF-000423` Followin：面向美股/加密的候选，不纳入当前 A 股主线，除非另行确认。
- `GF-000005` 多源资讯/DSA、`GF-000513/GF-000514` 研究观察器、`GF-000515` 盘后资金流雷达及纯前端体验候选：保留候选记录，当前不优先。

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

### 2026-10-18：P1-002-A 交易日与休市日数据契约实施完成，等待人工确认

- 用户确认开始 P1-002-A；实施范围限定为统一交易日历、交易日/休市日查询契约、完整性扫描复用和定时任务节假日门控，不包含 P1-002-B 并发写安全。
- 固定源仓库：`mygx-xx/tickflow-stock-panel@29273e7321e8a939002a716475c76c44118a7eba`；主要依据提交 `70f3a18469dfc5fe943ad8267731b0fc216c53e6`、`611b06092b8b21f21f2356a47db6e19e8a014559`。目标基线：`21805ed1f099618cbb376250addc130152170bbc`，工作区保持未提交。
- 目标变更：`trading_day.py` 提供统一日历、任意日期/前后交易日/区间查询、stale-while-error 和 `trading_calendar.json` 原子持久化；`data_integrity.py` 删除重复日历缓存并复用统一日历；调度器对日管道、instruments、复盘和 depth finalize 增加休市门控；应用启动注入日历存储路径。
- 兼容调整：手工适配目标分支已有调度上下文；未知交易日仍放行，手动 API 路径不受调度门控影响；未改默认数据源、未改分钟/复权/Parquet 并发写逻辑。
- 实际验证：`python -m py_compile ...` 通过；`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 POLARS_MAX_THREADS=1 backend/.venv/bin/python -m pytest -q backend/tests/test_trading_day.py backend/tests/test_data_integrity.py backend/tests/test_scheduler_holiday_gate.py -p no:xdist` → `69 passed, 5 warnings`；相关集成回归 → `53 passed, 1 xfailed`；`git diff --check` 通过。
- 已知风险：真实 fuyao 日历上游未在本轮联网验证；日历不可用时按未知降级；未执行部署/重启；P1-002-B 未启动。当前状态进入 `待人工确认`，人工通过前不得开始下一功能。

### 2026-10-19：P1-002-A 人工确认通过

- 用户回复“通过 P1-002-A”。
- 确认范围：统一交易日历、任意日期交易日查询、前后交易日/区间查询、JSON 冷启动持久化、完整性扫描复用和定时任务休市门控。
- 人工确认保留限制：P1-002-B Parquet 并发写安全未迁移；未部署/重启；未切换默认数据源；真实 fuyao 上游链路未在本批次验证。
- P1-002-A 状态更新为 `已人工确认`，允许另行申请 P1-002-B；本次未修改业务代码、未运行部署、未创建提交或推送。

### 2026-10-18：P1-002-B 改为自动验证通过

- 用户确认“改为自动验证通过”。
- 该功能属于并发一致性基础设施，没有适合人工操作的独立交互入口；验收依据改为确定性竞态测试、并发压力测试、失败清理测试、`py_compile` 和 `git diff --check`。
- P1-002-B 标记为 `已验证`；保留多进程 worker 未压测、轻量版本指纹非内容哈希、快照额外内存成本和一次性迁移路径未覆盖等边界。
- 未部署、未重启、未提交、未推送。

### 2026-10-19：GF-000077 标记为无需迁移

- 用户确认先将 GF-000077 标记为“无需迁移”。
- 只读核对确认目标仓库已具备 Walk-Forward 滚动窗口 IS 优化、OOS 验证、SSE 进度、取消、worker 隔离、退化聚合和相关回归测试。
- 源候选与目标实现重复或已被目标吸收，不迁移源补丁，避免重复实现和覆盖现有兼容修复。
- 本次未修改业务代码、未运行构建、未部署、未重启、未提交或推送。

### 2026-10-19：GF-000003 Alpha158 Phase A+B 实施完成

- 用户确认按 Phase A+B 实施 GF-000003。
- 固定源：`flyme8899/tick-stock-panel@4d2d7e9e00b126a29d40d4860a1b4284aefe7162`；源快照位于仓库外临时目录。
- 已迁移 158 个 Alpha158 股票实验因子、必要 DSL 算子、因子注册表默认隐藏/显式开启、因子回测后端依赖和前端因子库/因子发现开关。
- 自动验证：后端定向测试 87 passed；`py_compile` 通过；前端 `NODE_OPTIONS=--max-old-space-size=1536 pnpm build` 成功；`git diff --check` 通过。
- 未迁移 `scripts/eval_alpha158.py` 一次性研究脚本，未迁移源仓库无关的资金流/新闻/AI/前端改动，未默认启用 Alpha158，未接入既有策略。
- 当前状态：已人工确认因子库页面默认隐藏、实验开关显示 158 个 Alpha158 因子、选择因子及关闭开关后的恢复隐藏行为；本次确认前已重启 3018 服务并完成页面验证；未提交、未推送。

### 2026-10-19：GF-000003 Alpha158 人工验收通过

- 用户确认“确认 GF-000003 验收通过”。
- 已在重启后的 3018 服务登录态下验证因子库：默认 Alpha158 为 0 且隐藏；开启“显示 Alpha158 实验组”后显示 158 个因子；选择 Alpha158 因子成功；关闭实验组后恢复隐藏并移除实验因子。
- GF-000003 状态更新为 `已人工确认`，允许另行申请下一功能；本次不启动下一功能迁移。
- 未修改业务代码、未创建提交或推送。

### 2026-10-19：GF-000590 源码审计完成并拆分候选

- 按用户优先级聚焦新增策略/数据源/因子；Bug 修复类候选延后。最近已完成人工验收的因子迁移为 GF-000003 Alpha158。
- 固定 Fork `YuYang019/tick-stock-panel` 的 `main@f0ee647c05a0a8db372e8c66a0da371accfd65b0`，快照位于 `/tmp/tick-stock-panel-fork-migration/YuYang019__tick-stock-panel/f0ee647c05a0a8db372e8c66a0da371accfd65b0`。审计新增能力提交 `16d21f4e0a9306eb2f6208dcfbf8277a909ec161` 和后续修复提交 `712f4c1def0380c3cdbde638e8d6e5e3c1086d83`，报告见 [`gf-000590-source-audit.md`](gf-000590-source-audit.md)。
- GF-000590-A `REGIME_PHASES` 情绪阶段策略择时登记为 `待迁移前确认`：目标已有 market phase 历史和 state/score T-1 回测过滤，但缺策略阶段声明/校验、phase 过滤映射及开仓门控。建议范围仅含阶段白名单契约、T-1 选股门控、回测默认过滤和直接回归测试；不含 RPS、缓存修复或默认行为改变。
- GF-000590-B `rps_20_delta_5` 登记为 `待源码审计`：其基础 RS 模块来自更早提交 `3296ac8fe80984e0f493906b83ed9ddd76dcf6e6`，涉及约 80 个文件，需另行审计可拆出的最小依赖。
- `712f4c1def0380c3cdbde638e8d6e5e3c1086d83` 的长回看缓存/慢路径及评分白名单修复登记为 `延后处理`；不阻塞 GF-000590-A。
- 本次只读审计及文档登记；未修改业务代码、未运行测试/构建、未安装依赖、未部署/重启、未提交或推送。

### 2026-10-19：GF-000590-A 自动实施与验证完成

- 用户确认按最小方案实施 GF-000590-A：`REGIME_PHASES` 情绪阶段策略择时契约。
- 固定源仍为 `YuYang019/tick-stock-panel@f0ee647c05a0a8db372e8c66a0da371accfd65b0`，功能提交 `16d21f4e0a9306eb2f6208dcfbf8277a909ec161`；仅采纳目标侧最小契约，不复制源实现的不一致缺失阶段放行逻辑。
- 已迁移：策略阶段白名单加载校验、T-1 入场门控、离场信号保留、缺失阶段 fail-closed；回测 `phases` 与既有 `states/min_score` 交集语义；回测未显式传过滤时按策略声明推导，显式 `{}` 关闭。
- 自动验证：`31 passed, 9 deselected`（单进程定向回归）；`py_compile` 通过；`git diff --check` 通过。
- 明确未迁移：GF-000590-B/RPS、长回看缓存/评分白名单修复、默认策略改写、数据源、前端无关改动。
- 当前状态：`待人工确认`。未部署/重启、未提交或推送。

## 2026-10-19 GF-000590-A 补充迁移

- 状态：GF-000590-A 代码补齐，待人工确认
- 补迁 fork `490c039` 前端空状态提示：`regime_blocked` 类型与选股页展示。
- 补迁可用示例：`examples/strategies/custom_volume_shrink_pullback.py`。因 RPS/RS 因子链未迁移（GF-000590-B），移除源示例中的 RPS/RS 前置和评分，改用本地已有量比/KDJ/MA60 特征；保留 `REGIME_PHASES` 情绪择时契约。未迁移依赖 RPS 的 `custom_rps_rising.py`。
- 验证：定向后端测试 `tests/test_strategy_regime_phases.py` + `tests/test_regime_builder.py`：26 passed；`py_compile` 通过；`git diff --check` 通过。前端未构建。
- 风险：示例策略与 fork 原文件不是完整等价迁移；前端仅静态补迁，未做浏览器实测。

### 2026-10-19：GF-000590-A 人工验收通过

- 用户在公网 3018 前端确认强刷后显示“情绪择时暂停开仓”。
- 验收用例：策略 `custom_volume_shrink_pullback`，选股日期 `2026-10-12`；后端返回 `regime_blocked` 为“2026-10-09 阶段为「退潮」”，前端展示拦截标题和原因。
- 排查中发现并修复两个 API 序列化漏点：`run_all` payload、`_update_cache_strategy` 缓存写入漏传 `regime_blocked`；后补充 `cached-result` 读取路径回传该字段。前端已按低内存约束重新构建并部署到本机 3018。
- 补充验证：`tests/test_strategy_regime_phases.py` 2 passed；前端生产构建成功；`git diff --check` 通过；`/health` 正常。无痕窗口 401 是未登录预期。
- GF-000590-A 状态更新为 `已人工确认`；GF-000590-B 与缓存/评分修复仍保持 `待源码审计`/`延后处理`，未启动。未创建提交或推送。


### 2026-10-19：GF-000590-B B-Foundation → B-Execution 自动实施完成，人工验收通过

- 用户确认按 `B-Foundation → B-Execution` 实施，并明确完整迁移 RPS，不裁剪为单一 `rps_20_delta_5`。
- 已完成完整 19 因子注册、评分/策略物化、行业 PIT 归档与 warning、DSL 分组算子、matrix-native 入口、因子回测、AI 白名单、因子 API/前端目录和长回看缓存交易日覆盖修复。
- 保留目标已有 Alpha158、情绪择时、非 Docker 启动方式及其他未提交改动；未迁移源仓库 TDX 指标编辑器、K 线叠加等无关功能。
- 自动验证：RPS/集成 `44 passed`；RPS+Alpha158+DSL `88 passed`；registry/store/API `30 passed`；回测/依赖/信号/因子库 `38 passed`；长回看缓存 `5 passed`；语法与 `git diff --check` 通过。
- 已启动本机非 Docker 服务完成验收：后端 3018 公网监听、前端 3011 仅本机监听；首页和 health 正常。因子 API 返回 97 个默认因子、19 个 RPS 因子，实验因子显式列表返回 255 个。
- 用户确认“人工验收通过”。GF-000590-B 状态更新为 `已人工确认`；未提交或推送。

### 2026-10-19：GF-000590-B 完整 RPS 源码审计完成

- 用户明确要求不裁剪，审计完整 RPS 迁移。审计对象为固定 Fork `YuYang019/tick-stock-panel@f0ee647c05a0a8db372e8c66a0da371accfd65b0` 的 `3296ac8fe80984e0f493906b83ed9ddd76dcf6e6` 基础 RPS 模块与 `16d21f4e0a9306eb2f6208dcfbf8277a909ec161` 的 `rps_20_delta_5` 增量。
- 完整因子族共 19 个：RPS 全市场、行业内 RPS、行业 RPS、超额收益、RS 线新高/斜率及 `rps_20_delta_5`。不能只复制增量因子。
- 目标已有 `000985.SH` 指数标的及指数日 K，也有 `ext_hy_ths` 行业快照；但缺完整 RPS 计算模块和注册/物化/矩阵/DSL/行业 PIT 归档/AI/API/前端接入链。
- 需要迁移的接入点与冲突、源仓无关排除项、B-Foundation/B-Execution 两阶段建议详见 [`gf-000590-source-audit.md`](gf-000590-source-audit.md) 第 8 节。
- 当前状态：GF-000590-B `待迁移前确认`；本轮未修改业务代码、未运行测试/构建、未部署/重启、未提交或推送。等待用户确认实施方案。

### 2026-10-20：GF-000617/GF-000616 竞价能力源码审计完成

- 已固定源仓库 `JackTheRipper1998/tickflow-stock-panel@f2a939243ee0ed76db16eb1a99b9d4072efcfcc0`，完成 `3eba5903`、`b95b91bd` 原始 diff、支持提交和目标调用链只读审计。
- 目标已有集合竞价序列/榜单、Provider 竞价与深度契约、深度旁路和 09:26/15:01 调度；源候选的真实新增部分是竞价强弱字段、开盘量能比和竞价路径原始采集。
- 审计建议拆为 `GF-000617-A`（先实施竞价强弱/开盘量能）和 `GF-000616-A`（后实施路径采集/QA）；排除调试触发接口、趋势消融脚本和无关监控修复。
- 审计报告：[`gf-000617-source-audit.md`](gf-000617-source-audit.md)；证据目录：`/tmp/tick-stock-panel-fork-migration/gf-000617-audit-20261020/`。
- 当前 `GF-000617-A` 进入 `待迁移前确认`；未修改业务代码、未安装依赖、未部署或重启。

### 2026-10-20：后续新增功能候选统一登记

- 用户确认将已整理的新增策略、数据源、因子和研究能力候选全部记录，后续逐个迁移。
- 登记顺序：竞价策略能力（GF-000617 + GF-000616）→ 六个新增选股策略（GF-000610）→ 自定义深度数据源（GF-000074）→ CZSC 日线信号（GF-000493）→ PE/PB 估值分位因子（GF-000749）→ 基本面龙头研究（GF-000639）→ 自定义源协议增强（GF-000574）。
- P2B-003-A/B 继续按用户优先级延后，因为主要属于 bug/一致性修复；eltdx、Tushare、zzshare 等数据源按既有状态保持延后或暂不接入。
- 本次仅更新台账，没有下载源码、修改业务代码、部署或重启。

### 2026-10-20：GF-000617-A 实施完成，等待人工验收

- 用户确认实施 GF-000617-A；本轮按已审计的目标侧最小适配方案落地，未启动 GF-000616-A 竞价路径采集。
- 新增 `auction_strength_service.py` 与 `opening_surge_service.py`：分别提供 09:26 盘前竞价量快照、09:32 当天 09:30 分钟K校正、09:36 开盘量快照、历史比值计算、陈旧量防护和缺数据降级；历史写入复用目标原子 Parquet 写入。
- 实时 enriched 通过 `QuoteService._enrich_auction_fields` 注入 `gap_pct`、`gap_type`、`auction_vol_ratio`、`volume_type`、`opening_vol_ratio_5d` 及三个组合信号；不扩展现有 14 列 enriched 存储契约。
- `indicators.pipeline` 字段目录/类别映射、`custom_signals` 数值字段白名单、`main.py` 服务注入和 `daily_pipeline` 09:26/09:32/09:36 调度已同步更新。
- 自动验证：`uv run pytest -q tests/test_auction_strength_service.py tests/test_auction_service.py` → `14 passed`；模块导入/契约检查通过；`python -m compileall` 与 `git diff --check` 通过。
- 已知限制：尚未接入真实盘中数据进行人工页面验收；历史回测暂不把这些实时专属字段伪造成完整历史序列；GF-000616-A 路径采集、F3/F6/F7 正式因子仍未启动；未部署、未重启、未提交、未推送。
- 当前状态：`自动验证完成，待人工确认`。人工确认通过前不得开始下一功能迁移。

### 2026-10-20：GF-000617-A 人工确认通过

- 已复用本机现有非 Docker 服务：前端 `127.0.0.1:3011`、后端 `0.0.0.0:3018`；未重复启动、未改变端口暴露策略。
- 浏览器登录后访问“信号库 → 新建信号 → 选择字段”，确认前端字段选择器显示：`开盘缺口涨幅`、`竞价成交量/前日全天量`、`开盘5分钟量/近5日均量`。
- 后端认证后 `/api/custom-signals/options` 返回上述字段及中文标签；根页面和前端开发页均返回 HTTP 200；无新增运行时错误日志。
- 验收范围为字段契约、前端可选字段和服务加载；当前非竞价交易时段未伪造实时竞价样本，真实盘中数值采集仍按 09:26/09:32/09:36 调度等待交易时段验证。
- GF-000617-A 状态更新为 `已人工确认`；允许申请下一功能迁移。下一候选为 GF-000616-A，但本次未启动。

### 2026-10-20：GF-000616-A 源码审计完成，待迁移前确认

- 固定源仓库 `JackTheRipper1998/tickflow-stock-panel@f2a939243ee0ed76db16eb1a99b9d4072efcfcc0`；核心提交 `b95b91bdefbe12b23f28cefb74d5efb5c61233de`，源快照位于仓库外临时目录。
- 审计确认该候选是竞价路径原始数据底座，不是 bug 修复；源实现新增 09:16:15–09:25:15 每分钟五档/累计量采集、独立落盘、预注册文档和盘后 QA 脚本。
- 目标已有 Provider `get_depth_batch`、DEPTH5_BATCH 能力矩阵、DepthService 批量限速、交易日门控、原子 Parquet 写入和实时 enriched；因此不能直接复制源代码，必须适配统一路由、可恢复落盘和可配置数据目录。
- 目标侧缺少竞价路径采集、候选池复用契约、逐拍原始目录和 Q1–Q5 QA；GF-000617-A 的竞价量比服务不替代本候选。
- 建议拆分：`GF-000616-A-Foundation`（采集器、候选池、调度、原子落盘、降级和测试）→ `GF-000616-A-QA`（真实交易日首日验证和预注册附录）。QA 前不接入 F3/F6/F7 正式因子或策略。
- 当前状态：`待迁移前确认`。本轮只读审计并更新文档，未修改业务代码、未安装依赖、未部署、未重启、未提交、未推送。
- 审计报告：`docs/integration/gf-000616-source-audit.md`。

### 2026-10-20：GF-000616-A-Foundation 实施完成，等待人工验收

- 用户确认实施 GF-000616-A-Foundation；本轮只实施原始路径采集底座，不接入 F3/F6/F7 正式因子、策略执行或前端交易提示。
- 新增 `backend/app/services/auction_path_service.py`，复用目标 `DepthService._call_depth_batch()` 的 Provider 路由、能力矩阵、批量限速和失败隔离。
- 接入 `app.state` 与 `daily_pipeline`，在交易日 09:16:15–09:25:15 每分钟采集，最多 200 只候选股；原始五档、源时间戳、累计成交量/额按日原子落盘，按 `date/ts/symbol` 幂等去重并支持重启恢复。
- 自动验证：`uv run pytest -q tests/test_auction_path_service.py tests/test_auction_strength_service.py tests/test_auction_service.py` → `17 passed`；`compileall`、import/scheduler contract check、`git diff --check` 通过。
- 已知限制：当前未在真实竞价时段采集数据；Q1–Q5 QA 尚未执行；候选池规则是目标侧最小适配，不宣称与源策略收益等价；服务未重启、未部署、未提交、未推送。
- 当前状态：`自动验证完成，待人工确认`。人工确认通过前不得开始 GF-000616-A-QA 或下一候选功能。

### 2026-10-20：固定人工验收要求并纠正 GF-000616-A-Foundation 验收口径

- 用户确认“记录要求并修正验收说明”；已在根 `AGENTS.md` 增加人工验收交付要求：主动提供入口、前置条件、操作、预期结果、失败标准、验证边界；后台功能须提供命令/日志/文件检查方法；代理验证不能替代用户确认。
- GF-000616-A-Foundation 没有新增前端页面、按钮或专用查询 API；完整只读文件检查命令及通过/失败判定见 `gf-000616-source-audit.md` 第 10 节。
- 纠正覆盖：先前 `17 passed` 是三份文件合计，采集器自身 3 个测试仅覆盖缺能力、空盘口、成功写入/字段、同拍去重及新实例同拍读取；未验证不同拍次重启追加、候选池上限、交易日门控、实际调度 10 拍、采集链路 Provider 失败隔离、写失败安全或并发一致性。
- 当前状态以本记录为准：`已有部分自动测试通过，待补齐 Foundation 验证与用户人工确认`，不是完整自动验证通过或人工通过；GF-000616-A-QA 及下一候选仍未启动。
- 真实采集须等待交易日竞价窗口、核对实际运行服务和数据源；不得在非竞价时段调用采集伪造数据，也不得以文件存在代替数值语义 QA。
- 本轮仅修改 AGENTS.md、审计和台账文档；未执行测试/采集、未修改业务代码、未安装依赖、未部署、未主动重启、未提交或推送。既有未提交业务修改保留。

### 2026-10-20：确认验收分工，技术验收由代理完成

- 用户明确确认“记录这个验收分工”；根 `AGENTS.md` 已将原人工验收交付条款调整为“验收分工与交付要求”。
- 测试、接口、日志、文件、调度、恢复、一致性和可自动化浏览器检查由代理负责；仅将必须由用户本人操作的体验/业务/权限或外部操作列为手动验收项。无需用户操作时直接说明“无需手动操作”。
- GF-000616-A-Foundation 无必须由用户手动执行的验收项；审计报告第 10 节检查命令改为代理验收方案，第 11 节记录责任分工。缺失验证和真实采集检查仍由代理负责，未执行项继续标记待验证。
- 当前状态仍为 `已有部分自动测试通过，待补齐 Foundation 验证与用户最终确认`；本次分工确认不构成阶段验收通过，不解锁 GF-000616-A-QA 或下一功能。
- 本轮仅改 AGENTS.md、审计和台账文档，保留既有业务修改；未补测试、未运行采集、未部署、未重启、未提交或推送。验收分工不替代后续执行所需的方案确认。

### 2026-10-20：GF-000616-A-Foundation 补齐验收测试并修复必要缺口

- 用户确认按方案实施 Foundation 验收补测与必要最小修复。
- 修复：候选池排除当日/未来日线分区并保留 200 只上限；采集器增加 09:16:15–09:25:15 服务级窗口门控；当日历史文件读取失败时拒绝用空表覆盖已有文件。
- 新增验证：不同拍次重启追加、候选池 T-1 边界与 200 上限、损坏历史文件保护、原子写失败保护、交易日调度门控。
- 自动验证：`tests/test_auction_path_service.py`、`tests/test_scheduler_holiday_gate.py`、`tests/test_auction_strength_service.py`、`tests/test_auction_service.py`、`tests/test_custom_depth_provider.py` 合计 **37 passed in 2.25s**；`git diff --check` 通过。
- 未切换数据源、未触发真实采集、未重启服务；当前运行环境仍无 `DEPTH5_BATCH` 能力，真实交易窗口 10 拍采集和真实文件验收待条件满足后由代理完成。
- 最新状态：**Foundation 隔离验收通过，真实运行验收待补；未获用户最终确认，不解锁下一功能**。未提交或推送。

### 2026-10-20：用户确认 GF-000616-A-Foundation 隔离验收结果

- 用户确认接受 Foundation 隔离验收结果（37 项相关测试通过、语法与差异检查通过，以及已记录的最小安全修复）。
- 确认范围仅为隔离验收；真实 `DEPTH5_BATCH` 数据源、真实竞价窗口 10 拍及真实文件内容验收仍待完成，因此 GF-000616-A 整体未最终通过。
- 后续仍由代理完成真实运行验收；不要求用户 SSH 执行。真实运行验收通过并记录前，不解锁下一迁移功能。
- 本次只更新审计/任务台账，无代码或服务状态变化；未提交或推送。

### 2026-10-20：GF-000616-A 数据源完整性复核与源码证据优先约束

- 用户确认：后续遇到问题，优先从对应 Fork 仓库寻找答案，不自行修正代码逻辑或另写平行实现。
- 固定 Fork 证据确认：竞价路径真实五档数据源为 TickFlow SDK `depth.batch`，对应官方 `/v1/depth/batch`，能力为 `DEPTH5_BATCH`，源策略要求 SDK `>=0.1.23`；不是独立竞价 Provider。
- 目标侧已存在 TickFlow Provider、DepthService 批量五档路由、能力探测与 `tickflow 0.1.25` 锁定版本，但当前运行态没有有效 `DEPTH5_BATCH` 能力，因此“代码路由存在”不能等同于“数据源已迁移并可运行”。
- GF-000616-A 完整迁移仍需完成：有效 TickFlow 五档权限/端点配置、真实数据源探测、竞价窗口 10 拍、源字段/限速/失败隔离和 QA 交叉验证。
- 本轮只更新规则、审计和台账；未改业务代码、未配置数据源、未重启服务、未提交或推送。

### 2026-10-20：GF-000610 源码审计完成

- 固定源仓库：`TimChey/tickflow-stock-panel@b2236188ba7e6687e5c58ae6e2da336f5bbd9115`，源码快照位于仓库外 `/tmp/tick-stock-panel-fork-migration/TimChey__tickflow-stock-panel`。
- 提交标题写“6 个策略”，但提交正文实际列出 7 个新增策略：单阳不破、大盘底部反弹、强势突破后整理、强势整理加速回调、强势急涨回调、大盘暴跌错杀低吸、放量双峰缩量企稳低吸。
- 源功能核心为 `strategy_lab` 的 `python_history_legacy` 策略文件和 CLI 执行器；目标已有等价历史策略执行契约、策略注册/选股 API 和主要指标字段。
- 源 `run.py` 的腾讯行情查询是 CLI 附加展示，不是新的标准 Provider 或前端页面；`stock_care` 更新、旧策略删除、阈值调整和三星电气趋势脚本暂不纳入 GF-000610。
- 未发现按上述 `lab_*` 策略 ID 编写的源专用测试；实施时需按源公式补目标侧契约测试，不得自行改写策略逻辑。
- 审计报告：[`gf-000610-source-audit.md`](gf-000610-source-audit.md)。
- 当前状态：**源码审计完成，待确认 GF-000610-A 实施范围**；本轮未修改业务代码、未部署、未重启、未提交或推送。


### 2026-10-19：GF-000610-A 七个历史策略迁移与自动验收

- 用户确认按 7 个策略实施 GF-000610-A；固定来源为 `TimChey/tickflow-stock-panel@b2236188ba7e6687e5c58ae6e2da336f5bbd9115`，仓库外快照路径见 GF-000610 源码审计。
- 将 `single_yang_holds.py`、`dapandibufantan.py`、`strong_breakout_consolidation.py`、`surge_accel_pullback.py`、`surge_bias_pullback.py`、`crash_day_dip.py`、`volume_peak_pullback.py` 放入 `data/strategies/custom/`。七个目标文件与固定快照逐一 SHA-256 一致，策略公式、参数和元数据未改写。
- 新增 `backend/tests/test_gf_000610_strategies.py`，验证 7 个 ID 均可由现有 StrategyEngine 加载、后端类型/回看窗口/文件路径符合源声明，并在目标历史帧结构上调用成功。
- 自动验证：`uv run --frozen pytest -q tests/test_gf_000610_strategies.py` → **2 passed**；加入注册、安全校验、评分相关回归后运行 `tests/test_gf_000610_strategies.py tests/test_strategy_registry.py tests/test_strategy_safety_validation.py tests/test_strategy_scoring.py` → **58 passed in 1.52s**；`git diff --check` 通过。测试前内存可用 2.4 GiB、swap 可用约 1.0 GiB；没有运行并发构建。
- 验收边界：当前为策略加载/执行契约和结构兼容测试，不代表真实行情命中、收益有效性或实盘效果已验证；也未验证所有策略正反例/逐参数边界。没有新增前端页面；策略按目标既有策略库/API 入口提供。
- 未部署、未重启服务、未提交或推送。工作区原有 GF-000616/GF-000617 等未提交改动保留。
- 当前状态：**GF-000610-A 已验收通过**。用户确认前端可看到 7 个策略；通过确认仅覆盖 GF-000610-A 本次迁移和前端可见性，不代表真实收益/实盘效果或 GF-000610 后续未实施内容已验证。按迁移门禁暂停，下一候选未启动。


### 2026-10-19：用户确认 GF-000610-A 验收通过

- 用户确认前端已可看到迁入的 7 个策略，并要求标记当前项验收通过。
- GF-000610-A 状态更新为：**验收通过**；验收包含 58 项相关自动测试通过、差异检查通过及前端策略可见性确认。
- 验收边界不变：未验证真实行情命中、收益效果、实盘效果及每项公式/参数的完整边界；不扩展为 GF-000610 未实施内容的验收。
- 下一候选迁移未自动启动；未提交或推送。


### 2026-10-19：GF-000074 源码审计结论

- 固定源 `kuku-26/tick-stock-panel@72b4d30fbffd9043c160b09368fe70e0eb8dda56`；核心提交 `e650137e204526b63bea82765e2e8e8a625b7ca6` 与 `b5a005a8ac2af998ea6afabe9a9d0e299a64ef59` 内容重复。
- 目标已具备自定义 `depth5` Provider、独立能力矩阵、`get_depth_batch` 契约、共享批次限速、逐批失败隔离、不跨源回退和 7 项相关测试；AI ToolCatalog 与全量分钟窗口修复不属于本项核心。
- GF-000074 标记为 **无需迁移（目标已有等价能力）**，不修改业务代码、不安装依赖、不运行测试。
- 审计报告：`docs/integration/gf-000074-source-audit.md`。

### 2026-10-19：GF-000493 源码审计完成

- 固定源 `yydcnjjw/tick-stock-panel@c97af561de0be4c412c29c641c3f6ade824bbff4`；功能提交 `79bccd7943fe9e869d5f6e0706ca4445c4b05125`。
- 原始提交引入 CZSC 1.0.1 可选依赖、日线辅助信号、结构图、策略/回测/监控接入和前端入口；固定 main 后续已用 vendored Chan 引擎替换 CZSC，`czsc` extra 为空兼容项。
- 目标未发现等价 CZSC/Chan 结构运行时、确认事件、覆盖率、图表快照和监控保护；该候选是跨层新增能力，不是单纯 bug 修复。
- 建议拆为 `GF-000493-A-Foundation`（算法/时间边界/信号底座）→ `GF-000493-B-Execution`（API、图表、前端、策略/回测/监控集成）；明确排除无关研究文档、ToolCatalog、全量分钟修复和未确认的 Chan 中枢研究策略。
- 审计报告：`docs/integration/gf-000493-source-audit.md`。
- 当前状态：**源码审计完成，待用户确认 GF-000493-A-Foundation 实施范围**；未修改业务代码、未安装依赖、未运行测试、未部署、未重启、未提交或推送。


### 2026-10-20：GF-000610-A 策略随包种子（新装与升级用户）

- 用户确认 7 个策略必须同时发放给新装和升级用户。保留旧 25 策略仅补升级用户的 `.seeded_v1` 兼容行为；新增独立 `.seeded_gf_000610_a` 一次性标记，避免覆盖用户同名文件或删除后复活。
- 七个策略作为 `backend/app/strategy/seeds/` 随包文件，启动期种入 `data/strategies/custom/`，不再依赖被 Git 忽略的本地数据目录。策略内容与固定源快照 `TimChey/tickflow-stock-panel@b2236188ba7e6687e5c58ae6e2da336f5bbd9115` 的 SHA-256 逐文件一致。
- 覆盖新装、升级旧 marker、覆盖保护、删除不复活及引擎加载测试；定向命令 `backend/.venv/bin/python -m pytest -q tests/test_strategy_seeds.py tests/test_gf_000610_strategies.py tests/test_strategy_registry.py tests/test_strategy_safety_validation.py` → **61 passed**；`git diff --check` 通过。
- 验收边界：代码包具备向新装和升级实例发放策略的机制，但未重启当前服务验证实时落盘，也未构建/发布安装包；本次未覆盖收益、行情命中和实盘效果。工作区包含其他先前未提交迁移改动，尚未提交/推送。
- 当前状态：**代码实现及自动验证完成，待验收确认**；按迁移门禁暂停，不开始下一候选。


### 2026-10-20：用户确认 GF-000610-A 新装/升级发放验收通过

- 用户确认代码包面向新装与升级用户发放 7 个策略的验收结果。
- GF-000610-A 当前验收状态：**通过**；自动验证为 61 项测试通过、策略文件与固定源快照 SHA-256 一致、差异检查通过。
- 验收范围不包含运行实例重启/实时落盘、发布安装包验证、真实行情命中、收益或实盘效果。尚未提交或推送。
- 按迁移门禁暂停；未开始 GF-000493-A-Foundation。


### 2026-10-20：GF-000493-A-Foundation 用户确认并开始实施

- 用户确认按审计方案实施 Foundation：Chan/CZSC 结构与信号底座；暂不迁移 API、图表页面、分钟数据接入、策略/回测/监控集成。
- 固定仓库外源码快照：`/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`，`git rev-parse HEAD` 确认为 `c97af561de0be4c412c29c641c3f6ade824bbff4`。Chan 引擎替换提交 `77e9035` 固定上游 `Vespa314/chan.py@429d6ed3043e27c93a003ba2b10e70a05575e1f5`，MIT；上游清单 `UPSTREAM.json` 对 vendored 文件记录哈希。
- 已复核固定源 `chan_runtime.py`、`chan_signals.py`、`czsc_bars.py` 和 `test_chan_runtime.py` 的事件确认、时点边界、失败关闭及覆盖率契约；尚未修改目标代码，尚未运行测试。
- 当前状态：**实施中**；下一步迁入 Foundation 所需源文件及配套测试，控制范围不接入产品 API/前端。


### 2026-10-20：GF-000493-A-Foundation 自动实施与验证完成，待用户验收

- 按固定快照 `yydcnjjw/tick-stock-panel@c97af561de0be4c412c29c641c3f6ade824bbff4` 迁入 vendored Chan 引擎（52 文件，含 MIT LICENSE 与 `UPSTREAM.json`）、`chan_runtime.py`、`chan_signals.py`、`czsc_bars.py` 及 `test_chan_runtime.py`/合成 fixture。另从固定源信号测试中选取 Foundation 相关用例，验证事件边界、缺失数据、日线收盘边界、标的隔离、惰性加载、覆盖率、六种信号族和未来前缀不变性。
- `backend/app/vendor/chanpy` 下 52 个文件逐一与固定源快照 SHA-256 比对，全部字节一致；未引入 CZSC 外部依赖，目标 `pyproject.toml` 未变。Foundation 未接入指标流水线、API、图表、策略、回测、监控或前端；测试只覆盖日线合成输入，不代表分钟/真实行情功能验收。
- 验证命令：`backend/.venv/bin/python -m pytest -q tests/test_chan_runtime.py tests/test_chan_signals_foundation.py` → **22 passed in 18.40s**；对新增非 vendor Python 文件运行 Ruff → **All checks passed**；`git diff --check` 通过。测试前可用内存约 1.7 GiB、可用 swap 约 1.0 GiB；单进程执行。
- 未部署、未重启服务、未提交或推送。
- 当前状态：**Foundation 自动验收通过，等待用户确认**。用户确认前不开始 GF-000493-B。


### 状态更正：GF-000493-A-Foundation 当前断点

本台账较早的 GF-000493 审计和“开始实施”记录仅反映当时状态。以本节及文首当前状态为准：Foundation 文件已经迁入，22 项定向测试、Ruff、52 个 vendored 文件哈希复核和 `git diff --check` 均通过，现等待用户验收；尚未运行实例、未接入产品调用链、未提交/推送。GF-000493-B 未开始。


### 2026-10-20：用户确认 GF-000493-A-Foundation 验收通过

- 用户确认接受 Foundation 隔离验收结果：22 项定向测试通过、Ruff 通过、vendored Chan 52 个文件与固定快照哈希一致、`git diff --check` 通过。
- GF-000493-A-Foundation 状态：**验收通过**。本次确认不代表产品 API、策略/回测/监控集成、分钟或真实行情、前端图表已迁移或验收。
- 未部署、未重启、未提交或推送。
- 按迁移门禁暂停；GF-000493-B 尚未启动。下一步需先提交 B 阶段方案并获得用户明确确认。


### 2026-10-20：GF-000493-B-Execution 用户确认并开始实施

- 用户确认实施 B 方案：结构图服务/API、股票分析扩展页面与入口、Chan 信号接入策略/回测、监控保护及对应测试。
- 固定源仍为仓库外快照 `yydcnjjw/tick-stock-panel@c97af561de0be4c412c29c641c3f6ade824bbff4`。目标调用链不同，尤其源图表服务依赖的 published/index/minute generation 仓库 API 在目标不存在；实施将按目标已存在的 K 线仓库和前端扩展契约适配，不覆盖目标核心文件。
- 分钟数据采集、新 Provider、Chan 中枢研究策略和无关变更不在本次范围。
- 当前状态：**B 实施中**；先完成目标侧调用链对照与路由/服务适配，再接入策略/回测/监控与前端。


### 2026-10-20：GF-000493-B-Execution 初批接线与验证断点（未完成）

- status：**迁移中**，不是待人工确认，不开始下一候选。
- source_sha：`c97af561de0be4c412c29c641c3f6ade824bbff4`；source_snapshot_path：`/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`。
- last_completed_step：迁入现行图表结构计算；新增 `/api/kline/czsc`、`/czsc-daily` 与本地行情适配服务；图表结构序列/扩展入口/日线页面；Chan 按需流水线、信号依赖、策略历史预热、旧 CZSC 拒绝、回测使用及次日开盘限制、监控保存/执行前置限制。普通选股结果数据类补齐固定源的可选 `czsc_coverage` 字段并添加运行回归。
- compatibility_changes：图表服务使用目标 `get_daily_asset` / `get_matrix_data_generation` / `get_minute_range`，分钟无世代 token 暂不缓存；不增加采集、Provider 或同步按钮。前端当前仅日线初版，参考初始页面的部分结构，**尚不满足固定快照完整多周期页面契约**，必须继续补齐，不能视为获批裁剪。Foundation 的既有未提交文件保留。
- validation_commands/result：四组 Chan 后端测试（`test_chan_chart.py`、`test_chan_runtime.py`、`test_chan_signals_foundation.py`、`test_chan_retirement.py`）在新增普通运行回归前 **37 passed in 16.83s**；补字段与普通运行回归后单独执行 `test_chan_retirement.py` → **10 passed in 1.62s**，未把两次结果相加冒充整组重跑。`pnpm exec tsc --noEmit` 通过；`pnpm exec vitest run src/components/chartStructures.test.ts` → **2 passed**；`pnpm build` 通过（含既有大 chunk 提示）。新增非 vendor 模块及当时测试 Ruff 通过；已有修改核心文件的整文件 Ruff 报告包含大量既有问题，未宣称全仓 Ruff 通过。`git diff --check` 通过。
- verification_limits：上述窄测试不足以证明完整选股/回测/监控链路正确；尚缺 Chan 正常策略回放、成交前确认与覆盖率、API 503/400、分钟/指数时点、页面与信号选择器上下文、旧监控规则禁用/状态清理等源契约测试。图表服务的发布世代/指数语义仍需核对。前端类型及构建通过不等于功能完整。
- next_step：沿固定源现行实现补齐以上缺口，不使用历史日线页面替代现行多周期/事件展示；在完成前保持迁移中，不提交/推送，不启动下一功能。
- 系统状态：未启动/重启后端，未写业务数据，未提交/推送；已运行前端构建并更新 `frontend/dist`，若现有实例直接托管该目录，静态资源可能即时受影响，不应称“运行产物完全未变”。


### 2026-10-20：GF-000493-B 图表只读快照补齐

- status：**迁移中**；GF-000493-B 未整体完成，未进入人工确认。
- 固定来源：仓库外快照 `yydcnjjw/tick-stock-panel@c97af561de0be4c412c29c641c3f6ade824bbff4`，路径 `/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`。
- last_completed_step：图表服务改用有界 published 日线/分钟读取；分钟与指数目录按分区文件指纹校验；股票日线 generation 以只读模式读取。generation 读取新增可选 `readonly`，并向 `get_enriched_generation` 传递 `initialize=False/recover=False`，保留其他既有调用的默认初始化/恢复行为。回归覆盖：读 published 日线不触碰实时缓存、分钟 bounded read/fingerprint 对变化敏感且损坏显式抛错、只读 generation 不初始化缺失标记也不恢复孤儿 publishing 标记。此范围不新增采集/provider/数据写入。
- validation_commands：`uv run --frozen pytest -q tests/test_chan_chart.py tests/test_chan_retirement.py tests/test_enriched_generation.py`（backend 工作目录）。
- validation_result：**39 passed in 4.14s**；`git diff --check` 通过。更早单独运行兼容性测试文件结果为 14 passed；本次 39 项已包含该文件当前用例。
- compatibility_changes：目标历史实现的 generation 默认会初始化及恢复；图表读使用显式只读模式。指数/分钟无统一发布令牌，以现有 parquet 文件相对路径、mtime_ns、size 的 SHA-256 指纹校验；损坏扫描错误不转空表。原有其他 `get_matrix_data_generation` 调用保持默认行为。
- verification_limits：仅完成图表仓库快照、Chan 图表和退役相关自动测试；没有完整重跑所有后端/前端测试。固定快照中的回测取消/进度、覆盖率路径、完整前端多周期页面/信号选择上下文及端到端产品调用链仍需继续核对，B 不可标待人工确认。
- next_step：继续对照固定源的 B-Execution 剩余调用链与配套测试，优先补齐未完成且源证据明确的回测及前端上下文缺口；保持不部署、不重启、不提交/推送。
- 系统状态：未启动/重启服务，未做数据修复/写入，未提交/推送。


### 2026-10-20：GF-000493-B Chan 回测进度、取消与覆盖率切片

- status：**迁移中**；B 阶段整体未完成，未进入人工确认。
- source：`yydcnjjw/tick-stock-panel@c97af561de0be4c412c29c641c3f6ade824bbff4`，固定快照路径 `/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`；对照源 `app/backtest/strategy.py`、`app/backtest/engine.py`、`backend/tests/backtest/test_czsc_preparation.py`。
- last_completed_step：目标 `BacktestEngine.load_panel_for_backtest` 新增可选 progress/cancel/max-workers 参数并转交 `compute_signals`；`StrategyBacktestService.run` 转发回测进度和取消事件，Chan 结构回放使用 `czsc_max_workers=1`（遵守本机低资源限制）；捕获 `ChanReplayCancelledError` 返回 `error=cancelled`，不生成部分结果；成功生成日线面板后计算正式日期区间的 `czsc_coverage`，错误结果携带已生成的覆盖率诊断。按方法签名筛选可选参数，兼容旧接口轻量测试替身。
- validation_commands/results：`uv run --frozen pytest -q tests/test_chan_backtest.py tests/test_chan_retirement.py tests/backtest/test_strategy_backtest_correctness.py tests/backtest/test_minute_backtest.py` → **35 passed in 2.11s**；`uv run --frozen pytest -q tests/test_chan_chart.py tests/test_enriched_generation.py tests/test_chan_backtest.py` → **27 passed in 3.96s**；`git diff --check`、Python `compileall` 通过。
- verification_limits：尚未对成功回测 API/SSE 序列化及前端覆盖率显示做端到端验证；多周期确认事件前端上下文/可用性和策略/筛选器/监控全链路测试仍待补齐。本轮不代表 B 阶段整体完成。
- next_step：继续按固定快照补齐图表前端完整上下文/状态展示及产品链路测试，之后再做整体自动验收。
- 系统状态：未启动/重启服务，未部署，未写业务数据，未提交/推送。


### 2026-10-20：GF-000493-B 回测覆盖率 API/SSE 与前端呈现

- status：**迁移中**；B 阶段整体未完成，未进入人工确认。
- source：`yydcnjjw/tick-stock-panel@c97af561de0be4c412c29c641c3f6ade824bbff4`，固定快照路径 `/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`。对照 `backend/app/backtest/strategy.py` 成功结果赋值与 `select_stats` 诊断白名单、`backend/app/api/backtest.py` 失败 SSE、`frontend/src/lib/backtestTask.ts` 的 `errorStats`、`frontend/src/pages/backtest/StrategyBacktest.tsx` 的读取顺序，以及 `backend/tests/test_czsc_signals.py` 的 `test_sse_error_preserves_czsc_coverage`。
- last_completed_step：日线成功回测在统计写入后保存 `czsc_coverage`；`required_stats` 过滤时保留该字段，不保留未迁移的 `research_scope`、`center_rules`、`open_position_details`。失败 SSE 仅附加已有覆盖率，不附带其他统计。取消 SSE 仍只有「回测已取消」。前端预检和 SSE 错误保存 `errorStats`；有错误时优先展示错误覆盖率，成功时展示结果覆盖率。分钟回测路径与固定源一样不单独写入覆盖率。`czsc_max_workers` 仍为 1。
- validation_commands：`uv run --frozen pytest -q tests/test_chan_backtest.py tests/test_chan_retirement.py tests/backtest/test_strategy_backtest_correctness.py tests/backtest/test_minute_backtest.py`；`python -m compileall`（上述改动文件）；`git diff --check`；`pnpm exec vitest run src/pages/backtest/components/ChanCoverageSummary.test.tsx --maxWorkers=1 --minWorkers=1`。
- validation_result：后端组合 **39 passed in 2.05s**；compileall 通过；`git diff --check` 通过；前端覆盖率组件 **1 passed**。未跑全量后端/前端测试，未做浏览器端到端，未启动或重启服务。
- verification_limits：策略运行、选股和监控的真实调用链尚未按固定源整链回归。图表页既有多周期上下文未在本切片改动。B 阶段不能标为待人工确认。
- next_step：继续对照固定快照核对策略、选股、监控调用链，再做 B 阶段整体自动验收。
- 系统状态：未部署、未重启、未写业务数据、未提交、未推送。


### 2026-10-21：GF-000493-B 策略、选股、监控调用链

- status：**迁移中**；B 阶段整体未完成，未进入人工确认。
- source：`yydcnjjw/tick-stock-panel@c97af561de0be4c412c29c641c3f6ade824bbff4`。对照 `backend/app/strategy/engine.py` 的当日覆盖率与 `missing_data` 补齐、空结果仍返回 `czsc_coverage`；`backend/app/api/strategy.py` 的 `_invalidate_czsc_config`；`backend/app/services/strategy_cache.py` 的 `invalidate_strategy`；`backend/app/api/screener.py` 策略清单；`frontend/src/pages/Screener.tsx` 的不可执行提示与单策略覆盖率。源测试为 `test_empty_selection_keeps_coverage_for_only_the_requested_pool` 与 `test_czsc_config_changes_invalidate_only_affected_strategy`。
- last_completed_step：策略运行只对 `as_of` 行统计覆盖率，左连接未命中的空原因记为 `missing_data`；分钟空历史、历史过滤为空和候选为空都带上已算出的覆盖率。保存、局部更新和重置配置时，仅当变更涉及 Chan/旧 CZSC 信号才删除该策略的结果缓存和监控状态，不清除其他策略。叠加条件变化仍走原有的全量缓存清理。选股清单按已保存 override 重算 `execution_available`。选股页对不可执行策略提示原因且不发起新的运行，日线批量池排除这些策略，并展示单策略 `czsc_coverage`。
- compatibility_changes：目标策略缓存有进程内指纹镜像。单策略失效先拷贝缓存再删除键，写盘后丢弃旧镜像，避免改到共享只读对象。
- validation_commands：`uv run --frozen pytest -q tests/test_chan_retirement.py tests/test_chan_backtest.py`；`python -m compileall`（本切片 Python 文件）；`git diff --check`（本切片文件）。
- validation_result：定向测试 **27 passed in 1.56s**；compileall 通过；`git diff --check` 通过。未跑全量后端/前端测试，未做浏览器端到端，未启动或重启服务。
- verification_limits：选股页面新增展示没有单独组件测试。GF-000493-B 仍不能标为待人工确认。
- next_step：进行 B 阶段整体自动验收。
- 系统状态：未部署、未重启、未写业务数据、未提交、未推送。


### 2026-10-21：GF-000493-B 整体自动验收通过，待人工确认

- status：**待人工确认**。GF-000493-A-Foundation 维持已人工确认。B 阶段不记为人工通过，不开始下一功能。
- source：`yydcnjjw/tick-stock-panel@c97af561de0be4c412c29c641c3f6ade824bbff4`，快照 `/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`。
- last_completed_step：按已迁移范围完成整体自动验收。验收前可用内存约 2.4 GiB，可用 swap 约 1.8 GiB；单进程执行。`uv run --frozen` 每次启动时出现一次 “Uninstalled 1 package / Installed 1 package”，未改变已跟踪的锁文件或项目源码。
- validation_commands：在 `backend` 执行 `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 POLARS_MAX_THREADS=1 uv run --frozen pytest -q -p no:xdist tests/test_chan_runtime.py tests/test_chan_signals_foundation.py tests/test_chan_chart.py tests/test_chan_retirement.py tests/test_chan_backtest.py tests/test_enriched_generation.py tests/backtest/test_strategy_backtest_correctness.py tests/backtest/test_minute_backtest.py`。在 `frontend` 执行 `NODE_OPTIONS=--max-old-space-size=1536 pnpm exec vitest run src/components/chartStructures.test.ts src/pages/backtest/components/ChanCoverageSummary.test.tsx src/custom/czsc/CzscPage.test.tsx --maxWorkers=1 --minWorkers=1`，以及 `NODE_OPTIONS=--max-old-space-size=1536 pnpm exec tsc -b --pretty false`。随后对本次 B 阶段相关 Python 文件执行 `python -m compileall`，并在仓库根执行 `git diff --check`。
- validation_result：后端 **93 passed in 17.08s**，EXIT 0；前端 **3 files / 11 passed**，EXIT 0；`tsc -b` EXIT 0；compileall EXIT 0；`git diff --check` EXIT 0。
- verification_limits：不是全量后端或前端测试。本次没有重跑 Vite 生产构建，也没有启动、重启或部署服务，3018/3011 当前无监听。没有浏览器端到端，没有真实行情或收益验证。选股页新增提示没有独立组件测试。分钟采集、新 Provider 和 Chan 中枢研究策略仍排除。工作区还包含 B 阶段此前未提交改动，本次验收没有新增业务代码。
- next_step：等待用户人工确认。未确认通过前，`next_task_allowed` 为否。
- 系统状态：未部署、未重启、未写业务数据、未提交、未推送。


### 2026-10-21：为 GF-000493-B 页面查看启动服务

- 用户授权启动服务以便查看页面。本次不是人工验收通过。
- 旧 `frontend/dist` 生成于 2026-10-20 19:36，早于选股页和回测页后续改动。已单进程执行 `NODE_OPTIONS=--max-old-space-size=1536 pnpm build`，`tsc -b && vite build` 成功，EXIT 0；仅有既有大 chunk 警告。
- 后端已监听 `0.0.0.0:3018`，未使用 `--reload`，未另开 3011。本机 `/`、`/screener`、`/backtest`、`/czsc` 返回 200，`/health` 为 `{"status":"ok","version":"0.3.9","mode":"none"}`。系统防火墙已放行 3018/tcp。
- 启动后应用自身的开机完整性检查发现 2026-10-20 数据缺失，并自动开始修复任务 `ef1453d0de`。这不是本次迁移命令主动触发的数据修复。页面可以打开，但后台修复会占用网络和内存。
- 状态仍为 **待人工确认**。未提交、未推送，未开始下一功能。


### 2026-10-21：GF-000493-B 结构图入口维持固定源方式

- 用户确认结构图不加入左侧菜单，从股票预览框进入。
- 固定源 `c97af561` 的 `frontend/src/custom/czsc/extension.tsx` 只注册 `/czsc` 路由和 `stock-preview.footer` 链接，侧栏 `Layout.tsx` 没有结构图项。目标保持同一方式。
- 固定源指数页另有「查看 chan.py 结构图」链接。用户本次明确不补该入口，也不新增侧栏项。
- 状态仍为 **待人工确认**。未改业务代码，未提交、未推送。


### 2026-10-21：用户核对选股页 Chan 信号位置

- 用户确认选股策略名单中没有独立的 Chan/旧 CZSC 策略卡片。当前已安装策略均未引用 `signal_chan_*` 或 `signal_czsc_*`，因此不会出现“当前显示内容仅供历史查看”。
- 用户确认在策略设置的入场/出场触发器中看到 chan.py 笔级确认信号。这与固定源一致：信号可选，不另发一张预置选股策略。
- GF-000493-B 仍为 **待人工确认**。本次只记录页面观察，未改业务代码，未提交、未推送。


### 2026-10-21：用户确认 GF-000493-B 人工验收通过

- 用户回复“页面符合预期，确认人工通过”。
- GF-000493-B 状态由 `待人工确认` 改为 `已人工确认`。GF-000493-A-Foundation 维持已人工确认。
- 页面核对包括：结构图不加入侧栏，从股票预览进入；选股页没有独立 Chan/旧 CZSC 策略卡片，策略触发器中可见 chan.py 笔级确认信号。
- 确认不覆盖分钟采集、新 Provider、Chan 中枢研究策略、真实行情收益、全量测试，也不把自动测试结果单独当成人工通过。
- `next_task_allowed` 改为是，但本次不开始下一功能。未提交、未推送。3018 服务仍在运行，启动时的 2026-10-20 数据修复若未结束会继续由应用自己处理。


### 2026-10-21：登记 YYD-FOLLOWUP 并完成初评

- 用户要求为 `yydcnjjw/tick-stock-panel` 单独建立迁移任务，并先评估 GF-000493 之外还有没有值得迁移的功能。
- 固定快照仍是 `/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`。候选记录有 20 个独有提交、13 个启发式功能组。这些提交对象不在目标 Git 库中，已改用提交说明和目标文件/历史对照。
- 作者 `yydcnjjw` 的 Chan/CZSC 主线已由 GF-000493-A/B 迁移并人工确认，提交 `46f4732c` 已推送。
- 仍值得单独看待的是 `c97af561` 的 `chan_center_range` 中枢研究策略及 `backend/tests/test_chan_center_strategy.py`、`backend/tests/backtest/test_chan_center.py`。目标没有该策略。源策略默认阻断正式回测，只允许显式探索性回测，不进选股和监控。本次不把它记为可直接实施。
- `6244566a`、`9df7358e` 的两份 `.agents/skills` 是代理选股流程，依赖上述策略和已迁移的 Chan API。目标没有这两个技能。产品价值低于策略本身，暂不单独实施。
- `3909841e`（GF-000494）增加 `historical_trading_day_status`。目标已有 `trading_calendar()` 过滤近期完整性扫描，但没有这个历史三态函数。这是修复类收紧，按当前“新增功能优先、修复延后”的规则不先迁移。
- `b4f9c2ab` 是 Docker 依赖镜像重试和 host 网络。与本机使用官方源的要求冲突，不迁移。
- 其余 12 个提交作者不是 `yydcnjjw`，内容为 Docker/Codex、README、StockSDK、自选截图导入和自定义分钟补拉。目标历史已有对应提交或等价功能，无需从该 Fork 再迁。
- 状态：**评估完成，待确认是否审计 Chan 中枢研究策略**。未修改业务代码，未提交、未推送本记录。


### 2026-10-21：YYD-CENTER 源码审计完成，待迁移前确认

- 用户确认只审计 Chan 中枢研究策略。审计文档：`docs/integration/gf-yyd-center-source-audit.md`。
- 固定快照：`/tmp/tick-stock-panel-fork-migration/yydcnjjw__tick-stock-panel/c97af561de0be4c412c29c641c3f6ade824bbff4`。`git rev-parse HEAD` 为 `c97af561de0be4c412c29c641c3f6ade824bbff4`。功能提交就是该 SHA，28 个文件，+2587/−24。
- 策略 ID `chan_center_range`。正式收益回测在读行情前阻断；只有显式 `exploratory=true` 可以跑现有行情样本。不支持普通选股、监控和参数优化。八套规则各自独立。
- 目标没有该策略、`CenterBook` 和 `center_features`。`fractal_events` 已随 GF-000493 存在，不能重写。策略 API `run-all` 与监控已能识别 `backtest_only`，但单次执行、选股全量、回测门控、统计字段和回测页仍缺。
- 目标不再扫描 `backend/app/strategy/builtin/`。若以后实施，算法放回测模块，策略入口按现有种子补入 custom，且不覆盖同名文件。源仓库的内置策略计数断言不迁移。
- `YYD-FOLLOWUP` 的其余结论维持：两份技能暂不单迁；GF-000494 靠后；Docker 镜像重试不迁；另外 12 个非 `yydcnjjw` 提交不从该 Fork 再迁。
- 状态：`待迁移前确认`。未修改业务代码，未运行测试，未部署、未重启，未提交、未推送。


### 2026-10-21：YYD-CENTER 实施完成，待人工确认

- 用户确认按审计范围实施。固定源仍是 `c97af561de0be4c412c29c641c3f6ade824bbff4`。
- 新增回测模块 `chan_center.py`、`chan_center_context.py`、`chan_center_lesson24.py`，策略种子 `backend/app/strategy/seeds/chan_center_range.py`，产品说明 `docs/chan-center-backtest.md`。
- 目标不扫描 `builtin/`。种子使用独立标记 `.seeded_yyd_center`，所有安装补齐一次，不覆盖同名文件，删除后不复活。旧种子集合排除该文件，避免重复计数。
- 接入矩阵 `center_features`、撮合器 `CenterBook`、正式回测阻断、探索性 `research_scope`、`center_rules` 和 `open_position_details`。统计裁剪保留这三项。
- 选股批量任务排除 `backtest_only`。单次选股和监控沿用实盘阻断。参数优化即使勾选探索性也拒绝。叠加信号不能合并中枢持仓状态。
- 导入白名单只增加 `app.backtest.chan_center`。`ChanReplay.fractal_events` 已存在，没有重写。
- 监控函数在目标侧名为 `chan_strategy_monitor_warning`，测试导入按此适配，断言仍要求「仅支持回测」。
- 回测页增加规则方案说明、探索性勾选、阻断原因、结果口径和 CSV 字段。没有新侧栏。
- 验证：中枢与种子测试 122 passed in 7.58s；矩阵与叠加回归 49 passed, 1 deselected in 3.48s。`compileall`、`git diff --check` 和前端 `tsc -b` 通过。没有全量测试，没有真实历史回测，没有部署或重启。3018 仍是旧进程，页面要等下次授权重启后才能看到新策略。
- 状态：`待人工确认`。未提交、未推送。


### 2026-10-21：YYD-CENTER 重启 3018 并核对回测页，仍待人工确认

- 用户授权重启后再看页面。先停掉原 3018（父进程 656964、Python 656970），第一次拉起被命令会话一起结束；随后用独立会话按原命令重新启动：`uv run --no-sync python -m uvicorn app.main:app --env-file ../.env --host 0.0.0.0 --port 3018`，工作目录 `backend`，未加 `--reload`。当前父进程 702906、Python 702911。`/health` 返回 200。
- 启动按种子标记补入一次 `data/strategies/custom/chan_center_range.py` 和 `.seeded_yyd_center`，没有覆盖其他策略文件。策略接口共 38 个，包含 `chan_center_range`。
- 08:38 的 `frontend/dist` 不含新回测提示。为了让 3018 托管页能看到已确认的界面，按单进程、`NODE_OPTIONS=--max-old-space-size=1536` 执行 `pnpm build`，并用官方 registry 环境变量覆盖本机 npm 镜像。构建成功，3018 无需再重启即返回新的 `index-CIpTP1Si.js`。`frontend/dist` 仍被 gitignore，不进入提交。
- 浏览器核对 `http://127.0.0.1:3018/backtest`：策略列表有「中枢震荡: 分型 + 123买卖点」。未勾选时显示正式阻断原因，运行按钮禁用；勾选后正式阻断消失并显示探索性警告，按钮变为可用。规则从「完整新版」改到「第24课止损实验版」后，方案说明跟随变化。没有点击运行，没有真实回测。
- 选股页策略池能看到该策略，但没有「当前显示内容仅供历史查看」。列表接口 `execution_available=true`，因为这里只判断旧 CZSC 退役，没有把 `backtest_only` 算进不可执行原因。单次执行入口仍会在计算前抛出「仅支持回测」。本次没有在选股页发起运行，也没有打开监控中心。
- 状态保持 `待人工确认`。未提交、未推送，未开始下一功能。


### 2026-10-21：YYD-CENTER 实盘门禁，避免策略页拉起 1001 天全市场重算

- 用户确认按方案实施。原因是策略页启用 `chan_center_range` 时，`required_history_bars` 先取 1000 根预热，11:20:52 的日志出现 `_load_enriched_history cache miss, computing indicators (2026-10-21, 1001)`，约 20 分钟后 0 个策略完成、38 个挂起，随后机器重启。
- `screen_unavailable_reason`：`backtest_only` 的策略页、策略详情和选股清单 `execution_available=false`，原因使用 `live_block_reason`。
- `required_history_bars` 与实时矩阵准备跳过 `backtest_only`，避免它把其他策略的历史窗口抬高。
- `/api/strategies/run` 与选股 `run_preset` 在 `build_strategy_context` 之前返回 400。选股 `run_all` 若请求只含该策略，同样在读行情前返回 400；与其他策略混合时先剔除它再计算。
- 回测阻断、探索性回测、1000 根预热和中枢算法未改。监控原有拒绝未改。
- 验证：可用内存约 2.3 GiB，swap 2.0 GiB 空闲。`pytest -q tests/test_chan_center_strategy.py::test_backtest_only_does_not_widen_live_history_or_accept_screen_runs tests/test_chan_center_strategy.py::test_builtin_detail_api_and_dependency_plan tests/test_chan_center_strategy.py::test_cannot_run_as_live_screen_or_monitor tests/test_chan_retirement.py tests/test_strategy_required_history.py -p no:xdist` → **31 passed in 4.06s**。`compileall` 与 `git diff --check` 通过。未跑全市场，未重启 3018，未提交、未推送。
- 状态仍为 **待人工确认**。当前已运行的 3018 若还是旧进程，要等下次授权重启后才吃到这道门禁。

### 2026-10-21：按原命令启动 3018，门禁已生效

- 用户确认启动。命令为 `uv run --no-sync python -m uvicorn app.main:app --env-file ../.env --host 0.0.0.0 --port 3018`，工作目录 `backend`，未加 `--reload`。进程父进程为 1，uv 54231，Python 54236。
- `/health` 返回 200，`{"status":"ok","version":"0.3.9","mode":"none"}`。
- 登录后 `/api/strategies/chan_center_range` 与选股清单均为 `execution_available=false`，原因是「仅支持回测」。`/api/screener/run_preset` 返回 400，同一秒内完成，日志没有新的 1001 天指标重算。
- 状态仍为 **待人工确认**。未提交、未推送。

### 2026-10-21：用户确认 YYD-CENTER 人工通过

- 用户要求“把 YYD-CENTER 记为人工通过”。
- `YYD-CENTER` 状态由 `待人工确认` 改为 `已人工确认`。确认人是用户，时间 `2026-10-21T14:39:17+08:00`，结论为通过。
- 确认范围是中枢研究策略 `chan_center_range` 及其实盘门禁。正式回测默认阻断，回测页勾选探索性回测后可以运行；策略页和监控不可执行。
- 不包含真实全市场收益回测、参数优化、普通选股、监控、同仓库两份代理技能、`GF-000494` 和 Docker 改动。
- `next_task_allowed` 为是，但本次不开始下一功能。未提交、未推送。目标 HEAD 为 `46f4732c9c70505e785a704eb10918138deae163`，工作区仍有未提交改动。

### 2026-10-21：按用户要求提交 YYD-CENTER

- 用户要求在进行下一个功能前先提交当前代码。
- 提交内容为 `YYD-CENTER` 的回测实现、策略种子、实盘门禁、前端回测提示、定向测试、审计说明和本台账。
- 不包含运行时 `data/strategies` 落盘文件。未推送，未开始下一功能。
- 该提交的 SHA 就是包含本条目的提交。
