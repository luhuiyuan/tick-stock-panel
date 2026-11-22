"""One fixed event cohort; stage complete per-symbol histories in small local shards."""

import hashlib
import json
import os
from bisect import bisect_left
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from app.factors.registry import factor_dependencies, factor_warmup_bars
from app.indicators.pipeline import INDICATOR_COLUMNS, compute_indicators
from app.services.coverage_limits import COUNTS, POLICY, PRICES, ROSTER, market_counts, price_facts
from app.services.fs_utils import atomic_write_parquet
from app.services.strict_daily_context import attach_market_facts
from app.strategy import custom_signals as signals
from app.strategy.strict_signal_data import (
    EVENT_FIELDS,
    MARKET_FIELDS,
    MARKET_INDEX,
    attach_calendar,
)

FIRST, START, END = map(date.fromisoformat, ["2025-10-20", "2025-10-27", "2026-11-05"])
# v7: 放宽 eligible 连续性(停牌复牌日昨收有效即可算涨停价), 修复停牌样本误判 unknown;
#     同时移除覆盖榜对 limit_prices 缓存表的写回, 只保留指纹读取。
# v9: 默认非 ST 按板块比例算涨停价, 仅当维表 name 快照明确含 ST 才按 5%;
#     删掉振幅反推 ST 未知的逻辑(除权/停牌日振幅失真, 且无法区分真 ST 与平淡行情)。
# v10: ST 改为价格反推 —— 前后一月窗口内多次精准触及 5% 涨跌停价且无破 5.5% 证据才判 ST
#      按 5% 算; 其余默认非 ST 按板块比例。不依赖 name 快照(仅作兜底 OR)。
# v11: 严格契约 validity 的窗口完整性改为按个股真实 K 线行数判定, 移除 market_index
#      数值连续性(== n-1)检查, 修复停牌个股缺 K 线导致长窗口因子 null -> 信号 unknown。
RULE = "surge-pre5-bars-v11"
WINDOW = 5


class UnavailableError(ValueError):
    """No trustworthy historical observations; not a zero-coverage result."""


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def fingerprint(st):
    return f"{st.st_ino}:{st.st_mtime_ns}:{st.st_size}"


def input_stamp(root):
    # Source identity includes the actual immutable partition files, not just the surge as-of.
    files = [
        root / "surge_research/runs-v1.parquet",
        root / "instruments/instruments.parquet",
        root / "regime_history/part.parquet",
        root / "limit_prices/all.parquet",
        root / "adj_factor/all.parquet",  # 除权修正口径
        *sorted((root / "user_data/custom_factors").glob("*.json")),
    ]
    partitions = {}
    for path in (root / "kline_daily_enriched").glob("date=*/part.parquet"):
        try:
            day = date.fromisoformat(path.parent.name.removeprefix("date="))
        except ValueError:
            continue
        if day < END and path.parent.name == f"date={day}":
            partitions[day] = str(path.relative_to(root))
            files.append(path)
    files.append(root / ROSTER)
    manifest = {
        str(p.relative_to(root)): fingerprint(p.stat()) if p.exists() else None for p in files
    }
    return Inputs(manifest, root, partitions)


RAW_COLUMNS = {
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "raw_close",
    "raw_high",
    "raw_low",
}


@lru_cache(maxsize=640)
def column_stamps(path, identity):
    if identity is None:
        return {}
    cols = (RAW_COLUMNS | {"symbol", "date"}) & set(pl.read_parquet_schema(path))
    df = pl.read_parquet(path, columns=sorted(cols)).sort("symbol", "date")
    return {k: hashlib.sha256(df[k].hash(seed=17).to_numpy().tobytes()).hexdigest() for k in cols}


@lru_cache(maxsize=8)
def dated_instrument_stamp(path, identity, first, end):
    if identity is None:
        return None
    df = pl.read_parquet(path)
    if "as_of" not in df.columns:
        return None
    df = df.filter(pl.col("as_of").cast(pl.Date).is_between(first, end))
    cols = sorted({"as_of", "symbol", "limit_up", "limit_down", "name"} & set(df.columns))
    return digest(df.select(cols).sort(cols).rows()) if df.height else None


