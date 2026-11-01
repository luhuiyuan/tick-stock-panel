"""把按年 zip 备份灌回项目的 parquet 分区。

对应 ``export_history_minute.py`` 的两种产物：

* ``minute/kline_minute_1m/<year>.zip`` —— 成员 ``<symbol>.csv``，分钟增强表
* ``<year>_ticks.zip`` —— 成员 ``<symbol>.csv``，逐笔原层（3 秒快照）

设计约束
--------
* **守住单位**：备份里分钟表的 ``volume`` 已经是**手**，与项目 ``data/kline_minute``
  同口径，不做任何换算（实测 2026-10-20 半天 120 根 339,412 手，当日日K 全天
  935,776 手，比例约 36%，符合半日成交占比）。
* **守住 schema**：项目分钟表是 ``symbol, datetime, open, high, low, close,
  volume, amount, date`` 八列，没有买卖量。备份里的 ``buy_volume`` /
  ``sell_volume`` 默认丢弃，只有 ``--keep-direction`` 才写入（会与既有分区
  的列集不一致，读的时候要 ``union_by_name``）。
* **守住分区不变式**：每个 ``date=`` 分区最终只留一份 ``part.parquet``
  （见 CONTRIBUTING §6.3，DuckDB 逐文件读 footer，文件数直接乘进查询成本）。
* **逐笔单独落位**：项目没有逐笔表，所以必须显式 ``--ticks-out`` 指定输出目录，
  不混进 ``data/`` 的标准表。逐笔同一秒可能有多笔真实成交，没有可靠主键，
  因此不做去重——已存在的分区默认跳过，要重灌加 ``--overwrite``。

用法
----
::

    cd backend

    # 只看会写什么
    PYTHONPATH= uv run --no-sync python scripts/restore_minute_zip.py \
        --years 2025 --dry-run

    # 分钟表灌回项目
    PYTHONPATH= uv run --no-sync python scripts/restore_minute_zip.py --years 2025

    # 逐笔落到独立目录
    PYTHONPATH= uv run --no-sync python scripts/restore_minute_zip.py \
        --years 2025 --what ticks --ticks-out ../data/kline_ticks_raw
"""
from __future__ import annotations

import argparse
import io
import time
import zipfile
from pathlib import Path
from typing import Sequence

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

MINUTE_SUBDIR = "minute/kline_minute_1m"

# 分钟：项目标准列（前 8 项来自项目 schema，date 是分区列）
_MINUTE_CORE = [
    ("symbol", pa.string()),
    ("datetime", pa.timestamp("us")),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.float64()),
    ("amount", pa.float64()),
    ("date", pa.date32()),
]
_MINUTE_DIRECTION = [("buy_volume", pa.float64()), ("sell_volume", pa.float64())]

_TICKS_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("date", pa.date32()),
    ("time", pa.string()),
    ("price", pa.float64()),
    ("volume", pa.float64()),
    ("direction", pa.int8()),
])

# 攒够这么多行才把内存里的数据推到 parquet，控制峰值内存
_FLUSH_ROWS = 4_000_000
# 每个 row group 的行数：太大会让 200 多个 writer 各缓冲一堆数据
_ROW_GROUP = 20_000


