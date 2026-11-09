"""客观价格结构评测用的 vendored 依赖（零新增 pip 依赖）。

- ``ruptures_lite``：逐字移植自 deepcharles/ruptures (BSD-2) 的纯 Python 子集，
  只保留离线最优分割所需的 Pelt / Dynp / CostL2；不含依赖 C 扩展的
  KernelCPD / L1Potts，也不含绘图与数据集模块。
- ``tcpd_metrics``：逐字移植自 TCPDBench 评测标尺（MIT），零第三方依赖。

详见同目录 SOURCE.md 与 LICENSE.ruptures / LICENSE.tcpd。
"""
from .ruptures_lite import Dynp, Pelt
from . import tcpd_metrics

__all__ = ["Pelt", "Dynp", "tcpd_metrics"]
