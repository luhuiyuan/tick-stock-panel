"""Launch and read bounded forward-signal research.

启动走 POST /start（后台任务），浏览走只读 GET； browsing never runs the experiment.
"""

from bisect import bisect_left
from datetime import date
from typing import Any, Literal

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request

from app.services.signal_forward_jobs import ForwardConfig, ForwardPerformanceJobs, _identity
from app.tickflow.repository import KlineRepository

router = APIRouter(prefix="/api/signal-forward-performance", tags=["signal-forward-performance"])

# 进程内单例：recover 只在首次创建时执行，避免每次 API 请求都把在途任务补录为中断。
_services: dict[tuple[str, bool], ForwardPerformanceJobs] = {}


def _service(request: Request, *, read_only: bool = True) -> ForwardPerformanceJobs:
    data_dir = request.app.state.repo.store.data_dir
    key = (str(data_dir), read_only)
    service = _services.get(key)
    if service is None:
        service = ForwardPerformanceJobs(data_dir, read_only=read_only)
        _services[key] = service
    return service


def _all_market_symbols(root) -> tuple[str, ...]:
    path = root / "instruments" / "instruments.parquet"
    if not path.is_file():
        raise ValueError("股票池不可用：缺少 instruments.parquet")
    frame = pl.read_parquet(path, columns=["symbol"]).filter(
        pl.col("symbol").str.contains(r"^\d{6}\.(SH|SZ|BJ)$")
    )
    return tuple(sorted(frame["symbol"].unique().to_list()))


@router.post("/start", status_code=202)
def start(request: Request, payload: dict[str, Any]):
    """启动一次触发后表现研究（后台运行，用 /status/{job} 轮询）。

    symbols 传空数组代表全市场（后端展开 instruments 全部 A 股）。
    """
    symbols = tuple(payload.get("symbols") or ())
    if not symbols:
        symbols = _all_market_symbols(request.app.state.repo.store.data_dir)
    try:
        config = ForwardConfig(
            symbols=symbols,
            signal_ids=tuple(payload["signal_ids"]),
            date_from=date.fromisoformat(payload["date_from"]),
            date_to=date.fromisoformat(payload["date_to"]),
            split_date=date.fromisoformat(payload["split_date"]),
        )
        return _service(request, read_only=False).start(config)
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/status/{job}")
def status(request: Request, job: str):
    try:
        record = _service(request, read_only=False).status(job)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if record is None:
        raise HTTPException(404, "未找到研究任务")
    return record


@router.get("/default-config")
def default_config(request: Request):
    """给出开箱即用的默认配置：最近区间 + 全市场 + 三个布林回避信号。"""
    import re

    from app.services.signal_forward_jobs import _load_calendar_days

    root = request.app.state.repo.store.data_dir
    days = [d for d in _load_calendar_days(root) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    days.sort()
    # 研究区间 120 日，验证期起点在区间 2/3 处，date_to 留 T+10 上下文由 jobs 处理。
    if len(days) >= 130:
        window = days[-130:-10]
        date_from, split_date, date_to = window[0], window[80], window[-1]
    elif len(days) >= 30:
        window = days[-30:]
        date_from, split_date, date_to = window[0], window[20], window[-1]
    else:
        raise HTTPException(422, "交易日历不足，无法给出默认配置")
    return {
        "symbols": [],  # 空 = 全市场，由前端/start 展开
        "signal_ids": ["boll_squeeze_warning", "boll_mid_breakdown", "ma20_bias_over_10"],
        "date_from": date_from, "split_date": split_date, "date_to": date_to,
        "all_symbols": True,
    }


@router.get("/symbols")
def symbols(request: Request):
    """给出可用于研究的股票池（全市场 A 股代码+名称），供页面选择/校验。"""
    import polars as pl

    data_dir = request.app.state.repo.store.data_dir
    path = data_dir / "instruments" / "instruments.parquet"
    items: list[dict] = []
    if path.is_file():
        cols = set(pl.scan_parquet(path).collect_schema().names())
        select = [c for c in ("symbol", "name") if c in cols]
        frame = pl.read_parquet(path, columns=select).unique(subset=["symbol"], keep="last")
        frame = frame.filter(pl.col("symbol").str.contains(r"^\d{6}\.(SH|SZ|BJ)$"))
        if "name" not in frame.columns:
            frame = frame.with_columns(pl.lit("").alias("name"))
        items = [
            {"symbol": r["symbol"], "name": r["name"] or ""}
            for r in frame.sort("symbol").to_dicts()
        ]
    return {"symbols": items}


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
        summary, monthly, contrast = (
            service.read_table(job["id"], name) for name in ("summary", "monthly", "contrast")
        )
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
        "contrast": _rows(contrast) if contrast is not None else [],
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
