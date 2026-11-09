"""上游 ``detection/__init__.py`` 还导出 Binseg / BottomUp / KernelCPD /
L1Potts / Window，本子集只保留离线最优分割用的 Pelt 与 Dynp。
"""
from .pelt import Pelt
from .dynp import Dynp

__all__ = ["Pelt", "Dynp"]
