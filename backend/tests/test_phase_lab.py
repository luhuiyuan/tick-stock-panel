"""/phase-lab 时间轴的纯函数测试。

不依赖 FastAPI / repo / 真实 parquet：用合成日线直接驱动 ``build_timeline``。
四类断言对应最终方案第六节：PIT 合规、与 vendored 输出一致、时间轴结构、
chip_degraded 标注。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.api import phase_lab
from app.indicators.vendor.hot_money_radar import adapter, radar_core

SYMBOL = "000006.SZ"
CODE = "000006"


def _synth(n: int = 244, *, with_turnover: bool = False) -> list[dict]:
    """确定性的合成日线。

    默认 244 根 = WARMUP(184) + lookback(60)，窗口起点正好是 index 0，
    便于把「第 idx 个展示日的窗口」精确写成 ``rows[:185+idx]``。
    展示段内安排涨停 spike + 放量，保证有形态命中。
    """
    rows: list[dict] = []
    price = 10.0
    start = dt.date(2024, 1, 1)
    for i in range(n):
        # 周期性涨跌 + 展示段内的放量/涨停，制造突破/破位/影线等事件
        wave = 0.02 * ((i % 17) - 8) / 8
        spike = 0.09 if 200 <= i < 205 else 0.0
        change = wave + spike
        close = price * (1 + change)
        op = price * (1 + change * 0.3)
        high = max(op, close) * (1 + abs(wave) * 0.6 + 0.004)
        low = min(op, close) * (1 - abs(wave) * 0.6 - 0.004)
        vol = 1e6 * (1 + (i % 9) * 0.12) * (3.2 if i >= 200 else 1.0)
        rows.append(
            {
                "symbol": SYMBOL,
                "date": (start + dt.timedelta(days=i)).isoformat(),
                "open": float(op),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(vol),
                "amount": float(vol * close * 100),
                "turnover_rate": (3.5 + (i % 5)) if with_turnover else None,
            }
        )
        price = close
    return rows


def _strip_notes(fired: list[dict]) -> list[dict]:
    return [{k: f[k] for k in ("code", "name", "phase", "signal")} for f in fired]


# ---------------------------------------------------------------------------
# 1. 时间轴结构
# ---------------------------------------------------------------------------

def test_timeline_structure():
    rows = _synth(244)
    out = phase_lab.build_timeline(rows, SYMBOL, lookback=60)
    assert out["symbol"] == SYMBOL
    assert out["lookback"] == 60
    assert out["rules"] == "all"
    assert out["warmup"] == phase_lab.WARMUP
    assert out["rule_count"] == 26
    assert out["total_days"] == len(out["timeline"]) == 60
    assert out["from_date"] == out["timeline"][0]["date"]
    assert out["to_date"] == out["timeline"][-1]["date"]
    dates = [t["date"] for t in out["timeline"]]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    assert out["hit_days"] == sum(1 for t in out["timeline"] if t["fired"])
    for t in out["timeline"]:
        assert set(t) == {"date", "ohlcv", "fired", "phase", "chip_degraded"}
        assert set(t["ohlcv"]) == {
            "open", "high", "low", "close", "volume", "amount", "chg", "turnover",
        }
        assert isinstance(t["phase"], str) and t["phase"]
        for f in t["fired"]:
            assert set(f) == {"code", "name", "phase", "signal", "reason", "retest"}
            assert f["code"].startswith("P")
            assert f["reason"] and f["retest"]


@pytest.mark.parametrize("lookback", [60, 250, 500, 1000])
def test_lookback_options(lookback):
    # 足够长的历史：4 档都应给满对应展示天数（1000 日是当前最大档）
    rows = _synth(phase_lab.WARMUP + lookback)
    out = phase_lab.build_timeline(rows, SYMBOL, lookback=lookback)
    assert out["total_days"] == lookback
    assert out["rule_count"] == 26
    # 数据不足时只给可用天数，不臆造
    short = phase_lab.build_timeline(_synth(244), SYMBOL, lookback=lookback)
    assert short["total_days"] == min(lookback, 244 - phase_lab.WARMUP)


def test_invalid_params():
    rows = _synth(244)
    with pytest.raises(ValueError):
        phase_lab.build_timeline(rows, SYMBOL, lookback=30)
    with pytest.raises(ValueError):
        phase_lab.build_timeline(rows, SYMBOL, rules="unknown")


# ---------------------------------------------------------------------------
# 2. 与 vendored match_patterns 输出一致（不引入新判定逻辑）
# ---------------------------------------------------------------------------

def test_fired_matches_vendored_match_patterns():
    # 两边必须喂同一批行（含补好的 chg），否则上游依赖 chg 的规则会静默退化
    rows = phase_lab._with_chg(_synth(244))
    out = phase_lab.build_timeline(rows, SYMBOL, lookback=60)
    # 第 idx 个展示日的窗口 = rows[:WARMUP + idx + 1]
    for idx in (0, 5, 17, 40, 59):
        end = phase_lab.WARMUP + idx + 1
        theirs = adapter.match_patterns(CODE, rows[:end])
        ours = _strip_notes(out["timeline"][idx]["fired"])
        assert ours == theirs, f"第 {idx} 天命中与 vendored 不一致"


def test_phase_matches_vendored_pattern_phase():
    rows = phase_lab._with_chg(_synth(244))
    out = phase_lab.build_timeline(rows, SYMBOL, lookback=60)
    for idx in (0, 10, 30, 59):
        end = phase_lab.WARMUP + idx + 1
        fired = adapter.match_patterns(CODE, rows[:end])
        assert out["timeline"][idx]["phase"] == radar_core._pattern_phase(fired)


# ---------------------------------------------------------------------------
# 3. PIT 合规：截断未来数据不改变已有日期的结果
# ---------------------------------------------------------------------------

def test_pit_no_future_leak():
    rows = _synth(244)
    full = phase_lab.build_timeline(rows, SYMBOL, lookback=60)
    truncated = phase_lab.build_timeline(rows[:-1], SYMBOL, lookback=60)
    # 少一根未来 bar，前 59 个展示日（其窗口完全不含被删的那根）应逐项一致
    for a, b in zip(full["timeline"][:-1], truncated["timeline"]):
        assert a["date"] == b["date"]
        assert _strip_notes(a["fired"]) == _strip_notes(b["fired"])
        assert a["phase"] == b["phase"]
        assert a["ohlcv"] == b["ohlcv"]


def test_first_display_day_has_full_warmup_window():
    rows = _synth(244)
    out = phase_lab.build_timeline(rows, SYMBOL, lookback=60)
    # 第一个展示日的窗口长度 = WARMUP + 1 = 185 根，满足 P21 的 185 根 PIT 窗口
    assert phase_lab.WARMUP + 1 == 185
    assert out["timeline"][0]["date"] == rows[phase_lab.WARMUP]["date"]


# ---------------------------------------------------------------------------
# 4. chip_degraded 标注
# ---------------------------------------------------------------------------

def test_chip_degraded_when_turnover_missing():
    rows = _synth(300, with_turnover=False)
    out = phase_lab.build_timeline(rows, SYMBOL, lookback=60)
    assert out["chip_degraded"] is True
    assert out["degraded_rules"] == ["P8", "P10", "P13", "P26"]
    assert all(t["chip_degraded"] is True for t in out["timeline"])
    assert "不伪造" in out["degraded_note"]
    # 缺失值如实传 None，不估算
    assert all(t["ohlcv"]["turnover"] is None for t in out["timeline"])


def test_chip_degraded_false_when_turnover_present():
    rows = _synth(300, with_turnover=True)
    out = phase_lab.build_timeline(rows, SYMBOL, lookback=60)
    assert out["chip_degraded"] is False
    assert all(t["ohlcv"]["turnover"] is not None for t in out["timeline"])


def test_chg_is_percent():
    rows = _synth(244)
    out = phase_lab.build_timeline(rows, SYMBOL, lookback=60)
    first = out["timeline"][0]["ohlcv"]["chg"]
    # 上游 daily_change_pct 是百分数（涨停阈值 ~9.5），不是小数
    assert first is not None and abs(first) < 100


# ---------------------------------------------------------------------------
# 5. rules=effective 只跑核心有效集
# ---------------------------------------------------------------------------

def test_effective_rules_only():
    rows = _synth(244)
    all_out = phase_lab.build_timeline(rows, SYMBOL, lookback=60, rules="all")
    eff_out = phase_lab.build_timeline(rows, SYMBOL, lookback=60, rules="effective")
    assert all_out["rule_count"] == 26
    assert eff_out["rule_count"] == len(radar_core.PATTERN_EFFECTIVE) == 19
    fired_codes = {f["code"] for t in eff_out["timeline"] for f in t["fired"]}
    assert fired_codes <= radar_core.PATTERN_EFFECTIVE
    assert eff_out["total_days"] == all_out["total_days"]


def test_all_pattern_notes_cover_every_rule():
    codes = {p[0] for p in radar_core.PATTERNS}
    assert codes == set(phase_lab._PATTERN_NOTES)
