"""GF-000616-A-Foundation: 竞价路径采集契约测试。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from app.services import auction_path_service as module
from app.services.auction_path_service import AuctionPathService, PATH_COLUMNS
from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet


class _Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir


class _Repo:
    def __init__(self, data_dir: Path) -> None:
        self.store = _Store(data_dir)

    def get_enriched_latest(self):
        return pl.DataFrame(), None

    def get_instruments(self):
        return pl.DataFrame()


def _state(capabilities: CapabilitySet) -> SimpleNamespace:
    return SimpleNamespace(
        capabilities=capabilities,
        depth_service=SimpleNamespace(),
        quote_service=None,
    )


def _depth(symbols: list[str]) -> dict[str, dict]:
    return {
        symbol: {
            "timestamp": 123,
            "bid_prices": [10.0], "bid_volumes": [100],
            "ask_prices": [10.1], "ask_volumes": [200],
        }
        for symbol in symbols
    }


def test_missing_capability_degrades_without_write(tmp_path, monkeypatch) -> None:
    service = AuctionPathService()
    service.set_repo(_Repo(tmp_path))
    service.set_app_state(_state(CapabilitySet()))
    monkeypatch.setattr(module, "cn_today", lambda: date(2026, 10, 20))

    result = service.collect_tick()

    assert result["reason"] == "depth5_batch_unavailable"
    assert not list((tmp_path / "user_data").rglob("*.parquet"))


def test_tick_is_atomic_idempotent_and_restart_recoverable(tmp_path, monkeypatch) -> None:
    service = AuctionPathService()
    service.set_repo(_Repo(tmp_path))
    service.set_app_state(_state(CapabilitySet({Cap.DEPTH5_BATCH: CapabilityLimits(batch=10, rpm=60)})))
    monkeypatch.setattr(module, "cn_today", lambda: date(2026, 10, 20))
    monkeypatch.setattr(module, "cn_now", lambda: SimpleNamespace(strftime=lambda _: "09:16:15"))
    monkeypatch.setattr(service, "_trend_symbols", lambda _day: ["600000.SH"])
    monkeypatch.setattr(service, "_depth_batch", _depth)

    first = service.collect_tick()
    second = service.collect_tick()
    assert first["rows"] == 1 and second["rows"] == 1

    path = tmp_path / "user_data" / "auction_path" / "date=2026-10-20" / "part.parquet"
    saved = pl.read_parquet(path)
    assert saved.columns == PATH_COLUMNS
    assert saved.height == 1
    assert saved["ts"].to_list() == ["09:16:15"]

    restarted = AuctionPathService()
    restarted.set_repo(_Repo(tmp_path))
    restarted.set_app_state(_state(CapabilitySet({Cap.DEPTH5_BATCH: CapabilityLimits(batch=10, rpm=60)})))
    monkeypatch.setattr(restarted, "_trend_symbols", lambda _day: ["600000.SH"])
    monkeypatch.setattr(restarted, "_depth_batch", _depth)
    restarted.collect_tick()
    assert pl.read_parquet(path).height == 1


def test_empty_depth_does_not_write(tmp_path, monkeypatch) -> None:
    service = AuctionPathService()
    service.set_repo(_Repo(tmp_path))
    service.set_app_state(_state(CapabilitySet({Cap.DEPTH5_BATCH: CapabilityLimits(batch=10, rpm=60)})))
    monkeypatch.setattr(module, "cn_today", lambda: date(2026, 10, 20))
    monkeypatch.setattr(module, "cn_now", lambda: SimpleNamespace(strftime=lambda _: "09:16:15"))
    monkeypatch.setattr(service, "_trend_symbols", lambda _day: ["600000.SH"])
    monkeypatch.setattr(service, "_depth_batch", lambda _symbols: {})

    result = service.collect_tick()

    assert result["reason"] == "empty_depth"
    assert not list((tmp_path / "user_data").rglob("*.parquet"))


def test_collect_tick_rejects_outside_auction_window(tmp_path, monkeypatch) -> None:
    service = AuctionPathService()
    service.set_repo(_Repo(tmp_path))
    service.set_app_state(_state(CapabilitySet({Cap.DEPTH5_BATCH: CapabilityLimits(batch=10, rpm=60)})))
    monkeypatch.setattr(module, "cn_today", lambda: date(2026, 10, 20))
    monkeypatch.setattr(module, "cn_now", lambda: SimpleNamespace(strftime=lambda _: "09:30:00"))
    monkeypatch.setattr(service, "_trend_symbols", lambda _day: ["600000.SH"])
    monkeypatch.setattr(service, "_depth_batch", _depth)

    result = service.collect_tick()

    assert result["reason"] == "outside_auction_window"
    assert not list((tmp_path / "user_data").rglob("*.parquet"))


def test_restart_appends_a_new_tick(tmp_path, monkeypatch) -> None:
    service = AuctionPathService()
    service.set_repo(_Repo(tmp_path))
    service.set_app_state(_state(CapabilitySet({Cap.DEPTH5_BATCH: CapabilityLimits(batch=10, rpm=60)})))
    monkeypatch.setattr(module, "cn_today", lambda: date(2026, 10, 20))
    monkeypatch.setattr(service, "_trend_symbols", lambda _day: ["600000.SH"])
    monkeypatch.setattr(service, "_depth_batch", _depth)
    current = ["09:16:15"]
    monkeypatch.setattr(module, "cn_now", lambda: SimpleNamespace(strftime=lambda _: current[0]))

    assert service.collect_tick()["total_rows"] == 1
    current[0] = "09:17:15"
    restarted = AuctionPathService()
    restarted.set_repo(_Repo(tmp_path))
    restarted.set_app_state(_state(CapabilitySet({Cap.DEPTH5_BATCH: CapabilityLimits(batch=10, rpm=60)})))
    monkeypatch.setattr(restarted, "_trend_symbols", lambda _day: ["600000.SH"])
    monkeypatch.setattr(restarted, "_depth_batch", _depth)

    result = restarted.collect_tick()

    assert result["total_rows"] == 2
    assert pl.read_parquet(tmp_path / "user_data/auction_path/date=2026-10-20/part.parquet")["ts"].to_list() == ["09:16:15", "09:17:15"]


def test_corrupt_history_is_not_overwritten(tmp_path, monkeypatch) -> None:
    service = AuctionPathService()
    service.set_repo(_Repo(tmp_path))
    service.set_app_state(_state(CapabilitySet({Cap.DEPTH5_BATCH: CapabilityLimits(batch=10, rpm=60)})))
    monkeypatch.setattr(module, "cn_today", lambda: date(2026, 10, 20))
    monkeypatch.setattr(module, "cn_now", lambda: SimpleNamespace(strftime=lambda _: "09:16:15"))
    monkeypatch.setattr(service, "_trend_symbols", lambda _day: ["600000.SH"])
    monkeypatch.setattr(service, "_depth_batch", _depth)
    path = tmp_path / "user_data/auction_path/date=2026-10-20/part.parquet"
    path.parent.mkdir(parents=True)
    original = b"not a parquet file"
    path.write_bytes(original)

    result = service.collect_tick()

    assert result["reason"] == "history_read_failed"
    assert path.read_bytes() == original


def test_trend_candidates_are_prior_day_only_and_capped_at_200(tmp_path, monkeypatch) -> None:
    prior = date(2026, 10, 19)
    rows = []
    for i in range(201):
        rows.append({
            "date": prior, "symbol": f"600{i:03d}.SH", "close": 100.0,
            "amount": 1e9 - i, "ma5": 110.0, "ma10": 100.0, "ma20": 90.0,
            "momentum_5d": 0.10, "momentum_10d": 0.12, "high_60d": 100.0,
            "vol_ratio_5d": 1.0,
        })
    rows.append({**rows[0], "date": date(2026, 10, 20), "symbol": "601999.SH", "amount": 2e9})
    panel = pl.DataFrame(rows)
    part = tmp_path / "kline_daily_enriched/date=2026-10-19"
    part.mkdir(parents=True)
    panel.filter(pl.col("date") == prior).write_parquet(part / "part.parquet")
    today_part = tmp_path / "kline_daily_enriched/date=2026-10-20"
    today_part.mkdir(parents=True)
    panel.filter(pl.col("date") == date(2026, 10, 20)).write_parquet(today_part / "part.parquet")

    instruments = pl.DataFrame({
        "symbol": [r["symbol"] for r in rows], "name": ["普通股"] * len(rows),
        "total_shares": [300e6] * len(rows), "float_shares": [200e6] * len(rows),
    })
    repo = _Repo(tmp_path)
    repo.get_instruments = lambda: instruments
    service = AuctionPathService()
    service.set_repo(repo)
    monkeypatch.setattr("app.indicators.pipeline.compute_indicators", lambda df, needed: df)

    symbols = service._trend_symbols(date(2026, 10, 20))

    assert len(symbols) == 200
    assert "601999.SH" not in symbols
    assert symbols[0] == "600000.SH"


def test_atomic_write_failure_preserves_previous_file_and_state(tmp_path, monkeypatch) -> None:
    service = AuctionPathService()
    service.set_repo(_Repo(tmp_path))
    service.set_app_state(_state(CapabilitySet({Cap.DEPTH5_BATCH: CapabilityLimits(batch=10, rpm=60)})))
    monkeypatch.setattr(module, "cn_today", lambda: date(2026, 10, 20))
    monkeypatch.setattr(module, "cn_now", lambda: SimpleNamespace(strftime=lambda _: "09:16:15"))
    monkeypatch.setattr(service, "_trend_symbols", lambda _day: ["600000.SH"])
    monkeypatch.setattr(service, "_depth_batch", _depth)

    assert service.collect_tick()["total_rows"] == 1
    path = tmp_path / "user_data/auction_path/date=2026-10-20/part.parquet"
    original = path.read_bytes()
    monkeypatch.setattr(module, "atomic_write_parquet", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(module, "cn_now", lambda: SimpleNamespace(strftime=lambda _: "09:17:15"))

    try:
        service.collect_tick()
    except OSError:
        pass
    else:
        raise AssertionError("atomic write failure must be propagated")

    assert path.read_bytes() == original
    assert pl.read_parquet(path).height == 1
