"""涨停研究工作台。一段连续涨停是一条记录。"""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.limit_up_research import LimitUpResearchService

router = APIRouter(prefix="/api/limit-up-research", tags=["limit-up-research"])


@router.get("/streaks")
def limit_up_streaks(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    min_boards: int = Query(1, ge=1, le=30),
    q: str = Query("", max_length=32),
    sort: str = Query("end_date", pattern="^(end_date|boards)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=48),
    full_window: bool = Query(False),
):
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    service = LimitUpResearchService(request.app.state.repo)
    return service.list_streaks(
        start=start,
        end=end,
        min_boards=min_boards,
        q=q,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
        full_window=full_window,
    )


_SYMBOL = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


@router.post("/rebuild")
def limit_up_rebuild(request: Request) -> dict:
    """按当前日线重算连板，并写入内存和落盘文件。不改变连板规则。"""
    service = LimitUpResearchService(request.app.state.repo)
    return service.rebuild()


@router.get("/bars")
def limit_up_zoom_bars(
    request: Request,
    symbol: str = Query(min_length=9, max_length=9),
    start: date | None = None,
    end: date | None = None,
):
    """放大图。窗口固定为首板前一年到结束后一年，调用方不能自行加长。"""
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="需要首板日和结束日")
    if start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    normalized = symbol.upper()
    if _SYMBOL.fullmatch(normalized) is None:
        raise HTTPException(status_code=400, detail="代码格式不对")
    service = LimitUpResearchService(request.app.state.repo)
    return service.zoom_bars(normalized, start, end)
