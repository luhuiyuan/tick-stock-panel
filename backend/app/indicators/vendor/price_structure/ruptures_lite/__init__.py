"""ruptures 纯 Python 子集（vendored）。

上游 ``__init__.py`` 还导出 datasets / show / KernelCPD / L1Potts / Window /
BottomUp / Binseg / version，这些依赖 C 扩展、matplotlib 或 setuptools_scm，
本子集全部裁掉，只保留离线最优分割需要的两个估计器与异常类。
其余 7 个 .py 文件的内容逐字保留，仅把 ``from ruptures.X`` 改为包内相对导入。
"""
from .exceptions import BadSegmentationParameters, NotEnoughPoints
from .detection import Dynp, Pelt

__all__ = ["Pelt", "Dynp", "NotEnoughPoints", "BadSegmentationParameters"]
