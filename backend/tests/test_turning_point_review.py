from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import turning_point_review as api
from app.services.turning_point_review import (
    ReviewScope,
    _alerts,
    _compile,
    _flags,
    _pivots,
    _with_limit_prices,
    build_review,
    match_events,
)
from app.strategy.signal_seeds import SEED_SIGNALS


def pivot(kind, index, name):
    return {"id": name, "type": kind, "index": index}


def alert(index):
    return {"id": str(index), "start_index": index}


def test_overlapping_windows_do_not_reuse_alerts_or_steal_newer_alerts():
    pivots = [pivot("low", 50, "p1"), pivot("low", 65, "p2"), pivot("high", 51, "p3")]
    result = match_events(pivots, [alert(48)], "low")
    assert result["covered_count"] == 1
    assert result["missed_count"] == 1
    result = match_events(pivots, [alert(21), alert(48), alert(81)], "low")
    assert result["covered_count"] == 2
    assert result["unmatched_alert_count"] == 1
    assert match_events(pivots, [alert(48)], "high")["covered_count"] == 1


def test_window_boundaries_and_duplicate_are_distinct():
    result = match_events([pivot("high", 42, "p")], [alert(i) for i in [11, 12, 26, 27, 36, 37, 41, 42]], "high")
    assert result["lead_windows"] == {"1_5": 0, "6_15": 0, "16_30": 1}
    assert result["duplicate_alert_count"] == 5
    assert result["unmatched_alert_count"] == 2
    assert {c["lead_bars"]: c["window"] for c in result["cases"] if c["pivot"]} == {
        30: "16_30", 16: "16_30", 15: "6_15", 6: "6_15", 5: "1_5", 1: "1_5",
    }


def test_incomplete_windows_and_true_run_after_unknown():
    result = match_events([pivot("low", 0, "p")], [alert(10)], "low", pivot_complete=lambda p: False,
                          alert_complete=lambda a: False)
    assert result["indeterminate_alert_count"] == result["indeterminate_pivot_count"] == 1
    assert result["missed_count"] == result["unmatched_alert_count"] == 0
    flags = [True, False, True, True, None, True, False, True]
    assert [a["trigger_dates"] for a in _alerts(flags, list("abcdefgh"))] == [["c", "d"], ["h"]]


def _frame():
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(180)]
    close = [100.0 + (i % 50 if i % 50 < 25 else 50 - i % 50) for i in range(180)]
    return pl.DataFrame({"symbol": ["600519.SH"] * len(days), "date": days, "close": close, "open": close,
                         "high": [v + 1 for v in close], "low": [v - 1 for v in close],
                         "volume": [100.0] * len(days), "amount": [1000.0] * len(days)})


def test_atom_and_composite_use_past_only_and_report_missing_roots():
    frame = _frame()
    seeds = {sig["id"]: sig for sig in SEED_SIGNALS}
    seeds["historical"] = {"id": "historical", "name": "历史", "type": "composite", "kind": "both",
                           "timeframe": "daily", "expr": {"ref": "n_day_high", "days": 2}}
    seeds["missing"] = {"id": "missing", "name": "缺价", "kind": "both", "timeframe": "daily",
                        "conditions": [{"left": "raw_close", "op": ">", "right": "5"}]}
    for sid in ("n_day_high", "historical"):
        expr, roots, _ = _compile(sid, seeds)
        flags = _flags(frame, expr, roots, [])
        assert flags[:100] == _flags(frame.head(100), expr, roots, [])
        assert flags[0] is None
        assert any(item is True for item in flags[60:])
    scope = ReviewScope(frame["date"][65], frame["date"][150], ("n_day_high", "missing"))
    result = build_review(frame, seeds, scope, [])
    assert _pivots(frame)
    assert [s["signal_id"] for s in result["signals"]] == ["n_day_high", "missing"]
    assert result["signals"][1]["status"] == "unavailable"
    assert result["signals"][1]["directions"] == {}
    assert all(c["signal_id"] == "n_day_high" for c in result["cases"])


