from datetime import date
from types import SimpleNamespace

import pytest

from app.api.strategy import _strategy_detail
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.indicators import chan_signals
from app.strategy.engine import StrategyDataContext, StrategyDef, StrategyEngine


def strategy(entry_signals=None, exit_signals=None, required_features=None):
    return StrategyDef(meta={"id": "czsc_test", "name": "CZSC test"}, basic_filter={},
        entry_signals=entry_signals or [], exit_signals=exit_signals or [], stop_loss=None,
        trailing_stop=None, trailing_take_profit_activate=None, trailing_take_profit_drawdown=None,
        max_hold_days=None, filter_fn=None, filter_history_fn=None, lookback_days=1, source="custom",
        required_features=frozenset(required_features or []))


@pytest.mark.parametrize("field", ["entry_signals", "exit_signals", "required_features"])
def test_retired_declarations_cannot_be_reactivated_by_empty_overrides(field):
    spec = strategy(entry_signals=[], exit_signals=[], **({} if field != "required_features" else {field: []}))
    setattr(spec, field, ["signal_czsc_first_buy"])
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    overrides = {"entry_signals": [], "exit_signals": []}
    detail = _strategy_detail(spec, overrides, engine)
    assert detail["id"] == "czsc_test"
    assert detail["execution_available"] is False
    assert detail["required_features"] == sorted(spec.required_features)
    assert "已停用" in detail["execution_unavailable_reason"]
    with pytest.raises(ValueError, match="已停用"):
        engine.run("czsc_test", StrategyDataContext(asset_type="stock", timeframe="1d", as_of=date(2024, 1, 2)), overrides=overrides)


def test_retired_backtest_rejects_before_loading_data():
    spec = strategy(entry_signals=["signal_czsc_first_buy"])
    service = StrategyBacktestService(SimpleNamespace(), SimpleNamespace(get=lambda _: spec))
    result = service.run(StrategyBacktestConfig(strategy_id="czsc_test", symbols=None,
                                              start=date(2024, 1, 1), end=date(2024, 2, 1)))
    assert "已停用" in result.error


def test_legacy_ids_are_never_aliased_to_new_meanings():
    with pytest.raises(ValueError, match="已停用"):
        chan_signals.selected(["czsc_first_buy"])
    assert chan_signals.selected(["chan_bi_1_buy"]) == {"signal_chan_bi_1_buy"}


def test_explicit_bulk_run_rejects_retired_strategy_before_data_access(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.api import screener

    spec = strategy(entry_signals=["signal_czsc_first_buy"])
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=engine)))
    monkeypatch.setattr(screener, "ScreenerService", lambda *a, **kw: SimpleNamespace())
    with pytest.raises(HTTPException) as error:
        screener.run_all(request, {"as_of": "2024-01-02", "strategy_ids": ["czsc_test"]})
    assert error.value.status_code == 400
    assert "已停用" in str(error.value.detail)
    assert engine.list_strategies()[0]["execution_available"] is False


def test_default_bulk_run_skips_retired_overrides(tmp_path, monkeypatch):
    from app.api import strategy as strategy_api
    from app.services import screener

    spec = strategy(entry_signals=[], exit_signals=[])
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    seen = []

    class Service:
        def __init__(self, *args, **kwargs):
            pass

        def build_strategy_context(self, engine, as_of, ids, **kwargs):
            seen.extend(ids)

    monkeypatch.setattr(screener, "ScreenerService", Service)
    monkeypatch.setattr(strategy_api.strategy_config, "list_overrides", lambda _: {
        "czsc_test": {"entry_signals": ["signal_czsc_first_buy"]},
    })
    monkeypatch.setattr(engine, "run_all", lambda *a, **kw: {})
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=engine)))
    result = strategy_api.run_all(strategy_api.RunAllRequest(as_of=date(2024, 1, 2)), request)
    assert result["results"] == {}
    assert seen == []


def test_strategy_monitor_rejects_chan_and_retired_signals_before_data_work():
    from app.strategy.monitor import chan_strategy_monitor_warning

    chan_spec = strategy(entry_signals=["signal_chan_bi_1_buy"])
    legacy_spec = strategy(entry_signals=["signal_czsc_first_buy"])
    assert "暂不支持盘中监控" in chan_strategy_monitor_warning(chan_spec)
    assert "已停用" in chan_strategy_monitor_warning(legacy_spec)
    assert chan_strategy_monitor_warning(strategy()) is None


def test_strategy_monitor_warning_accepts_lightweight_strategy_without_required_features():
    from app.strategy.monitor import chan_strategy_monitor_warning

    light = SimpleNamespace(
        meta={"id": "light"},
        entry_signals=["signal_ma20_breakout"],
        exit_signals=[],
    )
    assert chan_strategy_monitor_warning(light) is None


def test_strategy_history_window_includes_chan_warmup_and_skips_retired():
    active = strategy(entry_signals=["signal_chan_bi_1_buy"])
    retired = strategy(entry_signals=["signal_czsc_first_buy"])
    engine = StrategyEngine([])
    engine._strategies.update({"active": active, "retired": retired})
    assert engine.required_history_bars(["active", "retired"]) == chan_signals.WARMUP_BARS + 1


