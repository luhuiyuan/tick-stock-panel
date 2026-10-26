"""日线三重背离：规则对照 cn_stock_scan，扫描不得使用未来 K 线。"""

from __future__ import annotations

import numpy as np

from app.backtest.macd_triple_divergence import (
    Wave,
    check_divergence_rules,
    detect_triple_divergence,
    scan_both,
    scan_frame,
)
from app.strategy.ai_generator import AIStrategyGenerator
from app.strategy.seeds.macd_triple_bottom import MATRIX_STRATEGY as BOTTOM
from app.strategy.seeds.macd_triple_top import META as TOP_META
from app.strategy.seeds.macd_triple_top import MATRIX_STRATEGY as TOP


def _wave(start, end, cross, extreme_idx, price, area) -> Wave:
    return Wave("down", start, end, cross, extreme_idx, price, area)


def _rules(direction, w1, w2, w3, dif, bars_since=3, **overrides):
    params = {
        "min_area_reduction": 0.10,
        "dif_zero_tolerance": 0.0,
        "dif_approach_zero_ratio": 0.50,
        "min_price_increase_pct": 0.001,
        "recency_bars": 30,
    }
    params.update(overrides)
    return check_divergence_rules(
        w1, w2, w3, dif,
        direction=direction,
        bars_since_last_peak=bars_since,
        **params,
    )


def test_bottom_rules_pass_only_when_all_five_hold():
    dif = np.full(30, -1.0)
    dif[4:9] = -0.4
    dif[12:17] = -0.2
    w1 = _wave(0, 4, -1.0, 2, 10.0, 30.0)
    w2 = _wave(8, 12, -0.6, 10, 9.5, 20.0)
    w3 = _wave(16, 22, -0.2, 20, 9.0, 12.0)
    checks, strength = _rules("bottom", w1, w2, w3, dif)
    assert [item.passed for item in checks] == [True, True, True, True, True]
    assert [item.code for item in checks] == ["R1", "R2", "R3", "R4", "R5"]
    assert checks[0].detail.startswith("10.00→9.50→9.00")
    assert 0.0 < strength <= 1.0

    loose_w3 = _wave(16, 22, -0.2, 20, 9.0, 19.5)
    checks, _ = _rules("bottom", w1, w2, loose_w3, dif)
    assert [item.code for item in checks if not item.passed] == ["R4"]
    assert "衰减不足" in checks[3].detail

    broken = _wave(16, 22, -0.7, 20, 9.0, 19.5)
    checks, _ = _rules("bottom", w1, w2, broken, dif)
    assert {item.code for item in checks if not item.passed} == {"R2", "R4"}


def test_top_rules_reject_a_pullback_through_zero():
    dif = np.full(30, 1.0)
    dif[4:9] = 0.4
    dif[12:17] = -0.05
    w1 = _wave(0, 4, 1.0, 2, 10.0, 30.0)
    w2 = _wave(8, 12, 0.6, 10, 11.0, 20.0)
    w3 = _wave(16, 22, 0.2, 20, 12.0, 12.0)
    checks, _ = _rules("top", w1, w2, w3, dif)
    assert [item.passed for item in checks] == [True, True, False, True, True]
    assert "破零" in checks[2].detail


def test_scan_matches_prefix_detector_and_ignores_the_future():
    indexes = np.arange(140)
    series = [
        20.0 + np.sin(indexes / 3.0) + 0.01 * indexes,
        30.0 + np.cumsum(np.sin(indexes / 5.0)),
        15.0 + np.sin(indexes / 2.5) * (1.0 + indexes / 80.0),
    ]
    for close in series:
        high = close + 0.4
        low = close - 0.4
        for allow_loose in (False, True):
            params = {"allow_loose": allow_loose}
            bottom, top, b_score, t_score = scan_both(close, high, low, params)
            short_b, short_t, _, _ = scan_both(close[:100], high[:100], low[:100], params)
            assert np.array_equal(bottom[:100], short_b)
            assert np.array_equal(top[:100], short_t)
            for end in range(60, 140, 13):
                for direction, hits, scores in (
                    ("bottom", bottom, b_score),
                    ("top", top, t_score),
                ):
                    result = detect_triple_divergence(
                        close[:end], high[:end], low[:end],
                        direction=direction, params=params,
                    )
                    expected = result.hit or (allow_loose and result.hit_kind == "loose")
                    assert bool(hits[end - 1]) is bool(expected)
                    if expected:
                        assert abs(float(scores[end - 1]) - result.strength) < 1e-5


