# 上游 v0.3.37 本地合并验收记录

## 状态（2026-11-18）

- 用户授权：本地合并、解决冲突、验证；不提交、不推送、不部署。
- 当前集成分支：`integration/upstream-v0.3.37-20261118`。
- fork 基线：`d832daf48fe9ef516379b9a36a372e3d281c5260`。
- 上游目标：`shy3130/tick-stock-panel:main`，`f9cb383c5918c6a88a412f3ce43d59fe81b94ccc`（v0.3.37）。
- 共同基线：`487eab57a871`；获取时 fork 独有 115 个提交，上游独有 11 个提交。
- 使用 `merge --no-commit --no-ff`；保留合并父提交信息，等待用户另行授权提交。
- 原 `fork-integration` 分支未移动，远程未修改。GitHub ahead/behind 不会因未提交的本地处理改变。
- 技术结论：文本冲突已解决，所列自动测试和构建通过；不是全仓审查或生产验收。
- 用户最终接受状态：2026-11-18 用户明确确认验收，并授权提交合并、推送到 fork-integration；无需用户手动执行技术检查。

## 冲突处理（L3 核心源码升级兼容）

1. `backend/app/strategy/ai_generator.py`：同时保留 fork 的相对强度因子导入和上游的自定义信号导入。保留两种执行后端的相对强度评分字段、fork 的受控模块白名单，以及上游信号库、提示词和 data_dir 接入。
2. `frontend/src/pages/settings/MenuSettings.tsx`：采用上游完整分类菜单实现，不保留被替代的平铺菜单组件和重复页面清单。研究工作台通过既有扩展注册继续进入统一 navigation 列表，其类型与架构属性仍为扩展；旧页面特有的架构原因说明不再作为单独菜单列展示，沿用上游 UI。
3. 无新增注册系统、业务 API 或交易规则；研究工作台和后端 custom 目录相对 fork 基线无改动。

预检发现 12 个重叠文件。除上述冲突外，检查了设置/策略 API、偏好设置、扩展数据、维表同步、筛选、仓库、维表测试、前端 API 和功能文档的自动合并。重点验证 fork 股本补全与上游名称保留、筛选缓存名称补全、维表去重、菜单原子保存以及 AI 生成调用的兼容性。不是对 fork 全部 115 个独有提交的逐行审计。

## 本次补充回归

- `backend/tests/test_ai_strategy_meta_normalize.py`：增加 rps_20 / rps_60 评分字段仍可验证通过的两项测试。
- `frontend/src/extensions/loading.test.tsx`：真实加载研究工作台注册，验证它进入新导航、能移动到分类、不会重复且旧路由仍注册。

## 实际验证

后端使用现有虚拟环境，`uv run --frozen --no-sync pytest ... -q`，串行执行；扩展回归使用临时 DATA_DIR。设置 `POLARS_MAX_THREADS=1 NUMBA_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1`。

- 上游受影响回归：**152 passed**。
  - test_ai_iterate.py、test_ai_minute_integration.py、test_ai_probe.py
  - test_ext_pull_schedule_seconds.py、test_instrument_sync_share_fields.py、test_limit_ladder_names.py
  - test_nav_layout.py、test_packaging_spec.py、test_pipeline_status.py
  - test_strategy_build_stream.py、test_watchlist_enriched_join.py
- 合并兼容扩展回归：**156 passed**。
  - test_ai_generator_prompt.py、test_ai_strategy_meta_normalize.py、test_relative_strength_integration.py
  - test_preferences_cache.py、test_preferences_concurrent_write.py
  - test_screener_*.py、test_ext_data_*.py、test_repository_index.py
- 上述两组共 **308 项**，无重复计入早期 47 项冒烟结果。
- 前端：`pnpm exec vitest run --maxWorkers=1 --no-file-parallelism`，**11 文件、73 passed**。
  - navLayout、MenuSettings、Layout.realtime、extensions/loading
  - aiSettings、settings/AI、CreateExtDialog、ExtDataPullPanel
  - StrategyBuilderDialog.tools、limit-ladder/Page.names、Watchlist.charts
- 构建：`pnpm build --outDir /tmp/tsp-upstream-v0337-build`，**通过**（TypeScript + Vite）；未覆盖原 frontend/dist。
- `git diff --check`、`git diff --cached --check`：通过；`git ls-files -u`：为空。
- Ruff 对 ai_generator.py 与对应新增测试文件：1 条 BLE001（ai_generator.py 的既有宽泛捕获）。对 MERGE_HEAD 原文件执行相同规则也报告同一条，未新增告警，未将 lint 报告为全通过。

## 验证过程中遇到的问题

- 本机依赖仍是 lightweight-charts 4.2.3，初次 TypeScript 检查失败；锁文件要求 5.2.1。使用官方 npm registry 执行 frozen-lockfile 安装，禁用安装脚本、子任务与网络并发均为 1，更新到 5.2.1 后完整构建通过。未更改全局 npm 配置或锁文件。
- 新增导航回归初次误把 layout 传给接受单个节点的 navPageIds；修正为 items.flatMap(navPageIds)，最终全组重跑通过。
- 保留非阻断警告：Python datetime.utcnow 弃用、Polars schema 性能提示、React Router future flag、Vite 大 chunk 提示。
- 每次实质测试/构建前检查内存、swap、磁盘，构建期间检查内存；未并发运行构建/测试，未清缓存。

## 证据与限制

本机临时日志：
- `/tmp/tsp-upstream-backend-tests.log`
- `/tmp/tsp-upstream-backend-regression.log`
- `/tmp/tsp-upstream-frontend-final.log`
- `/tmp/tsp-upstream-build.log`
- `/tmp/tsp-upstream-install.log`
- `/tmp/tsp-upstream-ruff.log`、`/tmp/tsp-upstream-ruff-baseline.log`

这些日志是临时证据，不随 Git 提交分发。

未运行全仓测试、真实浏览器联调、真实交易数据采集、生产重启恢复或 Docker 打包；没有启动应用或触发业务任务。自动测试中的页面渲染不等同于代理浏览器验证或用户人工验收。未做部署，不能声称生产已升级。此次为上游合并，不额外新增业务页面或按钮。

下一步需用户确认本地交付结果；只有获得明确提交/推送授权后，才可完成 merge commit、推进目标分支并推送。不得在合并待提交期间误切分支或再次拉取覆盖合并上下文。

## 用户确认与发布授权（2026-11-18）

用户明确回复“确认验收，提交合并并推送到 fork-integration”。上述待提交状态为验证完成时的历史快照。执行前复核远程 fork-integration 仍为 d832daf48fe9，无新增提交；差异格式检查通过、无未解决冲突。获准创建带 Signed-off-by 的 merge commit，将目标分支快进并正常推送，不使用强制推送。最终提交与远程同步结果以 Git 记录及会话交付报告为准。本次授权不包括部署或重启。
