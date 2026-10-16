# Fork 功能迁移任务台账

- **迁移标识**：`fork-feature-migration-r1`
- **目标仓库**：`luhuiyuan/tick-stock-panel`
- **目标分支**：`fork-integration`
- **目标基线**：`7708bec3`
- **源仓库基线**：`shy3130/tick-stock-panel:main@df2da2dc8a87e6f0d9eb1611f3ba660f3632de47`
- **总体状态**：规划中
- **当前阶段**：Phase 0：功能去重与基线审计
- **当前任务**：`P0-001`
- **最后完成步骤**：已建立迁移计划、源码迁移约束和任务台账；尚未下载迁移用本地源码
- **下一步**：建立全局功能去重表，并下载第一批相关 Fork 的固定源码快照
- **更新时间**：2026-10-16T00:00:00+08:00

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

未完成本地源码快照、原始 diff 或配套测试审计的任务，不得标记为 `待迁移前确认`、`迁移中`、`待验证`、`待人工确认`、`已人工确认`、`已验证` 或 `已完成`。

完成源码审计后必须先进入 `待迁移前确认`，向用户说明迁移意义、预期收益、范围和风险；只有用户明确确认后才能进入 `迁移中`。自动验证完成后，任务必须进入 `待人工确认`；人工确认通过后才能进入 `已人工确认`，并且前一功能未进入 `已人工确认` 前，不得开始下一功能迁移。

## 3. 任务清单

### 3.1 Phase 0：功能去重与基线审计

| task_id | 任务 | 范围/来源 | status | priority | last_completed_step | next_step | evidence |
|---|---|---|---|---|---|---|---|
| `P0-001` | 建立全局功能去重表和 `global_feature_id` | 15 个 Fork、`feature_updates.csv`、`feature_commits.csv` | `待处理` | P0 | 尚未开始 | 合并跨 Fork 重复功能并登记全局 ID | `analysis/fork-analysis/feature-updates/` |
| `P0-002` | 排除非业务迁移项 | 文档、截图、生成 CSV、任务归档、纯工程维护 | `待处理` | P0 | 尚未开始 | 标记 `无需迁移` 或 `待复核` | `feature_updates.csv`、原始独有提交 CSV |
| `P0-003` | 复核高影响数据、策略和回测候选 | Phase 1–3 候选功能 | `待处理` | P1 | 尚未开始 | 按原始 diff 建立依赖和风险记录 | `feature_commits.csv` |
| `P0-004` | 固定第一批迁移用源码快照 | 相关 Fork 固定 SHA | `待处理` | P0 | 尚未开始 | 下载到仓库外临时目录并记录快照 manifest | `${TMPDIR:-/tmp}/tick-stock-panel-fork-migration/` |

### 3.2 Phase 1：数据底座与数据源

| task_id | 任务 | 范围/来源 | status | priority | last_completed_step | next_step | source_snapshot_path | validation_result |
|---|---|---|---|---|---|---|---|---|
| `P1-001` | 数据源插件、能力矩阵和失败降级 | AkShare、TDX/`eltdx`、Stock SDK、Fuyao 等候选 | `待源码审计` | P1 | 尚未下载相关源码 | 完成 Phase 0 去重后固定源码快照 | 待建立 | 未执行 |
| `P1-002` | K 线、分钟数据、交易日和历史回补 | 多个 Fork 的数据契约候选 | `待源码审计` | P1 | 尚未下载相关源码 | 核对字段、单位、交易日和幂等语义 | 待建立 | 未执行 |
| `P1-003` | ETF、资金流、复权和扩展数据 | 多个 Fork 的数据能力候选 | `待源码审计` | P1 | 尚未下载相关源码 | 核对资产类型和复权口径 | 待建立 | 未执行 |

### 3.3 Phase 2-A：现成策略集成

以下任务来自 `AINiuNiuCollection/tick-stock-panel` 的独有提交。策略源码必须先下载到本机并完成原始 diff、配套测试和主线契约审计，当前均未开始迁移。

