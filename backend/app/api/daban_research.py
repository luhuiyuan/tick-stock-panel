"""打板接力研究工作台：点按钮重算，接口返回全量交易与按页 K 线。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.daban_research import DabanResearchService
from app.services.daban_tag_evolution import run_evolution

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
    """返回重算结果 (meta / stats / tags)。没算过时 computed=false。"""
    return DabanResearchService(request.app.state.repo).dataset()


@router.get("/evolution")
def daban_evolution(
    max_size: int = Query(3, ge=1, le=3, description="组合层数上限"),
    beam_width: int = Query(24, ge=4, le=48, description="每层束宽"),
    top_n: int = Query(12, ge=1, le=40, description="每层展示 Top-N"),
    min_samples: int = Query(200, ge=50, le=2000, description="最小样本门槛"),
):
    """标签进化搜索：pre 标签的正向/反向 AND 组合，逐层找高胜率公式。

    返回单标签基线、每层 Top-N 路径与父子谱系（进化树）、IS/OOS 防过拟合指标。
    约 4 秒，结果含未来数据仅供研究复盘，不构成实盘信号。
    """
    try:
        return run_evolution(max_size=max_size, beam_width=beam_width,
                             top_n=top_n, min_samples=min_samples)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="还没有计算过打板数据，请先点「重新计算」") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
