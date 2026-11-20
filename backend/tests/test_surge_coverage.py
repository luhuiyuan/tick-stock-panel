def test_duplicate_submit_and_failed_replay_keeps_old_file(tmp_path, monkeypatch):
    import threading
    import time

    from app.services import surge_coverage as coverage

    svc = coverage.Coverage(tmp_path)
    svc.path.write_bytes(b"previous successful artifact")
    entered, release = threading.Event(), threading.Event()

    def fail(*args):
        entered.set()
        assert release.wait(5)
        raise ValueError("test replay failure")

    monkeypatch.setattr(svc, "batch", fail)
    first = svc.start()
    try:
        assert entered.wait(5)
        assert svc.start() == {"job_id": first["job_id"], "created": False}
    finally:
        release.set()
    for _ in range(100):
        if svc.store.get(first["job_id"])["status"] == "failed":
            break
        time.sleep(0.025)
    assert svc.store.get(first["job_id"])["status"] == "failed"
    assert svc.path.read_bytes() == b"previous successful artifact"


def test_resume_isolation_and_versions(tmp_path, monkeypatch):
    import json

    import polars as pl
    import pytest

    from app.services import surge_coverage as mod

    catalog = {k: {"id": k, "name": k} for k in ("a", "b", "c")}
    svc = mod.Coverage(tmp_path)
    monkeypatch.setattr(svc, "catalog", lambda: catalog)
    monkeypatch.setattr(mod, "input_stamp", lambda root: "input")
    monkeypatch.setattr(svc.store, "progress", lambda *a: None)
    broken = set()
    calls = []

    class Panel:
        stamp = "input"

        def __init__(self, *args):
            pass

        def verify(self):
            pass

        def evaluate(self, s, path, version, checkpoint):
            calls.append(s["id"])
            if s["id"] == "c":
                raise mod.UnavailableError("缺少历史来源")
            if s["id"] in broken:
                raise ValueError("模拟出错")
            path.parent.mkdir(exist_ok=True)
            pl.DataFrame({"category": ["hit"]}).write_parquet(
                path,
                metadata={
                    "coverage": json.dumps(
                        dict(
                            signal_id=s["id"],
                            version=version,
                            stamp=self.stamp,
                            signal_hash=mod.digest(s),
                            source="x",
                        )
                    )
                },
            )

    monkeypatch.setattr(mod, "Panel", Panel)
    assert svc.batch("one", None, lambda: None) == dict(processed=3, ready=2, unavailable=1)
    assert svc.batch("two", None, lambda: None) == {"processed": 0}
    assert len(calls) == 3
    a, b = [svc.artifact(k).read_bytes() for k in "ab"]
    broken.add("a")
    assert svc.batch("three", "a", lambda: None)["failed"] == 1
    assert svc.artifact("a").read_bytes() == a and svc.artifact("b").read_bytes() == b
    assert svc.listing()[0]["status"] == "failed"
    assert svc.read("hit", 0, "one-a", signal_id="a")[1]
    with pytest.raises(ValueError):
        svc.read("hit", 0, "one-a", signal_id="b")
    with pytest.raises(ValueError):
        svc.start("../a")
    broken.clear()
    assert svc.batch("four", None, lambda: None)["processed"] == 1
    catalog["b"]["name"] = "changed"
    assert svc.listing()[1]["status"] == "stale"


def test_real_panel_uses_prior_five_market_days_and_preserves_unknowns(tmp_path, monkeypatch):
    import json
    from datetime import date, timedelta

    import polars as pl

    from app.services import coverage_replay as replay

    days = [date(2026, 1, 5) + timedelta(days=i) for i in (0, 1, 2, 3, 4, 7)]
    for field, day in [("FIRST", days[0]), ("START", days[5]), ("END", days[5])]:
        monkeypatch.setattr(replay, field, day)
    (tmp_path / "trading_calendar.json").write_text(json.dumps([str(d) for d in days]))
    syms = ["A", "B", "C"]
    source = tmp_path / "surge_research/runs-v1.parquet"
    source.parent.mkdir()
    pl.DataFrame(
        {
            "id": syms,
            "symbol": syms,
            "name": syms,
            "start_date": [days[-1]] * 3,
            "end_date": [days[-1]] * 3,
            "pending": [False] * 3,
        }
    ).write_parquet(source, metadata={"as_of": str(days[-1])})
    for i, day in enumerate(days):
        symbols = syms[:2] if i == 2 else syms
        bar = pl.DataFrame(
            {
                "symbol": symbols,
                "date": [day] * len(symbols),
                "close": [3.0 if ((s == "A" and i == 1) or i == 5) else 1.0 for s in symbols],
            }
        )
        bar = bar.with_columns(
            *[
                pl.col("close").alias(c)
                for c in [
                    "open",
                    "high",
                    "low",
                    "volume",
                    "amount",
                    "raw_close",
                    "raw_low",
                    "raw_high",
                ]
            ]
        )
        folder = tmp_path / f"kline_daily_enriched/date={day}"
        folder.mkdir(parents=True)
        bar.write_parquet(folder / "part.parquet")
    stage = tmp_path / "stage"
    stage.mkdir()
    panel = replay.Panel(tmp_path, stage, lambda: None)
    path = tmp_path / "result.parquet"
    sig = {
        "id": "positive",
        "name": "大于2",
        "enabled": True,
        "timeframe": "daily",
        "conditions": [{"left": "close", "op": ">", "right": "2", "leftDays": 0, "rightDays": 0}],
    }
    summary = panel.evaluate(sig, path, "v1", lambda: None)
    rows = pl.read_parquet(path).sort("symbol")
    assert rows["category"].to_list() == ["hit", "miss", "unknown"]
    assert rows["flags"].to_list() == [
        [False, True, False, False, False],
        [False] * 5,
        [False, False, None, False, False],
    ]
    assert (summary["fired"], summary["evaluable"]) == (1, 14)