| task_id | global_feature_id | 策略候选 | source_repository | source_branch | source_sha | source_commit_ids | source_files | status | priority | source_snapshot_path | last_completed_step | next_step | validation_result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `P2A-001` | `FU-000010`/`FU-000011` | 二板龙头·绝望弱转强（炸板回封型） | `AINiuNiuCollection/tick-stock-panel` | `main` | `bc96ad5cc4370af8851fcfadd697c53f25263cfa` | `f0512f96e88c170f58768f5bc86e1313269e2555`; `602d14642b27f37d17789beee0768b8c408c3692` | `data/strategies/custom/custom_despair_reversal_leader.py` | `待源码审计` | P1 | 待建立 | 尚未下载源码 | 下载并固定 Fork 源码，核对策略源码和测试 | 未执行 |
| `P2A-002` | `FU-000012`/`FU-000014` | 连板股改进版 | `AINiuNiuCollection/tick-stock-panel` | `main` | `bc96ad5cc4370af8851fcfadd697c53f25263cfa` | `7a061b19c56762040add8089c8dcd717eb879a6c`; `f7c8587d5608af615ac12b55424a20d70499699` | `data/strategies/custom/custom_consecutive_limit_ups_v2.py` | `待源码审计` | P1 | 待建立 | 尚未下载源码 | 下载并固定 Fork 源码，核对连板、MA10 退出和参数契约 | 未执行 |
| `P2A-003` | `FU-000042` | 2 连板特殊配置 | `AINiuNiuCollection/tick-stock-panel` | `main` | `bc96ad5cc4370af8851fcfadd697c53f25263cfa` | `2c370bb7214006c8829193783095a90a498b914b` | `data/strategies/custom/custom_two_board_special.py` | `待源码审计` | P1 | 待建立 | 尚未下载源码 | 下载并固定 Fork 源码，核对与现有连板策略的重复和 ID 冲突 | 未执行 |
| `P2A-004` | `FU-000052` | MA75 + KDJ 超卖 | `AINiuNiuCollection/tick-stock-panel` | `main` | `bc96ad5cc4370af8851fcfadd697c53f25263cfa` | `ed79ece02ad32bc7b538481b5b481c50939b2a82` | `data/strategies/custom/custom_ma75_kdj_oversold.py` | `待源码审计` | P1 | 待建立 | 尚未下载源码 | 下载并固定 Fork 源码，核对 KDJ/MA75 依赖和预热窗口 | 未执行 |

### 3.4 Phase 2-B：策略框架增强

| task_id | 任务 | 范围/来源 | source_commit_ids | status | priority | last_completed_step | next_step | validation_result |
|---|---|---|---|---|---|---|---|---|
| `P2B-001` | cooldown 冷却参数和连续亏损暂停 | `AINiuNiuCollection/tick-stock-panel` | `e96cb375efe00cc0a01f3643c470e3d776f52296`; `cb67dd928e7ea4411d251e3b31f8b167503a58a9` | `待源码审计` | P2 | 尚未下载源码 | 在现成策略审计后核对框架边界 | 未执行 |
| `P2B-002` | 策略生命周期、发布、归档和绩效淘汰 | 多个 Fork 的策略管理候选 | 待 Phase 0 去重 | `待处理` | P2 | 尚未开始 | 建立功能组和原始提交关联 | 未执行 |
| `P2B-003` | overlay、自定义信号和组合策略增强 | 多个 Fork 的策略候选 | 待 Phase 0 去重 | `待处理` | P2 | 尚未开始 | 合并重复功能并固定源码快照 | 未执行 |

### 3.5 单功能迁移前用户确认记录

每个任务开始代码迁移前，必须先向用户提交迁移说明并等待明确回复。用户未确认、暂不迁移或要求调整范围时，不得修改目标代码、配置或数据。

| task_id | status | migration_significance | expected_outcome | scope_and_risks | source_snapshot_path | user_confirmation | confirmed_at | target_baseline |
|---|---|---|---|---|---|---|---|---|
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
| 待填写 | `待人工确认` | 待建立 | 待填写实际结果 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 否 |

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
| 待源码审计任务 | 8 |
| 待迁移前确认任务 | 0 |
| 待验证任务 | 0 |
| 待人工确认任务 | 0 |
| 已人工确认任务 | 0 |
| 已验证任务 | 0 |
| 阻塞任务 | 0 |
| 已下载源码快照 | 0 |
| 已迁移功能 | 0 |
| 已执行验证 | 0 |

以上统计由本台账任务表维护；没有实际记录的完成项不得计入统计。

## 8. 更新历史

### 2026-10-16：建立迁移任务台账

- 新增任务台账，支持按任务记录状态和断点；
- 登记 Phase 0、Phase 1、Phase 2-A、Phase 2-B 及 Phase 3–6 占位任务；
- 登记 4 个现成策略候选，当前均为 `待源码审计`；
- 当前未下载源仓库源码，未迁移业务代码，未执行迁移验证；
- 后续源码只允许下载到仓库外临时目录，不得写入本项目目录；
- 当前没有进入人工确认阶段的任务。
