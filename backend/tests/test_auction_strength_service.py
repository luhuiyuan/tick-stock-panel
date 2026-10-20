"""GF-000617-A: 竞价强弱与开盘量能字段契约测试。"""
from __future__ import annotations

import polars as pl
import pytest

from app.services.auction_strength_service import AuctionStrengthService


def test_gap_and_volume_boundaries_and_signals() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D", "E", "F", "G"],
            "open": [10.6, 10.4, 10.2, 10.0, 9.8, 9.6, 9.4],
            "prev_close": [10.0] * 7,
            "auction_vol_ratio": [0.012, 0.03, 0.05, 0.08, 0.10, 0.07, 0.001],
        }
    )
    result = AuctionStrengthService.inject_volume_fields(
        AuctionStrengthService.inject_gap_fields(frame)
    )

    assert result["gap_type"].to_list() == [
        "大高开", "小高开", "高开", "平开", "低开", "小低开", "大低开"
    ]
    assert result["volume_type"].to_list() == [
        "放量", "巨量", "巨量", "巨量", "巨量", "巨量", "极致缩量"
    ]
    assert result["signal_auction_safe_high_open"].to_list()[0] is True
    assert result["signal_auction_distribution_risk"].to_list()[0] is False


def test_missing_auction_ratio_degrades_without_fabrication() -> None:
    frame = pl.DataFrame(
        {"symbol": ["A"], "open": [10.6], "prev_close": [10.0]}
    )
    result = AuctionStrengthService.inject_gap_fields(frame)
    result = result.with_columns(pl.lit(None, dtype=pl.Float64).alias("auction_vol_ratio"))
    result = AuctionStrengthService.inject_volume_fields(result)

    assert result["gap_pct"].item() == pytest.approx(0.06)
    assert result["gap_type"].item() == "大高开"
    assert result["volume_type"].item() is None
    assert result["signal_auction_safe_high_open"].item() is False
