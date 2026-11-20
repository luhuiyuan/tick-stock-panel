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


def coverage(request):
    from pathlib import Path

    from app.services.surge_coverage import get_coverage

    return get_coverage(Path(request.app.state.repo.store.data_dir))


@router.get("/coverage")
def coverage_status(request: Request):
    svc = coverage(request)
    latest = svc.store.list_recent(1)
    signals = svc.listing()
    return {
        "signals": signals,
        "result": next(
            (e["result"] for e in signals if e["signal_id"] == "t_shrink_pullback"), None
        ),
        "job": svc.store.get(latest[0]["id"]) if latest else None,
    }


@router.post("/coverage/calculate", status_code=202)
def coverage_calculate(request: Request, signal_id: str | None = None):
    try:
        return coverage(request).start(signal_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/coverage/events")
def coverage_events(
    request: Request,
    category: Literal["hit", "miss", "unknown"],
    version: str,
    signal_id: str = "t_shrink_pullback",
    offset: int = Query(0, ge=0),
):
    try:
        from app.services.rise_research import RiseResearchService

        charts = RiseResearchService(request.app.state.repo)
        return {
            "rows": coverage(request).read(
                category, offset, version, charts=charts, signal_id=signal_id
            )[1]
        }
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
