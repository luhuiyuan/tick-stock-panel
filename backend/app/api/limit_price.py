"""涨停价缓存: GET 只读概况, 显式 POST 才启动/停止后台缓存。"""

from datetime import date

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/limit-price", tags=["limit-price"])


def _service(request: Request):
    from pathlib import Path

    from app.services.limit_price_job import get_cache

    return get_cache(Path(request.app.state.repo.store.data_dir))


@router.get("/status")
def status(request: Request):
    """缓存概况(键数/日期范围/接口可用) + 最近任务状态。"""
    return _service(request).status()


@router.get("/days")
def days(request: Request):
    """可缓存的交易日范围(供前端设置默认起始日)。"""
    svc = _service(request)
    ds = svc.available_days()
    return {
        "earliest": str(ds[0]) if ds else None,
        "latest": str(ds[-1]) if ds else None,
        "count": len(ds),
    }


@router.post("/cache", status_code=202)
def start_cache(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
):
    try:
        return _service(request).start(start_date, end_date)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/cancel", status_code=202)
def cancel_cache(request: Request):
    """请求取消当前任务(由 batch 的 checkpoint 在下一个安全点停下, 已落盘数据保留)。"""
    from app.services.pipeline_jobs import request_cancel

    svc = _service(request)
    latest = svc.jobs.list_recent(1)
    if not latest:
        raise HTTPException(404, "没有可取消的任务")
    job_id = latest[0]["id"]
    cur = svc.jobs.get(job_id)
    if not cur or cur.get("status") not in ("pending", "running"):
        raise HTTPException(409, "当前任务已结束")
    ok = request_cancel(job_id)
    return {"job_id": job_id, "cancelled": ok}
