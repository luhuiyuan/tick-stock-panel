"""涨停研究：把连续涨停收成一条记录，并给出这段前后的日 K。

连板数沿用 enriched 的 consecutive_limit_ups。本模块不重新计算涨停价。
一段记录从连板数回到 1，或不再比上一涨停日大 1 时切开。
"""
from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path

import polars as pl

BARS_BEFORE = 30
BARS_AFTER = 30
# 交易日前后多取自然日，覆盖周末和长假后再裁到固定根数。
CALENDAR_BEFORE = 80
CALENDAR_AFTER = 80

_STREAK_SCHEMA = {
    "id": pl.Utf8,
    "symbol": pl.Utf8,
    "name": pl.Utf8,
    "start_date": pl.Date,
    "end_date": pl.Date,
    "boards": pl.UInt32,
    "days": pl.UInt32,
}


def group_limit_up_streaks(frame: pl.DataFrame) -> pl.DataFrame:
    """同一标的上连板数连续加 1 的涨停日合并成一条。"""
    empty = pl.DataFrame(schema={
        "symbol": pl.Utf8,
        "start_date": pl.Date,
        "end_date": pl.Date,
        "boards": pl.UInt32,
        "days": pl.UInt32,
    })
    if frame.is_empty() or "consecutive_limit_ups" not in frame.columns:
        return empty
    rows = (
        frame.select(
            pl.col("symbol").cast(pl.Utf8),
            pl.col("date").cast(pl.Date),
            pl.col("consecutive_limit_ups").cast(pl.UInt32, strict=False).alias("boards"),
        )
        .filter(pl.col("symbol").is_not_null() & (pl.col("boards") > 0) & pl.col("date").is_not_null())
        .unique(subset=["symbol", "date"], keep="last")
        .sort(["symbol", "date"])
    )
    if rows.is_empty():
        return empty
    previous = pl.col("boards").shift(1).over("symbol")
    grouped = rows.with_columns(
        (previous.is_null() | (pl.col("boards") != previous + 1))
        .cast(pl.UInt32)
        .cum_sum()
        .over("symbol")
        .alias("streak_no")
    )
    return (
        grouped.group_by(["symbol", "streak_no"], maintain_order=True)
        .agg(
            pl.col("date").min().alias("start_date"),
            pl.col("date").max().alias("end_date"),
            pl.col("boards").max().alias("boards"),
            pl.len().cast(pl.UInt32).alias("days"),
        )
        .drop("streak_no")
    )


def slice_streak_bars(
    bars: list[dict],
    start: date,
    end: date,
    *,
    before: int = BARS_BEFORE,
    after: int = BARS_AFTER,
) -> list[dict]:
    """保留首板前 before 根、结束后 after 根。缺数据时有多少留多少。"""
    if not bars:
        return []
    ordered = sorted(bars, key=lambda row: row["date"])
    start_idx = next((i for i, row in enumerate(ordered) if row["date"] >= start), None)
    end_idx = next((i for i in range(len(ordered) - 1, -1, -1) if ordered[i]["date"] <= end), None)
    if start_idx is None or end_idx is None or end_idx < start_idx:
        return []
    lo = max(0, start_idx - before)
    hi = min(len(ordered), end_idx + after + 1)
    return ordered[lo:hi]


def shift_years(day: date, years: int) -> date:
    """按公历加减整年。2 月 29 日落到平年时用 2 月 28 日。"""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def slice_year_bars(
    bars: list[dict],
    start: date,
    end: date,
    *,
    years: int = 1,
) -> list[dict]:
    """保留首板前 years 年到结束后 years 年之间的交易日。缺数据时有多少留多少。"""
    if not bars or years < 1:
        return []
    ordered = sorted(bars, key=lambda row: row["date"])
    lo = shift_years(start, -years)
    hi = shift_years(end, years)
    window = [row for row in ordered if lo <= row["date"] <= hi]
    if not any(start <= row["date"] <= end for row in window):
        return []
    return window


