"""Vendored 主力/游资形态规则核心 (sorahjy/financial-analysis).

``radar_core`` 是从上游 ``stock_hot_money_radar.py`` 逐字提取的纯规则子集:
形态匹配 P1-P26、阶段汇总 ``_pattern_phase``、三角筹码分布与量价/位置辅助函数。
不含上游的 sqlite 数据层、实时行情、CLI 与回测入口。

调用方应使用 ``adapter`` 把项目日线数据转成上游要求的 ``bars`` 结构,
再调用 ``match_patterns``; 不要把 vendored 模块直接暴露给 API 层。
"""
from . import radar_core

__all__ = ["radar_core"]