class DaySink:
    """按 ``date=`` 分区落 parquet 的接收器。

    逐股票读 csv 时数据天然是「按股票、跨全年」的顺序，而目标是「按天分区」。
    这里用「一天一个 ParquetWriter」直接把行追加进对应分区，避免先把全年数据
    攒在内存里（分钟一年 1.24 亿行、逐笔 5.7 亿笔，攒不住）。
    """

    def __init__(
        self,
        root: Path,
        schema: pa.Schema,
        *,
        keys: list[str] | None,
        dedupe: bool,
        overwrite: bool,
        dry_run: bool,
        label: str,
    ) -> None:
        self.root = root
        self.schema = schema
        self.keys = keys
        self.dedupe = dedupe
        self.overwrite = overwrite
        self.dry_run = dry_run
        self.label = label
        self.rows = 0
        self.frames = 0
        self.skipped = 0
        self._writers: dict[str, pq.ParquetWriter] = {}
        self._buffer: dict[str, list[pl.DataFrame]] = {}
        self._buffered_rows = 0

    # -- 写入 ------------------------------------------------------------- #
    def add(self, frame: pl.DataFrame) -> None:
        self.rows += frame.height
        self.frames += 1
        if self.dry_run:
            self._buffer.clear()
            self._buffered_rows = 0
            return
        for day, chunk in frame.group_by("date", maintain_order=True):
            key = str(day[0] if isinstance(day, tuple) else day)
            self._buffer.setdefault(key, []).append(chunk)
            self._buffered_rows += chunk.height
        if self._buffered_rows >= _FLUSH_ROWS:
            self._flush()

    def _flush(self) -> None:
        for key, chunks in self._buffer.items():
            writer = self._writer(key)
            table = pl.concat(chunks, how="diagonal_relaxed").to_arrow().cast(self.schema)
            writer.write_table(table, row_group_size=_ROW_GROUP)
        self._buffer.clear()
        self._buffered_rows = 0

    def _writer(self, key: str) -> pq.ParquetWriter:
        writer = self._writers.get(key)
        if writer is None:
            part_dir = self.root / f"date={key}"
            part_dir.mkdir(parents=True, exist_ok=True)
            writer = pq.ParquetWriter(part_dir / "_ingest.parquet", self.schema, compression="zstd")
            self._writers[key] = writer
        return writer

    # -- 收尾 ------------------------------------------------------------- #
    def close(self) -> dict:
        if self.dry_run:
            return {"label": self.label, "rows": self.rows, "parts": 0, "skipped": 0}
        self._flush()
        for writer in self._writers.values():
            writer.close()

        parts = 0
        for key in sorted(self._writers):
            part_dir = self.root / f"date={key}"
            ingest = part_dir / "_ingest.parquet"
            part = part_dir / "part.parquet"
            new = pl.read_parquet(ingest)
            if part.exists() and not self.overwrite:
                if self.dedupe and self.keys:
                    merged = pl.concat(
                        [pl.read_parquet(part), new], how="diagonal_relaxed"
                    ).unique(subset=self.keys, keep="last")
                else:
                    # 没有主键可去重时，已有分区视为已灌过，不重复追加
                    ingest.unlink(missing_ok=True)
                    self.skipped += 1
                    continue
            else:
                merged = new
            if self.keys:
                merged = merged.sort(self.keys)
            merged.write_parquet(part)
            ingest.unlink(missing_ok=True)
            # 分区里只许留一份 part.parquet
            for extra in part_dir.glob("*.parquet"):
                if extra != part:
                    extra.unlink()
            parts += 1
        return {"label": self.label, "rows": self.rows, "parts": parts, "skipped": self.skipped}


def _iter_members(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(n for n in zf.namelist() if n.endswith(".csv"))


def _read_minute_member(zf: zipfile.ZipFile, name: str) -> pl.DataFrame:
    raw = pl.read_csv(
        io.BytesIO(zf.read(name)),
        schema_overrides={"symbol": pl.String, "datetime": pl.String, "date": pl.String},
    )
    out = raw.with_columns(
        pl.col("datetime").str.strptime(pl.Datetime("us"), "%Y-%m-%d %H:%M:%S", strict=False),
        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
    )
    return out


def _read_ticks_member(zf: zipfile.ZipFile, name: str) -> pl.DataFrame:
    raw = pl.read_csv(
        io.BytesIO(zf.read(name)),
        schema_overrides={
            "symbol": pl.String, "date": pl.String, "time": pl.String,
            "price": pl.Float64, "volume": pl.Float64, "direction": pl.Int8,
        },
    )
    return raw.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))


def restore_minute(
    src: Path, dst: Path, years: Sequence[int], *, keep_direction: bool,
    overwrite: bool, dry_run: bool,
) -> list[dict]:
    schema = pa.schema(_MINUTE_CORE + (_MINUTE_DIRECTION if keep_direction else []))
    results = []
    for year in years:
        zip_path = src / MINUTE_SUBDIR / f"{year}.zip"
        if not zip_path.exists():
            continue
        members = _iter_members(zip_path)
        print(f"\n== 1分钟 {year} → {dst} ({len(members)} 只) ==", flush=True)
        sink = DaySink(dst, schema, keys=["symbol", "datetime"], dedupe=True,
                       overwrite=overwrite, dry_run=dry_run, label=f"{year} 分钟")
        started = time.time()
        with zipfile.ZipFile(zip_path) as zf:
            for idx, name in enumerate(members, 1):
                frame = _read_minute_member(zf, name)
                if not keep_direction:
                    frame = frame.select(["symbol", "datetime", "open", "high", "low",
                                          "close", "volume", "amount", "date"])
                else:
                    frame = frame.select(["symbol", "datetime", "open", "high", "low",
                                          "close", "volume", "amount", "date",
                                          "buy_volume", "sell_volume"])
                sink.add(frame)
                if idx % 500 == 0:
                    print(f"  {idx}/{len(members)} 只 / {sink.rows:,} 行 / "
                          f"{(time.time() - started) / 60:.1f}min", flush=True)
        result = sink.close()
        results.append(result)
        skip_note = f"（跳过已存在 {result['skipped']} 个）" if result["skipped"] else ""
        print(f"  {year}: {sink.rows:,} 行 → {result['parts']} 个分区{skip_note}"
              f" / {(time.time() - started) / 60:.1f}min", flush=True)
    return results


