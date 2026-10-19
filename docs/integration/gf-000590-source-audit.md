# GF-000590 源码审计报告

- **候选**：情绪周期策略择时 `REGIME_PHASES` + `rps_20_delta_5`
- **审计日期**：2026-10-19
- **目标分支**：`fork-integration`
- **目标工作区 HEAD（审计时）**：`21805ed1f099618cbb376250addc130152170bbc`
- **源仓库**：`YuYang019/tick-stock-panel`
- **固定源分支/SHA**：`main@f0ee647c05a0a8db372e8c66a0da371accfd65b0`
- **源快照**：`/tmp/tick-stock-panel-fork-migration/YuYang019__tick-stock-panel/f0ee647c05a0a8db372e8c66a0da371accfd65b0`
- **新增能力提交**：`16d21f4e0a9306eb2f6208dcfbf8277a909ec161`
- **后续修复提交**：`712f4c1def0380c3cdbde638e8d6e5e3c1086d83`
- **审计范围**：固定源码、原始提交差异、配套测试和目标调用链只读对照
- **明确未执行**：业务代码修改、测试/构建、依赖安装、数据修复、部署、服务重启

## 1. 门禁结论

GF-000590 不应作为一个整体迁移包。拆分为三个边界：

| 子项 | 状态 | 审计结论 |
|---|---|---|
| **GF-000590-A：`REGIME_PHASES` 情绪阶段策略择时契约** | `待迁移前确认` | 目标已有市场情绪阶段生成/持久化，以及 `state/score` 的 T-1 回测过滤；目标策略引擎尚无策略阶段白名单契约，回测阶段映射也未传递 `phase`。属于可接入既有市场阶段体系的新增策略能力，建议只迁移该契约和直接测试。 |
| **GF-000590-B：`rps_20_delta_5` 因子** | `待源码审计` | 因子公式为横截面 `rps_20` 减去 5 个交易日前的 `rps_20`。目标没有相对强度计算模块。源功能提交只是在既有 RS 模块上追加因子；该模块来自更早的 `3296ac8fe80984e0f493906b83ed9ddd76dcf6e6`，涉及约 80 个文件和多类功能，必须另做最小依赖审计，不得直接复制大提交。 |
| **后续缓存/评分修复** | `延后处理` | `712f4c1d…` 包含长回看历史缓存覆盖修复、慢路径预热窗口调整和 RS 评分白名单放行。按“新增策略/数据源/因子优先，Bug 修复延后”的优先级，不纳入 GF-000590-A；未来若推进 B，再核查评分接入是否不可缺少。 |

GF-000590-A 当前只是**待迁移前确认候选**，本报告不构成实施授权。

## 2. GF-000590-A 候选最小范围

若用户另行确认实施，建议范围限定为：

1. 策略声明阶段 ID 白名单，并在加载时拒绝未知阶段；空列表保持原有行为。
2. 复用目标已有情绪阶段历史，以 T-1 阶段进行策略选股开仓过滤。
3. 缺失所需阶段数据时按 fail-closed 处理；保留卖出信号，不因开仓门控阻止离场。
4. 将 `phases` 纳入既有 `states/min_score` 回测过滤契约并按交集组合；策略声明阶段在未显式传过滤时作为默认，显式 `{}` 仍关闭过滤。
5. 增加直接覆盖上述契约的目标侧回归测试。

明确排除：RPS 因子及其底层模块、`712f4c1d…` 缓存/慢路径修复、默认策略改写、数据源切换、无关 UI/指标系统改造。

## 3. 验收验证建议

- 阶段 ID 合法性、未知 ID 加载失败、空阶段列表不改变现有策略行为；
- T-1 对齐，首个交易日边界及缺失阶段 fail-closed；
- `phases` 与 `states/min_score` 的交集语义；
- 阶段不允许开仓时清空入场结果，但卖出信号仍保留；
- 回测未显式提供过滤时采用策略声明，显式 `{}` 关闭；
- 运行新增策略阶段测试及受影响的策略引擎/回测环境过滤定向回归；按低内存约束单并发执行。

