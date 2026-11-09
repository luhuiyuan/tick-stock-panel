"""客观价格结构评测引擎的测试。

覆盖范围：
- vendor 的 ruptures 纯 Python 子集与**官方 wheel** 输出逐字一致（golden 值由
  官方 wheel 在固定合成信号上预先生成并写死，测试不依赖临时目录）；
- TCPDBench 评测标尺的原有 doctest 与已知值；
- 标准答案（离线分割）的确定性、pen 粒度、段方向阈值；
- 三个实时算法的 **PIT / 无未来数据泄漏**；
- 评分在「完美 / 空 / 乱报」三种输入下的已知值；
- evaluate() 返回契约与确定性。
"""
from __future__ import annotations

import datetime as dt
import doctest

import numpy as np
import polars as pl
import pytest

from app.indicators.vendor.price_structure import Dynp, Pelt, tcpd_metrics
from app.services import structure_eval as se

SYMBOL = "TEST.SZ"

# 官方 ruptures wheel（临时隔离目录安装，BSD-2）在 _official_signal() 上的输出。
# 生成方式：np.random.default_rng(20261109) + 5 段 N(loc, 0.35) 各 40 点，
# Pelt(model="l2", min_size=15, jump=1).predict(pen=0.5) /
# Dynp(...).predict(n_bkps=4)。写死在此，保证 vendor 与官方实现不漂移。
OFFICIAL_BKPS = [40, 80, 120, 160, 200]


def _official_signal() -> np.ndarray:
    rng = np.random.default_rng(20261109)
    return np.concatenate(
        [rng.normal(loc, 0.35, 40) for loc in (0.0, 2.5, -1.0, 3.0, 0.5)]
    ).reshape(-1, 1)


def _prices(n: int = 200) -> np.ndarray:
    """由固定 log 信号取 exp 得到的正价格序列，便于 ground_truth 取 log。"""
    rng = np.random.default_rng(20261109)
    log_sig = np.concatenate(
        [rng.normal(loc, 0.35, 40) for loc in (0.0, 2.5, -1.0, 3.0, 0.5)]
    )
    return np.exp(log_sig[:n])


def _dates(n: int) -> list[str]:
    start = dt.date(2024, 1, 1)
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(n)]


def _rows(prices: np.ndarray) -> list[dict]:
    dates = _dates(len(prices))
    return [
        {
            "date": dates[i],
            "open": float(prices[i] * 0.99),
            "high": float(prices[i] * 1.01),
            "low": float(prices[i] * 0.98),
            "close": float(prices[i]),
            "volume": 1_000_000.0,
        }
        for i in range(len(prices))
    ]


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r["date"] for r in rows],
            "open": [r["open"] for r in rows],
            "high": [r["high"] for r in rows],
            "low": [r["low"] for r in rows],
            "close": [r["close"] for r in rows],
            "volume": [r["volume"] for r in rows],
        }
    )


# ---- 1. vendor 与官方 wheel 一致性 ---------------------------------------
def test_vendor_pelt_dynp_match_official_wheel():
    sig = _official_signal()
    ours_pelt = Pelt(model="l2", min_size=15, jump=1).fit(sig).predict(pen=0.5)
    ours_dynp = Dynp(model="l2", min_size=15, jump=1).fit(sig).predict(n_bkps=4)
    assert ours_pelt == OFFICIAL_BKPS
    assert ours_dynp == OFFICIAL_BKPS
    # 如果临时区里官方 wheel 可用，再做一次直接对照（不可用不跳过，golden 已足够）
    import importlib.util

    if importlib.util.find_spec("ruptures") is not None:
        import ruptures as rpt

        assert rpt.Pelt(model="l2", min_size=15, jump=1).fit(sig).predict(pen=0.5) == ours_pelt
        assert rpt.Dynp(model="l2", min_size=15, jump=1).fit(sig).predict(n_bkps=4) == ours_dynp


def test_vendor_module_paths_are_isolated():
    """vendor 使用独立包路径，不会与官方 ruptures 包混淆。"""
    assert Pelt.__module__.startswith("app.indicators.vendor.price_structure")
    assert Dynp.__module__.startswith("app.indicators.vendor.price_structure")
    assert tcpd_metrics.__name__.endswith("tcpd_metrics")


# ---- 2. 评测标尺 ---------------------------------------------------------
def test_tcpd_metrics_doctests_pass():
    result = doctest.testmod(tcpd_metrics, verbose=False)
    assert result.failed == 0
    assert result.attempted >= 20


def test_known_metric_values():
    ann = {1: [40, 80, 120, 160]}
    assert tcpd_metrics.f_measure(ann, [40, 80, 120, 160]) == pytest.approx(1.0)
    assert tcpd_metrics.covering(ann, [40, 80, 120, 160], 200) == pytest.approx(1.0)
    # 上游 doctest 中的已知值
    assert tcpd_metrics.f_measure({1: [], 2: [10], 3: [50]}, [10]) == pytest.approx(
        0.9090909090909091
    )
    assert tcpd_metrics.covering({1: [10, 20], 2: [10], 3: [0, 5]}, [10, 20], 45) == pytest.approx(
        0.7962962962962963
    )
    # 不报 / 乱报必须明显低于完美
    assert tcpd_metrics.f_measure(ann, []) < 0.5
    assert tcpd_metrics.f_measure(ann, list(range(10, 200, 10))) < 0.6
    assert tcpd_metrics.covering(ann, [], 200) < 0.9