def restore_ticks(
    roots: Sequence[Path], out: Path, years: Sequence[int], *,
    overwrite: bool, dry_run: bool,
) -> list[dict]:
    results = []
    for year in years:
        zip_path = next((r / f"{year}_ticks.zip" for r in roots if (r / f"{year}_ticks.zip").exists()), None)
        if zip_path is None:
            continue
        members = _iter_members(zip_path)
        print(f"\n== 逐笔 {year} → {out} ({len(members)} 只) ==", flush=True)
        sink = DaySink(out, _TICKS_SCHEMA, keys=None, dedupe=False,
                       overwrite=overwrite, dry_run=dry_run, label=f"{year} 逐笔")
        started = time.time()
        with zipfile.ZipFile(zip_path) as zf:
            for idx, name in enumerate(members, 1):
                sink.add(_read_ticks_member(zf, name))
                if idx % 500 == 0:
                    print(f"  {idx}/{len(members)} 只 / {sink.rows:,} 笔 / "
                          f"{(time.time() - started) / 60:.1f}min", flush=True)
        results.append(sink.close())
        print(f"  {year}: {sink.rows:,} 笔 ({time.time() - started:.0f}s)", flush=True)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把按年 zip 备份灌回项目 parquet 分区")
    parser.add_argument("--src", type=Path, default=Path("D:/tsp/backup_export"),
                        help="备份包根目录（分钟 zip 在其 minute/kline_minute_1m 下）")
    parser.add_argument("--data", type=Path, default=None, help="项目 data 目录（默认 <repo>/data）")
    parser.add_argument("--years", default="", help="逗号分隔年份；留空则扫描所有存在的 zip")
    parser.add_argument("--what", default="minute,ticks", help="minute / ticks / 两者逗号分隔")
    parser.add_argument("--keep-direction", action="store_true",
                        help="分钟保留 buy_volume/sell_volume（与既有分区列集不一致）")
    parser.add_argument("--ticks-roots", default="E:/tsp_backup_ticks,D:/tsp_backup_ticks",
                        help="逐笔 zip 所在盘（逗号分隔）")
    parser.add_argument("--ticks-out", type=Path, default=None,
                        help="逐笔落位目录（项目无逐笔表，必须显式指定；不指定则跳过逐笔）")
    parser.add_argument("--overwrite", action="store_true", help="整分区替换，不合并")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    data_dir = args.data.resolve() if args.data else repo_root / "data"
    src = args.src.resolve()
    if not src.exists():
        print(f"备份目录不存在: {src}")
        return 2

    if args.years.strip():
        years = sorted({int(y) for y in args.years.split(",") if y.strip()})
    else:
        years = sorted({int(p.stem) for p in (src / MINUTE_SUBDIR).glob("[0-9]*.zip")})

    what = {w.strip() for w in args.what.split(",") if w.strip()}
    started = time.time()
    results: list[dict] = []

    if "minute" in what:
        results += restore_minute(
            src, data_dir / "kline_minute", years,
            keep_direction=args.keep_direction, overwrite=args.overwrite, dry_run=args.dry_run,
        )
    if "ticks" in what:
        if args.ticks_out is None:
            print("跳过逐笔：未指定 --ticks-out（项目没有逐笔表，必须显式给输出目录）")
        else:
            roots = [Path(p.strip()) for p in args.ticks_roots.split(",") if p.strip()]
            results += restore_ticks(
                roots, args.ticks_out.resolve(), years,
                overwrite=args.overwrite, dry_run=args.dry_run,
            )

    total = sum(r["rows"] for r in results)
    parts = sum(r["parts"] for r in results)
    print(f"\n恢复完成，用时 {(time.time() - started) / 60:.1f} 分钟")
    print(f"共 {total:,} 行 → {parts} 个分区"
          + ("（dry-run 未落盘）" if args.dry_run else f" → {data_dir}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
