from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from app.indicators import chan_signals as cs
from app.market_time import CN_TZ


@pytest.fixture
def history():
    rng = np.random.default_rng(2019)
    n = 900
    prices = 20 * np.exp(np.cumsum(rng.normal(0, .025, n)))
    return pl.DataFrame({
        "symbol": ["600000.SH"] * n,
        "date": [date(2018, 1, 1) + timedelta(days=i) for i in range(n)],
        "open": prices, "close": prices, "high": prices * 1.015, "low": prices * .985,
        "volume": rng.uniform(1000, 3000, n), "amount": prices * 200000,
    })


def test_chart_matches_signal_events_and_native_structure(history):
    from copy import deepcopy

    from app.indicators.chan_runtime import CONFIG
    from app.indicators.czsc_chart import build_chart
    from app.vendor.chanpy.Chan import CChan
    from app.vendor.chanpy.ChanConfig import CChanConfig
    from app.vendor.chanpy.Common.CEnum import KL_TYPE
    from app.vendor.chanpy.Common.CTime import CTime
    from app.vendor.chanpy.KLine.KLine_Unit import CKLine_Unit

    chart = build_chart(history)
    expected = cs.compute(history, set(cs.SIGNALS))
    for name in cs.SIGNALS:
        assert sorted({event["date"] for event in chart["signals"] if event["signal_id"] == name}) == [day.isoformat() for day in expected.filter(pl.col(name))["date"]]
    assert chart["coverage"] == cs.coverage(expected, set(cs.SIGNALS))
    native = CChan("600000.SH", lv_list=[KL_TYPE.K_DAY], config=CChanConfig(deepcopy(CONFIG)))
    for row in history.iter_rows(named=True):
        day = row["date"]
        unit = CKLine_Unit({"time_key": CTime(day.year, day.month, day.day, 15, 0, auto=False),
                           **{key: row[key] for key in ("open", "high", "low", "close")},
                           "volume": row["volume"] * 100, "turnover": row["amount"]})
        native.trigger_load({KL_TYPE.K_DAY: [unit]})
    level = native[0]
    assert [(x["start_price"], x["end_price"]) for x in chart["strokes"]] == [(b.get_begin_val(), b.get_end_val()) for b in level.bi_list if b.is_sure]
    assert [(x["low"], x["high"]) for x in chart["centers"]] == [(z.low, z.high) for z in level.zs_list if not z.is_one_bi_zs()]
    assert [(x["start_price"], x["end_price"]) for x in chart["segments"]] == [(s.get_begin_val(), s.get_end_val()) for s in level.seg_list]
    assert [(x["low"], x["high"]) for x in chart["segment_centers"]] == [(z.low, z.high) for z in level.segzs_list if not z.is_one_bi_zs()]


def test_bad_data_splits_structures_and_unclosed_tail_is_excluded(history):
    from app.indicators.czsc_chart import build_chart

    split_day = history[450, "date"]
    broken = history.with_columns(pl.when(pl.col("date") == split_day).then(None).otherwise(pl.col("close")).alias("close"))
    chart = build_chart(broken)
    assert split_day.isoformat() in chart["invalid_dates"]
    for shape in chart["strokes"] + chart["centers"] + chart["unfinished"]:
        assert not shape["start"] < split_day.isoformat() < shape["end"]
    last_day = history[-1, "date"]
    before_close = build_chart(history, now=datetime.combine(last_day, datetime.min.time()).replace(hour=14, tzinfo=CN_TZ))
    prefix = build_chart(history.head(899))
    assert before_close == prefix


def test_short_history_does_not_fabricate_centers(history):
    from app.indicators.czsc_chart import build_chart
    chart = build_chart(history.head(5))
    assert chart["centers"] == chart["segment_centers"] == []



