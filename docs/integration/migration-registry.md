# 仓库功能迁移总记录

## 仓库总览

| 仓库标识 | 仓库地址 | 移植标识 | 活动/最近轮次 | 状态 | 代码进度 | 验证进度 | 计划文档 | 更新时间 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| github.com/flyme8899/tick-stock-panel | https://github.com/flyme8899/tick-stock-panel | market-intelligence | market-intelligence-r1 | F02-A/B/C/D/E/F主项目 DSA 集成、TSP桥接、决策工作台、ETF sidecar、非 root 部署预检与.env bind mount 兼容、ETF分桶/行业筛选/复盘通知收尾、F10公告日感知基本面预设、F13 K线形态/选股字段、F18六子批次及F19帮助/部署配套已移植并完成定向验证；F02已完成；真实外部源/部署/人工验收待确认 | 19/19（100%） | 0/19（0%；自动定向证据已追加，整项验收待确认） | [首轮计划](market-intelligence-migration-plan.md) | 2026-10-14T20:24:03+08:00 |

## github.com/flyme8899/tick-stock-panel

- 稳定移植标识：market-intelligence。
- 仓库地址：https://github.com/flyme8899/tick-stock-panel。
- 创建时间：2026-10-13T12:48:26+08:00。

### market-intelligence-r1

- 计划：[市场情报功能迁移首轮计划](market-intelligence-migration-plan.md)。
- 状态：已完成第二批及 F03/F04/F11 批次，本轮完成 F18 六子批次代码迁移与定向验证；本轮F03后端定向90通过、F04后端认证32通过、F11定向107通过1阻塞，前端TypeScript与Vite生产构建通过；F15两个失败仍为主线去内置化策略测试阻塞；F19帮助文案修复后定向测试全通过；F10已完成定向收尾验证；F13已完成定向实施验证；F02-A/B/C已完成。
- Fork 分支：main；源码 SHA：`eb7c4ae36e0c975e14580cdc6504078539002439`；源码提交时间：2026-10-13T12:36:16+08:00。
- 上游比较 SHA：`53ed6755ff0f066cf23e681ed714f4a4d57c8a17`；提交时间：2026-10-12T22:50:50+08:00。
- 目标分支：integration/market-intelligence；目标基线：`c513121dadb0555b8eccacaf6b81a96d3f51a603`。
- 用户授权：已确认F01–F19完整功能计划并确认继续实施F04、F11；确认落档时间见计划实施历史；本次更新时间：2026-10-14T15:01:59+08:00。
- 候选121个提交归并19个已确认功能组及1个主线已有排除项；本轮确认分母N=19，不含N01。
- 整项代码完成18/19（94.7%）：F01/F03/F04/F05/F06/F07/F08/F09/F10/F11/F12/F13/F14/F15/F16/F17/F18/F19；整项验收验证0/19（0%，本轮自动定向证据已追加但仍待整项边界、外部源和人工联调确认）。此前第二批自动验证：后端137通过/2失败、前端定向Vitest57通过；本轮F03定向后端90通过，F04认证后端32通过、密码策略2通过，F11后端107通过1阻塞、前端6通过，TypeScript与生产构建通过；剩余1项未计入代码完成（F02暂缓）；F10定向收尾验证通过，F13定向实施验证通过，F19帮助源文案不一致已修复。百分比不代表代码量、工时或整个Fork比例。
- 阻塞/风险：F15原trade_marks测试中两个测试依赖主线已移除的trend_breakout/macd_golden；真实外部源/人工UI联调未执行；F19帮助源“5、15、30”文案与测试不一致问题已修复并通过定向验证；F10已按主线去内置化策略架构完成迁移，定向验收通过。完整DSA尚未迁移；采集默认开关及示例需部署前确认。
- 下一步：处理F11 `signal_limit_up` 主线契约边界并补充F03/F04/F11人工、部署和数据边界验收；同时保留F15策略测试适配边界、test_minute_routing.py及完整F15联调作为后续验证项。当前F04批次新增文件与共享合入已追加，完整出处见计划及F04 CSV，出处、兼容调整、指纹和验证命令见计划。未提交/推送、未启动或重启应用/采集服务。
- 更新时间：2026-10-14T16:32:54+08:00。

