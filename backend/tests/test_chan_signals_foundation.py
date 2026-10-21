from datetime import date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.indicators import chan_signals as cs
from app.market_time import CN_TZ


def bars(n=12):
    return pl.DataFrame({
        "symbol": ["600000.SH"] * n,
        "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "open": [10.0] * n, "close": [10.0] * n,
        "high": [11.0] * n, "low": [9.0] * n,
        "volume": [123.0] * n, "amount": [123000.0] * n,
    })


def fake_runtime(monkeypatch, values):
    captured = []
    class Replay:
        def __init__(self, *args, **kwargs):
            self.previous = None
            self.ready = True
            self.baseline = False
        def update(self, row):
            captured.append(SimpleNamespace(vol=row["volume"] * 100, amount=row["amount"]))
            hit = values[(row["date"] - date(2024, 1, 1)).days] == "一买"
            self.baseline = self.previous is None
            fire = hit and self.previous is False
            self.previous = hit
            return [{"is_buy": True, "types": ["1"]}] if fire else []
    monkeypatch.setattr(cs, "ChanReplay", Replay)
    return captured


def test_edges_are_per_signal_and_initial_true_only_establishes_baseline(monkeypatch):
    fake_runtime(monkeypatch, ["一买", "一买", "其他", "一买", "一买", "其他", "一买"])
    sig = "signal_chan_bi_1_buy"
    result = cs.compute(bars(7), {sig})
    assert result[sig].to_list() == [None, False, False, True, False, False, True]


def test_distinct_confirmed_points_on_adjacent_bars_both_fire(monkeypatch):
    class Replay:
        ready = True
        baseline = False

        def __init__(self, *args, **kwargs):
            pass

        def update(self, row):
            return [{"is_buy": True, "types": ["1", "2"]}]

    monkeypatch.setattr(cs, "ChanReplay", Replay)
    signals = {"signal_chan_bi_1_buy", "signal_chan_bi_2_buy"}
    result = cs.compute(bars(2), signals)
    for signal in signals:
        assert result[signal].to_list() == [True, True]


def test_units_order_and_isolation_between_symbols(monkeypatch):
    captured = fake_runtime(monkeypatch, ["其他", "一买", "一买"])
    sig = "signal_chan_bi_1_buy"
    frame = pl.concat([bars(3), bars(3).with_columns(pl.lit("600001.SH").alias("symbol"))]).reverse()
    result = cs.compute(frame, {sig})
    assert result.select("symbol", "date").equals(frame.select("symbol", "date"))
    assert result.filter(pl.col("date") == date(2024, 1, 2))[sig].to_list() == [True, True]
    assert captured[0].vol == 12300.0  # enriched volume is lots; RawBar uses shares.
    assert captured[0].amount == 123000.0


def test_missing_bar_breaks_the_baseline_instead_of_fabricating_an_edge(monkeypatch):
    fake_runtime(monkeypatch, ["其他", "一买", "一买", "一买"])
    sig = "signal_chan_bi_1_buy"
    frame = bars(4).with_columns(
        pl.when(pl.col("date") == date(2024, 1, 2)).then(None).otherwise(pl.col("close")).alias("close")
    )
    result = cs.compute(frame, {sig})
    assert result[sig].to_list() == [None, None, None, False]
    assert cs.coverage(result, {sig})["signals"][0]["reasons"]["missing_data"] == 1


def test_unclosed_daily_bar_never_fires_and_timezone_is_explicit(monkeypatch):
    fake_runtime(monkeypatch, ["其他", "一买"])
    sig = "signal_chan_bi_1_buy"
    result = cs.compute(bars(2), {sig}, now=datetime(2024, 1, 2, 14, 59, tzinfo=CN_TZ))
    assert result[sig].to_list() == [None, None]
    assert cs.coverage(result, {sig})["signals"][0]["reasons"]["unclosed_bar"] == 1


def test_duplicate_daily_bars_are_rejected(monkeypatch):
    fake_runtime(monkeypatch, ["其他"])
    with pytest.raises(ValueError, match="重复"):
        cs.compute(pl.concat([bars(1), bars(1)]), {"signal_chan_bi_1_buy"})


def test_no_selected_signals_never_loads_optional_dependency(monkeypatch):
    monkeypatch.setattr(cs, "_load_runtime", lambda: pytest.fail("unused CZSC was loaded"))
    frame = bars()
    assert cs.compute(frame, set()).equals(frame)


def test_selected_signals_fail_clearly_without_optional_dependency(monkeypatch):
    def unavailable():
        raise ValueError("CZSC 信号组件未安装")
    monkeypatch.setattr(cs, "_load_runtime", unavailable)
    with pytest.raises(ValueError, match="CZSC"):
        cs.compute(bars(), {"signal_chan_bi_1_buy"})


def test_dependency_failures_disable_only_chan(monkeypatch):
    def broken():
        raise ImportError("missing vendored module")
    monkeypatch.setattr(cs, "_load_runtime", broken)
    assert cs.availability()["available"] is False
    frame = bars()
    assert cs.compute(frame, set()).equals(frame)
    with pytest.raises(ImportError):
        cs.compute(frame, {"signal_chan_bi_1_buy"})


@pytest.fixture(scope="module")
def native_history():
    from tests.chan_fixtures import synthetic_bars
    return pl.DataFrame([{**{k: v for k, v in row.items() if k != "dt"},
                          "date": row["dt"].date(), "symbol": "600000.SH",
                          "amount": row["volume"] * row["close"] * 100}
                         for row in synthetic_bars(5, 3000)])


def test_all_native_families_and_future_prefix_invariance(native_history):
    wanted = set(cs.SIGNALS)
    result = cs.compute(native_history, wanted)
    prefix = cs.compute(native_history.head(600), wanted)
    assert result.head(600).equals(prefix)
    for kind in ("1", "1p", "2", "2s", "3a", "3b"):
        assert sum(result[name].sum() or 0 for name in wanted if cs.SIGNALS[name][0] == kind) > 0
    assert cs.coverage(result, wanted)["profile_id"] == cs.PROFILE_ID


def test_structure_shortage_is_reported_separately_from_no_signal():
    sig = "signal_chan_bi_3a_buy"
    result = cs.compute(bars(), {sig})
    assert result[sig].null_count() == len(result)
    assert cs.coverage(result, {sig})["signals"][0]["reasons"] == {"insufficient_structure": len(result)}