def test_api_summary_pagination_and_chart(monkeypatch, tmp_path):
    frame = _frame()
    for day, row in frame.group_by("date"):
        path = tmp_path / "kline_daily_enriched" / f"date={day[0]}" / "part.parquet"
        path.parent.mkdir(parents=True)
        row.write_parquet(path)
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(api.router)
    monkeypatch.setattr(api, "signal_catalog", lambda _: {sig["id"]: sig for sig in SEED_SIGNALS})
    client = TestClient(app)
    base = "/api/turning-point-review"
    common = {"symbols": "600519.SH", "start": "2025-02-10", "end": "2025-06-10"}
    assert client.get(base + "/scope").json()["level"] == "L2"
    assert client.get(base, params=common).status_code == 422
    response = client.get(base, params={**common, "signal_ids": "n_day_high"})
    assert response.status_code == 200, response.text
    summary = response.json()
    assert not summary["errors"]
    assert len(summary["summary"]) == 2
    assert "cases" not in summary["results"][0]
    args = {**common, "signal_id": "n_day_high", "limit": 1, "snapshot_sha256": summary["snapshot_sha256"]}
    page = client.get(base + "/cases", params=args).json()
    assert page["total"] > 1 and len(page["cases"]) == 1
    case = page["cases"][0]
    detail = client.get(base + "/cases", params={**args, "case_id": case["case_id"]}).json()
    assert detail["case"] == case and detail["bars"]
    assert "bars" not in case
    batch_args = {**args, "include_bars": "true"}
    assert client.get(base + "/cases", params={**batch_args, "limit": 25}).status_code == 422
    assert client.get(base + "/cases", params={k: v for k, v in batch_args.items() if k != "snapshot_sha256"}).status_code == 422
    assert client.get(base + "/cases", params={**batch_args, "snapshot_sha256": "0" * 64}).status_code == 409
    batch = client.get(base + "/cases", params=batch_args).json()
    assert batch["total"] == page["total"] and len(batch["cases"]) == 1
    row = batch["cases"][0]
    zoom = client.get(base + "/cases", params={**args, "case_id": row["case_id"]}).json()
    assert row["bars"] == zoom["bars"] and isinstance(row["bars_truncated"], bool)
    assert 0 < len(row["bars"]) <= 66
    assert all(bar["volume"] == 100 for bar in row["bars"])
    second = client.get(base + "/cases", params={**batch_args, "offset": 1}).json()
    assert second["cases"][0]["case_id"] != row["case_id"]
    assert client.get(base + "/cases", params={**batch_args, "offset": batch["total"]}).json()["cases"] == []
    assert client.get(base + "/cases", params={**args, "snapshot_sha256": "0" * 64}).status_code == 409
    assert client.get(base, params={**common, "signal_ids": "unknown"}).status_code == 422
    assert client.get(base, params={**common, "signal_ids": "n_day_high", "direction": "high,typo"}).status_code == 422


def test_known_market_gap_censors_label_and_prevents_a_new_alert():
    frame = _frame()
    point = _pivots(frame)[1]
    missing = frame["date"][point["index"] - 1]
    damaged = frame.filter(pl.col("date") != missing)
    catalog = {sig["id"]: sig for sig in SEED_SIGNALS}
    scope = ReviewScope(frame["date"][0], frame["date"][-1], ("n_day_high",))
    result = build_review(damaged, catalog, scope, frame["date"].to_list(), (missing,))
    assert result["directions"][point["type"]]["label_excluded_for_market_gap"] >= 1
    result = match_events([pivot("low", 30, "p")], [alert(20)], "low", pair_complete=lambda a, b: False)
    assert not result["covered_count"]
    assert not _alerts([False, True, True], ["x", "y", "z"], frozenset({1}))


def test_case_chart_marks_clipped_context_and_market_partition_gap():
    first = date(2025, 1, 1)
    chart = [{"date": first + timedelta(days=i)} for i in range(70)]
    row = {"alert": {"start_index": 20}, "pivot": {"index": 50}}
    assert not api._case_chart(row, chart)["bars_truncated"]
    assert api._case_chart(row, chart[:65])["bars_truncated"]
    assert api._case_chart(row, chart, (first + timedelta(days=25),))["bars_truncated"]
    assert len(api._case_chart(row, chart)["bars"]) == 66


def test_bollinger_uses_existing_adjusted_rolling_indicators():
    frame = _frame()
    catalog = {sig["id"]: sig for sig in SEED_SIGNALS}
    for sid in ("boll_breakdown_lower", "boll_breakout_upper"):
        expr, roots, _ = _compile(sid, catalog)
        flags = _flags(frame, expr, roots, [])
        assert flags[:19] == [None] * 19
        assert all(flag is not None for flag in flags[19:])
        assert flags[:100] == _flags(frame.head(100), expr, roots, [])


def test_limit_flags_use_dated_records_and_inference_without_turning_unknown_into_false():
    days = [date(2026, 4, 1) + timedelta(days=i) for i in range(12)]
    frame = pl.DataFrame({"symbol": ["600519.SH"] * len(days), "date": days,
                         "close": [100.] * len(days), "open": [100.] * len(days),
                         "raw_close": [100.] * len(days), "raw_high": [100.] * len(days),
                         "raw_low": [100.] * len(days)})
    inst = pl.DataFrame({"symbol": ["600519.SH"], "as_of": [days[6]], "limit_up": [100.], "limit_down": [100.]})
    facts = _with_limit_prices(frame, days, (inst, pl.DataFrame()), frozenset({8}))
    catalog = {sig["id"]: sig for sig in SEED_SIGNALS}
    for sid in ("broken_limit_up", "limit_down", "limit_down_recovery", "limit_up"):
        expr, roots, _ = _compile(sid, catalog)
        flags = _flags(facts, expr, roots, days)
        assert flags[0] is None and flags[8] is None
        assert flags[6] is (sid in {"limit_up", "limit_down"})
        assert flags[7] is False
    for side in ("up", "down"):
        assert facts[f"_limit_{side}_price_source"][6:9].to_list() == ["recorded", "inferred", None]
