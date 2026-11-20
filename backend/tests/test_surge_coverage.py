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
                            rule=mod.RULE,
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


def test_real_panel_uses_prior_five_stock_bars_without_calendar(tmp_path, monkeypatch):
    from datetime import date, timedelta

    import polars as pl

    from app.services import coverage_replay as replay
    from app.services import surge_coverage as mod

    days = [date(2026, 1, 5) + timedelta(days=i) for i in (0, 1, 2, 3, 4, 7, 8, 9)]
    for field, day in [("FIRST", days[1]), ("START", days[6]), ("END", days[7])]:
        monkeypatch.setattr(replay, field, day)
    syms = ["A", "B", "C", "D", "E", "F"]
    source = tmp_path / "surge_research/runs-v1.parquet"
    source.parent.mkdir()
    pl.DataFrame(
        {
            "id": syms,
            "symbol": syms,
            "name": syms,
            "start_date": [days[6]] * len(syms),
            "end_date": [days[7]] * len(syms),
            "pending": [False] * len(syms),
        }
    ).write_parquet(source, metadata={"as_of": str(days[7])})
    for i, day in enumerate(days):
        symbols = ["A", "B", "F"] + (["C"] if i != 2 else []) + (["D", "E"] if i >= 5 else [])
        close = [
            None
            if s == "F" and i == 5
            else 3.0
            if (s == "C" and i == 0) or (s == "E" and i == 5) or i >= 6
            else 1.0
            for s in symbols
        ]
        bar = pl.DataFrame({"symbol": symbols, "date": [day] * len(symbols), "close": close})
        folder = tmp_path / f"kline_daily_enriched/date={day}"
        folder.mkdir(parents=True)
        bar.write_parquet(folder / "part.parquet")
    # E and D have one earlier bar, and no event may use same-day data.
    # G has no history at all and remains in the common denominator.
    g = (
        pl.read_parquet(source)
        .head(1)
        .with_columns(*(pl.lit("G").alias(c) for c in ("id", "symbol", "name")))
    )
    pl.concat([pl.read_parquet(source), g]).write_parquet(source, metadata={"as_of": str(days[7])})
    stage = tmp_path / "stage"
    stage.mkdir()
    assert not (tmp_path / "trading_calendar.json").exists()
    panel = replay.Panel(tmp_path, stage, lambda: None)
    svc = mod.Coverage(tmp_path)
    sig = {
        "id": "positive",
        "name": "大于2",
        "enabled": True,
        "timeframe": "daily",
        "conditions": [{"left": "close", "op": ">", "right": "2", "leftDays": 0, "rightDays": 0}],
    }
    monkeypatch.setattr(svc, "catalog", lambda: {"positive": sig})
    old, path = svc.artifact("positive", legacy=True), svc.artifact("positive")
    old.parent.mkdir(parents=True)
    import json

    pl.DataFrame({"category": ["hit"]}).write_parquet(
        old,
        metadata={
            "coverage": json.dumps(
                {
                    "version": "legacy",
                    "signal_id": "positive",
                    "source": panel.source,
                    "rule": "surge-pre5-ended-v2",
                    "stamp": replay.scoped(panel.stamp, sig),
                    "signal_hash": replay.digest(sig),
                }
            )
        },
    )
    legacy = old.read_bytes()
    assert svc.listing()[0]["status"] == "stale"
    summary = panel.evaluate(sig, path, "bars", lambda: None)
    rows = {r["symbol"]: r for r in pl.read_parquet(path).to_dicts()}
    assert {s: r["category"] for s, r in rows.items()} == {
        "A": "miss",
        "B": "miss",
        "C": "hit",
        "D": "unknown",
        "E": "hit",
        "F": "unknown",
        "G": "unknown",
    }
    assert rows["A"]["dates"] == days[1:6]
    assert rows["C"]["dates"] == [days[i] for i in (0, 1, 3, 4, 5)]
    assert rows["C"]["flags"] == [True, False, False, False, False]
    assert rows["D"]["flags"] == [False] and rows["F"]["flags"][-1] is None
    assert rows["G"]["dates"] == rows["G"]["flags"] == []
    assert (summary["hit"], summary["miss"], summary["unknown"]) == (2, 2, 3)
    assert svc.listing()[0]["status"] == "ready"
    assert old.read_bytes() == legacy
    assert svc.read("hit", 0, "legacy", signal_id="positive")[0]["stale"] is True
    assert svc.read("hit", 0, "bars", signal_id="positive")[0]["rule"] == replay.RULE