上述为拟定验收方案，尚未执行任何测试或构建。

## 4. 目标侧核对证据

- 市场阶段已有定义：`backend/app/services/market_phase.py`，阶段 ID 为 ice/ignite/rally/climax/ebb/repair。
- 回测已有 T-1 环境对齐：`backend/app/backtest/regime_alignment.py`；目前过滤条件为 `states/min_score`，未包含 phases。
- 回测加载阶段历史：`backend/app/backtest/strategy.py::_build_regime_mask`；当前映射只传 `state/score`，未传 `phase`。
- 策略引擎 `StrategyDef` / `StrategyResult` 目前未定义 `regime_phases` 或 `regime_blocked`；未发现现有阶段开仓门控。
- 目标因子目录有统一注册表、DSL 和 Alpha158，但没有 `backend/app/factors/relative_strength.py`，也没有 `rps_20_delta_5`。
- 源提交配套阶段测试：`backend/tests/test_strategy_regime_phases.py`，覆盖 T-1、阶段与 state 交集、缺失阶段、非法阶段、开仓拦截/卖出保留和无声明时不拦截。只阅读，未运行。

## 5. 原始提交边界

- `16d21f4e…`：修改 `regime_alignment.py`、回测服务、RS 因子模块和策略引擎，并更新因子测试、新增阶段策略测试。RS 部分依赖源仓更早提交建立的整套相对强度模块，不能把它误判为独立可复制的小补丁。
- `712f4c1d…`：独立后续修复提交，涉及 screener 长窗口缓存判定、回测/AI 评分白名单和 repository 历史覆盖判定；按当前优先级延后。
- 固定快照确认两个提交均包含于 `main`。本轮仅下载固定源码到仓库外临时目录，没有将源代码复制入目标仓库。

## 6. 审计后状态

- GF-000590-A：`待迁移前确认`；下一步向用户提交收益、范围、风险和验证方案，等待实施确认。
- GF-000590-B：`待源码审计`；不与 A 并行实施。
- 缓存/慢路径修复：`延后处理`。
- 未修改业务代码、未运行测试/构建、未安装依赖、未部署或重启、未提交或推送。

## 7. GF-000590-A 实施记录（2026-10-19）

- 用户已确认按最小方案实施 GF-000590-A；未迁移 GF-000590-B、RPS 模块或后续缓存/评分修复。
- 已实现 `REGIME_PHASES` 阶段 ID 白名单与加载期去重/未知阶段拒绝；策略结果新增 `regime_blocked`。
- 策略筛选在声明阶段白名单时按 T-1 情绪阶段门控入场：仅清空结果行、评分和入场信号，保留离场信号；阶段数据缺失或目录不可定位时 fail-closed。
- 回测沿用既有 `states/min_score` T-1 过滤通道，新增 `phases` 交集语义；`None` 仍表示未显式指定并从策略声明推导默认过滤，显式 `{}` 表示关闭；`phases` 与 `states/min_score` 交集组合，所需 T-1 阶段缺失 fail-closed。
- 自动验证：单进程定向回归 `31 passed, 9 deselected`；受改文件 `py_compile` 通过；`git diff --check` 通过。资源检查满足低内存约束。
- 未部署/重启、未提交或推送；GF-000590-B 仍未启动，当前状态进入待人工确认。

## 2026-10-19 补充：A 方案配套前端与示例溯源

- 复核 fork 固定快照后确认，GF-000590 功能链除后端主提交外，还有用户可见配套内容：
  - 前端提交 `490c03917ac7530786f919379f8ecbc689073a4a`：`ScreenerResult.regime_blocked` 类型，选股空结果时显示“情绪择时暂停开仓”和具体拦截原因。
  - 示例提交 `675ab38` 中的 `custom_volume_shrink_pullback.py` 与 `custom_rps_rising.py` 声明 `REGIME_PHASES`；README 记录示例用法和阶段 ID。
  - fork 后续 bugfix `712f4c1def0380c3cdbde638e8d6e5e3c1086d83` 仍维持延后处理。
