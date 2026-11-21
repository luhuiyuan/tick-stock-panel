"""普通日线信号的可选严格数据契约；不取数、不联网、不改变旧定义语义。"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

import polars as pl

MARKET_INDEX = "__signal_market_day_index"
RAW_FIELDS = frozenset({"open", "high", "low", "close", "amount"})
EVENT_FIELDS = frozenset({"strict_close_limit", "strict_broken_limit", "strict_limit_streak"})
MARKET_FIELDS = frozenset(
    {
        "strict_market_broken",
        "strict_market_limits",
        "strict_market_seal",
        "strict_market_cold",
        "strict_market_weak",
    }
)
STRICT_FIELDS = RAW_FIELDS | EVENT_FIELDS | MARKET_FIELDS
_BINARY_FIELDS = frozenset(
    {"strict_close_limit", "strict_broken_limit", "strict_market_cold", "strict_market_weak"}
)
_COUNT_FIELDS = frozenset({"strict_limit_streak", "strict_market_broken", "strict_market_limits"})


def validate_contract(sig: dict) -> None:
    contract = sig.get("data_contract")
    if contract is None:
        return
    if (
        not isinstance(contract, dict)
        or not {"version", "inputs", "ohlc_window"} <= set(contract)
        or set(contract) - {"version", "inputs", "ohlc_window", "offsets"}
    ):
        raise ValueError("data_contract 必须包含 version / inputs / ohlc_window")
    if (
        type(contract["version"]) is not int
        or contract["version"] not in (1, 2)
        or sig.get("timeframe", "daily") != "daily"
    ):
        raise ValueError("严格数据契约仅支持 version=1/2 的日线信号")
    inputs = contract["inputs"]
    if (
        not isinstance(inputs, dict)
        or not inputs
        or not set(inputs) <= (RAW_FIELDS if contract["version"] == 1 else STRICT_FIELDS)
    ):
        raise ValueError("严格数据契约 inputs 必须是价格/成交额窗口")
    if any(type(n) is not int or not 1 <= n <= 512 for n in inputs.values()):
        raise ValueError("严格数据契约窗口必须是 1..512 整数")
    offsets = contract.get("offsets", {})
    if ("offsets" in contract and contract["version"] != 2) or not isinstance(offsets, dict):
        raise ValueError("offsets 仅支持v2字段偏移")
    if not set(offsets) <= set(inputs) or any(
        type(v) is not int or v < 0 or v + inputs[k] > 512 for k, v in offsets.items()
    ):
        raise ValueError("offsets 必须是窗口内合法字段偏移")
    if any(offsets.get(f, 0) for f in ("open", "high", "low", "close")):
        raise ValueError("OHLC字段必须从当前日开始；事件字段可指定偏移")
    ohlc = contract["ohlc_window"]
    if type(ohlc) is not int or not 0 <= ohlc <= 512:
        raise ValueError("ohlc_window 必须是 0..512 整数")
    if ohlc and any(inputs.get(f, 0) < ohlc for f in ("open", "high", "low", "close")):
        raise ValueError("OHLC 校验必须声明完整价格窗口")


def _all_window(expr: pl.Expr, n: int) -> pl.Expr:
    value = expr.fill_null(False).cast(pl.Int32)
    return value == 1 if n == 1 else value.rolling_sum(n).over("symbol") == n


def validity(contract: dict) -> pl.Expr:
    """共享完整性表达式；v1行为不变，v2增加已归一化事件/市场值。

    窗口完整性按「个股真实 K 线行数」判定: 个股行已按交易日压缩, 停牌日无
    K 线行, 故 _all_window 的滚动 n 行就是该股真实的 n 个交易日。不再要求
    market_index 数值连续(原 == n-1), 因为停牌会让市场索引跳变, 但个股确凿
    拥有连续 n 根有效 K 线, 窗口仍可用(A方案: 按个股真实交易日计数)。
    """
    offsets = contract.get("offsets", {})
    n = max(v + offsets.get(k, 0) for k, v in contract["inputs"].items())
    index = pl.col(MARKET_INDEX)
    valid = _all_window(index.is_not_null(), n)
    # A方案: 删除 market_index 数值连续性检查(原 (index - index.shift(n-1)) == n-1)。
    # 停牌个股缺 K 线导致市场索引跳变(如 112 != 99), 旧逻辑据此判窗口无效 ->
    # 长窗口因子 null -> 信号 unknown。现仅按个股连续 n 行 K 线判定(上一行),
    # 停牌股用实际已有的交易日算窗口。index 仍用于确认每行是合法交易日(非重复)。
    for field, window in contract["inputs"].items():
        c = pl.col(field)
        if field in _BINARY_FIELDS:
            good = c.is_finite() & c.is_in([0, 1])
        elif field in _COUNT_FIELDS:
            good = c.is_finite() & (c >= 0) & (c == c.floor())
        elif field == "strict_market_seal":
            good = c.is_finite() & c.is_between(0, 1)
        else:
            good = c.is_finite() & (c >= 0 if field == "amount" else c > 0)
        if offsets.get(field, 0):
            shifted = good.fill_null(False).cast(pl.Int32).shift(offsets[field])
            complete = shifted if window == 1 else shifted.rolling_sum(window, min_samples=window)
            valid &= complete.over("symbol") == window
        else:
            valid &= _all_window(good, window)
    ohlc = contract["ohlc_window"]
    if ohlc:
        valid &= _all_window(
            (pl.col("high") >= pl.max_horizontal("open", "close"))
            & (pl.col("low") <= pl.min_horizontal("open", "close"))
            & (pl.col("high") >= pl.col("low")),
            ohlc,
        )
    return valid.fill_null(False)


def guard(sig: dict, combined: pl.Expr, operands: list[pl.Expr]) -> pl.Expr:
    """完整性优先于条件 AND：缺失与 false 同时出现也必须返回未知。"""
    contract = sig.get("data_contract")
    if contract is None:
        return combined
    validate_contract(sig)
    valid = validity(contract)
    for operand in operands:
        valid &= operand.is_finite()
    return pl.when(valid.fill_null(False)).then(combined).otherwise(pl.lit(None, dtype=pl.Boolean))


def attach_calendar(df: pl.DataFrame, trading_dates: Collection[date] | None) -> pl.DataFrame:
    """附加市场交易日序号；不补个股行，缺口由 guard 判定。

    调用者提供完整、权威的交易日集合。超出日历首尾、非交易日、重复记录
    均为未知。拒绝乱序输入，避免对已经算错的滚动指标再排序掩盖错误。
    """
    if not {"symbol", "date"} <= set(df.columns):
        return df.with_columns(pl.lit(None, dtype=pl.Int64).alias(MARKET_INDEX))
    keys = df.select("symbol", "date")
    if not keys.equals(keys.sort(["symbol", "date"])):
        raise ValueError("严格信号要求输入按 symbol/date 排序")
    dates = sorted(set(trading_dates or []))
    calendar = pl.DataFrame(
        {"date": dates, MARKET_INDEX: range(len(dates))},
        schema={"date": pl.Date, MARKET_INDEX: pl.Int64},
    )
    # 内部标记每次重新生成，不信任旧帧或调用者残留的索引。
    out = df.drop(MARKET_INDEX, strict=False).join(
        calendar, on="date", how="left", maintain_order="left"
    )
    return out.with_columns(
        pl.when(pl.struct("symbol", "date").is_duplicated())
        .then(None)
        .otherwise(pl.col(MARKET_INDEX))
        .alias(MARKET_INDEX)
    )


def coverage(frame: pl.DataFrame, columns: Collection[str]) -> dict[str, dict[str, int]]:
    """保留全股票-日期样本；只有 true 可以入池，未知不算未命中。"""
    result = {}
    for column in columns:
        values = (
            frame[column]
            if column in frame.columns
            else pl.Series([None] * frame.height, dtype=pl.Boolean)
        )
        true = int(values.fill_null(False).sum())
        unknown = values.null_count()
        result[column] = {"true": true, "false": frame.height - true - unknown, "unknown": unknown}
    return result
