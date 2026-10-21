"""股票及指数 chan.py 图表服务：只读本地行情并缓存有界结构快照。"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, time, timedelta
from threading import Lock

from app.enriched_generation import EnrichedGenerationUnavailableError
from app.indicators import chan_signals as cs
from app.indicators.chan_runtime import CONFIG, PROFILE_ID
from app.indicators.czsc_bars import FREQUENCIES, prepare_bars
from app.indicators.czsc_chart import build_chart
from app.market_time import CN_TZ, cn_now

ANALYSIS_BARS = 1000
_CACHE_LIMIT = 32
_cache: OrderedDict[tuple, dict] = OrderedDict()
_lock = Lock()


def _generation(repo, asset_type: str, *, minute: bool = False) -> str:
    """读取与图表来源对应的发布世代或只读分区指纹。"""
    if minute:
        return repo.get_minute_chart_generation()
    if asset_type == "index":
        return repo.get_index_chart_generation()
    return repo.get_matrix_data_generation(asset_type, readonly=True)


def get_chart(repo, symbol: str, *, now: datetime | None = None, timeframe: str = "1d",
              asset_type: str | None = None) -> dict:
    if timeframe not in FREQUENCIES:
        raise ValueError("不支持的 chan.py 图表周期")
    resolved_asset = repo.resolve_asset_type(symbol)
    if resolved_asset not in {"stock", "index"}:
        raise ValueError("chan.py 图表仅支持 A 股股票和指数")
    if asset_type is not None and asset_type != resolved_asset:
        raise ValueError("标的与所选资产类型不匹配，请通过股票或指数搜索选择标的")
    asset_type = resolved_asset
    name = repo.get_name_map([symbol]).get(symbol)
    if not name:
        raise ValueError("未找到标的，请通过股票或指数搜索选择标的")

    stamp = now or cn_now()
    stamp = stamp.replace(tzinfo=CN_TZ) if stamp.tzinfo is None else stamp.astimezone(CN_TZ)
    end = stamp.date() - timedelta(days=stamp.time() < time(15))
    minute = timeframe.endswith("m")
    is_index = asset_type == "index"
    if is_index and minute:
        return {
            "symbol": symbol, "name": name, "asset_type": asset_type, "timeframe": timeframe,
            "supported_timeframes": ["1d", "1w"], "status": "unavailable",
            "unavailable_reason": "unsupported_timeframe",
            "reason": "指数 chan.py 图表仅支持日线和周线，请选择对应周期",
        }

    # 分钟与指数目录没有 enriched 发布标记，使用只读分区指纹校验快照。
    generation = _generation(repo, asset_type, minute=minute)
    step = int(timeframe[:-1]) if minute else 1
    minute_end = stamp.replace(tzinfo=None, second=0, microsecond=0,
                               minute=(stamp.minute // step) * step)
    key = (repo, asset_type, symbol, name, timeframe, minute_end if minute else end,
           generation, cs.VERSION, PROFILE_ID, ANALYSIS_BARS, tuple(cs.SIGNALS))
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
    if cached is not None:
        if _generation(repo, asset_type, minute=minute) != generation:
            raise EnrichedGenerationUnavailableError("行情数据更新中，请稍后刷新")
        return {**cached, "refreshed_at": stamp.isoformat()}

    if minute:
        # 有界读取足以覆盖 1000 根 30 分钟线及多个交易日边界。
        read_limit = (ANALYSIS_BARS + 2) * step * 241 // 240 + 241
        source = repo.get_minute_published(symbol, minute_end, read_limit)
        supported = list(FREQUENCIES)
    else:
        # 日线限制读取尾部记录，周线额外读取足够的日线历史供聚合。
        read_limit = ANALYSIS_BARS * 5 + 5 if timeframe == "1w" else ANALYSIS_BARS
        source = repo.get_daily_published(
            symbol, end, read_limit, sorted(cs.INPUT_COLUMNS), asset_type=asset_type,
        )
        supported = ["1d", "1w"] if is_index else list(FREQUENCIES)

    base = {
        "symbol": symbol, "name": name, "asset_type": asset_type, "timeframe": timeframe,
        "version": cs.VERSION, "supported_timeframes": supported,
        "analysis_bars": ANALYSIS_BARS, "engine": "chan.py", "profile_id": PROFILE_ID,
        "algorithm_config": CONFIG, "confirmation": "endpoint_at_or_before_last_sure_pos",
        "source": "local_minute" if minute else "local_index_enriched" if is_index else "local_enriched",
        "price_basis": "沿用本地分钟价格" if minute else "沿用本地指数点位（不复权）" if is_index else "沿用本地日线价格",
        "volume_unit": "手", "amount_unit": "元", "source_count": len(source),
    }
    frame = prepare_bars(source, timeframe, now=stamp).tail(ANALYSIS_BARS)
    if frame.is_empty():
        reason = "本地暂无该周期已结束行情，请先在数据管理中同步对应数据"
        payload = {**base, "status": "empty", "reason": reason}
    else:
        chart = build_chart(frame, now=stamp, timeframe=timeframe)
        starts = [item["start"] for item in chart["strokes"] + chart["unfinished"]]
        payload = {
            **base, **chart, "status": "ready", "reason": None,
            "input_start": frame[0, "date"].isoformat(),
            "input_end": frame[-1, "date"].isoformat(), "input_count": len(frame),
            "structure_start": min(starts) if starts else None,
            "cutoff": chart["rows"][-1]["date"] if chart["rows"] else None,
        }
    if _generation(repo, asset_type, minute=minute) != generation:
        raise EnrichedGenerationUnavailableError("行情数据更新中，请稍后刷新")
    payload["generation"] = generation
    with _lock:
        _cache[key] = payload
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return {**payload, "refreshed_at": stamp.isoformat()}
