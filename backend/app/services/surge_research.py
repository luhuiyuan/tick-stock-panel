"""短期猛拉：只延长有效冲刺，不把待判定的回落日写进边界。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Lock

import polars as pl

from app.services.fs_utils import atomic_write_parquet
from app.services.rise_research import (
    RiseResearchService,
    _adjustment_break,
    iter_research_price_groups,
)

_SCHEMA = {
    "symbol": pl.String,
    "start_date": pl.Date,
    "end_date": pl.Date,
    "days": pl.UInt32,
    "base_close": pl.Float64,
    "end_close": pl.Float64,
    "gain": pl.Float64,
    "daily_gain": pl.Float64,
    "pending": pl.Boolean,
}
_BUILD = Lock()
EPS = 1e-12


def find_surges(rows):
    """One ordered symbol; changes compare adjacent available daily closes."""
    start = last = None

    def record(pending):
        base = rows[start - 1][2]
        days = last - start + 1
        ratio = rows[last][2] / base
        daily = ratio ** (1 / days) - 1
        if ratio >= 1.15 - EPS and daily >= 0.03 - EPS:
            return dict(
                symbol=rows[start][0],
                start_date=rows[start][1],
                end_date=rows[last][1],
                days=days,
                base_close=base,
                end_close=rows[last][2],
                gain=ratio - 1,
                daily_gain=daily,
                pending=pending,
            )

    for i in range(1, len(rows)):
        _, _, prev, prev_raw = rows[i - 1]
        _, _, close, raw = rows[i]
        broken = _adjustment_break(prev, close, prev_raw, raw)
        strong = not broken and close / prev >= 1.03 - EPS
        if start is not None:
            if strong and (i == last + 1 or close > rows[last][2]):
                last = i
                continue
            if not broken and i == last + 1 and close / rows[last][2] >= 0.95 - EPS:
                continue
            if result := record(False):
                yield result
            start = last = None
        if strong:
            start = last = i
    if start is not None and (result := record(True)):
        yield result


@lru_cache(maxsize=1)
def _snapshot(path: Path, fingerprint):
    """原子替换后 inode 会改变；一次快照供分页与分布查询共用。"""
    import pyarrow.parquet as pq

    with path.open("rb") as handle:
        file = pq.ParquetFile(handle)
        return file.metadata.metadata[b"as_of"].decode(), pl.from_arrow(
            file.read(use_threads=False)
        )


class SurgeResearchService:
    def __init__(self, repo):
        self.root = Path(repo.store.data_dir)
        self.path = self.root / "surge_research" / "runs-v1.parquet"
        self.charts = RiseResearchService(repo)

    def rebuild(self):
        with _BUILD:
            found = []
            as_of = None
            for rows in iter_research_price_groups(self.root):
                day = rows[-1][1]
                as_of = max(as_of, day) if as_of else day
                found.extend(find_surges(rows))
            if as_of is None:
                raise ValueError("没有可用复权日线，未替换已有结果")
            table = (
                pl.DataFrame(found, schema=_SCHEMA)
                .join(self.charts._names(), on="symbol", how="left")
                .with_columns(
                    pl.col("name").fill_null(""),
                    pl.concat_str(["symbol", "start_date"], separator=":").alias("id"),
                )
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_parquet(table, self.path, metadata={"as_of": as_of.isoformat()})
            return {"computed": True, "total": table.height, "as_of": as_of}

    def query(
        self,
        *,
        start=None,
        end=None,
        pending=False,
        q="",
        started="",
        sort="end_date",
        order="desc",
        offset=0,
        limit=24,
        bucket=None,
    ):
        try:
            stat = self.path.stat()
            as_of, snapshot = _snapshot(self.path, (stat.st_ino, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            return {"computed": False, "as_of": "", "total": 0, "rows": [], "points": []}
        table = snapshot.filter(pl.col("pending") == pending)
        if start:
            table = table.filter(pl.col("start_date") >= start)
        if end:
            table = table.filter(pl.col("start_date") <= end)
        if q.strip():
            query = q.strip().upper()
            table = table.filter(
                pl.any_horizontal(
                    [
                        pl.col(k).str.to_uppercase().str.contains(query, literal=True)
                        for k in ("symbol", "name")
                    ]
                )
            )
        if bucket:
            counts = {d: n for d, n in table.group_by("start_date").len().iter_rows()}
            points = {}
            for d in self.charts._start_axis(counts, start, end) if counts else []:
                key = d.isoformat()[: 7 if bucket == "month" else 10]
                points[key] = points.get(key, 0) + counts.get(d, 0)
            return {
                "computed": True,
                "points": [{"date": d, "count": n} for d, n in points.items()],
            }
        if started:
            table = table.filter(pl.col("start_date").cast(pl.String).str.starts_with(started))
        page = table.sort(
            [sort, "symbol", "start_date"], descending=[order == "desc", False, False]
        ).slice(offset, limit)
        bars = self.charts.page_bars(page)
        return {
            "computed": True,
            "as_of": as_of,
            "total": table.height,
            "rows": [dict(r, bars=bars.get(r["id"], [])) for r in page.iter_rows(named=True)],
        }