class Inputs(str):
    def __new__(cls, manifest, root, partitions):
        obj = super().__new__(cls, digest(manifest))
        obj.manifest = manifest
        obj.partitions = dict(sorted((d, p) for d, p in partitions.items() if manifest[p]))
        # Earlier history may supply sparse symbols' five bars and indicator warmup.
        # Use file identities for that tail instead of scanning years of data on each cold listing.
        obj.base = digest(
            [
                manifest.get("surge_research/runs-v1.parquet"),
                [(d, manifest[p] if d < FIRST else None) for d, p in obj.partitions.items()],
            ]
        )
        columns = {}
        for d, rel in obj.partitions.items():
            if d >= FIRST:
                for k, v in column_stamps(root / rel, manifest[rel]).items():
                    columns.setdefault(k, []).append((d, v))
        obj.columns = {k: digest(v) for k, v in columns.items()}
        obj.inst = dated_instrument_stamp(
            root / "instruments/instruments.parquet",
            manifest.get("instruments/instruments.parquet"),
            min(obj.partitions, default=FIRST),
            END,
        )
        obj.factors = {
            p.stem: json.loads(p.read_text())
            for p in (root / "user_data/custom_factors").glob("*.json")
        }
        obj.signals = {
            p.stem: json.loads(p.read_text())
            for p in (root / "user_data/custom_signals").glob("*.json")
        }
        return obj

    def _atom_refs(self, sig):
        """复合信号 expr 引用的原子 id 集合（含其定义，供依赖/指纹/编译用）。"""
        if signals.signal_type(sig) != signals.TYPE_COMPOSITE:
            return set()
        refs = set()
        todo = [sig.get("expr")]
        while todo:
            expr = todo.pop()
            if not isinstance(expr, dict):
                continue
            if "ref" in expr:
                refs.add(expr["ref"])
            for arg in expr.get("args") or []:
                todo.append(arg)
        return refs

    def dependencies(self, sig):
        import re

        seen = set()
        todo = list(
            re.findall(
                r"[A-Za-z_][A-Za-z_0-9]*",
                json.dumps([sig.get("conditions", []), sig.get("data_contract", {}), sig.get("expr", {})]),
            )
        )
        # 复合: 展开引用原子的 conditions/data_contract 到依赖扫描
        for ref in self._atom_refs(sig):
            atom = self.signals.get(ref)
            if atom:
                todo.extend(re.findall(
                    r"[A-Za-z_][A-Za-z_0-9]*",
                    json.dumps([atom.get("conditions", []), atom.get("data_contract", {})]),
                ))
        while todo:
            k = todo.pop()
            if k in seen:
                continue
            seen.add(k)
            if k in self.factors:
                todo.extend(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", json.dumps(self.factors[k])))
        return seen

    def scoped(self, sig):
        deps = self.dependencies(sig)
        parts = [RULE, self.base, *(self.factors[k] for k in sorted(deps & self.factors.keys()))]
        # 复合信号的指纹纳入引用原子的定义: 原子改了, 复合覆盖必须失效重算。
        atom_defs = [(ref, self.signals.get(ref)) for ref in sorted(self._atom_refs(sig))]
        parts.append(atom_defs)
        from app.factors.registry import factor_dependencies

        leaves = deps | set(factor_dependencies(deps))
        cols = (RAW_COLUMNS & leaves) | {"symbol", "date"}
        if leaves & INDICATOR_COLUMNS:
            for field in leaves & INDICATOR_COLUMNS:
                if field == "vol_ratio_5d":
                    cols.add("volume")
                elif field in {
                    "high_60d",
                    "low_60d",
                    "boll_upper",
                    "boll_lower",
                    "macd_dif",
                    "macd_dea",
                    "ma5",
                    "ma10",
                    "ma20",
                    "ma60",
                }:
                    cols.add("close")
                else:
                    cols.update(RAW_COLUMNS)
        if (sig.get("data_contract") or {}).get("ohlc_window"):
            cols.update({"open", "high", "low", "close"})
        if deps & (EVENT_FIELDS | PRICES | COUNTS):
            cols.update({"raw_high", "raw_low", "raw_close", "close"})
            parts += ["coverage-limits-v1", self.inst]
        parts.append([(k, self.columns.get(k)) for k in sorted(cols)])
        if deps & COUNTS:
            parts.append(self.manifest[ROSTER])
        if deps & (MARKET_FIELDS - COUNTS):
            parts.append(self.manifest["regime_history/part.parquet"])
        return digest(parts)


def scoped(stamp, sig):
    return stamp.scoped(sig) if isinstance(stamp, Inputs) else stamp


class Panel:
    def __init__(self, root: Path, stage: Path, checkpoint):
        self.root = root
        self.stamp = input_stamp(root)
        self.days = list(self.stamp.partitions)
        if not self.days:
            raise ValueError("没有可用的增强日线分区，请先同步或生成历史日线")
        # Continuity checks for inferred limits/streaks must use the real trading
        # sessions only. Padding with every weekday injects public holidays into
        # MARKET_INDEX, so the first session after a long holiday gets a jump
        # (diff > 1), price_facts.eligible fails and limit prices turn null —
        # which wrongly marks every such case as unknown. self.days already holds
        # the authoritative sessions (partitions with real bars); a genuinely
        # missing session is a data-integrity issue that should stay unknown.
        self.limit_days = list(self.days)
        source = root / "surge_research/runs-v1.parquet"
        with source.open("rb") as fh:
            pf = pq.ParquetFile(fh)
            self.as_of = pf.metadata.metadata[b"as_of"].decode()
            self.source = fingerprint(os.fstat(fh.fileno()))
            runs = pl.from_arrow(
                pf.read(
                    columns=["id", "symbol", "name", "start_date", "end_date", "pending"],
                    use_threads=False,
                )
            )
        runs = runs.filter(~pl.col("pending") & pl.col("start_date").is_between(START, END)).sort(
            "start_date", "symbol", descending=[True, False]
        )
        if not runs.height or runs["id"].n_unique() != runs.height:
            raise ValueError("猛拉快照为空或 ID 重复")
        self.shards = []
        writers = {}
        cols = [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "amount",
            "volume",
            "raw_high",
            "raw_low",
            "raw_close",
        ]
        try:
            for day, rel in self.stamp.partitions.items():
                checkpoint()
                path = root / rel
                available = set(pl.read_parquet_schema(path))
                if not {"symbol", "date"} <= available:
                    raise ValueError(f"{day} 分区缺少 symbol/date")
                df = pl.read_parquet(path, columns=[c for c in cols if c in available]).select(
                    "symbol",
                    "date",
                    *(
                        pl.col(c).cast(pl.Float64)
                        if c in available
                        else pl.lit(None, pl.Float64).alias(c)
                        for c in cols
                        if c not in {"symbol", "date"}
                    ),
                )
                if df.is_empty():
                    continue
                if (
                    df["date"].unique().to_list() != [day]
                    or df["symbol"].null_count()
                    or df["symbol"].n_unique() != df.height
                ):
                    raise ValueError(f"{day} 分区键异常")
                for (bucket,), part in (
                    df.with_columns((pl.col("symbol").hash(seed=17) % 16).alias("_bucket"))
                    .partition_by("_bucket", as_dict=True)
                    .items()
                ):
                    table = part.drop("_bucket").to_arrow()
                    path = stage / f"{bucket}.parquet"
                    if bucket not in writers:
                        writers[bucket] = pq.ParquetWriter(path, table.schema)
                        self.shards.append(path)
                    writers[bucket].write_table(table)
        finally:
            for writer in writers.values():
                writer.close()

        if not self.shards:
            raise ValueError("增强日线分区没有有效股票记录")
        events = {}
        for i, (symbol, start) in enumerate(runs.select("symbol", "start_date").iter_rows()):
            events.setdefault(symbol, []).append((i, start))
        windows = [[] for _ in range(runs.height)]
        symbols = runs["symbol"].unique().implode()
        for shard in self.shards:
            checkpoint()
            history = (
                pl.scan_parquet(shard)
                .filter(pl.col("symbol").is_in(symbols))
                .group_by("symbol")
                .agg(pl.col("date").sort())
                .collect()
            )
            for symbol, prior in history.iter_rows():
                for i, start in events[symbol]:
                    stop = bisect_left(prior, start)
                    windows[i] = prior[max(0, stop - WINDOW) : stop]
        self.runs = runs.with_columns(
            pl.Series("dates", windows, dtype=pl.List(pl.Date))
        ).with_row_index("_row")
        self.targets = (
            self.runs.select("_row", "symbol", "dates")
            .explode("dates", empty_as_null=False)
            .rename({"dates": "date"})
            .filter(pl.col("date").is_not_null())
            .with_row_index("_slot")
        )

        def optional(rel):
            return pl.read_parquet(root / rel) if (root / rel).exists() else pl.DataFrame()

        self.inst = optional("instruments/instruments.parquet")
        self.market = optional("regime_history/part.parquet")
        if "as_of" in self.inst.columns:
            self.inst = self.inst.filter(
                pl.col("as_of").cast(pl.Date).is_between(self.days[0], END)
            )
        self.limit_cache = {}
        self.roster = optional(ROSTER)
        self.counts = None
        self.verify()

    def limits(self, shard):
        if shard not in self.limit_cache:
            from app.services import xdxr_ref

            adj_events = xdxr_ref.load(self.root)
            df = attach_calendar(pl.read_parquet(shard).sort("symbol", "date"), self.limit_days)
            path = shard.with_suffix(".limits.parquet")

            # 纯规则推断(除权日用 xdxr 修正昨收): 一次算出最终涨跌停价证据。
            # 缓存表 limit_prices/all.parquet 由专门的缓存任务维护, 覆盖榜只读不写回:
            # 写回会改变该表指纹 → input_stamp 漂移 → 本批 verify 失败 + 其它信号连锁 stale。
            probe = price_facts(df, self.inst, self.limit_days, adj_events=adj_events)

            probe.write_parquet(path)
            self.limit_cache[shard] = path
        return pl.read_parquet(self.limit_cache[shard])

    def verify(self):
        if input_stamp(self.root) != self.stamp:
            raise RuntimeError("回放输入已变化，停止本批；请重新计算")

    def evaluate(self, sig, path, version, checkpoint):
        col = signals.resolve_column(sig)
        exprs = signals.build_expressions([sig], catalog=self.stamp.signals)
        expr = exprs[col]
        roots = set(expr.meta.root_names())
        dep = roots | set(factor_dependencies(roots))
        prices = dep & PRICES
        use_limits = bool(dep & (EVENT_FIELDS | PRICES | COUNTS))
        if dep & COUNTS and self.counts is None:
            if self.roster.is_empty():
                raise UnavailableError("缺逐日全市场完整范围凭证，不能用局部样本计数")
            parts = []
            for shard in self.shards:
                checkpoint()
                parts.append(
                    self.limits(shard).select(
                        "date", "symbol", "strict_close_limit", "strict_broken_limit"
                    )
                )
            self.counts = market_counts(pl.concat(parts), self.roster)
            if self.counts.is_empty():
                raise UnavailableError("没有通过范围与行情完整性核对的市场交易日")
        if dep & (MARKET_FIELDS - COUNTS) and not {
            "source_complete",
            "source_version",
            "available_date",
        } <= set(self.market.columns):
            raise UnavailableError("市场状态历史缺少完整性、来源或可用日期证据")
        contract = sig.get("data_contract")
        selected = []
        fired = known = 0
        for shard in self.shards:
            checkpoint()
            df = (
                self.limits(shard)
                if use_limits
                else attach_calendar(pl.read_parquet(shard).sort("symbol", "date"), self.days)
            )
            if dep & COUNTS:
                df = df.join(self.counts, on="date", how="left", maintain_order="left")
            if dep & (MARKET_FIELDS - COUNTS):
                df = attach_market_facts(df, self.market, dep & (MARKET_FIELDS - COUNTS))
            indicators = (dep & INDICATOR_COLUMNS) - set(df.columns)
            if indicators:
                df = compute_indicators(df, needed=indicators)
            df = signals.materialize_factor_columns(
                df, exprs, trading_dates=self.limit_days if use_limits else self.days
            )
            missing = roots - set(df.columns)
            if missing:
                raise UnavailableError("历史依赖不可用：" + ",".join(sorted(missing)))
            term = expr
            # 复合信号没有(也不允许)data_contract; 其 warmup/未知传播由内联的
            # 各原子 guard 负责, 不能再按"自身 conditions"跑 legacy 市场索引校验。
            is_composite = signals.signal_type(sig) == signals.TYPE_COMPOSITE
            if not contract and not is_composite:
                # Legacy definitions without a contract must not turn warmup/gaps into misses.
                valid = pl.lit(True)
                for c in sig["conditions"]:
                    operands = [(c["left"], int(c.get("leftDays", 0)))]
                    right = str(c["right"])
                    if right.startswith("field:"):
                        operands.append((right[6:], int(c.get("rightDays", 0))))
                    for field, off in operands:
                        window = max(1, factor_warmup_bars([field])) + off
                        # Legacy indicators in the catalog use closes, except volume ratio.
                        if field in INDICATOR_COLUMNS:
                            raw = "volume" if field == "vol_ratio_5d" else "close"
                            good = (pl.col(raw).is_finite() & (pl.col(raw) > 0)).fill_null(False)
                            valid &= (
                                good.cast(pl.UInt8)
                                .rolling_min(window - off)
                                .shift(off)
                                .over("symbol")
                                == 1
                            )
                        valid &= pl.col(field).shift(off).over("symbol").is_finite() & (
                            (
                                pl.col(MARKET_INDEX)
                                - pl.col(MARKET_INDEX).shift(window - 1).over("symbol")
                            )
                            == window - 1
                        )
                term = pl.when(valid).then(term).otherwise(None)
            if prices:
                term = pl.when(pl.col("_unlimited")).then(False).otherwise(term)
            flags = df.select("symbol", "date", term.alias("flag"))
            in_window = flags.filter(pl.col("date") >= FIRST)["flag"]
            fired += in_window.sum() or 0
            known += in_window.len() - in_window.null_count()
            selected.append(
                self.targets.join(flags, on=["symbol", "date"], how="inner").select("_slot", "flag")
            )
        flat = self.targets.join(
            pl.concat(selected), on="_slot", how="left", maintain_order="left"
        ).sort("_slot")
        groups = flat.group_by("_row", maintain_order=True).agg(pl.col("flag").alias("flags"))
        rows = self.runs.join(groups, on="_row", how="left", maintain_order="left").drop("_row")
        rows = rows.with_columns(pl.col("flags").fill_null(pl.lit([], dtype=pl.List(pl.Boolean))))
        flags = pl.col("flags")
        rows = rows.with_columns(
            pl.when(flags.list.any())
            .then(pl.lit("hit"))
            .when((flags.list.len() < WINDOW) | flags.list.eval(pl.element().is_null()).list.any())
            .then(pl.lit("unknown"))
            .otherwise(pl.lit("miss"))
            .alias("category")
        )
        counts = dict(rows.group_by("category").len().iter_rows())
        unknown = counts.get("unknown", 0)
        if unknown == rows.height:
            raise UnavailableError("历史有效输入不足，无法判定任何猛拉段")
        summary = dict(
            signal_id=sig["id"],
            name=sig["name"],
            version=version,
            rule=RULE,
            stamp=scoped(self.stamp, sig),
            limit_policy=POLICY if use_limits else None,
            as_of=self.as_of,
            source=self.source,
            signal_hash=digest(sig),
            computed_at=datetime.now(UTC).isoformat(),
            total=rows.height,
            hit=counts.get("hit", 0),
            miss=counts.get("miss", 0),
            unknown=unknown,
            eve=rows.select(flags.list.last().sum()).item(),
            fired=fired,
            evaluable=known,
        )
        checkpoint()
        self.verify()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(rows, path, metadata={"coverage": json.dumps(summary)})
        return summary