def test_plain_strategy_still_runs_with_optional_coverage_field():
    import polars as pl

    spec = strategy()
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    result = engine.run("czsc_test", StrategyDataContext(
        asset_type="stock", timeframe="1d", as_of=date(2024, 1, 2),
        current=pl.DataFrame({"symbol": ["600000.SH"], "date": [date(2024, 1, 2)], "close": [10.0]}),
    ))
    assert result.total == 1
    assert result.czsc_coverage is None


@pytest.mark.parametrize(
    ("entry_signals", "exit_signals", "entry_fill", "exit_fill", "expected"),
    [
        (["signal_chan_bi_1_buy"], [], "close_t", "open_t+1", "入场成交须选择次交易日开盘"),
        ([], ["signal_chan_bi_1_sell"], "open_t+1", "close_t", "出场成交须选择次交易日开盘"),
    ],
)
def test_chan_backtest_rejects_close_fill_before_loading_market_data(
    entry_signals, exit_signals, entry_fill, exit_fill, expected
):
    spec = strategy(entry_signals=entry_signals, exit_signals=exit_signals)
    service = StrategyBacktestService(SimpleNamespace(), SimpleNamespace(get=lambda _: spec))
    result = service.run(StrategyBacktestConfig(
        strategy_id="czsc_test", symbols=None, start=date(2024, 1, 1), end=date(2024, 2, 1),
        entry_fill=entry_fill, exit_fill=exit_fill,
    ))
    assert expected in result.error


def test_monitor_blocked_chan_rule_clears_only_its_runtime_state():
    import polars as pl

    from app.strategy.monitor import MonitorRuleEngine

    blocked = strategy(entry_signals=["signal_czsc_first_buy"])
    ordinary = strategy()

    class Engine:
        def get(self, strategy_id):
            return {"blocked": blocked, "ordinary": ordinary}.get(strategy_id)

    monitor = MonitorRuleEngine()
    monitor.set_strategy_engine(Engine())
    blocked_rule = {"id": "blocked-rule", "type": "strategy", "strategy_id": "blocked"}
    ordinary_rule = {"id": "ordinary-rule", "type": "strategy", "strategy_id": "ordinary"}
    monitor.set_rules([blocked_rule, ordinary_rule])
    monitor._strategy_pools = {
        ("blocked-rule", "blocked", "stock"): {"600000.SH"},
        ("ordinary-rule", "ordinary", "stock"): {"600001.SZ"},
    }
    monitor._strategy_signal_state = {
        ("blocked-rule", "blocked", "stock", "buy_signal"): ("2024-01-02", {"600000.SH"}),
        ("ordinary-rule", "ordinary", "stock", "buy_signal"): ("2024-01-02", {"600001.SZ"}),
    }
    monitor._strategy_signal_seen = {
        ("blocked-rule", "blocked", "stock", "buy_signal", "600000.SH"): "2024-01-02",
        ("ordinary-rule", "ordinary", "stock", "buy_signal", "600001.SZ"): "2024-01-02",
    }
    monitor._last_fire = {
        ("blocked-rule", "600000.SH", "buy_signal"): 100,
        ("ordinary-rule", "600001.SZ", "buy_signal"): 200,
    }
    monitor._latest_strategy_results = {"blocked": {"total": 1}, "ordinary": {"total": 1}}
    monitor._building_strategy_results = {"blocked": {"total": 1}, "ordinary": {"total": 1}}
    monitor._latest_strategy_result_ids = {"blocked", "ordinary"}
    shared = object()
    monitor._active_matrix_snapshots = {"stock": shared}

    monitor._match_strategy(
        pl.DataFrame({"symbol": ["600000.SH"], "close": [10.0]}), blocked_rule,
    )

    assert not any(key[1] == "blocked" for key in monitor._strategy_pools)
    assert not any(key[1] == "blocked" for key in monitor._strategy_signal_state)
    assert not any(key[1] == "blocked" for key in monitor._strategy_signal_seen)
    assert monitor._last_fire == {("ordinary-rule", "600001.SZ", "buy_signal"): 200}
    assert set(monitor.latest_strategy_results()) == {"ordinary"}
    assert set(monitor._building_strategy_results) == {"ordinary"}
    # 保留本轮变化通知，使策略页面重新读取清理后的结果快照。
    assert monitor._latest_strategy_result_ids == {"blocked", "ordinary"}
    assert monitor._active_matrix_snapshots == {"stock": shared}


