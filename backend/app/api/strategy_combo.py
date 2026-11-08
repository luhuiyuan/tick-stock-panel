"""策略组合探索：扫描 / 组合排名 / 两两胜率热力图 / 下钻信号明细。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services.strategy_combo import (
    DEFAULT_COST_PCT,
    HORIZON_DEFAULT,
    IS_RATIO_DEFAULT,
    MAX_COMBO_SIZE_DEFAULT,
    MAX_POOL_STRATEGIES,
    MIN_SAMPLES_DEFAULT,
    RANK_BY,
    TOP_N_DEFAULT,
    StrategyComboService,
)
from app.services.strategy_review import _market_dates

router = APIRouter(prefix="/api/strategy-combo", tags=["strategy-combo"])

DEFAULT_SCAN_DAYS = 40
MAX_SCAN_DAYS = 120


class ScanRequest(BaseModel):
    strategy_ids: list[str] = Field(default_factory=list, max_length=30)
    start: date | None = None
    end: date | None = None
    cost_pct: float = Field(DEFAULT_COST_PCT, ge=0, le=0.01)
    force: bool = False


def _service(request: Request) -> StrategyComboService:
    return StrategyComboService(
        request.app.state.repo,
        engine=getattr(request.app.state, "strategy_engine", None),
    )


def _resolve_range(service: StrategyComboService, start: date | None, end: date | None) -> tuple[date, date]:
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


@router.post("/scan")
def scan(req: ScanRequest, request: Request) -> dict:
    """提交阶段 A 扫描（后台异步，独立任务/独立锁/可取消）。页面首次打开无需扫描：
    会直接复用策略复盘已有的多策略信号池。"""
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
    return service.submit_scan(strategy_ids=ids, start=start, end=end,
                               cost_pct=req.cost_pct, force=req.force)


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> dict:
    job = _service(request).job_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    ok = _service(request).cancel_job(job_id)
    return {"job_id": job_id, "requested": ok}


@router.get("/meta")
def meta(request: Request) -> dict:
    """信号池来源、覆盖状态、区间与扫描信息。"""
    return _service(request).coverage()


@router.get("/rankings")
def rankings(
    request: Request,
    horizon: int = Query(HORIZON_DEFAULT, ge=1, le=5),
    rank_by: str = Query("composite", pattern="^(composite|win_rate|avg_ret)$"),
    min_samples: int = Query(MIN_SAMPLES_DEFAULT, ge=1, le=2000),
    top_n: int = Query(TOP_N_DEFAULT, ge=1, le=50),
    is_ratio: float = Query(IS_RATIO_DEFAULT, ge=0.5, le=0.9),
    max_combo_size: int = Query(MAX_COMBO_SIZE_DEFAULT, ge=2, le=3),
    max_strategies: int = Query(MAX_POOL_STRATEGIES, ge=2, le=20),
) -> dict:
    """阶段 B：组合自动排名（纯集合运算，不重跑策略）。"""
    if horizon not in (1, 3, 5):
        raise HTTPException(status_code=400, detail="持有期只支持 1 / 3 / 5 个交易日")
    if rank_by not in RANK_BY:
        raise HTTPException(status_code=400, detail="排名目标不合法")
    return _service(request).rankings(
        horizon=horizon, rank_by=rank_by, min_samples=min_samples, top_n=top_n,
        is_ratio=is_ratio, max_combo_size=max_combo_size, max_strategies=max_strategies,
    )


@router.get("/heatmap")
def heatmap(
    request: Request,
    horizon: int = Query(HORIZON_DEFAULT, ge=1, le=5),
    max_strategies: int = Query(MAX_POOL_STRATEGIES, ge=2, le=20),
) -> dict:
    """两两策略交集胜率矩阵（含对角=单策略）。"""
    if horizon not in (1, 3, 5):
        raise HTTPException(status_code=400, detail="持有期只支持 1 / 3 / 5 个交易日")
    return _service(request).heatmap(horizon=horizon, max_strategies=max_strategies)


@router.get("/combo-signals")
def combo_signals(
    request: Request,
    key: str = Query(..., min_length=3, max_length=256),
    horizon: int = Query(HORIZON_DEFAULT, ge=1, le=5),
    result: str = Query("all", pattern="^(all|win|lose|pending)$"),
    sort: str = Query("signal_date", pattern="^(signal_date|strategy_count|ret_1|ret_3|ret_5)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=48),
) -> dict:
    """点击组合下钻：该组合命中的信号明细（含 K 线缩略图）。"""
    if horizon not in (1, 3, 5):
        raise HTTPException(status_code=400, detail="持有期只支持 1 / 3 / 5 个交易日")
    return _service(request).combo_signals(
        key=key, horizon=horizon, result=result, sort=sort, order=order,
        offset=offset, limit=limit,
    )