# ---- 3. 标准答案 ---------------------------------------------------------
def test_ground_truth_known_bkps_and_coverage():
    prices = _prices()
    dates = _dates(len(prices))
    gt = se.ground_truth(dates, prices, pen=0.5)
    assert gt["bkps"] == [40, 80, 120, 160]  # 不含末端 n
    assert gt["segment_count"] == len(gt["bkps"]) + 1
    segs = gt["segments"]
    assert segs[0]["start"] == 0
    assert segs[-1]["end"] == len(prices) - 1
    for a, b in zip(segs, segs[1:]):
        assert b["start"] == a["end"] + 1, "分段必须连续覆盖整个区间"
    assert all(s["direction"] in ("UP", "DOWN", "SIDEWAYS") for s in segs)


def test_ground_truth_pen_changes_granularity():
    prices = _prices()
    dates = _dates(len(prices))
    coarse = se.ground_truth(dates, prices, pen=0.5)
    fine = se.ground_truth(dates, prices, pen=0.05)
    assert len(fine["bkps"]) > len(coarse["bkps"]), "pen 越小分段越细"
    assert coarse["bkps"] == [40, 80, 120, 160]


def test_segment_direction_threshold():
    """±5% 阈值：涨/跌/横盘的判定边界。"""
    assert se._segment_direction(0.06, 0.05) == "UP"
    assert se._segment_direction(-0.06, 0.05) == "DOWN"
    assert se._segment_direction(0.0, 0.05) == "SIDEWAYS"
    assert se._segment_direction(0.05, 0.05) == "SIDEWAYS"   # 边界含等号
    assert se._segment_direction(-0.05, 0.05) == "SIDEWAYS"
    assert se._segment_direction(0.06, 0.10) == "SIDEWAYS"   # 阈值可调


def test_ground_truth_change_pct_and_direction_match_endpoints():
    """每段的 change_pct / direction 必须严格由该段首尾收盘价算出。"""
    prices = _prices()
    dates = _dates(len(prices))
    gt = se.ground_truth(dates, prices, pen=0.05)
    for s in gt["segments"]:
        change = prices[s["end"]] / prices[s["start"]] - 1.0
        assert s["change_pct"] == pytest.approx(round(change * 100.0, 4))
        assert s["direction"] == se._segment_direction(
            change, se.DEFAULT_DIRECTION_THRESHOLD
        )
        assert s["from"] == dates[s["start"]]
        assert s["to"] == dates[s["end"]]


def test_ground_truth_rejects_short_series():
    with pytest.raises(ValueError, match="不足"):
        se.ground_truth(_dates(20), _prices(20), pen=0.5)


# ---- 4. 实时算法的 PIT 性质 ----------------------------------------------
def test_pit_pelt_raw_has_no_future_leak():
    """把第 60 根以后的数据大幅改写，第 60 根以前的首次报告结果必须不变。"""
    prices = _prices(120)
    base = se.pit_pelt_raw(prices, pen=0.05)
    perturbed = prices.copy()
    perturbed[60:] = perturbed[60:] * (1.0 + 0.4 * np.arange(len(prices) - 60) / 60.0)
    alt = se.pit_pelt_raw(perturbed, pen=0.05)
    early_base = {j: r for j, r in base.items() if r < 60}
    early_alt = {j: r for j, r in alt.items() if r < 60}
    assert early_base == early_alt
    assert early_base, "早期应有已确认的变点，否则断言是空跑"


def test_pit_pelt_reported_never_before_bkp():
    prices = _prices(120)
    confs = se.pit_pelt(prices, pen=0.05, margin=5)
    assert confs
    for c in confs:
        assert 0 < c["bkp"] < len(prices)
        assert c["reported"] >= c["bkp"]
        assert c["delay"] >= 0
    assert [c["bkp"] for c in confs] == sorted(c["bkp"] for c in confs)


def test_smc_confirmed_pit_semantics():
    prices = _prices(120)
    rows = _rows(prices)
    dates = _dates(len(prices))
    confs, note = se.smc_confirmed(rows, dates, 5)
    assert note is None, "smc_adapter 应可用"
    assert confs
    for c in confs:
        assert c["reported"] >= c["bkp"]
        assert c["delay"] >= 0
    # 确认延迟应等于 swing_length=3（上游对称窗口）
    assert all(c["delay"] == se.SMC_SWING_LENGTH for c in confs)


