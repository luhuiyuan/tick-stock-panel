from datetime import date

import pytest
from threading import Event
from types import SimpleNamespace

import polars as pl

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.indicators import chan_signals
from app.strategy.engine import StrategyDef


def test_chan_panel_preparation_forwards_progress_cancel_and_single_worker(monkeypatch):
    panel = pl.DataFrame({"symbol": ["600000.SH"], "date": [date(2024, 1, 2)]})
    plan = SimpleNamespace(
        base_columns=frozenset(panel.columns), indicator_columns=frozenset(),
        signal_columns=frozenset({"signal_chan_bi_1_buy"}),
        instrument_columns=frozenset(), execution_backend="polars_expr",
    )
    engine = BacktestEngine(None)
    monkeypatch.setattr(engine, "load_panel", lambda *args, **kwargs: panel)
    monkeypatch.setattr("app.indicators.pipeline.compute_indicators", lambda df, needed: df)
    events = []
    cancelled = Event()

    def compute_signals(df, needed, *, progress_cb, cancel_event, czsc_max_workers):
        assert needed == {"signal_chan_bi_1_buy"}
        assert cancel_event is cancelled
        assert czsc_max_workers == 1
        progress_cb({"phase": "chan_signals", "completed": 1, "total": 1})
        return df.with_columns(pl.lit(False).alias("signal_chan_bi_1_buy"))

    monkeypatch.setattr("app.indicators.pipeline.compute_signals", compute_signals)
    result = engine.load_panel_for_backtest(
        None, date(2024, 1, 1), date(2024, 1, 2), plan,
        progress_cb=events.append, cancel_event=cancelled, czsc_max_workers=1,
    )
    assert result["signal_chan_bi_1_buy"].to_list() == [False]
    assert events == [{"phase": "chan_signals", "completed": 1, "total": 1}]


def test_chan_strategy_backtest_returns_cancelled_without_partial_result():
    spec = StrategyDef(
        meta={"id": "czsc", "name": "CZSC"}, basic_filter={"enabled": False},
        entry_signals=["signal_chan_bi_1_buy"], exit_signals=[], stop_loss=None,
        trailing_stop=None, trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None, max_hold_days=None, filter_fn=None,
        filter_history_fn=None, lookback_days=1, source="custom",
    )
    cancelled = Event()
    events = []

    def load_panel(*args, progress_cb, cancel_event, czsc_max_workers, **kwargs):
        assert czsc_max_workers == 1
        assert cancel_event is cancelled
        progress_cb({"phase": "chan_signals", "completed": 0, "total": 10})
        cancelled.set()
        raise chan_signals.ChanReplayCancelledError("回测已取消")

    service = StrategyBacktestService(
        SimpleNamespace(load_panel_for_backtest=load_panel),
        SimpleNamespace(get=lambda sid: spec),
    )
    result = service.run(
        StrategyBacktestConfig(
            strategy_id="czsc", symbols=None, start=date(2024, 1, 1), end=date(2024, 1, 2),
        ),
        events.append, cancelled,
    )
    assert result.error == "cancelled"
    assert result.trades == []
    assert result.stats == {}
    assert events == [{"phase": "chan_signals", "completed": 0, "total": 10}]


def test_successful_chan_backtest_keeps_coverage_when_stats_are_filtered(monkeypatch):
    from app.backtest.strategy import BacktestResultPolicy

    spec = StrategyDef(
        meta={"id": "czsc", "name": "CZSC"}, basic_filter={"enabled": False},
        entry_signals=["signal_chan_bi_1_buy"], exit_signals=[], stop_loss=None,
        trailing_stop=None, trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None, max_hold_days=None, filter_fn=None,
        filter_history_fn=None, lookback_days=1, source="custom",
    )
    panel = pl.DataFrame({
        "symbol": ["600000.SH"],
        "date": [date(2024, 1, 2)],
        "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "volume": [100.0], "amount": [1000.0],
        "signal_chan_bi_1_buy": [True],
    })
    report = {
        "engine": "chan.py",
        "version": "test",
        "signals": [{"signal_id": "signal_chan_bi_1_buy", "ready_rows": 1}],
    }

    def load_panel(*args, **kwargs):
        return panel

    class _Simulation:
        def __init__(self):
            self.stats = {"total_return": 0.01, "statistics_ms": 1.0, "unrelated": 1}
            self.trades = []
            self.equity_curve = []
            self.drawdown_curve = []
            self.per_symbol_stats = []

        def simulate_market_matrix(self, *args, **kwargs):
            return self

    monkeypatch.setattr(chan_signals, "coverage", lambda df, names: report)
    monkeypatch.setattr("app.backtest.strategy.build_market_matrix", lambda *args, **kwargs: object())
    service = StrategyBacktestService(_Simulation(), SimpleNamespace(get=lambda sid: spec))
    service.engine.load_panel_for_backtest = load_panel
    result = service.run(
        StrategyBacktestConfig(
            strategy_id="czsc", symbols=["600000.SH"], start=date(2024, 1, 1), end=date(2024, 1, 2),
        ),
        result_policy=BacktestResultPolicy(
            required_stats=frozenset({"total_return"}),
            include_monte_carlo=False,
            include_curves=False,
            include_trades=False,
            include_per_symbol_stats=False,
            include_return_distribution=False,
            include_benchmark=False,
            include_strategy_info=False,
        ),
    )
    assert result.error is None, result.error
    assert result.stats["czsc_coverage"] == report
    assert result.stats["total_return"] == 0.01
    assert "unrelated" not in result.stats


