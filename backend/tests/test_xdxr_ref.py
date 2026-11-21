"""除权除息日昨收修正(xdxr_ref)与 price_facts 接入的回归测试。"""

from datetime import date, timedelta

import polars as pl

from app.services import xdxr_ref
from app.services.coverage_limits import price_facts
from app.strategy.strict_signal_data import attach_calendar


def _xd_limit(ref, pct, up=True):
    import math
    sign = 1 if up else -1
    return math.floor((ref * (1 + sign * pct) + 1e-9) * 100 + 0.5) / 100


def test_xd_ref_formula_matches_eltdx():
    """权息四元组 → 高精度修正昨收, 与 eltdx (前收-c)/m 等价。"""
    # 603383.SH 2024-05-30: D=11/10=1.1, S=2/10=0.2, 前收 39.40
    dividend, bonus, allot, allot_price, prev = 1.1, 0.2, 0.0, 0.0, 39.40
    df = pl.DataFrame({
        "xd_dividend": [dividend], "xd_bonus": [bonus],
        "xd_allot": [allot], "xd_allot_price": [allot_price],
        "xd_prev_close": [prev],
    })
    ref, present = xdxr_ref.xd_ref_expr()
    out = df.select(ref.alias("ref"), present.alias("present"))
    assert out["present"][0]
    expected = (prev - dividend + allot * allot_price) / (1 + bonus + allot)
    assert abs(out["ref"][0] - expected) < 1e-9
    # 主板 10% → 涨停 35.11 (eltdx 实测值)
    pct = 0.10
    up = df.select(xdxr_ref.xd_limit_price_expr(ref, pl.lit(pct), up=True).alias("p"))["p"][0]
    assert abs(up - 35.11) < 0.005


def test_xd_limit_no_pretruncation():
    """送股场景高精度昨收不得先截断到分(否则 605100.SH 2026-05-25 丢一分)。"""
    # 605100.SH 2026-05-25: S=4/10=0.4, 前收 55.36 → ref=39.542857...
    # ref*1.1=43.49714 → round=43.50; 若先把 ref 截到 39.54 则得 43.49(错)。
    dividend, bonus, allot, allot_price, prev = 0.0, 0.4, 0.0, 0.0, 55.36
    df = pl.DataFrame({
        "xd_dividend": [dividend], "xd_bonus": [bonus],
        "xd_allot": [allot], "xd_allot_price": [allot_price],
        "xd_prev_close": [prev],
    })
    ref, _ = xdxr_ref.xd_ref_expr()
    up = df.select(xdxr_ref.xd_limit_price_expr(ref, pl.lit(0.10), up=True).alias("p"))["p"][0]
    assert abs(up - 43.50) < 0.005, f"高精度涨停应为 43.50, 实际 {up}"
    assert abs(up - _xd_limit((prev - dividend) / (1 + bonus), 0.10)) < 0.005


def test_join_adj_events_degrades_when_empty():
    """不传/空/缺列事件表时, join 补齐占位列, 不抛错。"""
    base = pl.DataFrame({"symbol": ["A"], "date": [date(2026, 1, 2)], "raw_close": [10.0]})
    for adj in (None, pl.DataFrame(), pl.DataFrame({"symbol": ["A"]})):
        out = xdxr_ref.join_adj_events(base, adj)
        ref, present = xdxr_ref.xd_ref_expr()
        assert out.height == base.height
        assert all(f"xd_{c}" in out.columns for c in xdxr_ref.ADJ_COLUMNS)
        assert not out.select(present.alias("p"))["p"][0]


def test_price_facts_uses_xd_ref_on_ex_date():
    """除权日传 adj_events 时, _inferred_up 用修正昨收; 不传时用旧口径。"""
    sym = "600000.SH"
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(8)]
    # 普通日 raw_close=close=10; 除权日(最后一日) raw_close 反映除权后价格 9.0,
    # close(后复权)保持连续 = 10.0 → 旧口径 ref = prev(close)/adj = 10/(10/9)=9.0
    raw_close = [10.0] * 7 + [9.0]
    close = [10.0] * 8
    bars = pl.DataFrame({
        "symbol": [sym] * 8,
        "date": days,
        "close": close,
        "raw_close": raw_close,
        "raw_low": [v * 0.9 for v in raw_close],
        "raw_high": [v * 1.1 for v in raw_close],
    })
    inst = pl.DataFrame(schema={"symbol": pl.String, "as_of": pl.Date, "limit_up": pl.Float64})
    framed = attach_calendar(bars, days)
    ex_date = days[-1]

    # 不传事件表: 旧口径, 除权日昨收 ≈ 9.0 → 涨停 ≈ 9.90
    old = price_facts(framed, inst, days)
    old_up = old.filter(pl.col("date") == ex_date)["_inferred_up"][0]

    # 传事件表: 10股派 10元(D=10→dividend=1.0), 修正昨收 = (10 - 1.0)/1 = 9.0
    # 为体现差异, 用 D=5 (dividend=0.5): 修正昨收=(10-0.5)/1=9.5 → 涨停=10.45
    adj_events = pl.DataFrame({
        "symbol": [sym],
        "trade_date": [ex_date],
        "dividend": [0.5], "bonus": [0.0], "allot": [0.0],
        "allot_price": [0.0], "prev_close": [10.0],
    })
    new = price_facts(framed, inst, days, adj_events=adj_events)
    new_up = new.filter(pl.col("date") == ex_date)["_inferred_up"][0]
    assert new_up is not None and abs(new_up - 10.45) < 0.005, f"除权日修正涨停应 10.45, 实际 {new_up}"
    assert old_up is not None and abs(new_up - old_up) > 0.1, "除权修正应改变旧口径的推断价"

    # 普通日不受影响
    normal_day = days[5]
    assert (
        old.filter(pl.col("date") == normal_day)["_inferred_up"][0]
        == new.filter(pl.col("date") == normal_day)["_inferred_up"][0]
    )
