"""/api/phase-lab/structure 的纯函数测试（走势结构视图，只用未来数据）。

与 ``test_phase_lab.py`` 一样不依赖 FastAPI / repo / 真实 parquet：用合成日线
直接驱动 ``build_structure``。核心契约：

1. K 线 bars 与 dates 逐日对齐，且与旧 ``build_timeline`` 展示段的 OHLCV 一致；
2. ``ground_truth`` 与直接调用 ``structure_eval.ground_truth`` 完全一致；
3. **不逐日重放**：不调用 ``radar_core.match_patterns``，也不调用 ``build_timeline``；
4. 段连续覆盖展示区间，pen 越大分段越粗，非法参数与无效收盘价有明确行为。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.api import phase_lab
from app.indicators.vendor.hot_money_radar import radar_core
from app.services import structure_eval as se

SYMBOL = "000006.SZ"


def _synth(n: int = 434) -> list[dict]:
    """确定性合成日线：默认 WARMUP(184) + 250，正好覆盖默认展示区间。

    价格由几段不同斜率的走势拼接，保证离线 Pelt 能切出多个变点。
    """
    rows: list[dict] = []
    price = 10.0
    start = dt.date(2022, 1, 4)
    slopes = (0.006, -0.004, 0.0005, -0.007, 0.003, -0.002)
    for i in range(n):
        slope = slopes[(i // 70) % len(slopes)]
        close = price * (1.0 + slope + 0.004 * np.sin(i / 6.0))
        op = price * (1.0 + slope * 0.35)
        high = max(op, close) * 1.008
        low = min(op, close) * 0.992
        vol = 1e6 * (1.0 + (i % 9) * 0.11)
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
                "turnover_rate": None,
            }
        )
        price = close
    return rows


# ---------------------------------------------------------------------------
# 1. 结构契约
# ---------------------------------------------------------------------------

def test_structure_contract_and_bars_align_with_dates():
    rows = _synth()
    out = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.05)
    assert set(out) == {
        "symbol", "lookback", "pen", "direction_threshold", "pen_options",
        "from_date", "to_date", "n_obs", "dates", "bars", "ground_truth", "notes",
    }
    assert out["symbol"] == SYMBOL
    assert out["lookback"] == 250
    assert out["pen"] == 0.05
    assert out["pen_options"] == list(se.PEN_OPTIONS)
    assert out["n_obs"] == 250
    assert len(out["dates"]) == len(out["bars"]) == out["n_obs"]
    assert out["from_date"] == out["dates"][0] == out["bars"][0]["date"]
    assert out["to_date"] == out["dates"][-1] == out["bars"][-1]["date"]
    assert out["dates"] == sorted(out["dates"]) and len(set(out["dates"])) == 250
    for day, bar in zip(out["dates"], out["bars"]):
        assert bar["date"] == day
        assert set(bar) == {"date", "open", "high", "low", "close", "volume"}
        assert bar["close"] > 0
        assert all(isinstance(bar[k], float) for k in ("open", "high", "low", "close", "volume"))


@pytest.mark.parametrize("lookback", [60, 250])
def test_structure_bars_match_timeline_ohlcv(lookback):
    """新接口的 K 线必须与旧 timeline 展示段逐日一致（同一份日线，无重算口径差）。"""
    rows = _synth(phase_lab.WARMUP + lookback)
    tl = phase_lab.build_timeline(rows, SYMBOL, lookback=lookback, rules="all")
    out = phase_lab.build_structure(rows, SYMBOL, lookback=lookback, pen=0.05)
    assert len(tl["timeline"]) == len(out["bars"]) == lookback
    for day, bar in zip(tl["timeline"], out["bars"]):
        assert bar["date"] == day["date"]
        for key in ("open", "high", "low", "close", "volume"):
            assert bar[key] == pytest.approx(day["ohlcv"][key], rel=1e-9)


@pytest.mark.parametrize("pen", list(se.PEN_OPTIONS))
def test_structure_ground_truth_equals_structure_eval(pen):
    """标准答案就是 se.ground_truth 的原样输出，不得在本页另造一套分割。"""
    rows = _synth()
    out = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=pen)
    dates, closes = se._series(rows[-250:])
    expected = se.ground_truth(dates, closes, pen=pen)
    assert out["dates"] == dates
    assert out["ground_truth"] == expected


def test_structure_segments_cover_all_days():
    rows = _synth()
    out = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.05)
    gt = out["ground_truth"]
    bkps = gt["bkps"]
    assert bkps == sorted(set(bkps))
    assert all(0 < b < out["n_obs"] for b in bkps)
    bounds = [0, *bkps, out["n_obs"]]
    assert gt["segment_count"] == len(bounds) - 1 == len(gt["segments"])
    for i, seg in enumerate(gt["segments"]):
        assert seg["start"] == bounds[i]
        assert seg["end"] == bounds[i + 1] - 1
        assert seg["from"] == out["dates"][seg["start"]]
        assert seg["to"] == out["dates"][seg["end"]]
        assert seg["direction"] in {"UP", "DOWN", "SIDEWAYS"}


# ---------------------------------------------------------------------------
# 2. 不逐日重放 / 不评分
# ---------------------------------------------------------------------------

def test_structure_does_not_call_radar_core_or_timeline(monkeypatch):
    """把逐日重放相关入口换成会抛异常的哨兵，若仍能算出结构即证明没碰它们。"""
    def _boom(*args, **kwargs):
        raise AssertionError("build_structure 不应触发逐日重放")

    monkeypatch.setattr(radar_core, "match_patterns", _boom)
    monkeypatch.setattr(radar_core, "_pattern_phase", _boom)
    monkeypatch.setattr(phase_lab, "build_timeline", _boom)
    monkeypatch.setattr(se, "evaluate", _boom)

    out = phase_lab.build_structure(_synth(), SYMBOL, lookback=250, pen=0.05)
    assert out["n_obs"] == 250
    assert out["ground_truth"]["segment_count"] >= 1


def test_structure_notes_have_no_replay_or_scoring_wording():
    out = phase_lab.build_structure(_synth(), SYMBOL, lookback=250, pen=0.05)
    text = "".join(out["notes"])
    for forbidden in ("逐日重放", "F1", "covering", "确认延迟", "准确率评分", "PIT"):
        assert forbidden not in text, f"说明文案不应再出现「{forbidden}」"
    assert "未来数据" in text and "不可用于实盘" in text
    assert len(out["notes"]) == len(phase_lab.STRUCTURE_NOTES)
    # 返回结构里也不含任何算法/评分字段
    assert "scores" not in out
    assert "algorithms" not in out


# ---------------------------------------------------------------------------
# 3. 参数与边界
# ---------------------------------------------------------------------------

def test_structure_pen_changes_granularity():
    rows = _synth()
    fine = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.02)
    coarse = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.3)
    assert len(coarse["ground_truth"]["bkps"]) <= len(fine["ground_truth"]["bkps"])
    assert coarse["ground_truth"]["segment_count"] <= fine["ground_truth"]["segment_count"]


def test_structure_direction_threshold_affects_labels():
    rows = _synth()
    strict = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.05, direction_threshold=0.0)
    loose = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.05, direction_threshold=0.5)
    assert strict["direction_threshold"] == 0.0
    assert loose["direction_threshold"] == 0.5
    assert strict["ground_truth"]["bkps"] == loose["ground_truth"]["bkps"]
    assert sum(1 for s in loose["ground_truth"]["segments"] if s["direction"] == "SIDEWAYS") >= sum(
        1 for s in strict["ground_truth"]["segments"] if s["direction"] == "SIDEWAYS"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lookback": 100},
        {"pen": 0.07},
        {"direction_threshold": -0.1},
    ],
)
def test_structure_rejects_invalid_params(kwargs):
    with pytest.raises(ValueError):
        phase_lab.build_structure(_synth(), SYMBOL, **kwargs)


def test_structure_short_series_uses_available_days():
    rows = _synth(80)
    out = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.05)
    assert out["n_obs"] == 80
    assert out["from_date"] == rows[0]["date"]
    assert out["to_date"] == rows[-1]["date"]


def test_structure_drops_invalid_close_rows():
    rows = _synth(120)
    rows[10]["close"] = None
    rows[20]["close"] = float("nan")
    rows[30]["close"] = -1.0
    rows[40]["close"] = "not-a-number"
    out = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.05)
    assert out["n_obs"] == 116
    assert all(bar["close"] > 0 for bar in out["bars"])


def test_structure_numeric_fallbacks():
    """OHLC 缺列用当日收盘兜底，volume 缺列用 0，避免前端 K 线出现 null。"""
    rows = _synth(120)
    for row in rows:
        row["open"] = None
        row["volume"] = None
    out = phase_lab.build_structure(rows, SYMBOL, lookback=250, pen=0.05)
    for bar in out["bars"]:
        assert bar["open"] == bar["close"]
        assert bar["volume"] == 0.0


# ---------------------------------------------------------------------------
# 4. 路由注册
# ---------------------------------------------------------------------------

def test_structure_route_registered():
    paths = {getattr(r, "path", None) for r in phase_lab.router.routes}
    assert "/api/phase-lab/structure" in paths
    # 旧接口仍然保留，不做破坏性下线
    assert "/api/phase-lab/timeline" in paths
    assert "/api/phase-lab/evaluate" in paths
