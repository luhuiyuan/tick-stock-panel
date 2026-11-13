"""打板接力研究工作台：点按钮重算，接口返回全量交易与按页 K 线。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.daban_research import DabanResearchService

router = APIRouter(prefix="/api/daban-research", tags=["daban-research"])

# 一页最多 48 笔，和列表页大小一致；再多就该分页而不是一次要。
_MAX_IDS = 120


@router.post("/rebuild")
def daban_rebuild(request: Request) -> dict:
    """按当前本地日线重算 6 个板位的全量交易（约 20 秒），结果写入缓存。

    和涨停段 / 连涨段一样：只在用户点「重新计算」时跑，不会因为行情更新自动重算。
    """
    try:
        return DabanResearchService(request.app.state.repo).rebuild()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/dataset")
def daban_dataset(request: Request) -> dict:
    """返回重算结果 (meta / baseline / scan / stats)。没算过时 computed=false。"""
    return DabanResearchService(request.app.state.repo).dataset()


@router.get("/bars")
def daban_bars(
    request: Request,
    ids: str = Query(..., description="逗号分隔的行号，与 dataset 里 stats 的行号一致"),
):
    """按行号批量取 K 线窗口（O,H,L,C × 17 根，单位分）。"""
    raw = [p.strip() for p in ids.split(",") if p.strip()]
    if not raw:
        raise HTTPException(status_code=400, detail="需要行号")
    if len(raw) > _MAX_IDS:
        raise HTTPException(status_code=400, detail="一次最多取 %d 笔" % _MAX_IDS)
    try:
        parsed = [int(p) for p in raw]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="行号必须是整数") from exc

    service = DabanResearchService(request.app.state.repo)
    payload = service.bars(parsed)
    if not payload["ok"]:
        raise HTTPException(status_code=503, detail="还没有计算过打板数据，请先点「重新计算」")
    return payload
