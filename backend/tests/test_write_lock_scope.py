"""_write_lock 锁区间瘦身的回归测试。

背景: polars 并发执行存在死锁风险 (app.polars_guard), 若 polars 读/合并/排序
悬死在 _write_lock 内, 全局写锁被永久持有 → 所有写路径排队冻结 (2026-09-07
线上全站冻结事故的放大器)。乐观并发模式把重活移到锁外, 此处验证:
- 并发 upsert 不丢行 (乐观重试的正确性);
- 合并计算期间 _write_lock 可被其他线程获取 (重活确实不在锁内)。
"""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import polars as pl

from app.tickflow.repository import DataStore, KlineRepository


def _frame(symbols: list[str], dt: date = date(2026, 9, 7)) -> pl.DataFrame:
    n = len(symbols)
    return pl.DataFrame({
        "symbol": symbols,
        "date": [dt] * n,
        "close": [10.0 + i for i in range(n)],
    })


def test_concurrent_upserts_do_not_lose_rows(tmp_path: Path) -> None:
    repo = KlineRepository(DataStore(tmp_path))

    groups = [[f"{i:03d}{j:04d}.SZ" for j in range(8)] for i in range(6)]
    errors: list[BaseException] = []

    def worker(symbols: list[str]) -> None:
        try:
            for _ in range(3):  # 每线程多轮写, 提高乐观重试路径命中
                repo.merge_live_daily_asset("stock", _frame(symbols))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(g,), daemon=True) for g in groups]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert all(not t.is_alive() for t in threads)

    out = tmp_path / "kline_daily" / "date=2026-09-07" / "part.parquet"
    final = pl.read_parquet(out)
    assert final["symbol"].n_unique() == sum(len(g) for g in groups)  # 无一丢失
    assert final["symbol"].to_list() == sorted(final["symbol"].to_list())


def test_heavy_merge_runs_outside_write_lock(tmp_path: Path, monkeypatch) -> None:
    repo = KlineRepository(DataStore(tmp_path))
    # 预置旧分区内容, 让 upsert 走「读旧 + concat 合并」路径。
    repo.merge_live_daily_asset("stock", _frame(["000001.SZ"]))

    merge_entered = threading.Event()
    original_concat = pl.concat

    def slow_concat(*args, **kwargs):
        merge_entered.set()
        import time

        time.sleep(0.4)  # 模拟重合并耗时; 期间 _write_lock 必须是空闲的
        return original_concat(*args, **kwargs)

    monkeypatch.setattr(pl, "concat", slow_concat)

    done = threading.Event()

    def upsert() -> None:
        repo.merge_live_daily_asset("stock", _frame(["000002.SZ"]))
        done.set()

    t = threading.Thread(target=upsert, daemon=True)
    t.start()
    assert merge_entered.wait(timeout=5), "合并路径未被触发"

    acquired = repo._write_lock.acquire(timeout=1.0)
    assert acquired, "合并计算期间 _write_lock 被占用 — 重活仍在锁内"
    repo._write_lock.release()

    assert done.wait(timeout=5)
    t.join(timeout=5)
    out = tmp_path / "kline_daily" / "date=2026-09-07" / "part.parquet"
    assert pl.read_parquet(out)["symbol"].to_list() == ["000001.SZ", "000002.SZ"]


def test_snapshot_fingerprint_is_from_open_file_version(tmp_path: Path) -> None:
    from app.services.fs_utils import read_parquet_snapshot

    path = tmp_path / "part.parquet"
    pl.DataFrame({"symbol": ["old"]}).write_parquet(path)
    frame, fingerprint = read_parquet_snapshot(path)
    assert frame["symbol"].to_list() == ["old"]
    assert fingerprint is not None
    assert fingerprint == KlineRepository._partition_fingerprint(path)


def test_atomic_parquet_uses_unique_staging_and_cleans_failure(tmp_path: Path, monkeypatch) -> None:
    from app.services import fs_utils

    names: list[Path] = []
    original_write = pl.DataFrame.write_parquet

    def capture_write(df, file, *args, **kwargs):
        names.append(Path(file))
        return original_write(df, file, *args, **kwargs)

    monkeypatch.setattr(pl.DataFrame, "write_parquet", capture_write)
    out = tmp_path / "part.parquet"
    fs_utils.atomic_write_parquet(_frame(["A"]), out)
    fs_utils.atomic_write_parquet(_frame(["B"]), out)
    assert len(set(names)) == 2
    assert all(name.name.endswith(".tmp") for name in names)

    def fail_write(df, file, *args, **kwargs):
        path = Path(file)
        path.write_bytes(b"partial")
        raise OSError("injected write failure")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", fail_write)
    try:
        fs_utils.atomic_write_parquet(_frame(["C"]), out)
    except OSError:
        pass
    else:
        raise AssertionError("expected injected failure")
    assert not list(tmp_path.glob(".*.tmp"))


def test_concurrent_minute_upserts_do_not_lose_symbols(tmp_path: Path) -> None:
    from datetime import datetime
    from app.services.kline_sync import _write_minute_partition

    minute_dir = tmp_path / "kline_minute"
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(symbol: str) -> None:
        frame = pl.DataFrame({
            "symbol": [symbol],
            "datetime": [datetime(2026, 9, 7, 1, 30)],
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
            "volume": [1.0], "amount": [1.0],
        })
        try:
            _write_minute_partition(frame, minute_dir, write_lock=lock)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"{i:06d}.SZ",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors, errors
    out = minute_dir / "date=2026-09-07" / "part.parquet"
    assert pl.read_parquet(out)["symbol"].n_unique() == 8


def test_concurrent_adj_factor_merges_do_not_lose_events(tmp_path: Path) -> None:
    from datetime import date
    from app.services.kline_sync import _merge_adj_factor_store

    path = tmp_path / "adj_factor" / "all.parquet"
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        frame = pl.DataFrame({
            "symbol": [f"{i:06d}.SZ"], "trade_date": [date(2026, 9, 7)],
            "ex_factor": [1.0 + i / 100],
        })
        try:
            _merge_adj_factor_store(path, frame, write_lock=lock)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors, errors
    assert pl.read_parquet(path)["symbol"].n_unique() == 8
