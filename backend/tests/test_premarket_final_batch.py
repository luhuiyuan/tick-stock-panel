"""集中收尾：独立参考、三态、窗口及新数据契约，不读取生产配置。"""

import math
import random
from datetime import date, timedelta

import polars as pl
import pytest

from app.factors import registry
from app.factors.premarket import FACTORS
from app.factors.store import register_definition
from app.strategy import custom_signals as cs
from app.strategy.premarket_signal_definitions import FINAL_SIGNALS
from app.strategy.strict_signal_data import attach_calendar, validate_contract

BY_ID = {s["id"]: s for s in FINAL_SIGNALS}


@pytest.fixture(autouse=True)
def factors():
    with registry.isolated_registry():
        for f in FACTORS:
            register_definition(f)
        yield


def frame(seed=0, n=130):
    rng = random.Random(seed)
    rows = []
    day = date(2025, 1, 2)
    for i in range(n):
        while day.weekday() > 4:
            day += timedelta(days=1)
        c, o = 90 + rng.random() * 12, 90 + rng.random() * 12
        sealed = float(i % 13 == 0)
        rows.append(
            dict(
                symbol="X",
                date=day,
                close=c,
                open=o,
                high=max(c, o) + 1 + rng.random() * 3,
                low=min(c, o) - 1 - rng.random() * 3,
                amount=50 + rng.random() * 180,
                strict_close_limit=sealed,
                strict_limit_streak=sealed,
                strict_broken_limit=float(i % 7 == 0 and not sealed),
                strict_market_limits=120.0,
                strict_market_broken=30.0,
                strict_market_seal=0.8,
                strict_market_cold=1.0,
                strict_market_weak=1.0,
            )
        )
        day += timedelta(days=1)
    return pl.DataFrame(rows)


def evaluate(df, signals=None, days=None):
    exprs = cs.build_expressions(FINAL_SIGNALS if signals is None else signals)
    df = cs.materialize_factor_columns(df, exprs, trading_dates=days or sorted(set(df["date"])))
    return cs.inject(df, exprs)


