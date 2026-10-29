"""连涨研究。中间可以下跌的上涨段，累计涨幅大于 30%，一段一条。"""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.rise_research import MIN_GAIN, RiseResearchService

router = APIRouter(prefix="/api/rise-research", tags=["rise-research"])


@router.get("/runs")
def rise_runs(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    min_gain: float = Query(MIN_GAIN, ge=MIN_GAIN, le=5),
    q: str = Query("", max_length=32),
    sort: str = Query("end_date", pattern="^(end_date|gain|days)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=48),
    full_window: bool = Query(False),
):
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    service = RiseResearchService(request.app.state.repo)
    return service.list_runs(
        start=start,
        end=end,
        min_gain=min_gain,
        q=q,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
        full_window=full_window,
    )


_SYMBOL = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


@router.get("/bars")
def rise_zoom_bars(
    request: Request,
    symbol: str = Query(min_length=9, max_length=9),
    start: date | None = None,
    end: date | None = None,
):
    """放大图。窗口固定为起涨前一年到结束后一年，调用方不能自行加长。"""
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="需要起涨日和结束日")
    if start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    normalized = symbol.upper()
    if _SYMBOL.fullmatch(normalized) is None:
        raise HTTPException(status_code=400, detail="代码格式不对")
    service = RiseResearchService(request.app.state.repo)
    return service.zoom_bars(normalized, start, end)