- 已补迁前端 `490c039` 的两处最小提示；未改后端语义。
- 示例迁移限制：目标仓库当前没有 RPS/相对强度因子链（GF-000590-B），因此 `custom_rps_rising.py` 不可直接迁移。`custom_volume_shrink_pullback.py` 原实现也依赖 `rps_20`、`excess_20`、`rs_line_slope_20`，直接复制会破坏加载；为提供可用示例，迁移时移除了 RPS/RS 前置和评分依赖，改为只用本地已有量比/KDJ/MA60 特征。该文件不再是 fork 原始实现的完整等价迁移，仅是保留情绪择时契约的可运行示例。
- 验证：`uv run pytest tests/test_strategy_regime_phases.py tests/test_regime_builder.py -q`，26 passed。前端仅做类型和空状态文案映射，未执行前端构建。

## 2026-10-19 人工验收与部署补充

- 用户在 3018 前端确认：日期 `2026-10-12` 运行示例策略时空结果显示“情绪择时暂停开仓”及退潮阶段原因；GF-000590-A 人工验收通过。
- 为使结果在批量/缓存链路可见，补充最小序列化修复：`run_all` payload、盘后缓存写入和 `cached-result` 读取均保留 `regime_blocked`。定向测试 `tests/test_strategy_regime_phases.py` 2 passed；前端生产构建成功并部署到本机 3018。
- 状态更新为 `已人工确认`。GF-000590-B 和后续缓存/评分修复仍未启动，保持原状态；未创建提交或推送。

## 8. GF-000590-B 完整 RPS 迁移审计（2026-10-19）

用户明确要求“不裁剪，审计完整 RPS 迁移”。本节审计范围扩展为源仓库相对强度因子族及其完整接入链；仍明确排除同一基础提交中的通达信指标编辑器、K 线主图开关、无关前后端页面和其他非 RPS 功能。

### 8.1 源码证据与功能组成

- 固定源：`YuYang019/tick-stock-panel@f0ee647c05a0a8db372e8c66a0da371accfd65b0`。
- RPS 基础模块来源：`3296ac8fe80984e0f493906b83ed9ddd76dcf6e6`，其中 `backend/app/factors/relative_strength.py` 为约 739 行的独立模块；`16d21f4e0a9306eb2f6208dcfbf8277a909ec161` 在其上追加 `rps_20_delta_5`。
- 完整 RPS 因子族：
  - `rps_20/rps_60/rps_120/rps_250`：全市场截面分位；
  - `rps_ind_20/rps_ind_60/rps_ind_120/rps_ind_250`：二级行业内截面分位；
  - `industry_rps_20/60/120/250`：行业等权收益在行业之间的分位；
  - `excess_20/60/120/250`：相对 `000985.SH` 的超额收益；
  - `rs_line_new_high_60`：相对基准 RS 线 60 日新高；
  - `rs_line_slope_20`：RS 线 20 日 OLS 斜率；
  - `rps_20_delta_5`：`rps_20` 当前值减 5 个交易日前值。
- 共同计算契约：前复权 close、实际交易日 bar 窗口；RPS 截面资格包含有效价格/成交量、当前名称 ST/退剔除、股票维表、上市满 60 根和全市场有效样本下限；截面因子遇到非全市场子集 fail-closed。

### 8.2 目标侧现状核对

目标当前没有 `backend/app/factors/relative_strength.py`，没有上述 19 个 RPS/RS 因子，也没有 `rps_20_delta_5`。但完整迁移所需的两类数据基础已部分存在：