def full_window_cutoff(market_dates: list[date], after: int = BARS_AFTER) -> date | None:
    """结束日不晚于这一天时，市场上至少还有 after 个交易日。"""
    ordered = sorted(set(market_dates))
    if len(ordered) <= after:
        return None
    return ordered[-(after + 1)]


# 按仓库对象缓存。请求每次新建 service，索引不能放在实例上。
_CACHE: dict[int, tuple[tuple[str, int], pl.DataFrame]] = {}
_CACHE_LOCK = threading.Lock()


def clear_limit_up_research_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


class LimitUpResearchService:
    def __init__(self, repo) -> None:
        self.repo = repo

    def list_streaks(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        min_boards: int = 1,
        q: str = "",
        sort: str = "end_date",
        order: str = "desc",
        offset: int = 0,
        limit: int = 24,
        full_window: bool = False,
    ) -> dict:
        table = self._streaks_table()
        filtered = table
        cutoff = self._full_window_cutoff() if full_window else None
        if cutoff is not None:
            filtered = filtered.filter(pl.col("end_date") <= cutoff)
        if start is not None:
            filtered = filtered.filter(pl.col("end_date") >= start)
        if end is not None:
            filtered = filtered.filter(pl.col("start_date") <= end)
        if min_boards > 1:
            filtered = filtered.filter(pl.col("boards") >= min_boards)
        keyword = q.strip()
        if keyword:
            needle = keyword.upper()
            filtered = filtered.filter(
                pl.col("symbol").str.to_uppercase().str.contains(needle, literal=True)
                | pl.col("name").str.to_uppercase().str.contains(needle, literal=True)
            )
        descending = order != "asc"
        sort_key = "boards" if sort == "boards" else "end_date"
        filtered = filtered.sort(
            [sort_key, "end_date", "symbol"],
            descending=[descending, True, False],
        )
        total = filtered.height
        page = filtered.slice(offset, limit)
        bars_by_id = self._page_bars(page)
        rows = []
        for record in page.iter_rows(named=True):
            start_text = record["start_date"].isoformat()
            end_text = record["end_date"].isoformat()
            rows.append({
                "id": record["id"],
                "symbol": record["symbol"],
                "name": record["name"] or "",
                "start_date": start_text,
                "end_date": end_text,
                "boards": int(record["boards"]),
                "days": int(record["days"]),
                "bars": bars_by_id.get(record["id"], []),
            })
        as_of = ""
        if not table.is_empty():
            as_of = table.get_column("end_date").max().isoformat()
        return {
            "as_of": as_of,
            "total": total,
            "offset": offset,
            "limit": limit,
            "bars_before": BARS_BEFORE,
            "bars_after": BARS_AFTER,
            "full_window": full_window,
            "window_cutoff": cutoff.isoformat() if cutoff is not None else "",
            "rows": rows,
        }

    def _streaks_table(self) -> pl.DataFrame:
        revision = self.repo.limit_up_research_revision()
        key = id(self.repo)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] == revision:
                return cached[1]
        table = self._build_streaks()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] == revision:
                return cached[1]
            _CACHE[key] = (revision, table)
            return table

    def _build_streaks(self) -> pl.DataFrame:
        grouped = group_limit_up_streaks(self.repo.load_limit_up_days())
        names = self._names()
        if grouped.is_empty():
            return pl.DataFrame(schema=_STREAK_SCHEMA)
        named = grouped.join(names, on="symbol", how="left")
        return named.with_columns(
            pl.col("name").fill_null(""),
            (
                pl.col("symbol")
                + pl.lit(":")
                + pl.col("start_date").dt.strftime("%Y-%m-%d")
            ).alias("id"),
        ).select(list(_STREAK_SCHEMA))

    def _full_window_cutoff(self) -> date | None:
        return full_window_cutoff(self._market_dates())

    def _market_dates(self) -> list[date]:
        provider = getattr(self.repo, "limit_up_research_market_dates", None)
        if callable(provider):
            return list(provider())
        store = getattr(self.repo, "store", None)
        data_dir = getattr(store, "data_dir", None)
        if data_dir is None:
            return []
        root = Path(data_dir) / "kline_daily_enriched"
        if not root.is_dir():
            return []
        found: list[date] = []
        for path in root.iterdir():
            name = path.name
            if not path.is_dir() or not name.startswith("date="):
                continue
            try:
                found.append(date.fromisoformat(name[5:]))
            except ValueError:
                continue
        return found

    def _names(self) -> pl.DataFrame:
        empty = pl.DataFrame(schema={"symbol": pl.Utf8, "name": pl.Utf8})
        try:
            instruments = self.repo.get_instruments()
        except Exception:  # noqa: BLE001
            return empty
        if instruments is None or instruments.is_empty() or "symbol" not in instruments.columns:
            return empty
        if "name" not in instruments.columns:
            return instruments.select(pl.col("symbol").cast(pl.Utf8)).with_columns(pl.lit("").alias("name"))
        return (
            instruments.select(pl.col("symbol").cast(pl.Utf8), pl.col("name").cast(pl.Utf8))
            .unique(subset=["symbol"], keep="first")
        )

    def _page_bars(self, page: pl.DataFrame) -> dict[str, list[dict]]:
        if page.is_empty():
            return {}
        symbols = page.get_column("symbol").unique().to_list()
        start = page.get_column("start_date").min() - timedelta(days=CALENDAR_BEFORE)
        end = page.get_column("end_date").max() + timedelta(days=CALENDAR_AFTER)
        ohlcv = self.repo.load_enriched_ohlcv(symbols, start, end)
        by_symbol: dict[str, list[dict]] = {}
        if not ohlcv.is_empty():
            for row in ohlcv.iter_rows(named=True):
                if not _finite_price(row["open"], row["high"], row["low"], row["close"]):
                    continue
                by_symbol.setdefault(row["symbol"], []).append({
                    "date": row["date"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"] or 0.0,
                })
        result: dict[str, list[dict]] = {}
        for record in page.iter_rows(named=True):
            window = slice_streak_bars(
                by_symbol.get(record["symbol"], []),
                record["start_date"],
                record["end_date"],
            )
            result[record["id"]] = _serialize_bars(window)
        return result

    def zoom_bars(self, symbol: str, start: date, end: date) -> dict:
        """放大图：首板前一年到结束后一年，按交易日实有多少算多少。"""
        lo = shift_years(start, -1)
        hi = shift_years(end, 1)
        frame = self.repo.load_enriched_ohlcv([symbol], lo, hi)
        raw: list[dict] = []
        if frame is not None and not frame.is_empty():
            for row in frame.iter_rows(named=True):
                if row["symbol"] != symbol or not _finite_price(row["open"], row["high"], row["low"], row["close"]):
                    continue
                raw.append({
                    "date": row["date"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"] or 0.0,
                })
        window = slice_year_bars(raw, start, end)
        dates = [bar["date"] for bar in window]
        return {
            "symbol": symbol,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "from_date": lo.isoformat(),
            "to_date": hi.isoformat(),
            "bars_before": sum(day < start for day in dates),
            "bars_after": sum(day > end for day in dates),
            "bars": _serialize_bars(window),
        }


def _serialize_bars(window: list[dict]) -> list[dict]:
    return [
        {
            "date": bar["date"].isoformat(),
            "open": round(float(bar["open"]), 4),
            "high": round(float(bar["high"]), 4),
            "low": round(float(bar["low"]), 4),
            "close": round(float(bar["close"]), 4),
            "volume": float(bar["volume"] or 0.0),
        }
        for bar in window
    ]


def _finite_price(*values: float | None) -> bool:
    for value in values:
        if value is None or value <= 0:
            return False
    return True