def reference(df):
    """只用原始行计算最后一天，与生产因子公式/注册表实现独立。"""
    rows = list(reversed(df.to_dicts()))
    close, open_, high, low, amount, sealed, streak, broken = (
        [r[f] for r in rows]
        for f in (
            "close",
            "open",
            "high",
            "low",
            "amount",
            "strict_close_limit",
            "strict_limit_streak",
            "strict_broken_limit",
        )
    )

    def mean(x, a, b):
        return sum(x[a : b + 1]) / (b - a + 1)

    def hi(x, a, b):
        return max(x[a : b + 1])

    def lo(x, a, b):
        return min(x[a : b + 1])

    def count(a, b):
        return sum(sealed[a : b + 1])

    def peak(a, b):
        return max([close[k] for k in range(a, b + 1) if sealed[k] == 1], default=0)

    def upper(k):
        return (high[k] - max(open_[k], close[k])) / (2 * abs(close[k] - open_[k])) if close[k] != open_[k] else 0

    def lower(k):
        return (min(open_[k], close[k]) - low[k]) / (2 * abs(close[k] - open_[k])) if close[k] != open_[k] else 0

    ret5 = close[1] / close[6] - 1
    amt = amount[0] / mean(amount, 1, 5)
    dd = 1 - close[0] / hi(high, 0, 59)
    tau = 8 * 2.220446049250313e-16
    band = 0 if dd < 0.0598 - tau else 1 if dd < 0.1781 - tau else 2
    sorted_a = sorted(amount[:100])
    q = sorted_a[9] * 0.1 + sorted_a[10] * 0.9
    low_run = any(max(amount[k : k + 5]) <= q for k in range(96))
    old_low_run = any(max(amount[k : k + 5]) <= q for k in range(1, 96))
    g = any(low[k] > high[k + 1] and lo(low, 0, k - 1) > high[k + 1] for k in range(1, 20))
    v = any(amount[k] / amount[k + 1] >= 2 and close[k] > open_[k] and lo(low, 0, k - 1) > low[k] for k in range(1, 20))
    m = any(amount[k] >= hi(amount, 1, 20) and close[k] > open_[k] and lo(low, 0, k - 1) > low[k] for k in range(1, 20))
    w60 = (hi(high, 0, 59) - lo(low, 0, 59)) / close[0]
    w15 = (hi(high, 0, 14) - lo(low, 0, 14)) / lo(low, 0, 14)
    top_hits = sum(x >= hi(high, 0, 14) * 0.99 for x in high[:15])
    bottom_hits = sum(x <= lo(low, 0, 14) * 1.01 for x in low[:15])
    engulf = close[0] > open_[0] and close[1] < open_[1] and open_[0] <= close[1] and close[0] >= open_[1]
    first_break = streak[2] == 1 and streak[1] == 0
    small_fake = abs(close[1] - open_[1]) / close[1] <= 0.01 or (close[1] < open_[1] and close[1] > close[2])
    out = {
        "t_pos_high": band == 0,
        "t_pos_mid": band == 1,
        "t_pos_low": band == 2,
        "t_double_sword": max(min(upper(1), lower(0)), min(lower(1), upper(0))) >= 1,
        "t_diliangqun": low_run,
        "t_aijiangjun": old_low_run and 1 <= amount[0] / amount[1] <= 2 and 1 < close[0] / open_[0] <= 1.03,
        "t_niugu_sanjue": g and v and m,
        "t_range_compress_5_20": (hi(high, 0, 4) - lo(low, 0, 4)) / (hi(high, 0, 19) - lo(low, 0, 19))
        <= 0.379,
        "t_consolidation": w15 / w60 <= 0.509
        and top_hits >= 2
        and bottom_hits >= 2
        and mean(amount, 0, 14) / mean(amount, 15, 59) <= 0.8
        and hi(amount, 0, 14) / mean(amount, 0, 14) <= 2,
        "t_nshape": count(1, 19) >= 1 and close[0] <= 0.95 * peak(1, 19),
        "t_hm_single": count(1, 20) >= 1 and hi(streak, 1, 20) == 1 and close[0] <= 0.95 * peak(1, 20),
        "t_hm_platform": count(1, 20) >= 1
        and (hi(high, 0, 4) - lo(low, 0, 4)) / close[0] <= 0.09
        and mean(amount, 0, 4) / mean(amount, 5, 24) <= 0.9
        and close[0] <= 0.97 * peak(1, 20),
        "t_fanbao_duanban": engulf and first_break,
        "t_fanbao_shizi": engulf and not first_break and small_fake,
        "t_fanbao_broken_limit": engulf
        and not first_break
        and not small_fake
        and broken[1] == 1
        and upper(1) >= 1,
        "t_limit_history_platform_break": count(7, 19) >= 1
        and (hi(high, 1, 5) - lo(low, 1, 5)) / lo(low, 1, 5) <= 0.12
        and close[0] > hi(high, 1, 5)
        and open_[0] / hi(high, 1, 5) < 1.05,
        "t_close_one_word_limit": (high[0] - low[0]) / close[0] < 0.002,
        "t_full_platform": (hi(high, 1, 5) - lo(low, 1, 5)) / close[1] <= 0.08
        and abs(ret5) <= 0.05
        and amt >= 2
        and close[0] > hi(high, 1, 5),
        "t_full_newhigh": close[0] > hi(high, 1, 20)
        and any(high[k] > high[k - 1] and high[k] > high[k + 1] for k in range(2, 20)),
        "t_full_ma_cross": close[0] > open_[0]
        and open_[0] < min(mean(close, 1, j) for j in (5, 10, 20))
        and close[0] > max(mean(close, 1, j) for j in (5, 10, 20)),
        "t_prev_bear_recovery_limit": close[1] < open_[1]
        and close[0] > max(open_[1], close[1])
        and high[0] > high[1]
        and amt >= 1.2,
        "t_full_nshape": count(2, 10) >= 1
        and sealed[1] == 0
        and mean(amount, 1, 5) / mean(amount, 6, 25) <= 0.7
        and close[0] > peak(2, 10),
        "t_box_break": (hi(high, 2, 11) - lo(low, 2, 11)) / lo(low, 2, 11) <= 0.15 and close[0] > hi(high, 2, 11),
        "t_wbottom": abs(lo(low, 1, 10) / lo(low, 11, 20) - 1) <= 0.03 and close[0] > hi(high, 5, 12),
        "t_t_board": abs(open_[0] - close[0]) / close[0] <= 0.005
        and abs(high[0] - close[0]) / close[0] <= 0.005
        and (close[0] - low[0]) / close[0] >= 0.01,
        "t_shrink_board": amount[0] / amount[1] <= 0.7,
        "t_entity_range_limit": close[0] / open_[0] >= 1.03
        and (high[0] - low[0]) / low[0] >= 0.05
        and (close[0] - low[0]) / close[0] <= 0.09,
        "t_high_blow": amt >= 5 and close[1] / hi(close, 1, 60) >= 0.95,
        "t_accelerate": 0.08 <= close[1] / close[2] - 1 <= 0.16 and 2 <= amt <= 5,
        "t_flat_burst": abs(ret5) <= 0.02 and amt >= 2,
        "t_main_accel": close[1] > mean(close, 1, 20) > mean(close, 1, 60) and ret5 >= 0.08 and streak[0] >= 2,
        "t_broken30": rows[0]["strict_market_broken"] >= 30,
        "t_cold": rows[0]["strict_market_cold"] == 1,
        "t_highseal": rows[0]["strict_market_seal"] >= 0.8,
        "t_lumany": rows[0]["strict_market_limits"] >= 100,
        "t_weakmkt": rows[0]["strict_market_weak"] == 1,
        "t_open_gap_gt3": open_[0] / close[1] > 1.03,
    }
    for sid in (
        "t_close_one_word_limit",
        "t_full_platform",
        "t_full_newhigh",
        "t_full_ma_cross",
        "t_prev_bear_recovery_limit",
        "t_full_nshape",
        "t_box_break",
        "t_wbottom",
        "t_t_board",
        "t_shrink_board",
        "t_entity_range_limit",
        "t_high_blow",
        "t_accelerate",
        "t_flat_burst",
        "t_main_accel",
    ):
        out[sid] = out[sid] and sealed[0] == 1
    return out


