"""Read saved forward research; browsing never runs the experiment."""

from bisect import bisect_left
from typing import Literal

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request

from app.services.signal_forward_jobs import ForwardPerformanceJobs, _identity
from app.tickflow.repository import KlineRepository

router = APIRouter(prefix="/api/signal-forward-performance", tags=["signal-forward-performance"])


def _service(request: Request) -> ForwardPerformanceJobs:
    return ForwardPerformanceJobs(request.app.state.repo.store.data_dir, read_only=True)


def _rows(frame: pl.DataFrame) -> list[dict]:
    floats = [name for name, dtype in frame.schema.items() if dtype.is_float()]
    return frame.with_columns(
        pl.when(pl.col(name).is_finite()).then(pl.col(name)).alias(name) for name in floats
    ).to_dicts()


@router.get("/latest")
def latest(request: Request):
    service = _service(request)
    job = next((r for r in service.store.list_recent(50) if r["status"] == "succeeded"), None)
    if job is None:
        return None
    try:
        meta = service.result(job["id"])
        if meta is None:
            raise ValueError("已保存结果不可用")
        summary, monthly = (service.read_table(job["id"], name) for name in ("summary", "monthly"))
        if summary is None or monthly is None:
            raise ValueError("已保存结果不可用")
    except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
        raise HTTPException(409, "无法读取已保存研究结果") from exc
    snap = meta["snapshot"]
    cfg = snap["config"]
    return {
        "run_id": job["id"],
        "rule": snap["rule"],
        "config": cfg,
        "stale": meta["stale"],
        "missing_dates": [
            day
            for day in snap["calendar"]
            if cfg["date_from"] <= day <= cfg["date_to"]
            and any(
                snap["files"].get(f"{table}/date={day}/part.parquet") is None
                for table in ("kline_daily", "kline_daily_enriched")
            )
        ],
        "summary": _rows(summary),
        "monthly": _rows(monthly),
    }


def _case_bars(service, snapshot, rows):
    days = snapshot["calendar"]
    windows = []
    for row in rows:
        center = bisect_left(days, row["signal_date"].isoformat())
        windows.append(set(days[max(0, center - 15) : center + 12]))
    wanted = set().union(*windows)
    inputs = {}
    for day in sorted(wanted):
        rel = f"kline_daily_enriched/date={day}/part.parquet"
        expected = snapshot["files"].get(rel)
        path = service.root / rel
        if expected is not None and _identity(path) == expected:
            inputs[path] = expected
    bars = KlineRepository.read_stock_research_bars(
        list(inputs), tuple(sorted({r["symbol"] for r in rows})), ("open", "high", "low", "close")
    )
    unchanged = {p.parent.name[5:] for p, identity in inputs.items() if _identity(p) == identity}
    by_symbol = {}
    for bar in _rows(bars.sort("symbol", "date")):
        day = bar["date"].isoformat()
        if day in unchanged and all(
            bar[key] is not None for key in ("open", "high", "low", "close")
        ):
            by_symbol.setdefault(bar.pop("symbol"), []).append((day, bar))
    for row, window in zip(rows, windows, strict=True):
        row["bars"] = [bar for day, bar in by_symbol.get(row["symbol"], []) if day in window]


@router.get("/{run_id}/events")
def events(
    request: Request,
    run_id: str,
    signal_id: str = Query(pattern=r"^[a-z0-9_]{1,40}$"),
    split: Literal["discovery", "validation"] = "validation",
    symbol: str = Query("", max_length=16),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=24),
):
    service = _service(request)
    try:
        meta = service.result(run_id, check_current=False)
        frame = service.read_table(run_id, "events") if meta else None
        if frame is None:
            raise HTTPException(404, "未找到已完成的研究结果")
        frame = frame.filter(
            (pl.col("signal_id") == signal_id)
            & (pl.col("split") == split)
            & pl.col("symbol").str.contains(symbol.strip().upper(), literal=True)
        )
        rows = _rows(
            frame.sort(["signal_date", "symbol"], descending=[True, False]).slice(offset, limit)
        )
        if rows:
            _case_bars(service, meta["snapshot"], rows)
        return {"total": frame.height, "rows": rows}
    except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
        raise HTTPException(409, "无法读取触发案例，请刷新研究结果") from exc
