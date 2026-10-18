# opentdx 候选源码审计记录

- **记录日期**：2026-10-17
- **候选仓库**：`LisonEvf/tickflow-stock-panel`
- **候选排名**：前 80 Fork 中第 28 名
- **候选地址**：`https://github.com/LisonEvf/tickflow-stock-panel`
- **状态**：阻塞，尚未完成源码审计
- **临时目录**：`/tmp/tick-stock-panel-fork-migration/opentdx-lisonevf-20261017/`

## 已确认的候选元数据

来自 `analysis/fork-analysis/feature-updates/feature_commits.csv` 和对应独有提交记录：

| 提交 | SHA | 变更规模 | 说明 |
|---|---|---:|---|
| add submodules | `2a06c5bec8fe737ef9d697c8893a63859a8ca800` | +8/-0，3 文件 | 增加 `opentdx` 和 `openkph` 子模块指针 |
| 改用 opentdx 数据源 | `873c2ce9d301f444edfefc3a1144724e9f4f0a47` | +4565/-4287，58 文件 | Provider、服务、路由和测试大范围切换 |
| intraday/loading | `969a2b370b866d9527df3f6c5f154d749870f95d` | +1432/-317，10 文件 | 分钟 K 和前端加载相关变更 |
| desktop release | `faee04e8b66ce324e79f4aed54099a917bab47c9` | +253/-99，8 文件 | opentdx desktop v0.1.68 发布准备 |
| rename | `586a1c8700cb214b8ac0406df202b0fc57dcb71d` | +2630/-562，52 文件 | 后续项目重命名/大范围调整 |

候选路径涉及：

```text
backend/opentdx
backend/openkph
backend/app/data_providers/opentdx_provider.py
backend/app/services/quote_service.py
backend/app/services/kline_sync.py
backend/app/services/financial_sync.py
backend/app/tickflow/client.py
backend/app/tickflow/policy.py
backend/tests/data_providers/test_opentdx_provider_minute.py
backend/tests/services/test_financial_sync_opentdx_snapshot.py
```

## 获取结果

2026-10-17 尝试：

1. `git clone --filter=blob:none https://github.com/LisonEvf/tick-stock-panel.git`；
2. `git ls-remote` 和 Git fetch 固定候选 SHA；
3. GitHub REST `GET /repos/LisonEvf/tick-stock-panel`；
4. 查询 `opentdx`、`openkph` 子模块地址。

结果：

- GitHub REST 返回 HTTP 404 `Not Found`；
- Git smart HTTP 返回 `Repository not found`；
- 无法获得候选 commit 对象、`.gitmodules` 内容、子模块固定 SHA、Provider 源码或配套测试源码。

## 审计门禁结论

本轮**不能**根据提交标题、路径清单或分析 CSV 声称完成源码审计。以下内容尚未验证：

- opentdx 与 eltdx/tdxquant 的实际协议和连接方式；
- TDX 主站地址、握手、连接池、超时和重试；
- 日 K、分钟 K、实时、逐笔、竞价和 F10 的字段/单位/时间戳；
- 复权与交易日语义；
- Provider 是否能隔离为独立源，还是必须改全局 TickFlow 路由；
- 子模块/vendor 许可证和可分发边界；
- 候选测试是否真实覆盖失败降级和断线恢复。

## 恢复条件

满足以下任一条件后，才能继续固定源码审计：

1. 用户提供可访问的仓库地址、分支或镜像；
2. 用户提供候选仓库归档包及完整 Git SHA；
3. GitHub 仓库恢复公开/可访问后重新执行固定 SHA 下载。

在恢复前不下载替代版本、不依据提交标题迁移代码、不安装 opentdx/openkph 依赖、不部署或重启服务。

## 2026-10-18 复核补充

前一次将主仓库误判为 404，现已更正。2026-10-18 重新验证结果：