- `data/instruments_index/instruments_index.parquet` 含 `000985.SH`（中证全指）；
- `data/kline_index_enriched/date=*/part.parquet` 含 `000985.SH` 指数日 K；
- `data/ext_data/ext_hy_ths/part.parquet` 含行业快照（`所属同花顺行业`）；
- 目标已有因子注册表、评分物化、matrix-native 回测、DSL、扩展表和行业/指数数据读取能力，但缺 RPS 专用接入协议。

因此结论是：**数据源具备可迁移基础，计算与生命周期接入尚不存在**。不能只复制 `rps_20_delta_5` 的增量代码；必须迁移完整 RPS 模块及其必要挂接。

### 8.3 完整迁移必须包含的文件/接入点

1. `backend/app/factors/relative_strength.py` 完整模块及 `rps_20_delta_5` 增量；
2. `backend/app/factors/registry.py`：允许目录外模块化注册、保护 RPS 因子、计入因子快照/资产类型/预热契约；目标现有实验因子延迟注册逻辑不能被覆盖；
3. `backend/app/strategy/scoring.py`：RPS 因子物化、依赖展开、warmup、结果排序/评分接入；
4. `backend/app/backtest/matrix.py`：RPS 因子矩阵值适配，保证 panel 与 matrix-native 口径一致；
5. `backend/app/backtest/factor.py`：因子回测物化和结果 warning（行业快照/ST 非 PIT/子集截面风险）；
6. `backend/app/factors/dsl.py`：RPS 因子引用及行业分组表达式的挂接；
7. `backend/app/services/ext_data.py`：`ext_hy_ths` 快照按日期归档到 `history/date=...`，用于行业 PIT 回看；
8. `backend/app/strategy/ai_generator.py`：RPS 字段加入策略评分白名单；
9. 目标因子 API/前端因子库契约：完整 19 个因子显示、股票资产过滤、预热与描述；不迁移源仓库通达信指标页面；
10. RPS 直接测试、注册表快照、store/DSL/回测/matrix/行业归档集成测试。

### 8.4 与目标当前代码的主要冲突

- 目标 `registry.py` 当前包含 Alpha158 实验组的延迟注册和隐藏策略；源 RPS 的目录外注册不能按源版本直接覆盖注册表初始化，需保留 Alpha158 行为并接入独立 RPS 注册。
- 目标目标因子 `FactorSpec` 已有 Alpha158/indicator 相关扩展，源版本的 `Kind`、保护集合和目录顺序不同；直接覆盖会破坏现有未提交 Alpha158 迁移。
- 目标 `scoring.py` 当前只对注册表 virtual/composite/custom 走统一物化，需增加 RPS 专用物化入口，同时避免改变现有 Alpha158 和 DSL 语义。
- 目标矩阵回测已有大量 computed feature 分发，但没有 RPS 的 `matrix_values` 入口；若只接 panel 路径，会造成同一策略在选股与 matrix-native 回测结果不一致。
- 目标扩展快照写入目前没有源仓库的行业历史归档 hook；完整行业 RPS 必须补充归档，并验证原子写和失败降级。
- 目标已存在 `ext_hy_ths` 当前快照，但历史归档可能为空；历史早于首份归档的 RPS 行业结果必须按源模块约定产生偏差 warning，不能静默伪装成 PIT。
- `712f4c1def0380c3cdbde638e8d6e5e3c1086d83` 对相对强度评分白名单/长回看缓存的修复仍未迁移；如果完整 RPS 允许进入 AI 生成和 matrix scoring，需在实施计划中单独处理该白名单接入，不应把缓存 bugfix 顺带复制。

### 8.5 完整迁移范围建议

建议将 GF-000590-B 登记为“完整 RPS 因子族迁移”，分两个实施阶段但不裁剪最终能力：

