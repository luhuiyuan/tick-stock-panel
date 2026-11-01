"""量化「日K 分区文件数就是读侧成本」: 同一份数据, 单文件分区 vs 分片分区的查询耗时。

日K 以 ``read_parquet('**/*.parquet', union_by_name=true)`` 建 DuckDB 视图, 每次查询
都要逐个打开文件读 footer。数据量相同、文件数不同, 查询耗时直接按文件数放大 ——
这就是同一个日期分区被拆成 26 个分片后, /api/data/status 单次聚合 37~48s 的原因。

Run: uv run --frozen python -m scripts.benchmark_daily_sync_staging
Uses synthetic data only; no external data source is contacted.
"""
from __future__ import annotations

import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import polars as pl

DATES = 400
ROWS_PER_DATE = 600  # 与全市场单日行数同量级
SHARDS = 26  # fuyao/qatdx 全市场回填的批次数

# /api/data/status 每张表做的聚合: 全表 count + 标的数 + 日期范围
AGGREGATE = """
SELECT count(*) AS rows, count(DISTINCT symbol) AS symbols,
       min(date) AS first_date, max(date) AS last_date
FROM read_parquet('{glob}', union_by_name=true)
"""


def write_layout(root: Path, shards: int) -> Path:
    """按 date 分区写入; shards=1 时每个分区一份 part.parquet。"""
    start = date(2020, 1, 1)
    for offset in range(DATES):
        day = (start + timedelta(days=offset)).isoformat()
        dir_path = root / f"date={day}"
        dir_path.mkdir(parents=True)
        frame = pl.DataFrame({
            "symbol": [f"{i:06d}.SZ" for i in range(ROWS_PER_DATE)],
            "date": [day] * ROWS_PER_DATE,
            "open": [10.0] * ROWS_PER_DATE,
            "high": [11.0] * ROWS_PER_DATE,
            "low": [9.0] * ROWS_PER_DATE,
            "close": [10.5] * ROWS_PER_DATE,
            "volume": [100.0] * ROWS_PER_DATE,
            "amount": [1050.0] * ROWS_PER_DATE,
        })
        if shards == 1:
            frame.write_parquet(dir_path / "part.parquet")
            continue
        step = ROWS_PER_DATE // shards
        for index in range(shards):
            frame.slice(index * step, step if index < shards - 1 else ROWS_PER_DATE - index * step) \
                .write_parquet(dir_path / f"part-{index}.parquet")
    return root


def measure(root: Path, repeats: int = 3) -> tuple[int, float]:
    """返回 (文件数, 最快一次聚合耗时)。取最快值, 排除首次冷启动抖动。"""
    files = len(list(root.rglob("*.parquet")))
    glob = (root / "**" / "*.parquet").as_posix()
    sql = AGGREGATE.format(glob=glob)
    best = float("inf")
    for _ in range(repeats):
        con = duckdb.connect()
        try:
            started = time.perf_counter()
            con.execute(sql).fetchone()
            best = min(best, time.perf_counter() - started)
        finally:
            con.close()
    return files, best


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        single = measure(write_layout(Path(tmp) / "single", shards=1))
        sharded = measure(write_layout(Path(tmp) / "sharded", shards=SHARDS))

    rows = DATES * ROWS_PER_DATE
    print(f"dates={DATES}, rows={rows}")
    for label, (files, elapsed) in (("single part.parquet", single), (f"{SHARDS} shards", sharded)):
        print(f"{label:22s} files={files:6d} aggregate={elapsed:.3f}s")
    print(f"slowdown: {sharded[1] / single[1]:.1f}x  (同数据量, 只是文件数不同)")


if __name__ == "__main__":
    main()
