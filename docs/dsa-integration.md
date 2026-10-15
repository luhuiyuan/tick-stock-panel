# 决策工作台集成

TickFlow Stock Panel 只负责决策工作台的页面、后端路由、数据桥接和安全代理；DSA 服务作为**外部可部署服务**运行。本仓库不复制或发布 daily_stock_analysis 的完整源码。

浏览器只访问 TSP 的 `/api/dsa/*`，由 `backend/app/custom/dsa/` 按允许列表转发到配置的 DSA `/api/v1` 接口。DSA 的前端不作为第二套产品纳入 TSP。

## 外部服务配置

在 `.env` 中配置 DSA 服务地址：

```dotenv
DSA_BASE_URL=http://127.0.0.1:8000
DSA_INTERNAL_TOKEN=至少16位随机ASCII字符串
DSA_TIMEOUT_SECONDS=20
```

生产环境应将 DSA 部署在受控内网地址，不要把 DSA 管理端口直接暴露到公网。TSP 与外部 DSA 的版本应匹配，尤其是下列集成入口：

- `/api/v1/health`
- `/api/v1/history`、`/api/v1/stocks/*`
- `/api/v1/tsp/etf-rotation`（需要 `X-TSP-Internal-Token`）
- DSA 定时任务、通知、分享图和情报接口

未配置 `DSA_BASE_URL` 时，决策页面仍可打开，但显示服务未连接；TSP 不会用系统中未知的 Python 或隐式下载 DSA 源码。

常用外部 DSA 配置契约：

- `SCREENING_ENABLED=false`：默认关闭外部 DSA 多市场选股；
- `INDUSTRY_MAP_FILES`：可选的同花顺行业映射文件，由外部 DSA 部署读取；
- `EFINANCE_PRIORITY=3`：当外部 DSA 同时使用 efinance 与 TickFlow 时，让 TickFlow 优先；
- `TSP_QUANT_EVIDENCE_FILE`：可选的 TSP 机械回测证据文件；
- `X-TSP-Internal-Token`：仅由 TSP 服务端添加，浏览器不能直接提供。

## TSP 集成范围

本仓库保留以下主项目集成：

- `backend/app/custom/dsa/`：状态、目录、代理、认证会话、定时配置、ETF 请求、资讯/资金流/个股上下文桥接；
- `frontend/src/custom/dsa/`：决策工作台、历史、信号、ETF、组合风险和定时配置页面；
- `backend/tests/` 与 `frontend/src/custom/dsa/` 下的契约测试；
- `backend/app/custom/dsa/quant_evidence.yaml`：TSP 侧机械证据文件；外部 DSA 适配器可按约定读取。

TSP 不包含：

- daily_stock_analysis 的完整 API、Agent、Bot、数据源、策略和桌面/Web UI 源码；
- DSA 的完整测试树、文档树和无关市场策略；
- 本地自动创建 DSA 虚拟环境或从仓库内源码启动 sidecar。

## ETF 轮动安全边界

TSP 的 `/api/dsa/jobs/etf-rotation` 只向外部 DSA 的 `tsp/etf-rotation` 入口发送固定请求，不接受浏览器传入命令或参数。请求必须配置 `DSA_INTERNAL_TOKEN`，TSP 会在服务端添加 `X-TSP-Internal-Token`。

外部 DSA 侧需要自行安装和管理其 ETF 轮动依赖，并由 DSA 适配器提供固定命令入口。TSP 只解析结构化结果，不执行外部仓库中的 Python。

## 部署预检

TSP 本身使用：

```bash
python scripts/deploy_preflight.py --data-dir ./data
```

`1000:1000` 只是 Compose 示例值，不是固定部署要求。请按实际运行用户设置 `APP_UID` / `APP_GID`，并确保 `data/` 与 `.env` 对该用户可写。外部 DSA 的部署、权限和资源检查由其自身部署方式负责。

## 外部 DSA 适配器

如果需要让外部 DSA 使用 TSP 的资讯、资金流或个股上下文，可在 DSA 部署侧显式安装/挂载本仓库的适配器文件，并按 `backend/app/custom/dsa/` 中的 HTTP 契约联调。TSP 不会修改或复制 DSA 源码。

真实外部数据源、通知、AI 密钥和 DSA 任务必须在部署环境中单独授权；本仓库的静态测试不代表这些外部能力已执行。