- **B-Foundation**：完整模块、19 个因子注册、panel 评分物化、全市场资格、基准/行业读取、行业 PIT 归档、结果 warning、直接单元/集成测试；
- **B-Execution**：matrix-native 回测适配、因子回测/DSL/AI 白名单、因子 API/前端目录、全链路回归和人工验收。

这不是功能裁剪，而是为了避免一次修改 80 文件级基础提交；最终验收标准仍是完整 19 因子及其声明的全链路能力均可用。

明确排除：通达信公式解析器/编辑器、K 线主图逐线开关、与 RPS 无关的前端页面、源仓库示例策略（待 RPS 迁移完成后另行登记）。

### 8.6 审计结论

- GF-000590-B 不适合直接复制 `16d21f4e`，也不适合只加 `rps_20_delta_5`。
- 完整 RPS 迁移具备数据基础，但属于跨注册表、评分、因子回测、matrix、DSL、扩展快照归档、AI 白名单和前端目录的中型迁移。
- 建议状态更新为：`待迁移前确认`，待用户确认 B-Foundation/B-Execution 的实施顺序和是否纳入完整 RPS 用户可见因子库。
- 本轮仅完成固定源码与目标调用链审计；未修改业务代码、未安装依赖、未运行构建/测试、未部署或重启。

## 9. 2026-10-19 B-Foundation → B-Execution 实施记录

用户已确认按 `B-Foundation → B-Execution` 实施，且要求完整迁移 RPS，不裁剪为单一 `rps_20_delta_5`。

### B-Foundation

- 迁移 `backend/app/factors/relative_strength.py` 完整模块，注册完整 19 个因子：
  `rps_20/60/120/250`、`rps_ind_20/60/120/250`、`industry_rps_20/60/120/250`、`excess_20/60/120/250`、`rps_20_delta_5`、`rs_line_new_high_60`、`rs_line_slope_20`。
- 保留目标 Alpha158 延迟注册和既有 DSL 扩展，通过三方合并接入 `group_rank/group_zscore/group_mean` 及 Alpha158 时序算子。
- 接入评分物化、长窗口预热、行业快照按日期归档、行业 PIT 降级 warning、全市场截面 fail-closed。
- 修复 RPS 注册与 Alpha158 导入期间的循环依赖；RPS 注册改为直接检查 registry 内部目录，避免触发实验组加载。

### B-Execution

- 接入 matrix-native `matrix_feature` 的 RPS 计算入口。
- 接入因子回测的 RPS 物化、warmup、结果 warning 和注册因子元数据回退。
- 接入 DSL 行业分组键和点时行业挂载。
- 接入 AI 策略生成器的 Polars/matrix RPS 字段白名单。
- 接入 `/api/factors`、`/api/backtest/factor/columns` 的完整因子目录和前端因子库可见性；保留 Alpha158 默认隐藏、显式开关显示。
- 将长回看缓存覆盖判定改为交易日计数，补齐 RPS/MA120 长窗口慢路径预热；该修改是 RPS 可用所需的执行基础，不引入 fork 中与 RPS 无关的 TDX/K 线功能。

### 验证记录

- `tests/test_relative_strength.py` + `tests/test_relative_strength_integration.py`：`44 passed`。
- RPS + Alpha158 + DSL：`88 passed`。
- 因子 registry/store/API：`30 passed`。
- 因子回测批处理、指标、依赖、自定义信号、因子库：`38 passed`，1 个既有 Polars deprecation warning。
- 长回看缓存回归：`5 passed`。
- 变更 Python 文件 `py_compile` 通过；`git diff --check` 通过。
- 未执行前端构建、服务重启或公网部署；当前工作区保留未提交状态。

### 当前门禁

GF-000590-B 代码实现与自动验证已完成，现已进入**已人工确认**状态。用户已在本机服务上完成验收：完整 19 个 RPS 因子在因子库/API 可见；RPS 评分/回测链路可用；服务首页与 health 正常；matrix-native 与 panel 路径未出现 500 错误。
