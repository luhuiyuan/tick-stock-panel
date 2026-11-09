"""/stock-emotion「个股情绪分」页的后端。

接口：
- ``GET /api/stock-emotion``：给定标的 + 周期，用近期价格走势给个股打「恐惧↔亢奋」连续分。
  计算在 ``app.services.stock_emotion``，全部因果滚动、无未来数据、不逐日重放。

口径（见 backend/app/services/stock_emotion.py 顶部说明）：
- 数据源：本地 enriched 日线分区；
- 周期：近 60 / 120 / 250 / 500 交易日，默认 250；前 20 天预热留空；
- 5 指标等权：RSI(14) / ROC(10)±20% / Stochastic%K(14,3) / BIAS20±15% / Bollinger%B(20)；
- 0=极度恐惧、100=极度亢奋、50=中性。

复用：``app.api.phase_lab`` 的 ``_load_symbol_rows`` / ``_normalize_symbol`` / ``_cache_get``
（通用数据读取、符号规范化、短 TTL 缓存），取数目录沿用 ``app.state.repo.store.data_dir``。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.phase_lab import _cache_get, _load_symbol_rows, _normalize_symbol
from app.services import stock_emotion as se

router = APIRouter(prefix="/api/stock-emotion", tags=["stock-emotion"])

LOOKBACKS = se.LOOKBACKS
DEFAULT_LOOKBACK = se.DEFAULT_LOOKBACK


@router.get("")
def stock_emotion(
    request: Request,
    symbol: str = Query(..., description="标的代码，如 000006.SZ"),
    lookback: int = Query(DEFAULT_LOOKBACK, description="近 N 个交易日：60/120/250/500"),
    config: str | None = Query(
        None,
        description="可选 JSON，覆盖指标映射区间（如 {\"roc10\":{\"low\":-15,\"high\":15}}）或档位阈值（{\"thresholds\":{\"fear\":25}}）",
    ),
) -> dict:
    """用近期价格走势给个股打「恐惧↔亢奋」连续分。"""
    sym = _normalize_symbol(symbol)
    if lookback not in LOOKBACKS:
        raise HTTPException(status_code=400, detail=f"lookback 只支持 {LOOKBACKS}")

    parsed_config: dict[str, Any] | None = None
    if config:
        try:
            parsed_config = json.loads(config)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="config 不是合法 JSON")

    repo = request.app.state.repo
    data_dir = Path(getattr(getattr(repo, "store", None), "data_dir", "."))

    def produce() -> dict:
        started = time.perf_counter()
        rows = _load_symbol_rows(data_dir, sym)
        try:
            out = se.compute_stock(rows, sym, lookback=lookback, config=parsed_config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        out["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return out

    return _cache_get((f"emotion:{sym}", lookback, config or ""), produce)