- `https://github.com/LisonEvf/tickflow-stock-panel` 网页访问 HTTP 200；
- GitHub REST 仓库 API HTTP 200；
- `git ls-remote` 可读取 `main`，当前 HEAD 为 `969a2b370b866d9527df3f6c5f154d749870f95d`；
- Git smart fetch 在本机仍出现间歇性 `Repository not found`，改用 GitHub 官方 commit archive 成功获取源码。

已固定官方归档：

```text
/tmp/tick-stock-panel-fork-migration/opentdx-lisonevf-20261017/archive/873c2ce9d301f444edfefc3a1144724e9f4f0a47.tar.gz
/tmp/tick-stock-panel-fork-migration/opentdx-lisonevf-20261017/archive/969a2b370b866d9527df3f6c5f154d749870f95d.tar.gz
```

候选 `873c2ce9...` 的源码主体已核对，确认：

- `OpenTDXProvider` 声明 `instruments`、`daily`、`minute`、`realtime`，不声明 `adj_factor` 和 `financial`；
- 日 K和分钟 K均使用 `ADJUST.NONE`，本地按时间范围过滤；
- 实时行情调用 `stock_quotes` / `stock_quotes_list`；
- 五档通过 `stock_quotes_detail(...).handicap` 暴露；
- `backend/app/tickflow/client.py` 用 `TdxClient()` 替换原 TickFlow client，`current_mode()` 固定返回 `opentdx`，`current_endpoint()` 返回 `opentdx://local`；
- `backend/app/tickflow/policy.py` 固定声明 OpenTDX 的行情、K 线、分时、五档和财务能力；
- `backend/app/services/openkph_service.py` 通过 `openkph` 提供涨停梯队等附加能力；
- 候选没有采用当前目标的独立 Provider 按能力路由模式，而是大范围替换全局 client/policy/service 调用链。

## 当前审计状态

主仓库主体审计已完成，但完整审计仍为**阻塞/待补证**，原因是 `.gitmodules` 指向：

```text
https://github.com/qunyou-agent/opentdx.git
https://github.com/qunyou-agent/openkph.git
```

截至 2026-10-18，这两个子模块地址的 GitHub REST/API 和官方归档请求均返回 HTTP 404，无法固定子模块 commit、核对 TDX 主站地址与握手实现、复核 `openkph` 数据来源以及许可证。候选仓库主提交的 Git archive 只包含子模块指针，不包含子模块源码。

因此当前结论是：**opentdx 是真正独立的免费 TDX 数据源候选，值得继续审计；暂不允许迁移或安装依赖。** 继续审计需要获得两个子模块的可访问地址/归档，或用户明确接受仅依据主仓库 Provider 做风险评估（不进入实施）。


## 2026-10-18：luhuiyuan/opentdx 源码补证与最小实施

已通过 Git 读取并固定：

```text
仓库：luhuiyuan/opentdx
分支：main
commit：4e90696afac1d5f8094624fe559887924d5c0437
版本：0.2.4
许可证：MIT
临时快照：/tmp/tick-stock-panel-fork-migration/luhuiyuan-opentdx-20261018-review/repo/
```

该源码已复制到目标仓库的 `backend/opentdx/` 作为受控 vendoring，仅保留本地 `typing.override` Python 3.11 兼容改动；未包含 `openkph`。

目标侧已新增独立 `OpenTDXProvider`，声明并实现：

- instruments
- daily（日 K，`ADJUST.NONE`）
- minute（按 `1m/5m/15m/30m/60m` 映射）
- realtime
- depth5

未声明：

- adj_factor
- financial
- auction
- full_minute
- openkph 涨停/板块附加能力

自动验证通过：

```text
backend/tests/test_opentdx_provider.py：3 passed
固定源码 compileall：通过
目标文件 py_compile：通过
git diff --check：通过
```

真实单股日 K 探测使用项目 `uv` 环境执行，TDX 主站连接出现 `Connection reset by peer`，Provider 返回空表；因此目前只能确认代码接入和失败隔离，不能确认该海外服务器上的真实 TDX 数据可用性。