### 追加事件：2026-10-13T14:03:02+08:00

- 第一批原代码实施；详见计划实施历史、来源CSV与共享diff。锁任务退出1，无构建安装或采集运行；保持真实0/19整项完成、0/19验证，等待依赖闭环。

### 追加事件：2026-10-13T15:30:25+08:00

- 第二批F01/F14/F15/F16/F17原代码已移植；Python静态编译和diff检查通过；自动测试因资源门槛暂缓。来源指纹和共享差异证据已保存，未提交/推送、未启动采集。

### 追加事件：2026-10-13T14:24:16+08:00

- 原依赖锁/安装已完成，首批6项业务代码落地（6/19，31.6%）。后端563通过1跳过、主线47通过、前端类型/生产构建通过；前端原测试14通过1失败。保留F19原文断言不一致，不更改算法凑结果；整项验收暂0/19。历史阻塞仅适用于先前时点。


### 追加事件：2026-10-14T08:43:35+08:00

- OCR/HR任务结束后资源恢复，按单进程串行完成第二批定向验证：后端137通过/2失败/3 warning；前端定向Vitest 57通过；`StockDailyKChart.test.tsx`最小mock修复后TypeScript通过；`pnpm build`的tsc与Vite生产构建通过。
- 两个后端失败仅依赖主线已去内置化的`trend_breakout`和`macd_golden`，未恢复策略文件；构建仅提示echarts大chunk，未修改无关模块。验证命令、边界和剩余风险详见计划第11节。
- 仍未commit/push/stage，整项验证保持0/19，不把自动定向通过冒充整项验收。


### 追加事件：2026-10-14T08:55:00+08:00

- 用户确认并完成F03 Alpha158单功能批次：按3个原始提交逐提交三方合并，新增文件与固定F03提交树逐字节一致，共享文件未整体覆盖当前改动；出处见`market-intelligence-r1-f03-file-provenance.csv`。
- F03定向后端测试90通过；前端TypeScript和生产构建通过；F03新增文件Ruff有导入排序提示，未自动改写原始Fork代码；未运行全市场评估或真实数据写入。
- 代码进度更新为12/19（63.2%），整项验证保持0/19；仍未commit/push/stage。


### 追加事件：2026-10-14T09:16:20+08:00

- 用户确认并完成 F04 多用户认证批次：按 `457b3e5f0876f5f2f65012c43605b7e0cb06b1b2`、`bf9bd4a5a140f1a324a22720daa495d9b60bb1c2` 顺序逐提交三方合并；新增账号服务、测试、密码策略、设置页和管理脚本与固定源树逐字节一致。
- F04 后端认证定向测试 32 通过，前端密码策略 2 通过，TypeScript 和生产构建通过；锁文件保留既有依赖并加入 Argon2/bcrypt。
- 代码进度更新为 13/19（68.4%），整项验证保持 0/19；未 commit/push/stage，未启动或重启应用。


### 追加事件：2026-10-14T09:31:00+08:00

- 用户确认并完成 F11 回测流动性/一字板/风险指标/因子异步历史批次：按三个原始提交顺序三方合并；新增文件与固定源最终树逐字节一致，出处见 `market-intelligence-r1-f11-file-provenance.csv`。
- F11 受影响后端定向测试 107 通过、8 warning；原始全集多 1 个失败，原因是主线已去内置化的 `signal_limit_up` 测试契约，未恢复旧 builtin。前端回测测试6通过，TypeScript和生产构建通过。
- 代码进度更新为 14/19（73.7%），整项验证保持 0/19；未 commit/push/stage，未启动或重启应用。


