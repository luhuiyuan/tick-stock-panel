"""文件系统小工具 — 原子写等。

历史遗留: json_report_store / strategy_cache / kline_sync 等模块里各有一份内联的
同款原子写。新代码统一用本模块的 atomic_write_text, 一处实现一处维护。
"""
from __future__ import annotations

import contextlib
import io
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只做类型标注; JSON 原子写的调用方 (preferences/secrets) 不必加载 polars
    import polars as pl


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """临时文件 + os.replace 原子替换, 避免读侧读到半截 JSON。

    `mode` 在替换*之前*打到临时文件上。凭证类文件如果先落盘再 chmod, 中间有一段
    以默认权限存在的窗口; 先改临时文件就没有这个窗口。Windows 上 chmod 只影响
    只读位, 失败不该让写入失败, 所以吞掉 OSError, 与原调用处的处理一致。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
    os.replace(tmp, path)


def _staging_path(path: Path) -> Path:
    """Return a per-writer temporary path so concurrent writers never share staging."""
    return path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")


def atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    """Write parquet to an exclusive staging file, then atomically publish it."""
    from app.tickflow.repository import replace_with_retry

    tmp = _staging_path(path)
    try:
        df.write_parquet(tmp)
        replace_with_retry(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def parquet_fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


def read_parquet_snapshot(path: Path) -> tuple[pl.DataFrame, tuple[int, int] | None]:
    """Read immutable bytes and fingerprint from the same open file handle."""
    import polars as pl

    try:
        with path.open("rb") as fh:
            st = os.fstat(fh.fileno())
            raw = fh.read()
    except (FileNotFoundError, NotADirectoryError):
        return pl.DataFrame(), None
    return pl.read_parquet(io.BytesIO(raw)), (st.st_mtime_ns, st.st_size)


def optimistic_partition_write(
    path: Path,
    build: Callable[[pl.DataFrame], pl.DataFrame],
    write_lock: threading.Lock | None,
    *,
    retries: int = 3,
) -> pl.DataFrame:
    """Merge outside the lock; validate the snapshot and publish inside it."""
    if write_lock is None:
        existing, _ = read_parquet_snapshot(path)
        merged = build(existing)
        atomic_write_parquet(merged, path)
        return merged
    for _ in range(retries):
        existing, base_fp = read_parquet_snapshot(path)
        merged = build(existing)
        with write_lock:
            if parquet_fingerprint(path) != base_fp:
                continue
            atomic_write_parquet(merged, path)
            return merged
    with write_lock:
        existing, _ = read_parquet_snapshot(path)
        merged = build(existing)
        atomic_write_parquet(merged, path)
        return merged