@pytest.mark.parametrize("seed", range(12))
def test_all_37_against_independent_reference(seed):
    df = frame(seed)
    actual = evaluate(df).row(-1, named=True)
    for sid, expected in reference(df).items():
        assert actual[f"csg_{sid}"] is expected, sid


@pytest.mark.parametrize("sig", FINAL_SIGNALS, ids=lambda s: s["id"])
def test_declared_windows_all_positions_are_strict(sig):
    df = frame()
    days = df["date"].to_list()
    contract = sig["data_contract"]
    # Each required position is checked; reuse a separate symbol per missing cell.
    parts = []
    for field, n in contract["inputs"].items():
        offset = contract.get("offsets", {}).get(field, 0)
        for k in range(offset, offset + n):
            label = f"{field}_{k:03d}"
            parts.append(
                df.with_columns(
                    pl.lit(label).alias("symbol"),
                    pl.when(pl.col("date") == days[-1 - k])
                    .then(None)
                    .otherwise(pl.col(field))
                    .alias(field),
                )
            )
    both = pl.concat(parts).sort("symbol", "date")
    result = evaluate(both, [sig], days).group_by("symbol").last()
    assert result[f"csg_{sig['id']}"].null_count() == result.height


def test_all_signals_prefix_and_symbol_isolation():
    df = frame(88)
    days = df["date"].to_list()
    full = evaluate(df)
    prefix = evaluate(df.head(122), days=days)
    for sig in FINAL_SIGNALS:
        col = f"csg_{sig['id']}"
        assert full[col].head(122).equals(prefix[col]), sig["id"]
    other = frame(91).with_columns(pl.lit("Y").alias("symbol"))
    joined = evaluate(pl.concat([df, other]).sort("symbol", "date"), days=days)
    for sig in FINAL_SIGNALS:
        col = f"csg_{sig['id']}"
        assert joined.filter(pl.col("symbol") == "X")[col].equals(full[col])


def test_event_no_candidates_false_but_missing_unknown():
    df = frame().with_columns(
        pl.lit(0.0).alias("strict_close_limit"), pl.lit(0.0).alias("strict_limit_streak")
    )
    ids = [
        "t_nshape",
        "t_hm_single",
        "t_hm_platform",
        "t_full_nshape",
        "t_limit_history_platform_break",
    ]
    df = df.with_columns(
        pl.when(pl.col("date") == df["date"][-1])
        .then(1.0)
        .otherwise(pl.col("strict_close_limit"))
        .alias("strict_close_limit")
    )
    out = evaluate(df, [BY_ID[s] for s in ids]).row(-1, named=True)
    assert all(out[f"csg_{s}"] is False for s in ids)
    bad = df.with_columns(
        pl.when(pl.col("date") == df["date"][-9])
        .then(None)
        .otherwise(pl.col("strict_close_limit"))
        .alias("strict_close_limit")
    )
    out = evaluate(bad, [BY_ID[s] for s in ids]).row(-1, named=True)
    assert all(out[f"csg_{s}"] is None for s in ids)