### 追加事件：2026-10-14T12:21:01+08:00

- F18 六个子批次代码已落地并完成定向收敛；后端 183 passed、前端分钟测试 8 passed，TypeScript 与生产构建通过。
- F18-A custom provider 既有契约回归已恢复，相关 38 项测试通过；来源证据见 `market-intelligence-r1-f18-file-provenance.csv`。
- 代码进度更新为 15/19（78.9%）；整项验收仍为 0/19。F02 暂缓，F10/F13/F19 尚未完整实施。工作区未提交、未推送。


### 追加事件：2026-10-14T14:06:08+08:00

- F19 帮助页与部署配套已按固定源提交补齐；新增部署预检脚本/测试，Compose/Dockerfile 非 root 运行与宿主机采集文档完成兼容合入。
- F19 后端定向 40 passed、1 skipped；PageHelpButton 2 passed；TypeScript 与生产构建通过。`guide.test.ts` 保留 1 项固定源“5、15、30”文案/断言不一致。
- 代码进度更新为 16/19（84.2%）；整项验收仍为 0/19。F02 暂缓，F10/F13 尚未完整实施；未提交、未推送、未启动服务。来源证据见 `market-intelligence-r1-f19-file-provenance.csv`。


### 追加事件：2026-10-14T14:47:29+08:00

- F19 帮助源文案按后端 `market_confirm.py` 的发布后 5、15、30 分钟确认窗口补齐；未修改测试断言或计算逻辑。
- `guide.test.ts` 与 `PageHelpButton.test.tsx` 合计 **5 passed**，`pnpm exec tsc -b` 通过。真实外部源、部署和浏览器人工验收未执行，整项验收仍为 **0/19**。
- 当前代码迁移进度 **16/19（84.2%）**；F02暂缓，F10/F13未实施。未提交、未推送。


### 追加事件：2026-10-14T15:01:59+08:00

- F10 公告日感知基本面预设迁移收尾：核心源文件/原测试与 Fork 固定提交 `cd9b8cf9afc9be1643d308880deb13252c2cc774` 逐字节一致；共享文件保留其他工作区改动及主线去内置化架构，逐文件 provenance 已记录。
- 后端定向 **47 passed**，前端策略卡片/策略池 **10 passed**，TypeScript 检查通过；生产构建、外部数据联调和浏览器人工验收未执行。
- 当前代码迁移 **17/19（89.5%）**，整项验收 **0/19**；F02暂缓，F13未实施。未提交、未推送。


### 追加事件：2026-10-14T15:25:58+08:00

- F13 K线形态与选股补充字段按 5 个固定 Fork 提交完成收尾；新增/核心源文件核对一致，共享文件保留既有迁移改动，逐文件 provenance 已记录。
- 后端 F13 定向 **38 passed**，前端定向 **12 passed**，TypeScript、Python compileall、冲突标记和 `git diff --check` 通过。共享回归的 5 个失败属于既有因子数量/指数分钟 `capset` 契约问题，未修改。
- 当前代码迁移 **18/19（94.7%）**，整项验收 **0/19**；仅剩 F02 暂缓。未提交、未推送。


### 追加事件：2026-10-14T15:40:29+08:00

- F02-A 基础 vendor 与 TSP DSA 旁路完成：新增固定 Fork 首提交中的 500 个当前不存在文件，其中 vendor 486 个；已有 DSA 共享文件未覆盖，Compose、环境配置和其他共享文件暂留后续子批次。
- `test_dsa_integration.py` **14 passed**；vendor/DSA compileall、严格冲突标记检查和 `git diff --check` 通过。未启动 sidecar、未联网、未执行真实任务。
- F02 仍为移植中，F02-B 至 F02-F 待实施；总代码进度保持 **18/19（94.7%）**，整项验收 **0/19**。未提交、未推送。


### 追加事件：2026-10-14T16:32:54+08:00

