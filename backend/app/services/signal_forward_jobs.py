"""Version-bound, bounded forward-research jobs. A succeeded job commits its artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl
import pyarrow.parquet as pq

from app.enriched_generation import enriched_publication_incomplete
from app.polars_guard import guarded_collect
from app.services import xdxr_ref
from app.services.coverage_limits import ST_WINDOW_DAYS
from app.services.fs_utils import atomic_write_parquet, atomic_write_text
from app.services.pipeline_jobs import JobCancelledError, JobStore, is_cancelled, run_with_capacity
from app.services.signal_forward_performance import (
    BASE,
    HORIZONS,
    RULE,
    compile_signals,
    compute_forward_performance,
    summarize_monthly,
)
from app.strategy import custom_signals
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)
TIMEOUT_S = 1740
ID = re.compile(r"[a-f0-9]{10}")
TABLES = ("events", "summary", "monthly")


@dataclass(frozen=True)
class ForwardConfig:
    symbols: tuple[str, ...]
    signal_ids: tuple[str, ...]
    date_from: date
    date_to: date
    split_date: date

    def __post_init__(self):
        if (
            not 1 <= len(self.symbols) <= 128
            or len(set(self.symbols)) != len(self.symbols)
            or any(not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", s) for s in self.symbols)
            or not 1 <= len(self.signal_ids) <= 12
            or len(set(self.signal_ids)) != len(self.signal_ids)
            or any(not custom_signals.ID_RE.fullmatch(s) for s in self.signal_ids)
            or not self.date_from < self.split_date <= self.date_to
        ):
            raise ValueError("无效 V1 小样本配置（最多 128 只 A 股、12 条日线信号）")
        object.__setattr__(self, "symbols", tuple(sorted(self.symbols)))
        object.__setattr__(self, "signal_ids", tuple(sorted(self.signal_ids)))

    def payload(self):
        return json.loads(json.dumps(asdict(self), default=date.isoformat))

    @classmethod
    def from_payload(cls, data):
        return cls(
            tuple(data["symbols"]),
            tuple(data["signal_ids"]),
            *(date.fromisoformat(data[key]) for key in ("date_from", "date_to", "split_date")),
        )


def _identity(path: Path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _calendar(root: Path, config: ForwardConfig) -> list[date]:
    raw = json.loads((root / "trading_calendar.json").read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("交易日历格式无效")
    days = [date.fromisoformat(day) for day in raw]
    if days != sorted(set(days)) or config.date_from not in days or config.date_to not in days:
        raise ValueError("交易日历未完整覆盖研究起止日期")
    first, last = days.index(config.date_from), days.index(config.date_to)
    if last - first + 1 > 320:
        raise ValueError("V1 小样本最多覆盖 320 个研究交易日")
    context_end = bisect_right(days, config.date_to + timedelta(days=ST_WINDOW_DAYS))
    return days[max(0, first - 512) : context_end]


def _snapshot(root: Path, config: ForwardConfig):
    if enriched_publication_incomplete(root):
        raise ValueError("增强日线正在发布或状态不可用，请稍后重试")
    calendar = _calendar(root, config)
    catalog = {}
    for sid in config.signal_ids:
        definition = custom_signals.load_one(root, sid)
        if not definition or definition.get("id") != sid:
            raise ValueError(f"无有效本地信号定义：{sid}")
        catalog[sid] = definition
        for ref in custom_signals.expr_refs(definition.get("expr") or {}):
            atom = custom_signals.load_one(root, ref)
            if atom and atom.get("id") == ref:
                catalog[ref] = atom
    _, dependencies = compile_signals(catalog, config.signal_ids)
    # Do not silently substitute a reviewed formula for a locally edited one.
    for dep in dependencies.values():
        for fid, reviewed in dep.get("reviewed_factors", {}).items():
            path = root / "user_data" / "custom_factors" / f"{fid}.json"
            if not path.is_file():
                raise ValueError(f"缺少已审核因子的本地定义：{fid}")
            local = json.loads(path.read_text(encoding="utf-8"))
            keys = ("id", "formula", "kind", "lang", "version", "data_contract", "warmup_bars")
            if not isinstance(local, dict) or any(
                local.get(key) != reviewed.get(key) for key in keys
            ):
                raise ValueError(f"本地因子定义与已审核版本不一致：{fid}")
    files = [
        "instruments/instruments.parquet",
        xdxr_ref.REL_PATH,
        *(
            f"{table}/date={day}/part.parquet"
            for day in calendar
            for table in ("kline_daily_enriched", "kline_daily")
        ),
    ]
    snapshot = {
        "rule": RULE,
        "config": config.payload(),
        "calendar": [str(day) for day in calendar],
        "dependencies": dependencies,
        "files": {p: _identity(root / p) for p in files},
        "horizons": list(HORIZONS),
        "min_benchmark": 5,
        "universe": "explicit_symbols",
        "execution_policy": "dated_limit_then_retrospective_rule_next_open",
        "outcome_end": "within_own_split_t1_sell",
    }
    return snapshot, catalog, calendar


def _read_inputs(root: Path, config: ForwardConfig, snapshot, calendar, checkpoint, progress):
    files = snapshot["files"]
    chunks = []
    for start in range(0, len(calendar), 16):
        checkpoint()
        days = calendar[start : start + 16]

        def paths(table, days=days):
            rels = [f"{table}/date={day}/part.parquet" for day in days]
            return [root / p for p in rels if files[p] is not None]

        bars = KlineRepository.read_stock_research_bars(
            paths("kline_daily_enriched"),
            config.symbols,
            tuple(sorted(BASE)),
        )
        raw = KlineRepository.read_stock_research_bars(
            paths("kline_daily"), config.symbols, ("open",), raw=True
        )
        if not set(bars["date"].unique()) <= set(days) or not set(raw["date"].unique()) <= set(
            days
        ):
            raise ValueError("日线日期与分区批次不符")
        chunks.append(
            bars.join(
                raw.rename({"open": "raw_open"}), on=["symbol", "date"], how="left", validate="1:1"
            )
        )
        progress(5 + 45 * (start + len(days)) // len(calendar), "读取小样本日线")
    frame = pl.concat(chunks)
    if files["instruments/instruments.parquet"] is not None:
        quotes = pl.scan_parquet(root / "instruments/instruments.parquet")
        schema = set(quotes.collect_schema().names())
        if {"symbol", "as_of"} <= schema:
            keys = ["symbol", "date"]
            official = guarded_collect(
                quotes.select(
                    "symbol",
                    pl.col("as_of").cast(pl.Date).alias("date"),
                    (
                        pl.col("limit_up").cast(pl.Float64, strict=False)
                        if "limit_up" in schema
                        else pl.lit(None, pl.Float64)
                    ).alias("official_limit_up"),
                    (
                        pl.col("name").cast(pl.String, strict=False)
                        if "name" in schema
                        else pl.lit(None, pl.String)
                    ).alias("dated_instrument_name"),
                ).filter(
                    pl.col("symbol").is_in(config.symbols)
                    & pl.col("date").is_between(calendar[0], calendar[-1])
                ),
                priority="background",
            )
            official = official.filter(~pl.struct(*keys).is_duplicated())
            frame = frame.join(official, on=keys, how="left", validate="1:1")
    return frame


class ForwardPerformanceJobs:
    """One owner per data directory, like other in-process research job services."""

    def __init__(self, data_dir: Path, *, read_only: bool = False) -> None:
        self.root = data_dir
        self.folder = data_dir / "signal_forward_performance" / RULE
        self._read_only = read_only
        self.store = JobStore(store_dir=self.folder / "jobs", recover=not read_only)
        self._gate = threading.Lock()
        self._active: tuple[str, ForwardConfig] | None = None

    def start(self, config: ForwardConfig) -> dict:
        if self._read_only:
            raise RuntimeError("只读研究结果不能启动任务")
        with self._gate:
            if self._active:
                job, active_config = self._active
                if config != active_config:
                    raise ValueError("已有不同配置的触发后表现任务在途，请等待结束")
                return {"job_id": job, "created": False}
            job, created = self.store.create(timeout_s=TIMEOUT_S)
            if not created:
                raise RuntimeError("研究任务已在途")
            self._active = job, config
            deadline = time.monotonic() + TIMEOUT_S
            try:
                threading.Thread(
                    target=self._run, args=(job, config, deadline), daemon=True
                ).start()
            except Exception:
                self._active = None
                self.store.fail(job, "无法启动研究任务")
                raise
            return {"job_id": job, "created": True}

    def status(self, job: str) -> dict | None:
        if not ID.fullmatch(job):
            raise ValueError("无效研究任务 ID")
        record = self.store.get(job)
        return dict(record) if record else None

    def result(self, job: str, *, check_current: bool = True) -> dict | None:
        record = self.status(job)
        if not record or record["status"] != "succeeded":
            return None
        folder = self.folder / "runs" / job
        meta = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if record["result"] != {"run_id": job, "manifest_hash": _digest(meta)}:
            raise ValueError("研究结果与任务发布记录不一致")
        if check_current:
            try:
                snapshot, _, _ = _snapshot(
                    self.root, ForwardConfig.from_payload(meta["snapshot"]["config"])
                )
                stale = _digest(snapshot) != meta["input_hash"]
            except (ValueError, OSError, TypeError):
                stale = True
            meta["stale"] = stale
        return meta

    def read_table(self, job: str, table: str) -> pl.DataFrame | None:
        if table not in TABLES:
            raise ValueError("无效研究结果表")
        if self.result(job, check_current=False) is None:
            return None
        return pl.read_parquet(self.folder / "runs" / job / f"{table}.parquet")

    def _run(self, job, config, deadline):
        def checkpoint():
            if is_cancelled(job):
                raise JobCancelledError(job)
            if time.monotonic() >= deadline:
                raise TimeoutError("触发后表现任务达到总时限")

        def progress(percent, message):
            checkpoint()
            self.store.progress(job, "research", percent, message)

        def calculate():
            progress(2, "冻结研究输入")
            snapshot, catalog, calendar = _snapshot(self.root, config)
            input_hash = _digest(snapshot)
            frame = _read_inputs(self.root, config, snapshot, calendar, checkpoint, progress)
            progress(55, "计算触发后表现")
            events, summary = compute_forward_performance(
                frame,
                catalog,
                config.signal_ids,
                config.date_from,
                config.date_to,
                config.split_date,
                trading_dates=calendar,
                adj_events=xdxr_ref.load(self.root).filter(
                    pl.col("symbol").is_in(config.symbols)
                    & pl.col("trade_date").is_between(calendar[0], calendar[-1])
                ),
                checkpoint=checkpoint,
            )
            data = dict(events=events, summary=summary, monthly=summarize_monthly(events))
            progress(85, "校验并保存研究结果")
            meta = {
                "run_id": job,
                "snapshot": snapshot,
                "input_hash": input_hash,
                "rows": {key: table.height for key, table in data.items()},
            }
            runs = self.folder / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".stage-", dir=runs) as staging:
                stage = Path(staging)
                for key, table in data.items():
                    checkpoint()
                    path = stage / f"{key}.parquet"
                    atomic_write_parquet(table, path)
                    if pq.read_metadata(path).num_rows != table.height:
                        raise ValueError("研究结果行数校验失败")
                atomic_write_text(
                    stage / "manifest.json", json.dumps(meta, ensure_ascii=False, allow_nan=False)
                )
                if _digest(_snapshot(self.root, config)[0]) != input_hash:
                    raise ValueError("研究输入在计算期间已变化，未发布")
                checkpoint()
                stage.rename(runs / job)
                checkpoint()
                self.store.succeed(
                    job, {"run_id": job, "manifest_hash": _digest(meta)}, require_persisted=True
                )

        try:
            run_with_capacity(job, calculate, store=self.store, deadline=deadline)
        except JobCancelledError:
            self.store.fail(job, "触发后表现任务已取消")
        except (ValueError, TimeoutError) as exc:
            self.store.fail(job, str(exc))
        except Exception:
            logger.exception("触发后表现任务失败: %s", job)
            self.store.fail(job, "研究读取、计算或发布失败")
        finally:
            with self._gate:
                self._active = None
