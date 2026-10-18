# free_market_data 候选源码审计

- **审计日期**：2026-10-17
- **候选仓库**：`zengwenliang416/tickflow-stock-panel`
- **候选提交**：`138811095821da293206ac9af15b8c7c4946de3b` (`Add free market data source`)
- **父提交**：`24252cf77ceb2d7b426ba01279cf06c2bc6c4a46`
- **候选分支当前头**：`e9d9e4bd32e4ecff1658d12f2f57a4f761c1eabc`
- **源码快照**：`/tmp/tick-stock-panel-fork-migration/free-market-data-zengwenliang-20261017/`
- **审计性质**：只读源码、原始 diff、后续提交和目标调用链审计；未修改业务代码、未安装依赖、未部署或重启。

## 1. 结论

**结论：无需迁移。该候选不是新的免费数据源，核心接口与当前 `free_public` 重复；候选的全局免费模式改造也不应直接迁移。**

候选新增的 `backend/app/services/free_market_data.py` 直接调用：

- `https://push2.eastmoney.com/api/qt/clist/get`
- `https://push2.eastmoney.com/api/qt/stock/get`
- `https://push2his.eastmoney.com/api/qt/stock/kline/get`

当前目标工程的 `free_public_adapter.py` 已使用同一组东方财富公开接口，并结合新浪/腾讯实现标的、日 K、分钟 K、实时行情和五档盘口。因此把该候选整体接入会形成第二套东方财富适配路径，而不是新增数据源能力。

## 2. 候选实现范围

候选提交原始 diff 共 10 个文件，约 `+561/-77` 行，主要包括：

- 东方财富实时股票行情批量读取；
- 东方财富指数实时行情读取；
- 东方财富日 K读取；
- 从行情快照生成 instruments 维表；
- 免费模式下的日 K、实时行情、指数和标的同步回退；
- 修改 `tickflow/policy.py`、`tickflow/pools.py`、`tickflow/repository.py` 和多个同步服务，使无 Key 模式自动使用免费行情。

候选没有在该提交中提供针对 `free_market_data` 的专用测试文件。候选后续历史中可见的相关修正主要是：

- `f1d08fdaf50f150e585034f70c64ba25800823bc`：切换免费行情延迟主机；
- `093ac9944d89e30d1046c24f6e7a6db44c4f664e`：指数行情使用延迟主机；
- `dfbac7e69d76fb87f58a2978a58350e954ced0a2`：免费行情页面并发抓取；
- 后续 `1131625e125cd5eaaac066f2825140825afd4074` 和 `e09106a5cb606aa636b1b102a4cb5e7703044331` 转向 Longbridge，属于需要 OAuth/凭证的另一条数据源路线，不属于纯免费源。

## 3. 与当前实现的差异

| 维度 | 候选 `free_market_data` | 当前 `free_public` | 结论 |
|---|---|---|---|
| 实时行情 | 东方财富批量接口 | 新浪/东方财富/腾讯公开接口 | 能力重复 |
| 日 K | 东方财富 `kline/get` | 东方财富/新浪 K 线适配 | 能力重复 |
| 分钟 K | 原始候选提交未提供分钟 K | 已提供分钟 K | 当前实现覆盖更多 |
| 五档盘口 | 原始候选提交未提供 | 已提供 depth5 | 当前实现覆盖更多 |
| 标的列表 | 从东方财富行情快照推导 | 独立 Provider 标准化返回 | 能力重复 |
| 复权因子 | 未提供独立因子 | 明确不伪造，返回空表 | 当前契约更清晰 |
| 路由方式 | 修改全局 Free 模式和 TickFlow policy | 独立 Provider，按能力候选路由 | 当前隔离性更好 |
| 测试 | 候选提交未见专用测试 | 已有 Provider contract 测试 | 当前验证基础更完整 |

## 4. 关键风险

### 4.1 数据源重复与维护分叉

候选和当前 `free_public` 都依赖东方财富公开接口。整体迁移会同时维护两套字段解析、错误处理、重试、限流和主机切换逻辑，增加字段漂移和行为不一致风险。

### 4.2 全局默认路由侵入过大

候选通过 `settings.use_free_mode` 改变能力探测、池构建、日 K同步、指数同步、标的同步和行情服务行为，并把无 API Key 直接解释为使用免费行情。该设计会影响所有能力，不符合当前“免费源只作为独立 Provider、默认路由保持不变”的确认范围。

### 4.3 复权语义不完整

候选日 K使用东方财富 `fqt=1`（前复权）读取，但没有独立复权因子闭环；同时 `records_to_daily` 直接从实时快照生成当日日 K。若将其接入现有 raw/enriched 数据链，必须重新核对原始价、前复权价、除权因子和当日快照写入语义，不能直接复用。

### 4.4 网络稳定性未达到新增源验收标准

在本机于 2026-10-17 对候选所用东方财富接口进行低频只读探测：

- `push2.eastmoney.com` 返回 HTTP 302/随后受网关影响返回 502；
- `push2delay.eastmoney.com` 返回 HTTP 502；
- `push2his.eastmoney.com` 出现远端主动断开（`RemoteProtocolError`）。

这只能证明当前海外服务器链路不稳定，不能据此断言接口永久不可用；但候选本身也没有提供足够的独立测试来证明其长期可用性。由于当前目标已有同源 `free_public`，不再为重复实现增加网络验证和维护成本。

### 4.5 Longbridge 后续提交不属于免费源

候选分支后续引入 `longbridge.openapi`、OAuth token 和 `market_data_provider=longbridge`，并将 Longbridge 作为市场数据 provider。该路线需要账号/授权配置，不能归入本轮“只评估新的免费数据源”，因此不纳入本次迁移。

## 5. 最小处理建议

1. 不迁移候选 `free_market_data.py` 及其对全局 policy/client/repository 的修改；
2. 保持当前 `free_public` 独立 Provider 作为新浪/东方财富/腾讯公开源入口；
3. 如未来需要增强稳定性，只单独评估候选中的“延迟主机切换、并发抓取、限流/熔断”策略，并要求适配现有 Provider 契约；
4. 不启用候选的全局 `use_free_mode` 或 `data_backend` 路由；
5. Longbridge 另列为需凭证的数据源候选，不纳入免费源范围。

## 6. 审计验证记录

- Git 对象固定：候选提交和父提交可解析；
- 原始 diff：已核对 10 个文件及约 `+561/-77` 行变更；
- 后续修正：已核对延迟主机、并发抓取和 Longbridge 转向提交；
- 目标对照：已对照当前 `free_public_adapter.py`、`free_public_provider.py`、能力矩阵和偏好路由；
- 网络探测：仅低频 GET，不写入行情数据；结果为 302/502/远端断开；
- 工作区：未修改目标业务代码、未安装依赖、未部署/重启、未提交/推送。
