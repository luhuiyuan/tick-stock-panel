from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.surge_research import router
from app.services.surge_research import find_surges


def rows(prices):
    return [("600010.SH", date(2025, 1, 1) + timedelta(days=i), p, p) for i, p in enumerate(prices)]


def test_boundaries_and_pending():
    # Exactly 3% starts, exactly 15% qualifies, base day excluded.
    (r,) = find_surges(rows([100, 103, 115, 115, 115]))
    assert (r["start_date"].day, r["end_date"].day, r["days"], r["pending"]) == (2, 3, 2, False)
    assert not list(find_surges(rows([100, 114.99])))
    for prices in [[100, 115], [100, 115, 111]]:
        assert next(find_surges(rows(prices)))["pending"]
    assert not next(find_surges(rows([100, 115, 109])))["pending"]


def test_bridge_and_restart():
    # Exactly 5% pullback can reconnect on a new high.
    (r,) = find_surges(rows([100, 120, 114, 121, 121, 121]))
    assert (r["days"], r["end_close"]) == (3, 121)
    # Rebound >=3% but below old peak: reconsider it as a fresh start.
    a, b = find_surges(rows([100, 120, 114, 118, 135, 135, 135]))
    assert a["days"] == 1 and b["base_close"] == 114 and b["days"] == 2
    assert a["end_date"] < b["start_date"]
    assert not list(
        find_surges(rows([100, 104, 103, 107, 106, 110, 109, 113, 112, 116]))
    )  # daily compound too low


def test_equal_daily_threshold_and_adjustment_break():
    prices = [100 * 1.03**i for i in range(6)]
    (r,) = find_surges(rows(prices))
    assert r["daily_gain"] == pytest.approx(0.03)
    sample = rows([100, 116, 400, 410])
    sample[2] = (*sample[2][:3], 116)
    (r,) = find_surges(sample)
    assert r["end_date"].day == 2 and not r["pending"]


def test_routes_snapshot_isolation_and_counts(tmp_path):
    bars = rows([100, 115, 115, 115, 140])
    for bar in bars:
        root = tmp_path / "kline_daily_enriched" / f"date={bar[1]}"
        root.mkdir(parents=True)
        pl.DataFrame(
            [bar], schema=["symbol", "date", "close", "raw_close"], orient="row"
        ).write_parquet(root / "part.parquet")
    legacy = tmp_path / "rise_research" / "runs.parquet"
    legacy.parent.mkdir()
    legacy.write_bytes(b"unchanged")
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        get_instruments=lambda: None,
        load_enriched_ohlcv=lambda *args: pl.DataFrame(),
        research_market_dates=lambda: [b[1] for b in bars],
    )
    client = TestClient(app)
    url = "/api/surge-research"
    assert (
        not client.get(url + "/runs").json()["computed"]
        and not (tmp_path / "surge_research").exists()
    )
    assert client.post(url + "/rebuild").json()["total"] == 2
    for pending, day in [("false", "2025-01-02"), ("true", "2025-01-05")]:
        page = client.get(
            url + "/runs", params={"pending": pending, "sort": "daily_gain", "started": day}
        ).json()
        assert (
            page["as_of"] == "2025-01-05"
            and page["total"] == 1
            and page["rows"][0]["start_date"] == day
        )
        counts = client.get(url + "/starts", params={"pending": pending, "bucket": "month"}).json()
        assert counts["points"] == [{"date": "2025-01", "count": 1}]
    assert client.get(url + "/runs?start=2026-01-01&end=2025-01-01").status_code == 400
    assert client.get(url + "/runs?sort=invalid").status_code == 422
    assert legacy.read_bytes() == b"unchanged"