@pytest.mark.parametrize("as_object", [False, True])
def test_sse_error_preserves_czsc_coverage(monkeypatch, as_object):
    import json
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import backtest
    from app.backtest.strategy import StrategyBacktestResult

    report = {"version": "test", "signals": [{"signal_id": "signal_chan_bi_1_buy", "ready_rows": 0}]}
    result = {"error": "没有买入信号", "stats": {"czsc_coverage": report, "other": 1}}
    if as_object:
        result = StrategyBacktestResult(run_id="test", config={}, error=result["error"], stats=result["stats"])
    job = backtest._BacktestJob("czsc-test")
    job.result, job.done, job.finish_ts = result, True, time.time()
    monkeypatch.setattr(backtest, "_make_job_key", lambda *args, **kwargs: "czsc-test")
    monkeypatch.setattr(backtest, "_running_jobs", {"czsc-test": job})
    app = FastAPI()
    app.include_router(backtest.router)
    response = TestClient(app).get("/api/backtest/strategy/stream", params={
        "strategy_id": "czsc_test", "start": "2024-01-01", "end": "2024-01-02",
    })
    assert response.status_code == 200
    assert "event: error" in response.text
    payload = json.loads(response.text.split("data: ", 1)[1].strip())
    assert payload == {"message": "没有买入信号", "stats": {"czsc_coverage": report}}


def test_sse_done_includes_czsc_coverage_and_cancel_omits_it(monkeypatch):
    import json
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import backtest
    from app.backtest.strategy import StrategyBacktestResult

    report = {"version": "test", "signals": [{"signal_id": "signal_chan_bi_1_buy", "ready_rows": 1}]}
    app = FastAPI()
    app.include_router(backtest.router)
    client = TestClient(app)

    done = backtest._BacktestJob("czsc-done")
    done.result = StrategyBacktestResult(
        run_id="done", config={}, stats={"czsc_coverage": report, "total_return": 0.01},
    )
    done.done, done.finish_ts = True, time.time()
    monkeypatch.setattr(backtest, "_make_job_key", lambda *args, **kwargs: "czsc-done")
    monkeypatch.setattr(backtest, "_running_jobs", {"czsc-done": done})
    response = client.get("/api/backtest/strategy/stream", params={
        "strategy_id": "czsc_test", "start": "2024-01-01", "end": "2024-01-02",
    })
    assert response.status_code == 200
    assert "event: done" in response.text
    payload = json.loads(response.text.split("data: ", 1)[1].strip())
    assert payload["error"] is None
    assert payload["stats"]["czsc_coverage"] == report

    cancelled = backtest._BacktestJob("czsc-cancel")
    cancelled.result = StrategyBacktestResult(run_id="cancel", config={}, error="cancelled", stats={"czsc_coverage": report})
    cancelled.done, cancelled.finish_ts = True, time.time()
    monkeypatch.setattr(backtest, "_make_job_key", lambda *args, **kwargs: "czsc-cancel")
    monkeypatch.setattr(backtest, "_running_jobs", {"czsc-cancel": cancelled})
    response = client.get("/api/backtest/strategy/stream", params={
        "strategy_id": "czsc_test", "start": "2024-01-01", "end": "2024-01-02",
    })
    payload = json.loads(response.text.split("data: ", 1)[1].strip())
    assert payload == {"message": "回测已取消"}
