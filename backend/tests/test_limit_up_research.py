"""涨停研究：连续涨停合并成一条，并按固定根数裁 K 线。"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.limit_up_research import router
from app.services.limit_up_research import (
    clear_limit_up_research_cache,
    group_limit_up_streaks,
    shift_years,
    slice_streak_bars,
    slice_year_bars,
    LimitUpResearchService,
)


def _days(rows: list[tuple[str, str, int]]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [row[0] for row in rows],
        "date": [date.fromisoformat(row[1]) for row in rows],
        "consecutive_limit_ups": [row[2] for row in rows],
    })


def test_single_day_and_three_board_are_separate_records():
    grouped = group_limit_up_streaks(_days([
        ("000001.SZ", "2024-01-02", 1),
        ("000001.SZ", "2024-01-03", 2),
        ("000001.SZ", "2024-01-04", 3),
        ("000001.SZ", "2024-02-01", 1),
        ("000002.SZ", "2024-01-03", 1),
    ]))
    records = {(row["symbol"], row["start_date"].isoformat(), row["end_date"].isoformat(), row["boards"], row["days"])
               for row in grouped.iter_rows(named=True)}
    assert records == {
        ("000001.SZ", "2024-01-02", "2024-01-04", 3, 3),
        ("000001.SZ", "2024-02-01", "2024-02-01", 1, 1),
        ("000002.SZ", "2024-01-03", "2024-01-03", 1, 1),
    }


def test_board_count_must_increase_by_one():
    grouped = group_limit_up_streaks(_days([
        ("000001.SZ", "2024-03-01", 1),
        ("000001.SZ", "2024-03-04", 3),
    ]))
    assert grouped.height == 2
    assert grouped.sort("start_date").get_column("boards").to_list() == [1, 3]


def test_history_cut_keeps_observed_board_count():
    grouped = group_limit_up_streaks(_days([
        ("000001.SZ", "2019-09-26", 4),
        ("000001.SZ", "2019-09-27", 5),
    ]))
    row = grouped.row(0, named=True)
    assert row["boards"] == 5
    assert row["days"] == 2
    assert row["start_date"] == date(2019, 9, 26)


def test_empty_and_non_positive_boards_produce_no_records():
    assert group_limit_up_streaks(pl.DataFrame()).is_empty()
    assert group_limit_up_streaks(_days([
        ("000001.SZ", "2024-01-02", 0),
    ])).is_empty()


def test_slice_keeps_requested_bars_before_and_after():
    start = date(2024, 1, 1)
    bars = [{"date": start + timedelta(days=i), "close": i} for i in range(40)]
    window = slice_streak_bars(bars, bars[20]["date"], bars[22]["date"], before=15, after=10)
    assert window[0]["date"] == bars[5]["date"]
    assert window[-1]["date"] == bars[32]["date"]
    assert len(window) == 28


def test_slice_returns_empty_when_streak_dates_are_missing():
    bars = [{"date": date(2024, 1, 2), "close": 1}]
    assert slice_streak_bars(bars, date(2024, 2, 1), date(2024, 2, 2)) == []


class _Repo:
    def __init__(self) -> None:
        self.revision = ("2026-10-27", 1)
        self.days = _days([
            ("000001.SZ", "2024-05-06", 1),
            ("000001.SZ", "2024-05-07", 2),
            ("600000.SH", "2024-06-03", 1),
            ("600000.SH", "2025-01-02", 1),
        ])
        self.names = pl.DataFrame({
            "symbol": ["000001.SZ", "600000.SH"],
            "name": ["平安银行", "浦发银行"],
        })

    def limit_up_research_revision(self):
        return self.revision

    def load_limit_up_days(self):
        return self.days

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
    clear_limit_up_research_cache()
    yield
    clear_limit_up_research_cache()


def test_service_filters_name_boards_and_date_overlap():
    repo = _Repo()
    service = LimitUpResearchService(repo)
    service.rebuild()
    named = service.list_streaks(q="平安")
    assert named["total"] == 1
    assert named["rows"][0]["boards"] == 2
    assert named["rows"][0]["name"] == "平安银行"
    assert named["rows"][0]["bars"]
    bars = named["rows"][0]["bars"]
    assert sum(bar["date"] < "2024-05-06" for bar in bars) == 30
    assert sum(bar["date"] > "2024-05-07" for bar in bars) == 30
    assert named["bars_before"] == 30
    assert named["bars_after"] == 30

    high = service.list_streaks(min_boards=2)
    assert high["total"] == 1

    window = service.list_streaks(start=date(2024, 6, 1), end=date(2024, 6, 30))
    assert [row["symbol"] for row in window["rows"]] == ["600000.SH"]
    assert window["rows"][0]["start_date"] == "2024-06-03"


def test_api_rejects_reversed_dates_and_pages_records():
    repo = _Repo()
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    LimitUpResearchService(repo).rebuild()

    bad = client.get("/api/limit-up-research/streaks", params={"start": "2024-02-02", "end": "2024-01-01"})
    assert bad.status_code == 400

    ok = client.get("/api/limit-up-research/streaks", params={"limit": 1, "offset": 0, "sort": "end_date"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["total"] == 3
    assert len(body["rows"]) == 1
    assert body["rows"][0]["end_date"] == "2025-01-02"
    assert body["rows"][0]["bars"]

def test_full_window_keeps_only_streaks_with_thirty_later_sessions():
    repo = _Repo()
    days = []
    cursor = date(2024, 4, 1)
    while cursor <= date(2025, 1, 31):
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    repo.limit_up_research_market_dates = lambda: days
    service = LimitUpResearchService(repo)
    service.rebuild()
    page = service.list_streaks(full_window=True, limit=10)
    ends = [row["end_date"] for row in page["rows"]]
    assert "2024-05-07" in ends
    assert "2024-06-03" in ends
    assert "2025-01-02" not in ends
    assert page["window_cutoff"]
    assert page["full_window"] is True

def test_shift_years_handles_leap_day():
    assert shift_years(date(2024, 2, 29), 1) == date(2025, 2, 28)
    assert shift_years(date(2024, 2, 29), -1) == date(2023, 2, 28)


def test_slice_year_bars_keeps_one_calendar_year_each_side():
    start = date(2024, 6, 3)
    end = date(2024, 6, 4)
    bars = []
    day = date(2022, 1, 1)
    while day <= date(2026, 12, 31):
        bars.append({"date": day})
        day += timedelta(days=1)
    window = slice_year_bars(bars, start, end)
    assert window[0]["date"] == date(2023, 6, 3)
    assert window[-1]["date"] == date(2025, 6, 4)
    assert slice_year_bars(bars, date(2030, 1, 1), date(2030, 1, 2)) == []


def test_zoom_bars_returns_one_year_each_side_and_rejects_bad_symbol():
    repo = _Repo()
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    bad = client.get("/api/limit-up-research/bars", params={"symbol": "BADSYMBOL", "start": "2024-05-06", "end": "2024-05-07"})
    assert bad.status_code == 400
    ok = client.get("/api/limit-up-research/bars", params={"symbol": "000001.SZ", "start": "2024-05-06", "end": "2024-05-07"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["from_date"] == "2023-05-06"
    assert body["to_date"] == "2025-05-07"
    assert body["bars_before"] > 200
    assert body["bars_after"] > 200
    assert body["bars"][0]["date"] >= "2023-05-06"
    assert body["bars"][-1]["date"] <= "2025-05-07"
    assert any("2024-05-06" <= bar["date"] <= "2024-05-07" for bar in body["bars"])



def test_rebuild_drops_memory_cache_and_reloads():
    class Counting(_Repo):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def load_limit_up_days(self):
            self.calls += 1
            return self.days

    repo = Counting()
    service = LimitUpResearchService(repo)
    assert service.list_streaks()["computed"] is False
    assert repo.calls == 0
    assert service.rebuild()["total"] == 3
    repo.days = repo.days.head(2)
    assert service.list_streaks()["total"] == 3
    rebuilt = service.rebuild()
    assert rebuilt["ok"] is True
    assert rebuilt["total"] == 1
    assert rebuilt["as_of"] == "2024-05-07"
    assert service.list_streaks()["total"] == 1
    assert repo.calls == 2

    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    response = client.post("/api/limit-up-research/rebuild")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert repo.calls == 3

def test_revision_change_does_not_rescan_and_disk_reloads(tmp_path):
    class Counting(_Repo):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0
            self.store = type("Store", (), {"data_dir": tmp_path})()

        def load_limit_up_days(self):
            self.calls += 1
            return self.days

    repo = Counting()
    service = LimitUpResearchService(repo)
    assert service.list_streaks()["computed"] is False
    assert repo.calls == 0
    service.rebuild()
    assert (tmp_path / "limit_up_research" / "streaks.parquet").is_file()
    repo.revision = ("2099-01-01", 99)
    repo.days = repo.days.head(0)
    kept = service.list_streaks()
    assert kept["computed"] is True
    assert kept["total"] == 3
    assert repo.calls == 1

    clear_limit_up_research_cache()
    fresh = LimitUpResearchService(repo)
    loaded = fresh.list_streaks()
    assert loaded["computed"] is True
    assert loaded["total"] == 3
    assert repo.calls == 1

