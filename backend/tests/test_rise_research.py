"""连涨研究：中间可以下跌，从高点回落超过 5% 才切开。

区间收尾在「第一次收盘涨过 30%」那天，而不是整段最高收盘那天，
这样只留下短时间达标的段，长期慢涨会被 max_days 过滤掉。
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rise_research import router
from app.services.rise_research import (
    clear_rise_research_cache,
    find_rise_waves,
    RiseResearchService,
)


def _query(rows: list[tuple]) -> list[dict]:
    found = find_rise_waves(rows)
    return [
        {
            "symbol": row["symbol"],
            "start": row["start_date"].isoformat(),
            "end": row["end_date"].isoformat(),
            "days": int(row["days"]),
            "gain": float(row["gain"]),
            "base": float(row["base_close"]),
            "end_close": float(row["end_close"]),
        }
        for row in found
    ]


def test_rise_above_30_percent_runs_from_the_low_to_the_high():
    found = _query([
        ("000001.SZ", "2024-01-02", 10),
        ("000001.SZ", "2024-01-03", 11),
        ("000001.SZ", "2024-01-04", 12),
        ("000001.SZ", "2024-01-05", 14),
    ])
    assert len(found) == 1
    row = found[0]
    assert row["start"] == "2024-01-02"
    assert row["end"] == "2024-01-05"
    assert row["days"] == 4
    assert row["base"] == 10
    assert row["end_close"] == 14
    assert row["gain"] == pytest.approx(0.4)


def test_sub_30_percent_and_exact_30_are_dropped():
    found = _query([
        ("000001.SZ", "2024-01-02", 10),
        ("000001.SZ", "2024-01-03", 11),
        ("000001.SZ", "2024-01-04", 12),
        ("000002.SZ", "2024-01-02", 10),
        ("000002.SZ", "2024-01-03", 13),
    ])
    assert found == []


def test_one_day_spike_includes_the_prior_low():
    found = _query([
        ("000001.SZ", "2024-02-01", 10),
        ("000001.SZ", "2024-02-02", 14),
        ("000001.SZ", "2024-02-05", 13),
    ])
    assert len(found) == 1
    assert found[0]["start"] == "2024-02-01"
    assert found[0]["end"] == "2024-02-02"
    assert found[0]["days"] == 2
    assert found[0]["gain"] == pytest.approx(0.4)


def test_dip_under_5_percent_stays_inside_the_same_wave():
    found = _query([
        ("000001.SZ", "2024-03-01", 10),
        ("000001.SZ", "2024-03-04", 12),
        ("000001.SZ", "2024-03-05", 11.5),
        ("000001.SZ", "2024-03-06", 13),
        ("000001.SZ", "2024-03-07", 15),
    ])
    assert len(found) == 1
    assert found[0]["start"] == "2024-03-01"
    assert found[0]["end"] == "2024-03-07"
    assert found[0]["base"] == 10
    assert found[0]["days"] == 5
    assert found[0]["gain"] == pytest.approx(0.5)


def test_pullback_over_5_percent_ends_at_the_high_and_starts_again():
    found = _query([
        ("000001.SZ", "2024-05-06", 10),
        ("000001.SZ", "2024-05-07", 16),
        ("000001.SZ", "2024-05-08", 11),
        ("000001.SZ", "2024-05-09", 12),
        ("000001.SZ", "2024-05-10", 18),
    ])
    assert [(row["start"], row["end"], row["base"], row["end_close"]) for row in found] == [
        ("2024-05-06", "2024-05-07", 10, 16),
        ("2024-05-08", "2024-05-10", 11, 18),
    ]


def test_pullback_of_exactly_5_percent_does_not_split():
    found = _query([
        ("000001.SZ", "2024-06-03", 10),
        ("000001.SZ", "2024-06-04", 20),
        ("000001.SZ", "2024-06-05", 19),
        ("000001.SZ", "2024-06-06", 22),
    ])
    assert len(found) == 1
    assert found[0]["start"] == "2024-06-03"
    # 正好回落 5% 不切开，仍是一段；但收尾裁到第一次收盘涨过 30% 那天(06-04)，
    # 而不是整段最高收盘那天(06-06)。
    assert found[0]["end"] == "2024-06-04"
    assert found[0]["end_close"] == 20


def test_run_ends_on_first_close_above_30_percent_not_the_peak():
    """后面继续爬到 +60% 也不算进区间，只记到首次达标那天。"""
    found = _query([
        ("000001.SZ", "2024-01-02", 10),
        ("000001.SZ", "2024-01-03", 11),
        ("000001.SZ", "2024-01-04", 13.5),
        ("000001.SZ", "2024-01-05", 14),
        ("000001.SZ", "2024-01-08", 15),
        ("000001.SZ", "2024-01-09", 16),
    ])
    assert len(found) == 1
    row = found[0]
    assert row["start"] == "2024-01-02"
    assert row["end"] == "2024-01-04"
    assert row["days"] == 3
    assert row["end_close"] == pytest.approx(13.5)
    assert row["gain"] == pytest.approx(0.35)


def test_exact_30_percent_is_not_a_hit():
    """正好 30% 不算达标，要等第一次严格超过。"""
    found = _query([
        ("000001.SZ", "2024-02-01", 10),
        ("000001.SZ", "2024-02-02", 13),
        ("000001.SZ", "2024-02-05", 13.1),
        ("000001.SZ", "2024-02-06", 20),
    ])
    assert len(found) == 1
    assert found[0]["end"] == "2024-02-05"
    assert found[0]["days"] == 3
    assert found[0]["end_close"] == pytest.approx(13.1)


def test_slow_rise_is_left_with_long_burst_days():
    """慢涨：首次过 30% 就已经用了 25 个交易日，会被 max_days 过滤掉。"""
    day = date(2024, 7, 1)
    rows = [
        ("000001.SZ", (day + timedelta(days=i)).isoformat(), 10 + i * 0.12)
        for i in range(24)
    ]
    rows.append(("000001.SZ", (day + timedelta(days=24)).isoformat(), 13.5))
    found = _query(rows)
    assert len(found) == 1
    assert found[0]["days"] == 25
    assert found[0]["end"] == (day + timedelta(days=24)).isoformat()


def test_pullback_over_5_percent_before_a_new_high_ends_the_first_wave():
    found = _query([
        ("000001.SZ", "2024-06-03", 10),
        ("000001.SZ", "2024-06-04", 20),
        ("000001.SZ", "2024-06-05", 18.9),
        ("000001.SZ", "2024-06-06", 22),
    ])
    assert [(row["start"], row["end"], row["end_close"]) for row in found] == [
        ("2024-06-03", "2024-06-04", 20),
    ]


def test_adjustment_break_does_not_count_as_a_rise():
    found = _query([
        ("000002.SZ", "2025-09-23", 0.015, 6.76),
        ("000002.SZ", "2025-09-24", 4.67, 6.84),
        ("000002.SZ", "2025-09-25", 4.80, 7.02),
    ])
    assert found == []


def test_flat_close_does_not_break_the_wave():
    found = _query([
        ("000001.SZ", "2024-04-01", 10),
        ("000001.SZ", "2024-04-02", 12),
        ("000001.SZ", "2024-04-03", 12),
        ("000001.SZ", "2024-04-08", 16),
    ])
    assert len(found) == 1
    assert found[0]["start"] == "2024-04-01"
    assert found[0]["end"] == "2024-04-08"
    assert found[0]["base"] == 10


class _Repo:
    def __init__(self) -> None:
        self.revision = ("2026-10-27", 1)
        self.runs = pl.DataFrame({
            "symbol": ["000001.SZ", "600000.SH"],
            "start_date": [date(2024, 5, 6), date(2024, 6, 3)],
            "end_date": [date(2024, 5, 8), date(2024, 6, 3)],
            "days": [3, 1],
            "gain": [0.42, 0.35],
            "base_close": [10.0, 8.0],
            "end_close": [14.2, 10.8],
        })
        self.names = pl.DataFrame({
            "symbol": ["000001.SZ", "600000.SH"],
            "name": ["平安银行", "浦发银行"],
        })

    def limit_up_research_revision(self):
        return self.revision

    def load_consecutive_rises(self, min_gain: float = 0.30):
        return self.runs.filter(pl.col("end_close") > pl.col("base_close") * (1.0 + min_gain))

    def get_instruments(self):
        return self.names

    def load_enriched_ohlcv(self, symbols, start, end):
        rows = []
        for symbol in symbols:
            day = start
            while day <= end:
                if day.weekday() < 5:
                    rows.append({
                        "symbol": symbol,
                        "date": day,
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.5,
                        "close": 10.5,
                        "volume": 1000.0,
                    })
                day += timedelta(days=1)
        return pl.DataFrame(rows) if rows else pl.DataFrame()


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_rise_research_cache()
    yield
    clear_rise_research_cache()



def test_failed_load_is_not_cached():
    class Flaky(_Repo):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def load_consecutive_rises(self, min_gain: float = 0.30):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("scan failed")
            return super().load_consecutive_rises(min_gain)

    repo = Flaky()
    service = RiseResearchService(repo)
    with pytest.raises(RuntimeError):
        service.rebuild()
    page = service.list_runs()
    assert page["computed"] is False
    assert page["total"] == 0
    assert repo.calls == 1
    service.rebuild()
    page = service.list_runs()
    assert page["computed"] is True
    assert page["total"] == 2
    assert repo.calls == 2


def test_service_filters_name_gain_and_keeps_thirty_bars_each_side():
    service = RiseResearchService(_Repo())
    service.rebuild()
    named = service.list_runs(q="平安")
    assert named["total"] == 1
    row = named["rows"][0]
    assert row["name"] == "平安银行"
    assert row["gain"] == pytest.approx(0.42)
    assert row["days"] == 3
    bars = row["bars"]
    assert sum(bar["date"] < "2024-05-06" for bar in bars) == 30
    assert sum(bar["date"] > "2024-05-08" for bar in bars) == 30

    bigger = service.list_runs(min_gain=0.4)
    assert [item["symbol"] for item in bigger["rows"]] == ["000001.SZ"]

    window = service.list_runs(start=date(2024, 6, 1), end=date(2024, 6, 30))
    assert [item["symbol"] for item in window["rows"]] == ["600000.SH"]


def test_api_pages_runs_and_zoom_rejects_bad_symbol():
    repo = _Repo()
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    RiseResearchService(repo).rebuild()

    bad = client.get("/api/rise-research/runs", params={"start": "2024-07-02", "end": "2024-01-01"})
    assert bad.status_code == 400

    ok = client.get("/api/rise-research/runs", params={"limit": 1, "offset": 0, "sort": "gain"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["total"] == 2
    assert body["rows"][0]["symbol"] == "000001.SZ"
    assert body["rows"][0]["bars"]

    rejected = client.get("/api/rise-research/bars", params={"symbol": "BADSYMBOL", "start": "2024-05-06", "end": "2024-05-08"})
    assert rejected.status_code == 400
    zoom = client.get("/api/rise-research/bars", params={"symbol": "000001.SZ", "start": "2024-05-06", "end": "2024-05-08"})
    assert zoom.status_code == 200
    payload = zoom.json()
    assert payload["from_date"] == "2023-05-06"
    assert payload["to_date"] == "2025-05-08"
    assert payload["bars_before"] > 200
    assert payload["bars_after"] > 200
    assert any("2024-05-06" <= bar["date"] <= "2024-05-08" for bar in payload["bars"])


def test_rebuild_drops_memory_cache_and_reloads():
    class Counting(_Repo):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def load_consecutive_rises(self, min_gain: float = 0.30):
            self.calls += 1
            return super().load_consecutive_rises(min_gain)

    repo = Counting()
    service = RiseResearchService(repo)
    assert service.list_runs()["computed"] is False
    assert repo.calls == 0
    assert service.rebuild()["total"] == 2
    repo.runs = repo.runs.head(1)
    assert service.list_runs()["total"] == 2
    rebuilt = service.rebuild()
    assert rebuilt["ok"] is True
    assert rebuilt["total"] == 1
    assert rebuilt["as_of"] == "2024-05-08"
    assert service.list_runs()["total"] == 1
    assert repo.calls == 2

    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    response = client.post("/api/rise-research/rebuild")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert repo.calls == 3


def test_start_counts_fill_quiet_days_and_sum_months():
    repo = _Repo()
    repo.research_market_dates = lambda: [
        date(2024, 5, 6),
        date(2024, 5, 7),
        date(2024, 6, 3),
    ]
    service = RiseResearchService(repo)
    service.rebuild()
    daily = service.start_counts()
    assert daily["bucket"] == "day"
    assert [(item["date"], item["count"]) for item in daily["points"]] == [
        ("2024-05-06", 1),
        ("2024-05-07", 0),
        ("2024-06-03", 1),
    ]
    monthly = service.start_counts(bucket="month")
    assert [(item["date"], item["count"]) for item in monthly["points"]] == [
        ("2024-05", 1),
        ("2024-06", 1),
    ]
    bigger = service.start_counts(min_gain=0.4, bucket="month")
    assert bigger["points"] == [{"date": "2024-05", "count": 1}]
    window = service.start_counts(start=date(2024, 6, 1), end=date(2024, 6, 30))
    assert window["points"] == [{"date": "2024-06-03", "count": 1}]


def test_start_counts_api_rejects_reversed_dates():
    repo = _Repo()
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    RiseResearchService(repo).rebuild()
    bad = client.get("/api/rise-research/starts", params={"start": "2024-07-02", "end": "2024-01-01"})
    assert bad.status_code == 400
    ok = client.get("/api/rise-research/starts", params={"bucket": "month"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["bucket"] == "month"
    assert sum(item["count"] for item in body["points"]) == 2


def test_list_runs_filters_by_start_day_or_month():
    service = RiseResearchService(_Repo())
    service.rebuild()
    day = service.list_runs(started_on=date(2024, 5, 6))
    assert [row["symbol"] for row in day["rows"]] == ["000001.SZ"]
    month = service.list_runs(started_month="2024-06")
    assert [row["symbol"] for row in month["rows"]] == ["600000.SH"]
    both = service.list_runs(start=date(2024, 6, 1), end=date(2024, 6, 30), started_on=date(2024, 5, 6))
    assert both["total"] == 0

    app = FastAPI()
    app.include_router(router)
    repo = _Repo()
    app.state.repo = repo
    RiseResearchService(repo).rebuild()
    client = TestClient(app)
    bad = client.get("/api/rise-research/runs", params={"started": "2024/05"})
    assert bad.status_code == 400
    ok = client.get("/api/rise-research/runs", params={"started": "2024-05", "limit": 10})
    assert ok.status_code == 200
    assert [row["symbol"] for row in ok.json()["rows"]] == ["000001.SZ"]

def test_max_days_filters_list_runs_and_start_counts_together():
    repo = _Repo()
    repo.research_market_dates = lambda: [date(2024, 5, 6), date(2024, 6, 3)]
    service = RiseResearchService(repo)
    service.rebuild()
    assert service.list_runs(max_days=20)["total"] == 2
    narrow = service.list_runs(max_days=2)
    assert narrow["max_days"] == 2
    assert narrow["total"] == 1
    assert narrow["rows"][0]["symbol"] == "600000.SH"
    starts = service.start_counts(max_days=2)
    assert sum(point["count"] for point in starts["points"]) == 1


def test_api_forwards_max_days_to_runs_and_starts():
    repo = _Repo()
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    RiseResearchService(repo).rebuild()

    runs = client.get("/api/rise-research/runs", params={"max_days": 2})
    assert runs.status_code == 200
    assert runs.json()["max_days"] == 2
    assert runs.json()["total"] == 1
    starts = client.get("/api/rise-research/starts", params={"max_days": 2})
    assert starts.status_code == 200
    assert sum(point["count"] for point in starts.json()["points"]) == 1
    assert client.get("/api/rise-research/runs", params={"max_days": -1}).status_code == 422


def test_revision_change_does_not_rescan_and_disk_reloads(tmp_path):
    class Counting(_Repo):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0
            self.store = type("Store", (), {"data_dir": tmp_path})()

        def load_consecutive_rises(self, min_gain: float = 0.30):
            self.calls += 1
            return super().load_consecutive_rises(min_gain)

    repo = Counting()
    service = RiseResearchService(repo)
    assert service.list_runs()["computed"] is False
    assert repo.calls == 0
    service.rebuild()
    assert (tmp_path / "rise_research" / "runs.parquet").is_file()
    repo.revision = ("2099-01-01", 99)
    repo.runs = repo.runs.head(0)
    kept = service.list_runs()
    assert kept["computed"] is True
    assert kept["total"] == 2
    assert repo.calls == 1

    clear_rise_research_cache()
    fresh = RiseResearchService(repo)
    loaded = fresh.list_runs()
    assert loaded["computed"] is True
    assert loaded["total"] == 2
    assert [row["symbol"] for row in loaded["rows"]] == ["600000.SH", "000001.SZ"]
    assert repo.calls == 1