def test_smc_adapter_no_future_leak():
    """截断数据得到的摆动点，应覆盖全量结果中所有在截断点之前已确认的点。"""
    from app.indicators.smc_adapter import compute_smc_structure

    prices = _prices(120)
    dates = _dates(120)
    rows = _rows(prices)
    m = 80
    full = compute_smc_structure(_frame(rows), swing_length=3)["swing_points"]
    part = compute_smc_structure(_frame(rows[:m]), swing_length=3)["swing_points"]
    cutoff = dates[m - se.SMC_SWING_LENGTH]
    expected = {
        (p["date"], p["confirmed_date"])
        for p in full
        if p["confirmed_date"] < cutoff
    }
    actual = {(p["date"], p["confirmed_date"]) for p in part}
    assert expected <= actual, "未来数据不得影响已确认的早期摆动点"


def test_radar_hits_segment_starts_and_no_zero():
    dates = _dates(30)
    hit_dates = [dates[i] for i in (0, 1, 2, 5, 6, 10, 20)]
    confs, note = se.radar_hits(hit_dates, dates, 5)
    assert note is None
    assert 0 not in [c["bkp"] for c in confs], "0 是序列起点不是变点"
    assert 5 in [c["bkp"] for c in confs]
    assert all(c["delay"] == 0 for c in confs)
    empty, empty_note = se.radar_hits(None, dates, 5)
    assert empty == [] and "未提供" in empty_note


# ---- 5. 评分 -------------------------------------------------------------
def test_score_perfect_and_empty_and_random():
    gt = [20, 50, 80, 110]
    n = 150
    perfect = [{"bkp": t, "reported": t, "delay": 0} for t in gt]
    s = se.score_algorithm(gt, perfect, n, 5)
    assert s["f1"] == pytest.approx(1.0)
    assert s["covering"] == pytest.approx(1.0)
    assert s["detection_rate"] == pytest.approx(1.0)
    assert s["delay_mean"] == pytest.approx(0.0)
    assert s["detected"] == len(gt)

    empty = se.score_algorithm(gt, [], n, 5)
    assert empty["f1"] < 0.5
    assert empty["detected"] == 0
    assert empty["delay_mean"] is None

    random = [{"bkp": i, "reported": i, "delay": 0} for i in range(10, 150, 10)]
    sr = se.score_algorithm(gt, random, n, 5)
    assert sr["f1"] < s["f1"]
    assert sr["covering"] < s["covering"]


def test_score_late_detection_penalizes_delay():
    gt = [20, 50, 80, 110]
    late = [{"bkp": t, "reported": t + 4, "delay": 4} for t in gt]
    s = se.score_algorithm(gt, late, 150, 5)
    assert s["delay_mean"] == pytest.approx(4.0)
    assert s["detected"] == 4  # 仍在 ±5 容差内，位置命中


# ---- 6. evaluate() 契约 --------------------------------------------------
def test_evaluate_deterministic_and_contract():
    prices = _prices(80)
    rows = _rows(prices)
    dates = _dates(80)
    hit_dates = [dates[i] for i in (5, 6, 12, 30, 31, 55)]
    kwargs = dict(pen=0.05, radar_hit_dates=hit_dates)
    a = se.evaluate(rows, SYMBOL, **kwargs)
    b = se.evaluate(rows, SYMBOL, **kwargs)
    assert a == b, "纯函数必须确定性"

    assert a["symbol"] == SYMBOL and a["pen"] == 0.05
    assert a["n_obs"] == 80 and len(a["dates"]) == 80
    assert a["from_date"] == dates[0] and a["to_date"] == dates[-1]
    assert a["pen_options"] == list(se.PEN_OPTIONS)
    assert a["ground_truth"]["engine"].startswith("ruptures_lite.Pelt")
    assert [x["name"] for x in a["algorithms"]] == ["smc_confirmed", "pit_pelt", "radar_hits"]
    assert [x["name"] for x in a["scores"]] == ["smc_confirmed", "pit_pelt", "radar_hits"]
    assert all(0.0 <= s["f1"] <= 1.0 for s in a["scores"])
    assert all(0.0 <= s["covering"] <= 1.0 for s in a["scores"])
    assert all(s["label"] and s["description"] for s in a["scores"])
    assert len(a["notes"]) >= 5
    assert a["algorithms"][2]["bkps"], "radar_hits 应有变点"


def test_evaluate_rejects_invalid_pen():
    with pytest.raises(ValueError, match="pen"):
        se.evaluate(_rows(_prices(80)), SYMBOL, pen=0.999)
    with pytest.raises(ValueError, match="margin"):
        se.evaluate(_rows(_prices(80)), SYMBOL, margin=-1)


def test_evaluate_radar_hit_dates_are_pit():
    """只给前 m 天的命中日，radar_hits 不得出现 m 之后的变点。"""
    prices = _prices(80)
    rows = _rows(prices)
    dates = _dates(80)
    m = 40
    out = se.evaluate(rows, SYMBOL, pen=0.05, radar_hit_dates=dates[:m:3])
    rad = next(x for x in out["algorithms"] if x["name"] == "radar_hits")
    assert all(b < m for b in rad["bkps"])