- F02-B TSP 后端桥接与会话续期完成；补齐量化证据、个股上下文、价位登记、feed token 白名单及安全行情 hook，保留其他迁移改动。
- F02-B 定向 **48 passed**；compileall、严格冲突标记和 `git diff --check` 通过。两个主线去内置化 `trade_marks` 测试阻塞未纳入本批修复。
- F02-A/F02-B 已完成，F02-C 至 F02-F 待实施；总代码迁移保持 **18/19（94.7%）**，整项验收 **0/19**。未提交、未推送。


### 追加事件：2026-10-14T17:10:34+08:00

- F02-C 已按固定 Fork 提交实施：仪表盘/观察池历史、决策信号、量化证据、组合风险、ETF 面板与通知试发测试；共享前端契约仅做原代码必要补回。
- 前端串行 TypeScript 通过，DSA 定向 Vitest **7 files / 17 passed**，`git diff --check` 通过。vendor 测试因缺少 `markdown2`、`sqlalchemy` 未计通过；未启动 sidecar、未联网、未写入数据。
- F02-A/B/C 完成，F02-D/E/F 待实施；代码进度仍 **18/19（94.7%）**，严格整项验收仍 **0/19**，工作区未提交/未推送。


### 追加事件：2026-10-14T17:41:45+08:00

- F02-D 已按确认范围实施：DSA sidecar ETF job、分桶混合动量 vendor 核心、ETF 面板/原始测试、可选 Compose dsa profile 与配置契约已落地。
- Python compileall、Git 冲突标记检查和 `git diff --check` 通过；因可用内存最低约 125 MiB，pytest/tsc/Vitest/Compose 构建暂缓，未启动 sidecar、未联网、未写入数据。
- F02-A/B/C/D 完成，F02-E/F 待实施；代码进度仍 **18/19（94.7%）**，严格整项验收仍 **0/19**，工作区未提交/未推送。


### 追加事件：2026-10-14T18:15:44+08:00

- F02-E 已按确认范围完成：非 root app/DSA 部署预检、efinance 缓存目录权限、单文件 `.env` bind mount 原地写入 fallback 与 `.env.bak` 恢复链路。
- F02-E 定向后端测试 **29 passed**；compileall、严格冲突标记、静态 Compose/文档契约和 `git diff --check` 通过。未构建/启动 Docker，未启动 sidecar，未联网、未写入业务数据。
- F02-A/B/C/D/E 完成，F02-F 待实施；代码进度保持 **18/19（94.7%）**，整项验收保持 **0/19**。未提交、未推送。


### 追加事件：2026-10-14T18:30:31+08:00

- F02-F 已按确认范围完成：ETF 分桶名称展示、balanced_alpha 行业映射/同桶上限、盘后复盘通知补发与 `notification_runs` 记录。
- F02-F 原始定向测试 **27 passed**，F02-E 回归测试 **29 passed**；compileall、严格冲突标记和 `git diff --check` 通过。未构建/启动 Docker，未启动 sidecar，未联网、未写入业务数据。
- F02-A/B/C/D/E/F 完成，F02 代码迁移闭环；总代码进度 **19/19（100%）**，严格整项验收仍为 **0/19**。未提交、未推送。


### 整项验收检查点：2026-10-14T20:24:03+08:00

- Wave 0/1 静态基线、compileall、冲突标记、diff 和静态契约检查通过。Wave 2-A/B/C/D 分别记录 **164/1、269/1、133/3、148/0** 的通过/失败数（Wave 2-D 另 1 skipped）；详细失败测试与 warning 见计划检查点。
- Wave 3 TypeScript 通过；全量 Vitest 43 files / 226 tests passed，1 项断言失败、2 个无 test suite 文件。Wave 4 DSA/TSP 集成 108 passed/1 failed；`docker compose config --quiet` 通过。
- Wave 5 仅完成 Compose 静态解析；未启动服务、未联网、未使用外部凭据、未发送通知或写数据。
- 本轮未修改业务代码/config/test；所有失败保持未解决。代码迁移 19/19（100%），严格整项验收仍 0/19；部署和外部数据源测试待用户明确授权具体执行范围。

