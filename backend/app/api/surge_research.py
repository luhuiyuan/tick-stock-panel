"""短期猛拉快照；GET 只读，显式 POST 才计算。"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.surge_research import SurgeResearchService

router = APIRouter(prefix="/api/surge-research", tags=["surge-research"])


def service(request, start, end):
    if start and end and start > end:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    return SurgeResearchService(request.app.state.repo)


@router.get("/runs")
def runs(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    pending: bool = False,
    q: str = Query("", max_length=32),
    started: str = Query("", pattern=r"^($|\d{4}-\d{2}(-\d{2})?)$"),
    sort: Literal["end_date", "gain", "daily_gain"] = "end_date",
    order: Literal["asc", "desc"] = "desc",
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=48),
):
    return service(request, start, end).query(
        start=start,
        end=end,
        pending=pending,
        q=q,
        started=started,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
    )


@router.get("/starts")
def starts(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    pending: bool = False,
    q: str = Query("", max_length=32),
    bucket: Literal["day", "month"] = "day",
):
    return service(request, start, end).query(
        start=start, end=end, pending=pending, q=q, bucket=bucket
    )


@router.post("/rebuild")
def rebuild(request: Request):
    try:
        return SurgeResearchService(request.app.state.repo).rebuild()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