def test_native_support_counts_and_zero_denominator():
    from app.strategy.scoring import materialize_scoring_columns

    df = frame()
    days = df["date"].to_list()
    ids = [
        "history_gap_support_count19",
        "history_double_amount_support_count19",
        "history_peak_amount_support_count19",
    ]
    out = materialize_scoring_columns(attach_calendar(df, days), ids)
    assert all(out[c][-1] is not None for c in ids)
    df = df.with_columns(
        pl.when(pl.col("date") == days[-4]).then(0.0).otherwise(pl.col("amount")).alias("amount")
    )
    out = materialize_scoring_columns(attach_calendar(df, days), ids)
    assert out["history_double_amount_support_count19"][-1] is None


def test_invalid_v2_contract_rejected():
    for offsets in (
        {"strict_close_limit": -1},
        {"strict_close_limit": 513},
        {"close": 1},
        {"unknown": 1},
    ):
        with pytest.raises(ValueError):
            validate_contract(
                {
                    "data_contract": {
                        "version": 2,
                        "inputs": {"strict_close_limit": 2, "close": 3},
                        "ohlc_window": 0,
                        "offsets": offsets,
                    }
                }
            )


def positive_frame(sid):
    rows = frame().to_dicts()
    for r in rows:
        r.update(
            open=99.0,
            close=100.0,
            high=101.0,
            low=98.0,
            amount=100.0,
            strict_close_limit=0.0,
            strict_limit_streak=0.0,
            strict_broken_limit=0.0,
        )

    def edit(k, **values):
        rows[-1 - k].update({key: float(value) for key, value in values.items()})

    if sid == "t_pos_low":
        edit(0, open=80, close=80, low=70)
    if sid == "t_pos_mid":
        edit(0, open=90, close=90, low=80)
    if sid == "t_double_sword":
        edit(1, high=104)
        edit(0, low=95)
    if sid == "t_niugu_sanjue":
        edit(5, open=90, close=95, high=96, low=89, amount=400)
        edit(6, open=85, close=86, high=88, low=84)
    if sid == "t_range_compress_5_20":
        edit(10, high=130, low=90)
    if sid == "t_consolidation":
        edit(20, high=130, low=70)
        for k in range(15):
            edit(k, amount=50)
    if sid in {"t_nshape", "t_hm_single", "t_hm_platform"}:
        edit(
            10, strict_close_limit=1, strict_limit_streak=1, close=110, open=109, high=111, low=108
        )
        if sid == "t_hm_platform":
            for k in range(5):
                edit(k, amount=50)
    if sid == "t_fanbao_duanban":
        edit(1, open=102, close=99, high=110)
        edit(0, open=98, close=103, high=105, low=97)
        edit(2, strict_limit_streak=1, strict_close_limit=1)
    if sid == "t_fanbao_shizi":
        edit(1, open=100, close=99.5, low=99)
        edit(0, close=101, high=102)
    if sid == "t_fanbao_broken_limit":
        edit(1, open=105, close=100, high=116, low=99, strict_broken_limit=1)
        edit(0, close=106, high=107)
    if sid == "t_limit_history_platform_break":
        edit(0, open=100, close=104, high=105, low=99)
        edit(10, strict_close_limit=1, strict_limit_streak=1)
    gated = {
        "t_close_one_word_limit",
        "t_full_platform",
        "t_full_newhigh",
        "t_full_ma_cross",
        "t_prev_bear_recovery_limit",
        "t_full_nshape",
        "t_box_break",
        "t_wbottom",
        "t_t_board",
        "t_shrink_board",
        "t_entity_range_limit",
        "t_high_blow",
        "t_accelerate",
        "t_flat_burst",
        "t_main_accel",
    }
    if sid in gated:
        edit(0, strict_close_limit=1, strict_limit_streak=1)
    if sid == "t_close_one_word_limit":
        edit(0, open=100, high=100, low=100)
    if sid == "t_full_platform":
        edit(0, open=100, close=105, high=106, low=99, amount=250)
    if sid == "t_full_newhigh":
        edit(0, open=100, close=110, high=111, low=99)
        edit(5, high=105)
    if sid == "t_full_ma_cross":
        edit(0, close=101, high=102)
    if sid == "t_prev_bear_recovery_limit":
        edit(1, open=102, close=99, high=103)
        edit(0, open=100, close=104, high=105, low=99, amount=150)
    if sid == "t_full_nshape":
        edit(5, strict_close_limit=1, strict_limit_streak=1, close=102, open=101, high=103, low=100)
        for k in range(1, 6):
            edit(k, amount=50)
        edit(0, close=105, high=106)
    if sid in {"t_box_break", "t_wbottom"}:
        edit(0, close=105, high=106)
    if sid == "t_t_board":
        edit(0, open=105, close=105, high=105, low=103)
    if sid == "t_shrink_board":
        edit(0, amount=60)
    if sid == "t_entity_range_limit":
        edit(0, open=100, close=104, high=106, low=99)
    if sid == "t_high_blow":
        edit(0, amount=600)
    if sid == "t_accelerate":
        edit(1, close=110, open=109, high=111, low=108)
        edit(0, close=115, open=111, high=116, low=110, amount=300)
    if sid == "t_flat_burst":
        edit(0, amount=250)
    if sid == "t_main_accel":
        for k in range(1, 6):
            edit(k, close=115, open=114, high=116, low=113)
        edit(1, strict_close_limit=1, strict_limit_streak=1)
        edit(0, close=120, open=116, high=121, low=115, strict_limit_streak=2)
    if sid == "t_open_gap_gt3":
        edit(0, open=104, close=105, high=106, low=100)
    return pl.DataFrame(rows)