def test_chart_service_reads_existing_repository_and_emits_snapshot(history):
    from app.services.czsc_chart import get_chart

    class Repo:
        def resolve_asset_type(self, symbol):
            return "stock"

        def get_name_map(self, symbols):
            return {"600000.SH": "浦发银行"}

        def get_matrix_data_generation(self, asset_type, *, readonly=False):
            assert asset_type == "stock" and readonly is True
            return "stable-generation"

        def get_daily_published(self, symbol, end, limit, columns, *, asset_type="stock"):
            assert (asset_type, symbol) == ("stock", "600000.SH")
            return history.filter(pl.col("date") <= end).tail(limit).select(columns)

    result = get_chart(Repo(), "600000.SH", now=datetime(2020, 7, 1, 16, tzinfo=CN_TZ))
    assert result["status"] == "ready"
    assert result["engine"] == "chan.py"
    assert result["input_count"] == len(history)
    assert result["coverage"]["profile_id"] == cs.PROFILE_ID


def test_chart_service_rejects_asset_mismatch(history):
    from app.services.czsc_chart import get_chart

    class Repo:
        def resolve_asset_type(self, symbol):
            return "stock"

        def get_name_map(self, symbols):
            return {"600000.SH": "浦发银行"}

    with pytest.raises(ValueError, match="不匹配"):
        get_chart(Repo(), "600000.SH", asset_type="index")


def test_chart_api_routes_validate_and_delegate(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import kline
    from app.services import czsc_chart

    calls = []
    monkeypatch.setattr(czsc_chart, "get_chart", lambda repo, symbol, timeframe="1d", asset_type=None: (
        calls.append((symbol, timeframe, asset_type)) or {"status": "empty", "timeframe": timeframe}
    ))
    app = FastAPI()
    app.state.repo = object()
    app.include_router(kline.router)
    client = TestClient(app)

    assert client.get("/api/kline/czsc", params={"symbol": "600000.SH", "timeframe": "1w"}).json() == {
        "status": "empty", "timeframe": "1w"
    }
    assert client.get("/api/kline/czsc-daily", params={"symbol": "600000.SH"}).json() == {
        "status": "empty", "timeframe": "1d"
    }
    assert calls == [("600000.SH", "1w", None), ("600000.SH", "1d", None)]
    assert client.get("/api/kline/czsc", params={"symbol": "bad", "timeframe": "1d"}).status_code == 422


def test_published_daily_read_ignores_live_cache_and_counts_rows(history, tmp_path):
    from app.tickflow.repository import KlineRepository

    root = tmp_path / "kline_daily_enriched" / "date=2020-06-18"
    root.mkdir(parents=True)
    history.write_parquet(root / "part.parquet")
    repo = object.__new__(KlineRepository)
    repo.store = type("Store", (), {"data_dir": tmp_path})()
    repo.get_enriched_latest = lambda: pytest.fail("图表 published read must not use live cache")

    result = repo.get_daily_published(
        "600000.SH", date(2020, 6, 18), 100, sorted(cs.INPUT_COLUMNS), asset_type="stock",
    )
    expected = history.filter(pl.col("date") <= date(2020, 6, 18)).tail(100).select(sorted(cs.INPUT_COLUMNS))
    assert result.equals(expected)


def test_minute_chart_snapshot_is_bounded_revision_sensitive_and_surfaces_corruption(tmp_path):
    from types import SimpleNamespace

    from app.tickflow.repository import KlineRepository

    root = tmp_path / "kline_minute" / "date=2024-01-02"
    root.mkdir(parents=True)
    path = root / "part.parquet"
    repo = object.__new__(KlineRepository)
    repo.store = SimpleNamespace(data_dir=tmp_path)
    before = repo.get_minute_chart_generation()
    frame = pl.DataFrame({
        "symbol": ["600000.SH"] * 3,
        "datetime": [datetime(2024, 1, 2, 9, minute) for minute in (31, 32, 33)],
        "open": [10.0] * 3, "high": [12.0] * 3, "low": [9.0] * 3,
        "close": [11.0] * 3, "volume": [10.0] * 3, "amount": [110.0] * 3,
    })
    frame.write_parquet(path)
    assert repo.get_minute_chart_generation() != before
    result = repo.get_minute_published("600000.SH", datetime(2024, 1, 2, 9, 32), 1)
    assert result["datetime"].to_list() == [datetime(2024, 1, 2, 9, 32)]
    path.write_bytes(b"corrupt parquet")
    with pytest.raises(pl.exceptions.ComputeError):
        repo.get_minute_published("600000.SH", datetime(2024, 1, 2, 10), 10)
