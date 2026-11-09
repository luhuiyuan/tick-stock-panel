"""上游 ``costs/__init__.py`` 还导出 CostL1 / CostLinear / CostCLinear /
CostRbf / CostNormal / CostAR / CostMl / CostRank / CostCosine，本子集只做
``model="l2"`` 的离线分割，全部裁掉（``cost_factory`` 通过
``BaseCost.__subclasses__()`` 查找，只需导入 CostL2 即可工作）。

导入顺序与上游一致：先 NotEnoughPoints，再 factory，最后 CostL2，
以避免 costl2.py 里 ``from . import NotEnoughPoints`` 的循环导入。
"""
from ..exceptions import NotEnoughPoints
from .factory import cost_factory
from .costl2 import CostL2

__all__ = ["NotEnoughPoints", "cost_factory", "CostL2"]