@pytest.mark.parametrize("sid", list(BY_ID))
def test_each_signal_has_independent_positive(sid):
    df = positive_frame(sid)
    assert reference(df)[sid] is True, sid
    assert evaluate(df, [BY_ID[sid]])[f"csg_{sid}"][-1] is True, sid
    if BY_ID[sid]["data_contract"]["inputs"].get("strict_close_limit") == 1:
        df = df.with_columns(pl.lit(0.0).alias("strict_close_limit"))
        assert evaluate(df, [BY_ID[sid]])[f"csg_{sid}"][-1] is False, sid


@pytest.mark.parametrize(
    "fid",
    [
        "history_gap_support_count19",
        "range_high_touch_count15",
        "uf_position_fixed_band",
        "uf_open_prev_close_ratio",
    ],
)
def test_strict_factor_matrix_matches_panel(fid, monkeypatch):
    import numpy as np

    from app.backtest.matrix import build_market_data_matrix, matrix_feature
    from app.services import trading_day
    from app.strategy.scoring import materialize_scoring_columns

    df = positive_frame("t_niugu_sanjue")
    days = df["date"].to_list()
    monkeypatch.setattr(trading_day, "cached_trading_calendar", lambda: days)
    panel = materialize_scoring_columns(attach_calendar(df, days), [fid])
    market = build_market_data_matrix(df, field_columns=set(df.columns))
    actual = matrix_feature(market, fid)[:, 0]
    np.testing.assert_allclose(actual, panel[fid].to_numpy(), equal_nan=True, rtol=1e-5)
    # 不复用前次结果：权威日历增加一个实际缺失的交易日后，长窗口必须未知。
    extra = days[-2] + timedelta(days=1)
    if extra in days:
        extra = next(
            d + timedelta(days=1)
            for d in reversed(days[:-1])
            if d + timedelta(days=1) not in days and d < days[-1]
        )
    monkeypatch.setattr(trading_day, "cached_trading_calendar", lambda: [*days, extra])
    if fid != "uf_open_prev_close_ratio":
        assert math.isnan(matrix_feature(market, fid)[-1, 0])


def test_event_signal_post_pipeline_uses_official_facts(monkeypatch):
    from app.indicators import pipeline
    from app.services import trading_day

    df = positive_frame("t_close_one_word_limit").drop(
        "strict_close_limit", "strict_limit_streak", "strict_broken_limit"
    )
    df = df.with_columns(
        pl.col("close").alias("raw_close"),
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
        pl.lit(100.0).alias("volume"),
    )
    days = df["date"].to_list()
    monkeypatch.setattr(trading_day, "cached_trading_calendar", lambda: days)
    monkeypatch.setattr(
        pipeline, "_custom_signal_exprs", cs.build_expressions([BY_ID["t_close_one_word_limit"]])
    )
    inst = pl.DataFrame(
        {
            "symbol": ["X"],
            "name": ["测试"],
            "as_of": [days[-1]],
            "limit_up": [100.0],
            "limit_down": [80.0],
        }
    )
    needed = {"csg_t_close_one_word_limit"}
    pre = pipeline.compute_signals(df, needed=needed, attach_extensions=False)
    assert not needed & set(pre.columns)
    out = pipeline.compute_limit_signals(pre, inst, needed=needed)
    assert out["csg_t_close_one_word_limit"][-1] is True
    assert out["csg_t_close_one_word_limit"][:-1].null_count() == df.height - 1
    direct = pipeline.compute_limit_signals(df, inst, needed={"strict_close_limit"})
    assert direct["strict_close_limit"][-1] == 1
