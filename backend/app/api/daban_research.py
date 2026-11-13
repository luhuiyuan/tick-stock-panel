"""打板接力研究工作台。列表走全量交易，K 线按页按行号取。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.daban_research import DabanResearchService

router = APIRouter(prefix="/api/daban-research", tags=["daban-research"])

# 一页最多 48 笔，和列表页大小一致；再多就该分页而不是一次要。
_MAX_IDS = 120


@router.get("/bars")
def daban_bars(
    request: Request,
    ids: str = Query(..., description="逗号分隔的行号，与 daban_data.json 的 stats 行号一致"),
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
        raise HTTPException(status_code=503, detail="打板 K 线数据还没生成，请先跑 export_daban.py")
    return payload
