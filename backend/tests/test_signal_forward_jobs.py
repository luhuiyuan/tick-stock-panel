import json
import time
from dataclasses import replace
from threading import Event

import polars as pl
import pytest
from test_signal_forward_performance import DAYS, SIGNAL, bars

from app.services import signal_forward_jobs as jobs
from app.strategy import custom_signals

A, B = "600000.SH", "000001.SZ"


def _terminal(service, job):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        record = service.status(job)
        if record and record["status"] in ("succeeded", "failed") and service._active is None:
            return record
        time.sleep(0.01)
    raise AssertionError(service.status(job))


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "trading_calendar.json").write_text(json.dumps(list(map(str, DAYS))))
    custom_signals.save_one(tmp_path, SIGNAL)
    frame = bars().with_columns(pl.col("symbol").replace({"A": A, "B": B}))
    for day in DAYS:
        part = frame.filter(pl.col("date") == day)
        for table, data in (
            ("kline_daily", part.select("symbol", "date", pl.col("raw_open").alias("open"))),
            ("kline_daily_enriched", part.drop("raw_open", "official_limit_up")),
        ):
            path = tmp_path / table / f"date={day}" / "part.parquet"
            path.parent.mkdir(parents=True)
            data.write_parquet(path)
    quotes = tmp_path / "instruments/instruments.parquet"
    quotes.parent.mkdir()
    frame.select(
        "symbol", pl.col("date").alias("as_of"), pl.col("official_limit_up").alias("limit_up")
    ).write_parquet(quotes)
    return tmp_path, jobs.ForwardConfig((A, B), ("x",), DAYS[1], DAYS[-1], DAYS[12])


def test_publish_restart_and_failed_replacement(setup, monkeypatch):
    root, config = setup
    service = jobs.ForwardPerformanceJobs(root)
    first = service.start(config)["job_id"]
    assert _terminal(service, first)["status"] == "succeeded"
    restarted = jobs.ForwardPerformanceJobs(root)
    assert restarted.result(first)["stale"] is False
    assert restarted.read_table(first, "events").height == 3
    assert (
        restarted.read_table(first, "summary").height == restarted.result(first)["rows"]["summary"]
    )

    def fail(_frame, _path):
        raise OSError("write failed")

    monkeypatch.setattr(jobs, "atomic_write_parquet", fail)
    second = restarted.start(config)["job_id"]
    assert _terminal(restarted, second)["status"] == "failed"
    assert restarted.result(second) is None
    assert restarted.read_table(first, "events").height == 3
    custom_signals.save_one(root, {**SIGNAL, "name": "改名"})
    assert restarted.result(first)["stale"] is True


def test_same_config_dedup_and_input_drift(setup, monkeypatch):
    root, config = setup
    service = jobs.ForwardPerformanceJobs(root)
    reading, release = Event(), Event()
    real_read = jobs._read_inputs

    def hold(*args):
        frame = real_read(*args)
        reading.set()
        assert release.wait(5)
        return frame

    monkeypatch.setattr(jobs, "_read_inputs", hold)
    active = service.start(config)
    try:
        assert reading.wait(5)
        assert service.status(active["job_id"])["status"] == "running"
        assert service.start(config) == {**active, "created": False}
        with pytest.raises(ValueError, match="不同配置"):
            service.start(replace(config, split_date=DAYS[11]))
        custom_signals.save_one(root, {**SIGNAL, "name": "计算期间变化"})
    finally:
        release.set()
    assert "输入在计算期间已变化" in _terminal(service, active["job_id"])["error"]
    assert service.result(active["job_id"]) is None


def test_deadline_and_terminal_write_failure_do_not_publish(setup, monkeypatch):
    root, config = setup
    service = jobs.ForwardPerformanceJobs(root)
    first = service.start(config)["job_id"]
    assert _terminal(service, first)["status"] == "succeeded"

    def fail_commit(*args, **kwargs):
        raise OSError("terminal state could not be saved")

    monkeypatch.setattr(service.store, "succeed", fail_commit)
    second = service.start(config)["job_id"]
    assert _terminal(service, second)["status"] == "failed"
    assert service.result(second) is None
    assert service.read_table(first, "monthly").height > 0

    monkeypatch.setattr(jobs, "TIMEOUT_S", 0)
    third = service.start(config)["job_id"]
    assert _terminal(service, third)["status"] == "failed"
    assert service.result(third) is None


def test_incomplete_enriched_publication_fails_closed(setup, monkeypatch):
    root, config = setup
    monkeypatch.setattr(jobs, "enriched_publication_incomplete", lambda _: True)
    service = jobs.ForwardPerformanceJobs(root)
    job = service.start(config)["job_id"]
    assert "增强日线正在发布" in _terminal(service, job)["error"]
    assert service.result(job) is None


def test_read_only_routes_leave_missing_and_pending_jobs_untouched(tmp_path):
    from fastapi.testclient import TestClient

    with TestClient(_read_app(tmp_path)) as client:
        assert client.get(f"{_API}/latest").json() is None
        assert list(tmp_path.iterdir()) == []
        service = jobs.ForwardPerformanceJobs(tmp_path)
        job, _ = service.store.create(timeout_s=30)
        record = service.folder / "jobs" / f"{job}.json"
        before = record.read_bytes()
        assert client.get(f"{_API}/latest").json() is None
        assert client.get(f"{_API}/{job}/events?signal_id=x").status_code == 404
        assert record.read_bytes() == before


_API = "/api/signal-forward-performance"


def _read_app(root):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from app.api.signal_forward_performance import router

    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=root))
    app.include_router(router)
    return app


def test_saved_result_routes_page_filter_and_hide_revised_bars(setup):
    from fastapi.testclient import TestClient

    root, config = setup
    service = jobs.ForwardPerformanceJobs(root)
    job = service.start(config)["job_id"]
    assert _terminal(service, job)["status"] == "succeeded"
    with TestClient(_read_app(root)) as client:
        latest = client.get(f"{_API}/latest").json()
        assert latest["run_id"] == job and latest["stale"] is False
        assert latest["summary"] and latest["monthly"]
        url = f"{_API}/{job}/events?signal_id=x&split=discovery&limit=1"
        first = client.get(url).json()
        second = client.get(f"{url}&offset=1").json()
        assert first["total"] == second["total"] == 2
        assert len(first["rows"]) == len(second["rows"]) == 1
        day = DAYS[6].isoformat()
        assert first["rows"][0]["signal_date"] == day
        assert second["rows"][0]["signal_date"] == DAYS[1].isoformat()
        assert client.get(f"{url}&symbol={B}").json() == {"total": 0, "rows": []}
        assert any(bar["date"] == day for bar in first["rows"][0]["bars"])
        part = root / "kline_daily_enriched" / f"date={day}" / "part.parquet"
        pl.read_parquet(part).with_columns(pl.col("close") + 1).write_parquet(part)
        assert client.get(f"{_API}/latest").json()["stale"] is True
        revised = client.get(url).json()["rows"][0]
        assert revised["return_1d"] == first["rows"][0]["return_1d"]
        assert all(bar["date"] != day for bar in revised["bars"])
