"""策略复盘：指定策略在历史区间逐日选股，分页看 K 线缩略图与后续盈利统计。"""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services.strategy_review import (
    DEFAULT_COST_PCT,
    DEFAULT_SCAN_DAYS,
    HORIZONS,
    MAX_SCAN_DAYS,
    StrategyReviewService,
    _market_dates,
)

router = APIRouter(prefix="/api/strategy-review", tags=["strategy-review"])

_SYMBOL = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
_RESULTS = ("all", "win", "lose", "pending")


class RebuildRequest(BaseModel):
    strategy_ids: list[str] = Field(default_factory=list, max_length=30)
    start: date | None = None
    end: date | None = None
    cost_pct: float = Field(DEFAULT_COST_PCT, ge=0, le=0.01)


def _service(request: Request) -> StrategyReviewService:
    return StrategyReviewService(
        request.app.state.repo,
        engine=getattr(request.app.state, "strategy_engine", None),
    )


def _resolve_range(service: StrategyReviewService, start: date | None, end: date | None) -> tuple[date, date]:
    calendar = _market_dates(service.data_dir)
    if not calendar:
        raise HTTPException(status_code=400, detail="本地没有 enriched 日线数据")
    if start is None and end is None:
        window = calendar[-DEFAULT_SCAN_DAYS:]
        return window[0], calendar[-1]
    if end is None:
        end = calendar[-1]
    if start is None:
        window = [d for d in calendar if d <= end][-DEFAULT_SCAN_DAYS:]
        start = window[0] if window else calendar[0]
    if start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    days = [d for d in calendar if start <= d <= end]
    if not days:
        raise HTTPException(status_code=400, detail="所选区间内没有交易日")
    if len(days) > MAX_SCAN_DAYS:
        raise HTTPException(status_code=400, detail=f"区间交易日数 {len(days)} 超过上限 {MAX_SCAN_DAYS}")
    return start, end


@router.post("/rebuild")
def rebuild(req: RebuildRequest, request: Request) -> dict:
    """提交一次区间扫描（后台异步）。重复点击复用同一个活跃任务。"""
    service = _service(request)
    start, end = _resolve_range(service, req.start, req.end)
    ids = list(dict.fromkeys(req.strategy_ids))
    if not ids:
        raise HTTPException(status_code=400, detail="至少选择一个策略")
    engine = service.engine
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未初始化")
    for sid in ids:
        if not engine.has(sid):
            raise HTTPException(status_code=404, detail=f"未知策略: {sid}")
        meta = engine.get(sid).meta
        if meta.get("research_only") or meta.get("backtest_only"):
            raise HTTPException(status_code=400, detail=f"策略不可用于选股: {sid}")
    return service.submit_rebuild(
        strategy_ids=ids, start=start, end=end, cost_pct=req.cost_pct,
    )


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> dict:
    job = _service(request).job_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("/runs")
def runs(
    request: Request,
    horizon: int = Query(1, ge=1, le=5),
    result: str = Query("all", pattern="^(all|win|lose|pending)$"),
    start: date | None = None,
    end: date | None = None,
    q: str = Query("", max_length=32),
    strategy_id: str = Query("", max_length=64),
    min_ret: float | None = None,
    max_ret: float | None = None,
    sort: str = Query("signal_date", pattern="^(signal_date|strategy_count|ret_1|ret_3|ret_5)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=48),
) -> dict:
    if horizon not in HORIZONS:
        raise HTTPException(status_code=400, detail="持有期只支持 1 / 3 / 5 个交易日")
    if result not in _RESULTS:
        raise HTTPException(status_code=400, detail="结果类型不合法")
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    if min_ret is not None and max_ret is not None and min_ret > max_ret:
        raise HTTPException(status_code=400, detail="收益下限不能大于上限")
    return _service(request).list_runs(
        horizon=horizon, result=result, start=start, end=end, q=q,
        strategy_id=strategy_id, min_ret=min_ret, max_ret=max_ret,
        sort=sort, order=order, offset=offset, limit=limit,
    )


@router.get("/summary")
def summary(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    q: str = Query("", max_length=32),
    strategy_id: str = Query("", max_length=64),
) -> dict:
    """三档胜率、平均/中位收益、盈亏比。不含分页明细。"""
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    return _service(request).summary(start=start, end=end, q=q, strategy_id=strategy_id)


@router.get("/bars")
def zoom_bars(
    request: Request,
    symbol: str = Query(min_length=9, max_length=9),
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """放大图：按信号日自动向前后各扩一年。"""
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="需要起止日期")
    if start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    normalized = symbol.upper()
    if _SYMBOL.fullmatch(normalized) is None:
        raise HTTPException(status_code=400, detail="代码格式不对")
    return _service(request).zoom_bars(normalized, start, end)