def test_empty_selection_keeps_coverage_for_requested_pool(monkeypatch):
    from datetime import timedelta

    import polars as pl

    sig = "signal_chan_bi_1_buy"

    class Replay:
        def __init__(self, *args, **kwargs):
            self.previous = None
            self.ready = True
            self.baseline = False

        def update(self, row):
            hit = (row["date"] - date(2024, 1, 1)).days >= 1
            self.baseline = self.previous is None
            fire = hit and self.previous is False
            self.previous = hit
            return [{"is_buy": True, "types": ["1"]}] if fire else []

    monkeypatch.setattr(chan_signals, "ChanReplay", Replay)
    frame = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH", "600000.SH", "600001.SH", "600001.SH", "600001.SH"],
        "date": [date(2024, 1, 1) + timedelta(days=i % 3) for i in range(6)],
        "open": [10.0] * 6, "close": [10.0] * 6, "high": [11.0] * 6, "low": [9.0] * 6,
        "volume": [123.0] * 6, "amount": [123000.0] * 6,
    })
    spec = strategy(entry_signals=[sig], exit_signals=[])
    spec.basic_filter = {"enabled": False}
    spec.filter_history_fn = lambda df, params: df.head(0)
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    target = date(2024, 1, 3)
    result = engine.run("czsc_test", StrategyDataContext(
        asset_type="stock", timeframe="1d", as_of=target,
        current=frame.filter(pl.col("date") == target), history=frame,
    ), pool=["600000.SH"])
    assert result.rows == []
    report = result.czsc_coverage["signals"][0]
    assert report["ready_rows"] == 1
    assert report["unavailable_rows"] == 0


def test_strategy_coverage_counts_only_as_of_and_marks_unjoined_rows(monkeypatch):
    import polars as pl

    sig = "signal_chan_bi_1_buy"
    reason = chan_signals.reason_column(sig)
    seen = {}

    def compute(df, needed):
        return df.with_columns(pl.lit(False).alias(sig), pl.lit("baseline").alias(reason))

    monkeypatch.setattr(chan_signals, "compute", compute)
    real_coverage = chan_signals.coverage
    monkeypatch.setattr(chan_signals, "coverage", lambda df, names: seen.update({
        "dates": df["date"].to_list(),
        "symbols": df["symbol"].to_list(),
        "reasons": df[reason].to_list(),
    }) or real_coverage(df, names))
    spec = strategy(entry_signals=[sig], exit_signals=[])
    spec.basic_filter = {"enabled": False}
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    current = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH", "600001.SH"],
        "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 3)],
        "close": [10.0, 10.0, 10.0],
    })
    history = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH"],
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "open": [10.0, 10.0], "close": [10.0, 10.0], "high": [11.0, 11.0], "low": [9.0, 9.0],
        "volume": [123.0, 123.0], "amount": [123000.0, 123000.0],
    })
    result = engine.run("czsc_test", StrategyDataContext(
        asset_type="stock", timeframe="1d", as_of=date(2024, 1, 3),
        current=current, history=history,
    ))
    assert seen["dates"] == [date(2024, 1, 3), date(2024, 1, 3)]
    assert seen["symbols"] == ["600000.SH", "600001.SH"]
    assert seen["reasons"] == ["baseline", "missing_data"]
    assert result.czsc_coverage["signals"][0]["unavailable_rows"] == 1


@pytest.mark.parametrize("method", ["save", "patch", "reset", "reset_unknown"])
def test_chan_config_changes_invalidate_only_affected_strategy(tmp_path, method):
    from app.api.strategy import SaveConfigRequest, patch_config, reset_config, save_config
    from app.services import strategy_cache
    from app.strategy import config as strategy_config

    engine = StrategyEngine([])
    spec = strategy(entry_signals=[], exit_signals=[])
    engine._strategies["czsc_test"] = spec
    prior = {"entry_signals": ["signal_chan_bi_1_buy"]} if method.startswith("reset") else {}
    if method == "reset_unknown":
        prior = {"entry_signals": ["signal_czsc_retired"]}
    strategy_config.save_override(tmp_path, "czsc_test", prior)
    strategy_cache.write_cache(tmp_path, "2024-01-01", {
        "czsc_test": {"rows": [{"symbol": "600000.SH"}], "total": 1},
        "unrelated": {"rows": [{"symbol": "600001.SH"}], "total": 1},
    })
    calls = []
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        strategy_engine=engine, repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        monitor_engine=SimpleNamespace(invalidate_strategy_state=lambda **kw: calls.append(kw)),
    )))
    if method.startswith("reset"):
        reset_config("czsc_test", request)
    else:
        handler = save_config if method == "save" else patch_config
        handler(SaveConfigRequest(strategy_id="czsc_test", overrides={"entry_signals": ["signal_chan_bi_1_buy"]}), request)
    cached = strategy_cache.read_cache(tmp_path)
    assert set(cached["results"]) == {"unrelated"}
    assert "czsc_test" not in cached["today_ever_rows"]
    assert calls == [{"strategy_id": "czsc_test"}]


def test_screener_strategy_list_uses_saved_override_for_availability(tmp_path, monkeypatch):
    from app.api import screener
    from app.strategy import config as strategy_config

    spec = strategy(entry_signals=[], exit_signals=[])
    spec.meta = {**spec.meta, "asset_types": ["stock"], "timeframes": ["1d"]}
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    strategy_config.save_override(tmp_path, "czsc_test", {"entry_signals": ["signal_czsc_first_buy"]})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        strategy_engine=engine,
    )))
    listed = screener.strategies(request, asset_type="stock", timeframe="1d")
    preset = listed["presets"][0]
    assert preset["execution_available"] is False
    assert "已停用" in preset["execution_unavailable_reason"]
