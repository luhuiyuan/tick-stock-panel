"""项目日线数据 -> vendored hot_money_radar 的 ``bars`` 适配层。

这是唯一允许把本仓库数据结构喂给 vendored 规则的入口。适配层只做
「列映射 + 补预计算字段」, 不含任何形态判定逻辑；判定全部由
``radar_core.match_patterns`` / ``_pattern_phase`` 完成，保持与上游一致。

上游 ``_bar()`` 产出的 bar 字段(见 SOURCE.md)::

    date, open, high, low, close, volume, amount, chg, turnover, _chip_avg_price

其中 ``turnover`` 为换手率百分数(如 3.5 表示 3.5%)。本仓库 enriched 日线
用的是 ``turnover_rate``、``symbol``，这里做映射。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from . import radar_core

__all__ = [
    "rows_to_bars",
    "match_patterns",
    "dominant_phase",
]

# 上游 bar 字段 -> 本仓库日线可能的列名(按优先级尝试)
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "trade_date", "day"),
    "open": ("open", "daily_open"),
    "high": ("high", "daily_high"),
    "low": ("low", "daily_low"),
    "close": ("close", "daily_close"),
    "volume": ("volume", "daily_volume", "vol"),
    "amount": ("amount", "daily_amount", "turnover"),  # 注意: amount 可能与 turnover 同词冲突
    "chg": ("chg", "daily_change_pct", "change_pct", "pct_chg"),
    "turnover": ("turnover_rate", "turnover", "daily_turnover_rate"),  # 上游 turnover = 换手率
}


def _pick(row: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for key in aliases:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN/Inf 会让上游比较运算静默失真，统一视作缺失
    if f != f or f in (float("inf"), float("-inf")):  # noqa: PLR2004
        return None
    return f


def rows_to_bars(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把本仓库日线行(OHLCV + turnover_rate)转成上游 ``bars``。

    ``rows`` 可以是 polars ``DataFrame.iter_rows(named=True)``、
    ``DataFrame.to_dicts()`` 或任何 dict 可迭代对象。日期统一转 ``str``
    以匹配上游口径；``amount`` 与 ``turnover`` 分开映射，避免同词歧义。

    预计算 ``_chip_avg_price``：上游 ``_bar()`` 在入库读取时算一次，
    这里用 vendored 的 ``_bar_avg_price`` 逐字等价地补上。
    """
    bars: list[dict[str, Any]] = []
    for row in rows:
        amount_aliases = ("amount", "daily_amount")
        bar: dict[str, Any] = {
            "date": str(_pick(row, _FIELD_ALIASES["date"])),
            "open": _as_float(_pick(row, _FIELD_ALIASES["open"])),
            "high": _as_float(_pick(row, _FIELD_ALIASES["high"])),
            "low": _as_float(_pick(row, _FIELD_ALIASES["low"])),
            "close": _as_float(_pick(row, _FIELD_ALIASES["close"])),
            "volume": _as_float(_pick(row, _FIELD_ALIASES["volume"])),
            "amount": _as_float(_pick(row, amount_aliases)),
            "chg": _as_float(_pick(row, _FIELD_ALIASES["chg"])),
            "turnover": _as_float(_pick(row, _FIELD_ALIASES["turnover"])),
        }
        bar["_chip_avg_price"] = radar_core._bar_avg_price(bar)
        bars.append(bar)
    return bars


def match_patterns(
    code: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    p1_market_gate: bool = False,
) -> list[dict[str, str]]:
    """高层入口：项目日线行 -> 形态命中列表(PIT 安全，与上游逐字一致)。

    ``p1_market_gate`` 默认 ``False``：P1 的大盘门控依赖上游 510310 指数
    净值数据，本仓库暂不提供，故关闭。
    """
    bars = rows_to_bars(rows)
    return radar_core.match_patterns(code, bars, p1_market_gate=p1_market_gate)


def dominant_phase(
    code: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    p1_market_gate: bool = False,
) -> dict[str, Any]:
    """在 ``match_patterns`` 之上给出主导阶段，供展示层复用。

    返回 ``{"fired": [...], "phase": str}``。阶段口径完全沿用上游
    ``_pattern_phase``(纯形态驱动、优先级：出货预警 > 突破 > 买入区 >
    拉升 > 试盘)，适配层不重新定义。

    注意：上游另有 ``_phase_confidence`` 需要潜伏分/出货分作入参，那部分
    属于本仓库未移植的评分体系，故此处不提供 confidence，避免伪造口径。
    """
    fired = match_patterns(code, rows, p1_market_gate=p1_market_gate)
    phase = radar_core._pattern_phase(fired)
    return {"fired": fired, "phase": phase}
