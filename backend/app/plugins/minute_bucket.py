"""分钟 K 线的交易时段分桶与重采样(纯函数, 跨 provider 共用)。

A 股连续竞价分上午 09:30~11:30、下午 13:00~15:00 两段, 分钟 K 的 N 分钟分桶必须
在这两段内**各自独立** —— 按自然时间连续切会让 13:01 落进 11:35 之后的空桶。

官方分钟 K 的时间戳标的是区间**结束**(09:32 那根覆盖 09:31:00~09:31:59), 所以
聚合 1m → N 分钟时要 +1 分钟再归桶。分桶标签同样取桶的右端点: 5 分钟在上午是
09:35..11:30、下午 13:05..15:00, 各 24 根, 合计 48 根/日。

本模块只依赖 polars, 不依赖任何具体数据源; qatdx 与 eltdx 都从这里取用, 避免两处
各写一份分桶规则后在边界(午休、收盘竞价)上产生口径分歧。
"""

from __future__ import annotations

from datetime import date, datetime, time

import polars as pl

# 两个连续竞价时段, 单位「当日分钟数」: 09:30-11:30 / 13:00-15:00
CN_SESSIONS: tuple[tuple[int, int], ...] = ((570, 690), (780, 900))

FREQ_STEP: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}


def session_bucket(stamp: datetime, step: int) -> datetime | None:
    """把 1 分钟时间戳归到所属 N 分钟 K 的**结束**标签。

    ``stamp`` 落在交易时段外(集合竞价、午休、盘后)时返回 None, 由调用方丢弃。
    """
    minutes = stamp.hour * 60 + stamp.minute
    for head, tail in CN_SESSIONS:
        if head < minutes <= tail:
            offset = minutes - head
            end = head + ((offset + step - 1) // step) * step
            return datetime.combine(stamp.date(), time(end // 60, end % 60))
    return None


def session_minutes(day: date) -> list[datetime]:
    """一个交易日的 240 个分钟 K 时间戳: 09:31..11:30, 13:01..15:00。"""
    return [
        datetime.combine(day, time(minute // 60, minute % 60))
        for head, tail in CN_SESSIONS
        for minute in range(head + 1, tail + 1)
    ]


def resample_minute(
    frame: pl.DataFrame,
    step: int,
    *,
    time_col: str = "dt",
    symbol_col: str = "symbol",
) -> pl.DataFrame:
    """1 分钟帧 → N 分钟帧(按交易时段真实分钟数分桶)。

    ``time_col`` 必须是 naive 北京墙钟(两个 provider 的契约都是 naive)。返回列集与
    顺序不变, 桶标签写回 ``time_col``。``step <= 1`` 或空帧原样返回。
    """
    if step <= 1 or frame.height == 0:
        return frame
    buckets = [session_bucket(stamp, step) for stamp in frame[time_col].to_list()]
    labeled = frame.with_columns(
        pl.Series("__bucket", buckets, dtype=frame.schema[time_col])
    ).drop_nulls("__bucket")
    if labeled.height == 0:
        return frame.clear()
    return (
        labeled.group_by("__bucket", maintain_order=True)
        .agg(
            pl.col(symbol_col).first(),
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
            pl.col("amount").sum(),
        )
        .sort("__bucket")
        .rename({"__bucket": time_col})
        .select(frame.columns)
    )
