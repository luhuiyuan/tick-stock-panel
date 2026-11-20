"""One fixed event cohort; stage complete per-symbol histories in small local shards."""

import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
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
RULE = "surge-pre5-ended-v2"


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
        root / "trading_calendar.json",
        root / "instruments/instruments.parquet",
        root / "regime_history/part.parquet",
        *sorted((root / "user_data/custom_factors").glob("*.json")),
    ]
    files += [
        root / f"kline_daily_enriched/date={FIRST + timedelta(days=i)}/part.parquet"
        for i in range((END - FIRST).days)
    ]
    files.append(root / ROSTER)
    manifest = {
        str(p.relative_to(root)): fingerprint(p.stat()) if p.exists() else None for p in files
    }
    return Inputs(manifest, root)


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
    def __new__(cls, manifest, root):
        obj = super().__new__(cls, digest(manifest))
        obj.manifest = manifest
        dayfile = root / "trading_calendar.json"
        ds = (
            [d for d in json.loads(dayfile.read_text()) if str(FIRST) <= d <= str(END)]
            if dayfile.exists()
            else []
        )
        obj.base = digest([manifest.get("surge_research/runs-v1.parquet"), ds])
        columns = {}
        for d in ds[:-1]:
            rel = f"kline_daily_enriched/date={d}/part.parquet"
            for k, v in column_stamps(root / rel, manifest.get(rel)).items():
                columns.setdefault(k, []).append((d, v))
        obj.columns = {k: digest(v) for k, v in columns.items()}
        obj.inst = dated_instrument_stamp(
            root / "instruments/instruments.parquet",
            manifest.get("instruments/instruments.parquet"),
            FIRST,
            END,
        )
        obj.factors = {
            p.stem: json.loads(p.read_text())
            for p in (root / "user_data/custom_factors").glob("*.json")
        }
        return obj

    def dependencies(self, sig):
        import re

        seen = set()
        todo = list(
            re.findall(
                r"[A-Za-z_][A-Za-z_0-9]*",
                json.dumps([sig.get("conditions", []), sig.get("data_contract", {})]),
            )
        )
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
        self.days = [
            d
            for s in json.loads((root / "trading_calendar.json").read_text())
            if FIRST <= (d := date.fromisoformat(s)) <= END
        ]
        days = self.days
        if (
            len(days) < 6
            or days != sorted(set(days))
            or (days[0], days[5], days[-1]) != (FIRST, START, END)
        ):
            raise ValueError("交易日历不覆盖既定区间")
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
        idx = {d: i for i, d in enumerate(days)}
        if not runs.height or runs["id"].n_unique() != runs.height:
            raise ValueError("猛拉快照为空或 ID 重复")
        self.runs = runs.with_columns(
            pl.Series("dates", [days[idx[d] - 5 : idx[d]] for d in runs["start_date"]])
        ).with_row_index("_row")
        self.targets = (
            self.runs.select("_row", "symbol", "dates")
            .explode("dates")
            .rename({"dates": "date"})
            .with_row_index("_slot")
        )
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
            for day in days[:-1]:
                checkpoint()
                df = pl.read_parquet(
                    root / f"kline_daily_enriched/date={day}/part.parquet", columns=cols
                )
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

        def optional(rel):
            return pl.read_parquet(root / rel) if (root / rel).exists() else pl.DataFrame()

        self.inst = optional("instruments/instruments.parquet")
        self.market = optional("regime_history/part.parquet")
        if "as_of" in self.inst.columns:
            self.inst = self.inst.filter(pl.col("as_of").cast(pl.Date).is_between(FIRST, END))
        self.limit_cache = {}
        self.roster = optional(ROSTER)
        self.counts = None
        self.verify()

    def limits(self, shard):
        if shard not in self.limit_cache:
            df = attach_calendar(pl.read_parquet(shard).sort("symbol", "date"), self.days)
            path = shard.with_suffix(".limits.parquet")
            price_facts(df, self.inst, self.days).write_parquet(path)
            self.limit_cache[shard] = path
        return pl.read_parquet(self.limit_cache[shard])

    def verify(self):
        if input_stamp(self.root) != self.stamp:
            raise RuntimeError("回放输入已变化，停止本批；请重新计算")

    def evaluate(self, sig, path, version, checkpoint):
        col = signals.resolve_column(sig)
        exprs = signals.build_expressions([sig])
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
            df = signals.materialize_factor_columns(df, exprs, trading_dates=self.days)
            missing = roots - set(df.columns)
            if missing:
                raise UnavailableError("历史依赖不可用：" + ",".join(sorted(missing)))
            term = expr
            if not contract:
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
            fired += flags["flag"].sum() or 0
            known += flags.height - flags["flag"].null_count()
            selected.append(
                self.targets.join(flags, on=["symbol", "date"], how="inner").select("_slot", "flag")
            )
        flat = self.targets.join(
            pl.concat(selected), on="_slot", how="left", maintain_order="left"
        ).sort("_slot")
        groups = flat.group_by("_row", maintain_order=True).agg(pl.col("flag").alias("flags"))
        rows = self.runs.join(groups, on="_row", maintain_order="left").drop("_row")
        flags = pl.col("flags")
        rows = rows.with_columns(
            pl.when(flags.list.any())
            .then(pl.lit("hit"))
            .when(flags.list.eval(pl.element().is_null()).list.any())
            .then(pl.lit("unknown"))
            .otherwise(pl.lit("miss"))
            .alias("category")
        )
        counts = dict(rows.group_by("category").len().iter_rows())
        unknown = counts.get("unknown", 0)
        if not known or unknown == rows.height:
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