def test_panel_scan_skips_missing_bars_and_strategies_read_only_their_side():
    close = np.array([
        [10, np.nan, 10],
        [10.2, 9.0, np.nan],
        [10.1, 9.1, 10],
        [9.8, 8.7, 10.4],
    ], dtype=np.float64)
    # 太短不会出信号，但形状和缺口位置必须保持。
    high = np.where(np.isfinite(close), close + 0.2, np.nan)
    low = np.where(np.isfinite(close), close - 0.2, np.nan)
    bottom, top, b_score, t_score = scan_frame(close, high, low, {})
    assert bottom.shape == close.shape
    assert int(bottom.sum() + top.sum()) == 0
    assert b_score[1, 2] == 0.0
    assert np.isfinite(t_score).all()

    long_index = np.arange(80)
    long_close = 20.0 + np.sin(long_index / 3.0)
    panel_close = np.column_stack([long_close, long_close + 1.0])
    panel_high = panel_close + 0.3
    panel_low = panel_close - 0.3
    market = type("Market", (), {})()
    market.close = panel_close
    market.high = panel_high
    market.low = panel_low
    market.shape = panel_close.shape
    bottom_signals = BOTTOM.compute_signals(market, {"allow_loose": True})
    top_signals = TOP.compute_signals(market, {"allow_loose": True})
    assert bottom_signals.entry.shape == panel_close.shape
    assert top_signals.exit.shape == panel_close.shape
    assert int(top_signals.exit.sum()) == 0
    assert np.array_equal(bottom_signals.exit, top_signals.entry)
    assert TOP_META["backtest_block_reason"]
    assert "买入" in TOP_META["backtest_block_reason"]
    for name in ("macd_triple_bottom.py", "macd_triple_top.py"):
        code = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "app/strategy/seeds" / name
        ).read_text(encoding="utf-8")
        AIStrategyGenerator._validate_safety(code)


def test_screen_warmup_fits_memory_cache_and_last_bar_matches_full_scan():
    from app.backtest.macd_triple_divergence import SCREEN_WARMUP_BARS

    assert SCREEN_WARMUP_BARS == 150
    assert BOTTOM.required_warmup_bars({}) == SCREEN_WARMUP_BARS
    assert TOP.required_warmup_bars({}) == SCREEN_WARMUP_BARS
    assert BOTTOM.screen_last_bar_only is True
    assert TOP.screen_last_bar_only is True

    indexes = np.arange(140)
    close = 20.0 + np.sin(indexes / 3.0) + 0.01 * indexes
    high = close + 0.4
    low = close - 0.4
    params = {"allow_loose": True, "last_bar_only": True}
    bottom, top, b_score, t_score = scan_both(close, high, low, params)
    full_b, full_t, full_bs, full_ts = scan_both(close, high, low, {"allow_loose": True})
    assert int(bottom[:-1].sum() + top[:-1].sum()) == 0
    assert np.all(b_score[:-1] == 0)
    assert np.all(t_score[:-1] == 0)
    assert bool(bottom[-1]) is bool(full_b[-1])
    assert bool(top[-1]) is bool(full_t[-1])
    assert abs(float(b_score[-1]) - float(full_bs[-1])) < 1e-5
    assert abs(float(t_score[-1]) - float(full_ts[-1])) < 1e-5

    panel = np.column_stack([close, close + 1.0])
    panel_b, panel_t, _, _ = scan_frame(panel, panel + 0.3, panel - 0.3, params)
    assert int(panel_b[:-1].sum() + panel_t[:-1].sum()) == 0
    assert bool(panel_b[-1, 0]) is bool(full_b[-1])
    assert bool(panel_t[-1, 1]) is bool(full_t[-1])
