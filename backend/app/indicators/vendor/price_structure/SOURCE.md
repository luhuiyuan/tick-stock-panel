# price_structure —— vendored 来源与移植说明

本目录为「客观价格结构基准 + 实时算法评测」提供离线分割引擎与评测标尺。
**运行时零新增 pip 依赖**：只用项目 venv 已有的 `numpy` / `typing_extensions`
与标准库；不 `pip install ruptures`，也不引入 torch（在线 BOCPD 依赖它）。

## 1. 来源

| 组件 | 上游仓库 | commit | 许可证 | 本地文件 |
|---|---|---|---|---|
| `ruptures_lite/` | [deepcharles/ruptures](https://github.com/deepcharles/ruptures) | `ee1c8ff`（tarball 下载，SHA 记于 `/workspace/reference/ruptures/.source_sha`） | BSD-2-Clause（`LICENSE.ruptures`，ENS Paris-Saclay / CNRS） | 7 个 .py |
| `tcpd_metrics.py` | [hildensia/bayesian_changepoint_detection](https://github.com/hildensia/bayesian_changepoint_detection) `benchmarks/metrics.py` | `4bc68be` | MIT（`LICENSE.tcpd`，Johannes Kulick） | 146 行 |
| `tcpd_metrics.py` 上游算法出处 | [alan-turing-institute/TCPDBench](https://github.com/alan-turing-institute/TCPDBench) `analysis/scripts/metrics.py` | — | MIT（The Alan Turing Institute） | 见文件头 |

两个上游仓库的完整源码已下载在 `/workspace/reference/{ruptures,bayesian_changepoint_detection}`，
本目录只搬运行所必需的最小子集。

## 2. 移植方法（可复现）

7 个 `.py` 文件先用 `cp` **逐字复制**上游 `src/ruptures/` 对应文件，随后只做
**机械的 import 路径改写**（把绝对包前缀 `ruptures.` 改成包内相对导入），
代码主体、注释、docstring、算法逻辑一行未改。逐文件改动清单：

| 文件 | 改写行数 | 改写内容 |
|---|---|---|
| `ruptures_lite/base.py` | 1 | `from ruptures.utils import pairwise` → `from .utils import pairwise` |
| `ruptures_lite/exceptions.py` | 0 | 原样 |
| `ruptures_lite/utils/utils.py` | 0 | 原样 |
| `ruptures_lite/costs/factory.py` | 1 | `from ruptures.base import BaseCost` → `from ..base import BaseCost` |
| `ruptures_lite/costs/costl2.py` | 2 | `from ruptures.costs import NotEnoughPoints` → `from . import NotEnoughPoints`；`from ruptures.base import BaseCost` → `from ..base import BaseCost` |
| `ruptures_lite/detection/pelt.py` | 4 | costs / base / exceptions / utils 四行改相对导入 |
| `ruptures_lite/detection/dynp.py` | 4 | 同上 |

`tcpd_metrics.py` 是 `benchmarks/metrics.py` 的**完整逐字副本**（含原有 doctest），
零改动、零第三方 import。

5 个 `__init__.py` 是本目录**新写的精简导出**（上游同名文件是完整包导出），
不是逐字移植，已在各自 docstring 里注明裁掉了什么。裁剪理由：

- `detection/`：只留 `Pelt` / `Dynp`（离线最优分割）；Binseg / BottomUp /
  Window / KernelCPD / L1Potts 裁掉（后两者依赖 C 扩展）。
- `costs/`：只留 `CostL2` + `cost_factory`。`cost_factory` 用
  `BaseCost.__subclasses__()` 查找，导入 `CostL2` 即可工作。
- `utils/`：只留 `pairwise` / `sanity_check` / `unzip`；裁掉 `Bnode`、
  `draw_bkps`（matplotlib）、`from_path_matrix_to_bkps_list`。
- 包根：裁掉 `datasets`、`show`、`version`（`setuptools_scm` 生成）与
  `KernelCPD` / `L1Potts` 等。

## 3. 数据契约

- `Pelt(model="l2", min_size=15, jump=1).fit(signal).predict(pen=...)`：
  `signal` 形状 `(n_samples, n_features)` 或 `(n_samples,)`，本项目传
  `log(close).reshape(-1, 1)`；返回**含末端 `n_samples`** 的升序变点列表，
  业务层须自行剔除末端。
- `Dynp(model="l2", ...).fit(signal).predict(n_bkps=k)`：同上，用于交叉验证
  与固定段数对照。
- `tcpd_metrics.f_measure(annotations, predictions, margin=5)`：
  `annotations` 是 `{标注者id: [变点索引]}`，索引 0-based 且函数内部会
  把 0 视为所有分割的公共变点；`predictions` 是变点索引列表。返回 F1，
  `return_PR=True` 时返回 `(F, P, R)`。
- `tcpd_metrics.covering(annotations, predictions, n_obs)`：分段覆盖度。
- 外部依赖：`numpy.typing.NDArray`、`typing_extensions.Self`（venv 已具备）。

## 4. 不搬清单（明确边界）

- 不搬在线 BOCPD 检测器（依赖 `torch>=2`，O(T²)），只借其评测方法论；
- 不搬 `ruptures.show` / `datasets`（绘图与合成数据）；
- 不搬 `QuhiQuhihi/regime_model` 的任何代码（无 LICENSE，只参考思路）；
- 不改动本项目任何既有 `indicators/` 模块；
- 不安装 `ruptures` 到项目 venv；官方 wheel 只放在临时区做离线一致性对照。

## 5. 一致性验证

`backend/tests/test_structure_eval.py` 断言：本 vendor 子集与官方
`ruptures` wheel 在固定合成信号上输出**完全相同**的变点（golden 值由官方
wheel 预先生成并写死在测试里，避免测试依赖临时目录），并放行
`tcpd_metrics` 的原有 doctest 与已知值断言。