### 整项验收收敛记录：2026-10-14T21:30:00+08:00

- 19 项迁移代码进度保持 **19/19（100%）**。按已确认范围完成自动验收失败收敛，未 reset/clean/fetch/commit/push。
- 后端失败文件回归 **93 passed**；前端定向 **3 files / 7 passed**，全量 Vitest **46 files / 232 passed**，TypeScript、compileall、`git diff --check` 均通过。
- 修正均为现行迁移契约的测试/示例兼容：稳定因子注册表、用户定义信号种子、测试策略 fixture、Vitest 测试入口、监控标签映射和 EFINANCE_PRIORITY 示例；未恢复主线已移除的 builtin 策略，未改热点生产排序算法。
- Wave 5 真实部署、外部源和人工联调未执行，故严格整项发布验收仍待后续明确授权和条件；工作区未提交、未推送。

### Wave 5-A 静态验收记录：2026-10-14T22:00:00+08:00

- 普通 Compose 与 DSA profile 静态解析均通过。
- `scripts/deploy_preflight.py` 已改为默认宽松模式：不再把 `1000:1000`、历史文件属主漂移或未启用采集器的 Python 缺失当作一律阻塞；`--strict` 恢复严格属主/采集器检查，`--dsa` 显式启用 DSA 目录与 `.env` 可写检查。
- 部署预检测试 **17 passed**，compileall 与 `git diff --check` 通过。
- 当前宿主机 `data/`/`data/news/` 对配置 UID/GID 仍不可写，故尚不能直接进入容器启动验收；未执行权限修复、构建或启动。

### 2026-10-15 Wave 5-B/C 隔离部署验收

- **Wave 5-B app 构建：通过**：临时隔离 Compose 副本使用官方 PyPI/npm 源构建 `wave5isolated-app:latest` 成功；前端生产构建完成，镜像约 594 MB。
- **构建兼容修复：已记录**：Dockerfile 的 efinance 初始化改用 `/app/.venv/bin/python`；认证运行依赖补齐 `bcrypt`、`argon2-cffi`，`backend/uv.lock` 已同步；认证定向测试 32 passed。
- **Wave 5-C app 烟囱测试：通过**：隔离容器 `/health`、首页和 `/api/auth/status` 均返回 200；非 root 运行用户可写隔离数据目录；容器已停止并移除。
- **外部访问边界：部分触发**：隔离启动钩子访问 `www.gelonghui.com` 与 `free-api.tickflow.org`，后者返回 403；结果仅写入临时隔离目录，未使用外部密钥、未发送通知、未启动 DSA、未访问真实用户数据。
- **尚未完成**：Wave 5-D DSA profile 构建/启动、真实外部源授权联调及人工浏览器验收。由于根分区约剩 4.2 GiB 且 BuildKit 缓存较大，本轮不继续构建 DSA 镜像。
- **工作区纪律**：未执行 reset/clean/fetch/commit/push；所有迁移改动继续保持未提交。


### 2026-10-15 F02 范围纠偏

- 确认不将 `daily_stock_analysis` 完整 Fork 仓库 vendoring 到 `tick-stock-panel`；已移除约 486 个 vendor 文件和本地 DSA sidecar 构建入口。
- F02 仅保留主项目决策工作台、后端代理/桥接、前端页面、配置契约与必要测试；外部 DSA 由部署方独立提供。
- ETF 轮动改为通过 `DSA_BASE_URL` + `DSA_INTERNAL_TOKEN` 调用外部 DSA 受保护入口。
- 静态检查、Compose app-only 解析、Python 编译和 F02 定向测试 **51 passed**；尚未重写提交、rebase 或 push。
