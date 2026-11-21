"""涨停价缓存后台任务 —— 手动触发、按交易日增量、可续传。

设计
--------------------------------------------------------------------------
本任务按 ``(symbol, trade_date)`` 落盘规则推断涨跌停价(``*_inferred``)。规则已完善:
除权除息日改用 adj_factor 权息四元组算高精度修正昨收(见 xdxr_ref), 对齐官方口径;
同时保留更正确的历史时点化涨跌幅比例。涨停价不再依赖任何外部接口, 全程本地计算。

* 增量/续传: 按 symbol.hash 分桶读全历史, 只落 [start_date, end_date] 区间;
  重复点击不会重复计算, 已缓存键由 store.merge 的 coalesce 保证不回退。
* 范围: ``start_date ~ end_date``。默认起始日由前端传入(日K最早日),
  默认结束日为最新交易日。stop 后改日期即可续算更早/更新区间。
* 成本: 纯本地计算, 无接口调用; 任务用 checkpoint 支持超时/取消, 可反复续传。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import polars as pl

from app.services import limit_price_store as lps
from app.services.pipeline_jobs import (
    JobCancelledError,
    JobStore,
    is_cancelled,
    run_with_capacity,
)

logger = logging.getLogger(__name__)

# 单任务最长时间: 8 小时(到点自检停止, 可续传)。分段拉全历史时反复续传即可。
_TIMEOUT_S = 8 * 3600

# 推断价按 symbol.hash%_SHARDS 分桶, 与覆盖榜一致(每桶含该批 symbol 的完整历史)。
_SHARDS = 16
# price_facts 依赖的完整 K 线列(少了算不出 infer/eligible)。
_INFER_COLS = ("symbol", "date", "close", "raw_close", "raw_high", "raw_low")
# 推断阶段每处理这么多桶 flush 一次进度日志。
_INFER_PROGRESS_EVERY = 2


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


class LimitPriceCache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.store_dir = self.root / "limit_prices" / "jobs"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.jobs = JobStore(store_dir=self.store_dir)

    # ---- 可用交易日 ----------------------------------------------------
    def available_days(self) -> list[date]:
        """从 enriched 分区列出可缓存的交易日(有真实日K的日期)。"""
        days = []
        for p in (self.root / "kline_daily_enriched").glob("date=*/part.parquet"):
            d = _as_date(p.parent.name.replace("date=", ""))
            if d is not None:
                days.append(d)
        return sorted(days)

    def default_start(self) -> date | None:
        days = self.available_days()
        return days[0] if days else None

    # ---- 概况 ----------------------------------------------------------
    def status(self) -> dict:
        table = lps.load(self.root)
        inf = table.filter(pl.col("limit_up_inferred").is_not_null())
        inf_dates = sorted(inf["trade_date"].to_list())
        latest = self.jobs.list_recent(1)
        return {
            "inferred_keys": inf.height,
            "earliest": str(inf_dates[0]) if inf_dates else None,
            "latest": str(inf_dates[-1]) if inf_dates else None,
            "job": self.jobs.get(latest[0]["id"]) if latest else None,
        }

    # ---- 任务控制 ------------------------------------------------------
    def start(self, start_date: date | None, end_date: date | None) -> dict:
        if start_date and end_date and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")
        job, new = self.jobs.create(timeout_s=_TIMEOUT_S)
        if new:
            threading.Thread(
                target=self._run, args=(job, start_date, end_date), daemon=True
            ).start()
        return {"job_id": job, "created": new}

    def _run(self, job: str, start_date: date | None, end_date: date | None) -> None:
        deadline = time.monotonic() + _TIMEOUT_S

        def checkpoint():
            if time.monotonic() >= deadline or is_cancelled(job):
                raise TimeoutError("任务超时或取消, 可从断点续传")

        try:
            result = run_with_capacity(
                job,
                lambda: self.batch(job, start_date, end_date, checkpoint),
                store=self.jobs,
                deadline=deadline,
            )
            self.jobs.succeed(job, result)
        except (Exception, JobCancelledError) as exc:
            logger.exception("limit-price cache job %s failed", job)
            self.jobs.fail(job, str(exc) or "任务未完成")

    # ---- 推断价阶段 ----------------------------------------------------
    def _compute_inferred(
        self,
        job: str,
        start_date: date | None,
        end_date: date | None,
        checkpoint: Callable[[], None],
    ) -> int:
        """全历史分桶算规则推断价, 只落 [start_date, end_date] 区间, 返回落盘键数。

        复用覆盖榜 ``price_facts`` 的纯推断口径(**不传 limit_prices**, 避免接口价
        串入 inferred 列)。推断依赖每只票完整历史(diff/cum_count/prev), 必须按
        symbol 分桶读全历史后再逐桶算, 不能按天算。落盘幂等: store_inferred 的
        coalesce 保证已有非 null 值不被 null 回退。
        """
        from app.strategy.strict_signal_data import attach_calendar
        from app.services.coverage_limits import price_facts
        from app.services.xdxr_ref import load as load_adj_events

        adj_events = load_adj_events(self.root)  # 除权修正, 与覆盖榜同口径
        all_days = self.available_days()
        if not all_days:
            return 0

        # instruments: 覆盖榜同口径(as_of 过滤到范围)
        inst_path = self.root / "instruments/instruments.parquet"
        try:
            inst = pl.read_parquet(inst_path) if inst_path.exists() else pl.DataFrame()
        except Exception as e:  # pragma: no cover
            logger.warning("推断阶段读取 instruments 失败: %s", e)
            inst = pl.DataFrame()
        if "as_of" in inst.columns:
            hi = end_date or all_days[-1]
            inst = inst.filter(pl.col("as_of").cast(pl.Date).is_between(all_days[0], hi))

        # 一遍扫描全历史分区, 按 symbol.hash%_SHARDS 分桶写临时文件(与覆盖榜一致)。
        import shutil
        import tempfile

        import pyarrow.parquet as pq

        tmpdir = Path(tempfile.mkdtemp(prefix="lp-infer-"))
        writers: dict[int, object] = {}
        shard_paths: dict[int, Path] = {}
        try:
            for p in sorted((self.root / "kline_daily_enriched").glob("date=*/part.parquet")):
                checkpoint()
                avail = set(pl.read_parquet_schema(p))
                cols = [c for c in _INFER_COLS if c in avail]
                if not {"symbol", "date"} <= set(cols):
                    continue
                part = pl.read_parquet(p, columns=cols)
                if part.is_empty():
                    continue
                for (bucket,), sub in (
                    part.with_columns(
                        (pl.col("symbol").hash(seed=17) % _SHARDS).alias("_bucket")
                    )
                    .partition_by("_bucket", as_dict=True)
                    .items()
                ):
                    b = int(bucket)
                    arrow = sub.drop("_bucket").to_arrow()
                    path = tmpdir / f"{b}.parquet"
                    if b not in writers:
                        writers[b] = pq.ParquetWriter(path, arrow.schema)
                        shard_paths[b] = path
                    writers[b].write_table(arrow)
        finally:
            for w in writers.values():
                w.close()

        # 逐桶算纯推断价(不传小表), 只落请求区间, 分段落盘。
        added_total = 0
        try:
            buckets = sorted(shard_paths)
            for bi, b in enumerate(buckets):
                checkpoint()
                df = pl.read_parquet(shard_paths[b]).sort("symbol", "date")
                framed = attach_calendar(df, all_days)
                facts = price_facts(framed, inst, all_days, adj_events=adj_events)
                inf = facts.select(
                    pl.col("symbol"),
                    pl.col("date").alias("trade_date"),
                    pl.col("_inferred_up").alias("limit_up_inferred"),
                    pl.col("_inferred_down").alias("limit_down_inferred"),
                ).filter(
                    pl.col("limit_up_inferred").is_not_null()
                    | pl.col("limit_down_inferred").is_not_null()
                )
                if start_date:
                    inf = inf.filter(pl.col("trade_date") >= start_date)
                if end_date:
                    inf = inf.filter(pl.col("trade_date") <= end_date)
                added = 0
                if not inf.is_empty():
                    added, _ = lps.store_inferred(self.root, inf)
                    added_total += added
                if (bi + 1) % _INFER_PROGRESS_EVERY == 0 or bi + 1 == len(buckets):
                    self.jobs.progress(
                        job, "infer", 100 * (bi + 1) // max(1, len(buckets)),
                        f"推断价 {bi + 1}/{len(buckets)} 桶 · 本桶 +{added}",
                    )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return added_total

    # ---- 执行体 --------------------------------------------------------
    def batch(
        self,
        job: str,
        start_date: date | None,
        end_date: date | None,
        checkpoint: Callable[[], None],
    ) -> dict:
        # 纯本地规则推断价, 无接口调用。
        inferred_added = self._compute_inferred(job, start_date, end_date, checkpoint)
        return {"inferred_added": inferred_added}


_lock = threading.Lock()
_services: dict[Path, LimitPriceCache] = {}


def get_cache(root) -> LimitPriceCache:
    root = Path(root)
    with _lock:
        if root not in _services:
            _services[root] = LimitPriceCache(root)
        return _services[root]
