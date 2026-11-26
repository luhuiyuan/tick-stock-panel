"""Read-only, bounded API for L2 high/low signal review."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request

from app.enriched_generation import enriched_publication_incomplete
from app.market_time import cn_today
from app.polars_guard import guarded_collect
from app.services import xdxr_ref
from app.services.turning_point_review import (
    CASE_TYPES,
    FIELDS,
    LIMIT_SIGNALS,
    MAX_LEAD,
    RULE,
    SWING_LENGTH,
    WINDOWS,
    ReviewScope,
    build_review,
    describe_signal,
    digest,
    signal_catalog,
)
from app.tickflow.repository import KlineRepository, enriched_dirname

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/turning-point-review", tags=["turning-point-review"])
_capacity = BoundedSemaphore(1)
MAX_SYMBOLS, MAX_SIGNALS, MAX_DAYS = 5, 10, 366
THUMBNAIL_PAGE_SIZE = 24
CONTEXT_BEFORE, CONTEXT_AFTER = 850, 100  # calendar days, not synthetic K bars
DateQuery = Annotated[date, Query(ge=date(1991, 1, 1), le=date(2099, 12, 31))]


def _data_dir(request: Request) -> Path:
    store = getattr(getattr(request.app.state, "repo", None), "store", None)
    root = getattr(store, "data_dir", None)
    if root is None:
        raise HTTPException(503, "行情仓库尚未就绪")
    return Path(root)


def _csv(value: str, limit: int, field: str, pattern: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in value.split(","))
    if len(parts) > limit or any(not re.fullmatch(pattern, part) for part in parts):
        raise HTTPException(422, f"{field} 格式无效或超过 {limit} 项")
    return tuple(dict.fromkeys(parts))


@router.get("/scope")
def methodology():
    return {
        "rule": RULE, "level": "L2", "swing_length": SWING_LENGTH,
        "lead_windows": [{"id": key, "min_bars": lo, "max_bars": hi} for key, lo, hi in WINDOWS],
        "limits": {"symbols": MAX_SYMBOLS, "signals": MAX_SIGNALS, "calendar_days": MAX_DAYS},
        "notes": [
            "高低点分开评估。拐点与提醒首日必须都在研究区间内；预热与标签观察使用区间外行情。",
            "L2 使用前复权日线的事后标签，确认日仅代表局部观察窗到齐，未来行情仍可能改写标签。",
            "连续为真只记首日；前一日未知则不认定新提醒。当日触发不算提前。",
            "先处理较早的拐点，分配最早可用的提醒；同信号、同方向一对一。重复提醒单列。",
            "已知缺整日分区时，不把跨缺口的提醒与拐点配对；局部确认窗跨缺口的标签单列。单只股票缺行仍无法完全区分停牌与数据缺失。",
            "覆盖率 = 命中拐点 / (命中 + 漏报)；提醒匹配率 = 匹配提醒 / (匹配 + 重复 + 未匹配)。不可判定另计。",
            "信号使用当前定义重算历史日线，不使用当天盘中快照，也不检验成交或收益。",
            "限价信号优先用同日记录，缺失时复用覆盖榜规则（可能参考前后行情推断 ST），不视为官方记录或实时预测证据。",
            "案例的五根 K 线不利变动以提醒日收盘为参考：高点研究看之后最高价上偏，低点研究看之后最低价下偏。",
        ],
    }


def _partition_snapshot(root: Path, start: date, end: date) -> dict[str, tuple[int, ...]]:
    if enriched_publication_incomplete(root):
        raise HTTPException(503, "增强日线正在发布，请稍后重试")
    partitions = {}
    directory = root / enriched_dirname("stock")
    for delta in range((end - start).days + 1):
        day = start + timedelta(days=delta)
        path = directory / f"date={day.isoformat()}" / "part.parquet"
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        partitions[str(day)] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return partitions


def _file_signature(path: Path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _load_limits(root, symbols, lo, hi):
    path = root / "instruments" / "instruments.parquet"
    inputs = (path, xdxr_ref.path_for(root))
    before = tuple(_file_signature(p) for p in inputs)
    instruments = pl.DataFrame()
    if before[0] is not None:
        scan = pl.scan_parquet(path)
        fields = set(scan.collect_schema().names())
        if {"symbol", "as_of"} <= fields:
            columns = sorted(fields & {"symbol", "as_of", "name", "limit_up", "limit_down"})
            instruments = guarded_collect(
                scan.select(columns).with_columns(pl.col("as_of").cast(pl.Date, strict=False))
                .filter(pl.col("symbol").is_in(symbols) & pl.col("as_of").is_between(lo, hi)),
                priority="background",
            )
    events = xdxr_ref.load(root).filter(pl.col("symbol").is_in(symbols) & pl.col("trade_date").is_between(lo, hi))
    if before != tuple(_file_signature(p) for p in inputs):
        raise HTTPException(503, "读取期间历史限价来源发生变化，请重试")
    return (instruments, events), before


def _load(root, symbols, scope):
    lo = scope.start - timedelta(days=CONTEXT_BEFORE)
    hi = min(scope.end + timedelta(days=CONTEXT_AFTER), cn_today() - timedelta(days=1))
    partitions = _partition_snapshot(root, lo, hi)
    if not partitions:
        raise HTTPException(404, "区间内没有本地增强日线分区")
    days = list(partitions)
    directory = root / enriched_dirname("stock")
    chunks = []
    for i in range(0, len(days), 32):
        batch = days[i:i + 32]
        frame = KlineRepository.read_stock_research_bars(
            [directory / f"date={day}" / "part.parquet" for day in batch], symbols, FIELDS,
        )
        if not set(frame["date"].cast(pl.String).unique()) <= set(batch):
            raise ValueError("日线日期与分区批次不符")
        chunks.append(frame)
    calendar_file = root / "trading_calendar.json"
    calendar = [date.fromisoformat(day) for day in json.loads(calendar_file.read_text())] if calendar_file.is_file() else []
    if partitions != _partition_snapshot(root, lo, hi):
        raise HTTPException(503, "读取期间行情发生变化，请重试")
    missing = tuple(sorted(d for d in set(calendar) if lo <= d <= hi and str(d) not in partitions))
    snapshot = digest({"partitions": partitions, "calendar": [str(d) for d in calendar]})
    return pl.concat(chunks), calendar, missing, snapshot, lo, hi


def _evaluate(request, symbols, start, end, direction, signal_ids):
    if end >= cn_today():
        raise HTTPException(422, "结束日期必须早于当前交易日历日期，不使用当天盘中快照")
    if not 0 <= (end - start).days < MAX_DAYS:
        raise HTTPException(422, f"起止日期无效或所选区间超过 {MAX_DAYS} 天")
    symbols = _csv(symbols.upper(), MAX_SYMBOLS, "symbols", r"\d{6}\.(SH|SZ|BJ)")
    directions = _csv(direction, 2, "direction", r"high|low")
    ids = _csv(signal_ids, MAX_SIGNALS, "signal_ids", r"[a-zA-Z0-9_\-]{1,80}")
    root = _data_dir(request)
    if not _capacity.acquire(blocking=False):
        raise HTTPException(429, "正在计算另一个拐点评估，请稍后重试")
    try:
        catalog = signal_catalog(root)
        unknown = set(ids) - catalog.keys()
        if unknown:
            raise HTTPException(422, f"未知信号：{', '.join(sorted(unknown))}")
        scope = ReviewScope(start, end, ids, directions)
        try:
            frame, calendar, missing, snapshot, lo, hi = _load(root, symbols, scope)
        except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
            logger.warning("L2 review snapshot unavailable: %s", exc)
            raise HTTPException(503, "无法读取完整的本地日线或交易日历") from exc
        # Include these signatures even for non-limit signals so a single-signal
        # case request can use the same snapshot as a mixed-signal summary.
        limit_signatures = tuple(_file_signature(p) for p in (
            root / "instruments" / "instruments.parquet", xdxr_ref.path_for(root),
        ))
        limit_inputs = None
        if set(ids) & LIMIT_SIGNALS:
            try:
                limit_inputs, limit_signatures = _load_limits(root, symbols, lo, hi)
            except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
                logger.warning("L2 review limit inputs unavailable: %s", exc)
        results, errors = [], []
        for symbol in symbols:
            try:
                results.append(build_review(frame.filter(pl.col("symbol") == symbol), catalog, scope, calendar, missing,
                                            limit_inputs=limit_inputs))
            except ValueError as exc:
                errors.append({"symbol": symbol, "reason": str(exc)})
        return {
            "scope": {"symbols": symbols, "start": start, "end": end, "directions": directions, "signal_ids": ids},
            "rule": RULE, "snapshot_sha256": digest({"rule": RULE, "data": snapshot, "catalog": catalog, "limits": limit_signatures}),
            "missing_market_partitions": [d for d in missing if start <= d <= end],
            "_missing_context_partitions": missing,
            "results": results, "errors": errors,
        }
    finally:
        _capacity.release()


def _aggregate(results, directions, ids):
    summaries = []
    for sid in ids:
        for direction in directions:
            rows = [next(s for s in r["signals"] if s["signal_id"] == sid) for r in results]
            values = [row["directions"][direction] for row in rows if direction in row["directions"]]
            keys = ("pivot_count", "alert_count", "covered_count", "matched_alert_count", "missed_count",
                    "duplicate_alert_count", "unmatched_alert_count", "indeterminate_pivot_count", "indeterminate_alert_count")
            counts = {k: sum(v[k] for v in values) for k in keys}
            covered = counts["covered_count"]
            pivot_denominator = covered + counts["missed_count"]
            alert_denominator = covered + counts["unmatched_alert_count"] + counts["duplicate_alert_count"]
            summaries.append({
                "signal_id": sid, "direction": direction, "evaluated_symbols": len(values),
                "unavailable_symbols": len(rows) - len(values), **counts,
                "pivot_coverage": covered / pivot_denominator if pivot_denominator else None,
                "alert_match_rate": covered / alert_denominator if alert_denominator else None,
                "lead_windows": {key: sum(v["lead_windows"][key] for v in values) for key, _, _ in WINDOWS},
            })
    return summaries


@router.get("")
def review(request: Request, symbols: Annotated[str, Query(max_length=64)], start: DateQuery, end: DateQuery,
           signal_ids: Annotated[str, Query(max_length=810)], direction: str = "high,low"):
    result = _evaluate(request, symbols, start, end, direction, signal_ids)
    result.pop("_missing_context_partitions")
    for item in result["results"]:
        item.pop("cases")
        item.pop("chart")
    result["summary"] = _aggregate(result["results"], result["scope"]["directions"], result["scope"]["signal_ids"])
    return result


@router.get("/signals")
def signals(request: Request):
    catalog = signal_catalog(_data_dir(request))
    return {"signals": [describe_signal(catalog[sid]) for sid in sorted(catalog)]}


def _case_chart(case, chart, missing_partitions=()):
    alert, pivot = case["alert"], case["pivot"]
    first = (alert["start_index"] if alert else pivot["index"]) - 20
    last = (pivot["index"] if pivot else alert["start_index"] + MAX_LEAD) + SWING_LENGTH
    bars = chart[max(0, first):last + 1]
    gap = bool(bars) and any(bars[0]["date"] <= day <= bars[-1]["date"] for day in missing_partitions)
    return {"bars": bars, "bars_truncated": first < 0 or last >= len(chart) or not bars or gap}


@router.get("/cases")
def cases(request: Request, symbols: Annotated[str, Query(max_length=64)], start: DateQuery, end: DateQuery,
          signal_id: Annotated[str, Query(pattern=r"^[a-zA-Z0-9_\-]{1,80}$")], direction: str = "high,low",
          case_type: str | None = None, offset: Annotated[int, Query(ge=0)] = 0,
          limit: Annotated[int | None, Query(ge=1, le=100)] = None,
          case_id: Annotated[str | None, Query(max_length=256)] = None,
          snapshot_sha256: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
          include_bars: bool = False):
    if case_type is not None and case_type not in CASE_TYPES:
        raise HTTPException(422, "无效案例类型")
    limit = limit if limit is not None else THUMBNAIL_PAGE_SIZE if include_bars else 30
    if include_bars and (case_id is not None or not snapshot_sha256 or limit > THUMBNAIL_PAGE_SIZE):
        raise HTTPException(422, "批量 K 线需要汇总快照、每页最多 24 条，且不能指定单案例")
    result = _evaluate(request, symbols, start, end, direction, signal_id)
    missing = result.pop("_missing_context_partitions")
    if snapshot_sha256 and snapshot_sha256 != result["snapshot_sha256"]:
        raise HTTPException(409, "行情或信号定义已变化，请重新获取汇总")
    results = result.pop("results")
    if case_id is not None:
        for item in results:
            for case in item["cases"]:
                if case["case_id"] != case_id:
                    continue
                return {**result, "case": case, "bars": _case_chart(case, item["chart"])["bars"]}
        raise HTTPException(404, "当前范围内找不到该案例")
    records = [c for item in results for c in item["cases"] if not case_type or c["case_type"] == case_type]
    if include_bars:
        records.sort(key=lambda c: (
            (c["pivot"] or {}).get("date") or c["alert"]["start_date"], c["symbol"], c["case_id"],
        ))
    page = records[offset:offset + limit]
    if include_bars:
        charts = {item["data"]["symbol"]: item["chart"] for item in results}
        page = [{**row, **_case_chart(row, charts[row["symbol"]], missing)} for row in page]
    return {**result, "total": len(records), "offset": offset, "cases": page}
