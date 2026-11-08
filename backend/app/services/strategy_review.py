"""策略复盘：指定策略在历史区间逐日选股，统计后续 1/3/5 个交易日收益。

口径（与页面约定一致，改口径要同步改文档与测试）：
- 每个交易日装配一次 StrategyDataContext，多个策略共享同一份当日快照和历史窗口，
  再逐个 engine.run 得到命中；不做组合级资金/仓位撮合，保留全部选股结果。
- 同一交易日 + 同一股票只出一条记录（并集去重），strategy_ids 保存命中的策略列表；
  不同交易日算不同信号。
- 买入价 = 信号日 enriched 复权收盘；卖出价 = 之后第 N 根可用日线的复权收盘
  （N=1/3/5，三档同时算好，查询层切换当前档位，切换不重算）。
- 净收益 = 卖出/买入 - 1 - cost_pct（默认双边合计 0.15%）；净收益 > 0 为赢，
  <= 0 为输（失败）；第 N 根日线不存在时标 pending，不计入胜率。
- 结果用信号日之后的行情计算，属于历史复盘，含未来数据，不构成实盘信号。
- 停牌/一字板（high==low）只标注 tradable，不剔除。
- 缩略图 = 信号日前 20 根 + 后 10 根，查询分页时按页批量读取，不落盘嵌套结构。
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import polars as pl

from app.services.pipeline_jobs import JobCancelledError, JobStore

logger = logging.getLogger(__name__)

HORIZONS: tuple[int, ...] = (1, 3, 5)
DEFAULT_COST_PCT = 0.0015
BARS_BEFORE = 20
BARS_AFTER = 10
DEFAULT_SCAN_DAYS = 120
MAX_SCAN_DAYS = 500
SYMBOL_CHUNK = 200

_SCHEMA: dict[str, pl.DataType] = {
    "id": pl.Utf8,
    "signal_date": pl.Date,
    "symbol": pl.Utf8,
    "name": pl.Utf8,
    "strategy_ids": pl.Utf8,
    "strategy_count": pl.UInt32,
    "entry_close": pl.Float64,
    "tradable": pl.Utf8,
}
for _h in HORIZONS:
    _SCHEMA[f"ret_{_h}"] = pl.Float64
    _SCHEMA[f"exit_close_{_h}"] = pl.Float64
    _SCHEMA[f"exit_date_{_h}"] = pl.Date
    _SCHEMA[f"status_{_h}"] = pl.Utf8

_STATE: dict = {"table": None, "computed": False, "meta": {}}
_CACHE_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()
_JOB_STORES: dict[str, JobStore] = {}
_JOB_LOCK = threading.Lock()


def empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=_SCHEMA)


def _is_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _finite(*values: float | None) -> bool:
    return all(v is not None and v > 0 for v in values)


def _serialize_bars(bars: list[dict]) -> list[dict]:
    out = []
    for bar in bars:
        if not _finite(bar["open"], bar["high"], bar["low"], bar["close"]):
            continue
        out.append({
            "date": _is_date(bar["date"]).isoformat(),
            "open": round(float(bar["open"]), 4),
            "high": round(float(bar["high"]), 4),
            "low": round(float(bar["low"]), 4),
            "close": round(float(bar["close"]), 4),
            "volume": float(bar["volume"] or 0.0),
        })
    return out


def _market_dates(data_dir: Path) -> list[date]:
    from app.tickflow.repository import enriched_dirname

    root = Path(data_dir) / enriched_dirname("stock")
    days: list[date] = []
    for entry in root.glob("date=*"):
        if not entry.is_dir() or len(entry.name) <= 5:
            continue
        try:
            days.append(date.fromisoformat(entry.name[5:]))
        except ValueError:
            continue
    return sorted(days)


def _load_ohlcv(repo, symbols: list[str], lo: date, hi: date) -> pl.DataFrame:
    """分批按标的读 OHLCV，避免一次 SQL 的 IN 参数过长。"""
    frames: list[pl.DataFrame] = []
    for i in range(0, len(symbols), SYMBOL_CHUNK):
        chunk = symbols[i:i + SYMBOL_CHUNK]
        frame = repo.load_enriched_ohlcv(chunk, lo, hi)
        if frame is not None and not frame.is_empty():
            frames.append(frame)
    if not frames:
        return pl.DataFrame(schema={
            "symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64,
            "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
            "volume": pl.Float64,
        })
    return pl.concat(frames, how="vertical_relaxed")


def _group_bars(frame: pl.DataFrame) -> dict[str, list[dict]]:
    by_symbol: dict[str, list[dict]] = {}
    for row in frame.iter_rows(named=True):
        by_symbol.setdefault(str(row["symbol"]), []).append(row)
    for bars in by_symbol.values():
        bars.sort(key=lambda r: _is_date(r["date"]))
    return by_symbol


def _forward_returns(bars: list[dict], position: int | None, cost_pct: float) -> dict:
    """从信号日下标算三档前向收益。bars 按日期升序，position 是信号日下标。

    抽成纯函数便于断言：缺第 N 根日线 -> pending；净收益 > 0 为赢，<= 0 为输。
    """
    result: dict = {
        "entry_close": None,
        "tradable": "unknown" if position is None else "ok",
    }
    for h in HORIZONS:
        result[f"ret_{h}"] = None
        result[f"exit_close_{h}"] = None
        result[f"exit_date_{h}"] = None
        result[f"status_{h}"] = "pending"
    if position is None:
        return result
    bar = bars[position]
    entry = float(bar["close"] or 0.0)
    if entry <= 0:
        return result
    result["entry_close"] = entry
    result["tradable"] = "locked" if bar["high"] == bar["low"] else "ok"
    for h in HORIZONS:
        target = position + h
        if target >= len(bars):
            continue
        exit_bar = bars[target]
        exit_close = float(exit_bar["close"] or 0.0)
        if exit_close <= 0:
            continue
        # 胜负必须用落盘的同一个值判定：浮点毛收益与成本相减可能残留 1e-19，
        # 若用未 round 的值判会得到 ret=0.0 却是 win，与「净收益 <= 0 为输」矛盾。
        net = round(exit_close / entry - 1.0 - cost_pct, 8)
        result[f"ret_{h}"] = net
        result[f"exit_close_{h}"] = exit_close
        result[f"exit_date_{h}"] = _is_date(exit_bar["date"])
        result[f"status_{h}"] = "win" if net > 0 else "lose"
    return result


def _stats(table: pl.DataFrame) -> dict[str, dict]:
    """三档并列统计。pending 不计入分母。"""
    out: dict[str, dict] = {}
    for h in HORIZONS:
        ret_col, status_col = f"ret_{h}", f"status_{h}"
        rets = [float(v) for v in table.get_column(ret_col).to_list() if v is not None]
        wins = [v for v in rets if v > 0]
        loses = [v for v in rets if v <= 0]
        pending = table.filter(pl.col(status_col) == "pending").height
        n = len(rets)
        mean_win = statistics.fmean(wins) if wins else 0.0
        mean_lose = abs(statistics.fmean(loses)) if loses else 0.0
        out[str(h)] = {
            "horizon": h,
            "total": n,
            "pending": int(pending),
            "win": len(wins),
            "lose": len(loses),
            "win_rate": round(len(wins) / n, 6) if n else None,
            "avg_ret": round(statistics.fmean(rets), 6) if n else None,
            "median_ret": round(statistics.median(rets), 6) if n else None,
            "max_ret": round(max(rets), 6) if n else None,
            "min_ret": round(min(rets), 6) if n else None,
            "profit_loss_ratio": round(mean_win / mean_lose, 4) if mean_lose else None,
        }
    return out


def _job_store(data_dir: Path) -> JobStore:
    """独立目录 + 独立单例：不与 pipeline 的全局单飞 JobStore 抢任务槽。"""
    key = str(data_dir)
    with _JOB_LOCK:
        store = _JOB_STORES.get(key)
        if store is None:
            store = JobStore(max_jobs=20, store_dir=Path(data_dir) / "strategy_review_jobs")
            _JOB_STORES[key] = store
        return store


class StrategyReviewService:
    def __init__(self, repo, engine=None, data_dir: Path | None = None) -> None:
        self.repo = repo
        self.engine = engine
        if data_dir is None:
            data_dir = Path(getattr(getattr(repo, "store", None), "data_dir", "."))
        self.data_dir = Path(data_dir)

    # ── 落盘 ────────────────────────────────────────────────────────

    def _cache_dir(self) -> Path:
        return self.data_dir / "strategy_review"

    def _cache_path(self) -> Path:
        return self._cache_dir() / "runs.parquet"

    def _meta_path(self) -> Path:
        return self._cache_dir() / "meta.json"

    def _read_saved(self) -> tuple[pl.DataFrame | None, dict]:
        path, meta_path = self._cache_path(), self._meta_path()
        if not path.exists():
            return None, {}
        try:
            table = pl.read_parquet(path)
            missing = [c for c in _SCHEMA if c not in table.columns]
            if missing:
                raise ValueError(f"missing columns: {missing}")
            table = table.select(list(_SCHEMA)).cast(_SCHEMA, strict=False)
        except Exception:
            logger.warning("strategy_review: 落盘结果无法读取，按未计算处理", exc_info=True)
            return None, {}
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
            except Exception:
                meta = {}
        return table, meta

    def _write_saved(self, table: pl.DataFrame, meta: dict) -> None:
        folder = self._cache_dir()
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "runs.parquet.tmp"
        table.write_parquet(tmp)
        os.replace(tmp, folder / "runs.parquet")
        meta_tmp = folder / "meta.json.tmp"
        meta_tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(meta_tmp, folder / "meta.json")

    def _runs_state(self) -> tuple[pl.DataFrame, bool, dict]:
        with _CACHE_LOCK:
            table, computed, meta = _STATE["table"], _STATE["computed"], _STATE["meta"]
            if isinstance(table, pl.DataFrame):
                return table, bool(computed), dict(meta)
        loaded, meta = self._read_saved()
        with _CACHE_LOCK:
            in_memory = _STATE["table"]
            if isinstance(in_memory, pl.DataFrame):
                return in_memory, bool(_STATE["computed"]), dict(_STATE["meta"])
            if loaded is None:
                return empty_frame(), False, {}
            _STATE["table"] = loaded
            _STATE["computed"] = True
            _STATE["meta"] = meta
            return loaded, True, dict(meta)

    # ── 扫描 ────────────────────────────────────────────────────────

    def scan(
        self,
        *,
        strategy_ids: list[str],
        start: date,
        end: date,
        cost_pct: float = DEFAULT_COST_PCT,
        progress: Callable[[int, str], None] | None = None,
    ) -> tuple[pl.DataFrame, dict]:
        from app.services.screener import ScreenerService
        from app.strategy import config as strategy_config

        if not strategy_ids:
            raise ValueError("至少选择一个策略")
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
        if not 0 <= cost_pct <= 0.01:
            raise ValueError("成本比例超出范围")
        if self.engine is None:
            raise ValueError("策略引擎未初始化")

        calendar = _market_dates(self.data_dir)
        if not calendar:
            raise ValueError("本地没有 enriched 日线数据")
        days = [d for d in calendar if start <= d <= end]
        if not days:
            raise ValueError("所选区间内没有交易日")
        if len(days) > MAX_SCAN_DAYS:
            raise ValueError(f"区间交易日数 {len(days)} 超过上限 {MAX_SCAN_DAYS}")
        tail = [d for d in calendar if d > end][:max(HORIZONS)]
        last_needed = tail[-1] if tail else end

        overrides_map = {
            sid: strategy_config.load_override(self.data_dir, sid) or {}
            for sid in strategy_ids
        }
        params_map = {
            sid: dict((overrides_map[sid].get("params") or {}))
            for sid in strategy_ids
        }
        svc = ScreenerService(self.repo, asset_type="stock")

        hits: dict[tuple[date, str], dict] = {}
        skipped_days: list[str] = []
        for index, day in enumerate(days, 1):
            try:
                context = svc.build_strategy_context(
                    self.engine, day, strategy_ids,
                    timeframe="1d",
                    params_map=params_map,
                    overrides_map=overrides_map,
                )
            except Exception as exc:
                skipped_days.append(f"{day.isoformat()}: {exc}")
                continue
            for sid in strategy_ids:
                try:
                    result = self.engine.run(
                        sid, context, params=params_map[sid],
                        overrides=overrides_map[sid] or None,
                    )
                except Exception as exc:
                    logger.warning("strategy_review: %s %s 运行失败: %s", day, sid, exc)
                    continue
                for row in result.rows:
                    symbol = str(row.get("symbol") or "")
                    if not symbol:
                        continue
                    key = (day, symbol)
                    record = hits.get(key)
                    if record is None:
                        record = {"name": str(row.get("name") or ""), "strategy_ids": []}
                        hits[key] = record
                    if sid not in record["strategy_ids"]:
                        record["strategy_ids"].append(sid)
            if progress:
                progress(
                    int(index / len(days) * 80),
                    f"已扫描 {index}/{len(days)} 个交易日，命中 {len(hits)} 条",
                )

        symbols = sorted({symbol for _, symbol in hits})
        by_symbol: dict[str, list[dict]] = {}
        if symbols:
            lo = min(days) - timedelta(days=70)
            frame = _load_ohlcv(self.repo, symbols, lo, last_needed)
            by_symbol = _group_bars(frame)
        if progress:
            progress(90, f"读取 {len(symbols)} 只股票的日线，计算三档收益")

        records: list[dict] = []
        for (day, symbol), info in sorted(hits.items()):
            bars = by_symbol.get(symbol, [])
            position = next(
                (i for i, bar in enumerate(bars) if _is_date(bar["date"]) == day), None
            )
            computed = _forward_returns(bars, position, cost_pct)
            record: dict = {
                "id": f"{day.isoformat()}|{symbol}",
                "signal_date": day,
                "symbol": symbol,
                "name": info["name"],
                "strategy_ids": ",".join(info["strategy_ids"]),
                "strategy_count": len(info["strategy_ids"]),
                "entry_close": computed["entry_close"],
                "tradable": computed["tradable"],
            }
            for h in HORIZONS:
                record[f"ret_{h}"] = computed[f"ret_{h}"]
                record[f"exit_close_{h}"] = computed[f"exit_close_{h}"]
                record[f"exit_date_{h}"] = computed[f"exit_date_{h}"]
                record[f"status_{h}"] = computed[f"status_{h}"]
            records.append(record)

        table = pl.DataFrame(records, schema=_SCHEMA, strict=False) if records else empty_frame()
        meta = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "strategy_ids": list(strategy_ids),
            "horizons": list(HORIZONS),
            "cost_pct": cost_pct,
            "scan_days": len(days),
            "skipped_days": skipped_days[:20],
            "signals": int(table.height),
            "as_of": calendar[-1].isoformat(),
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "历史复盘结果，使用信号日之后的行情计算，不构成实盘信号",
        }
        return table, meta

    # ── 异步任务 ────────────────────────────────────────────────────

    def submit_rebuild(
        self,
        *,
        strategy_ids: list[str],
        start: date,
        end: date,
        cost_pct: float = DEFAULT_COST_PCT,
    ) -> dict:
        store = _job_store(self.data_dir)
        job_id, is_new = store.create(timeout_s=3600)
        if not is_new:
            return {"job_id": job_id, "reused": True, "status": store.get(job_id).get("status")}

        def progress(pct: int, msg: str) -> None:
            try:
                store.progress(job_id, "scan", pct, msg)
            except JobCancelledError as exc:
                raise RuntimeError("任务已取消") from exc

        def worker() -> None:
            store.start(job_id)
            try:
                with _BUILD_LOCK:
                    table, meta = self.scan(
                        strategy_ids=strategy_ids, start=start, end=end,
                        cost_pct=cost_pct, progress=progress,
                    )
                    self._write_saved(table, meta)
                    with _CACHE_LOCK:
                        _STATE["table"] = table
                        _STATE["computed"] = True
                        _STATE["meta"] = meta
                progress(100, "计算完成")
                store.succeed(job_id, {"total": int(table.height), "as_of": meta["as_of"]})
            except Exception as exc:
                logger.exception("strategy_review: 区间扫描失败")
                store.fail(job_id, str(exc))

        threading.Thread(
            target=worker, daemon=True, name=f"strategy-review-{job_id}",
        ).start()
        return {"job_id": job_id, "reused": False, "status": "pending"}

    def job_status(self, job_id: str) -> dict | None:
        return _job_store(self.data_dir).get(job_id)

    # ── 查询 ────────────────────────────────────────────────────────

    def _base_filter(
        self,
        table: pl.DataFrame,
        *,
        start: date | None,
        end: date | None,
        q: str,
        strategy_id: str,
    ) -> pl.DataFrame:
        filtered = table
        if start is not None:
            filtered = filtered.filter(pl.col("signal_date") >= start)
        if end is not None:
            filtered = filtered.filter(pl.col("signal_date") <= end)
        keyword = q.strip().upper()
        if keyword:
            filtered = filtered.filter(
                pl.col("symbol").str.to_uppercase().str.contains(keyword, literal=True)
                | pl.col("name").str.to_uppercase().str.contains(keyword, literal=True)
            )
        if strategy_id:
            filtered = filtered.filter(
                pl.col("strategy_ids").str.contains(strategy_id, literal=True)
            )
        return filtered

    def list_runs(
        self,
        *,
        horizon: int = 1,
        result: str = "all",
        start: date | None = None,
        end: date | None = None,
        q: str = "",
        strategy_id: str = "",
        min_ret: float | None = None,
        max_ret: float | None = None,
        sort: str = "signal_date",
        order: str = "desc",
        offset: int = 0,
        limit: int = 24,
    ) -> dict:
        table, computed, meta = self._runs_state()
        if horizon not in HORIZONS:
            horizon = HORIZONS[0]
        ret_col, status_col = f"ret_{horizon}", f"status_{horizon}"
        base = self._base_filter(table, start=start, end=end, q=q, strategy_id=strategy_id)
        stats = _stats(base)
        filtered = base
        if result in ("win", "lose", "pending"):
            filtered = filtered.filter(pl.col(status_col) == result)
        if min_ret is not None:
            filtered = filtered.filter(pl.col(ret_col) >= min_ret)
        if max_ret is not None:
            filtered = filtered.filter(pl.col(ret_col) <= max_ret)
        sort_key = sort if sort in {"signal_date", "strategy_count", *[f"ret_{h}" for h in HORIZONS]} else "signal_date"
        descending = order != "asc"
        filtered = filtered.sort(
            [sort_key, "signal_date", "symbol"],
            descending=[descending, True, False],
            nulls_last=True,
        )
        total = filtered.height
        page = filtered.slice(offset, limit)
        bars_by_id = self._page_bars(page)
        rows = []
        for record in page.iter_rows(named=True):
            item = {
                "id": record["id"],
                "signal_date": _is_date(record["signal_date"]).isoformat(),
                "symbol": record["symbol"],
                "name": record["name"] or "",
                "strategy_ids": record["strategy_ids"].split(",") if record["strategy_ids"] else [],
                "strategy_count": int(record["strategy_count"]),
                "entry_close": round(float(record["entry_close"]), 4) if record["entry_close"] else None,
                "tradable": record["tradable"],
                "bars": bars_by_id.get(record["id"], []),
            }
            for h in HORIZONS:
                item[f"ret_{h}"] = record[f"ret_{h}"]
                item[f"status_{h}"] = record[f"status_{h}"]
                item[f"exit_date_{h}"] = (
                    _is_date(record[f"exit_date_{h}"]).isoformat()
                    if record[f"exit_date_{h}"] is not None else None
                )
            rows.append(item)
        return {
            "computed": computed,
            "meta": meta,
            "total": total,
            "offset": offset,
            "limit": limit,
            "horizon": horizon,
            "result": result,
            "stats": stats,
            "bars_before": BARS_BEFORE,
            "bars_after": BARS_AFTER,
            "rows": rows,
        }

    def summary(self, *, start=None, end=None, q="", strategy_id="") -> dict:
        table, computed, meta = self._runs_state()
        base = self._base_filter(table, start=start, end=end, q=q, strategy_id=strategy_id)
        return {"computed": computed, "meta": meta, "total": int(base.height), "stats": _stats(base)}

    def _page_bars(self, page: pl.DataFrame) -> dict[str, list[dict]]:
        if page.is_empty():
            return {}
        symbols = sorted(set(page.get_column("symbol").to_list()))
        days = [_is_date(d) for d in page.get_column("signal_date").to_list()]
        lo, hi = min(days) - timedelta(days=70), max(days) + timedelta(days=70)
        try:
            frame = _load_ohlcv(self.repo, symbols, lo, hi)
            by_symbol = _group_bars(frame)
        except Exception:
            logger.warning("strategy_review: 缩略图日线读取失败", exc_info=True)
            return {}
        result: dict[str, list[dict]] = {}
        for record in page.iter_rows(named=True):
            bars = by_symbol.get(record["symbol"], [])
            day = _is_date(record["signal_date"])
            position = next(
                (i for i, bar in enumerate(bars) if _is_date(bar["date"]) == day), None
            )
            if position is None:
                result[record["id"]] = []
                continue
            window = bars[max(0, position - BARS_BEFORE):position + BARS_AFTER + 1]
            result[record["id"]] = _serialize_bars(window)
        return result

    def zoom_bars(self, symbol: str, start: date, end: date) -> dict:
        """放大图：信号日前后各一年，按本地实有日线返回。"""
        lo, hi = start - timedelta(days=365), end + timedelta(days=365)
        frame = self.repo.load_enriched_ohlcv([symbol], lo, hi)
        bars = []
        if frame is not None and not frame.is_empty():
            rows = sorted(frame.iter_rows(named=True), key=lambda r: _is_date(r["date"]))
            bars = _serialize_bars(rows)
        return {
            "symbol": symbol,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "from_date": lo.isoformat(),
            "to_date": hi.isoformat(),
            "bars": bars,
        }
