"""Run a small local L2 expression experiment."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import polars as pl

from app.parquet import scan_daily_parquet
from app.research.l2_expression_experiment import (
    L2ReplayConfig,
    event_deduplicate,
    replay_samples,
    summarize_rows,
)

REQUIRED_COLUMNS = ["symbol", "date", "open", "high", "low", "close"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("../data"))
    parser.add_argument("--max-symbols", type=int, default=1)
    parser.add_argument("--max-bars", type=int, default=500)
    parser.add_argument("--min-prefix-bars", type=int, default=120)
    parser.add_argument("--output", type=Path, default=Path("/tmp/tick-stock-l2-experiment"))
    return parser.parse_args()


def _select_symbols(source: str, max_symbols: int) -> list[str]:
    counts = (
        scan_daily_parquet(source)
        .select("symbol", "date")
        .group_by("symbol")
        .agg(pl.len().alias("bar_count"), pl.col("date").min().alias("first_date"))
        .sort("bar_count", descending=True)
        .head(max_symbols)
        .collect()
    )
    return [str(value) for value in counts["symbol"].to_list()]


def _load_symbol(source: str, symbol: str, max_bars: int) -> pl.DataFrame:
    frame = (
        scan_daily_parquet(source)
        .filter(pl.col("symbol") == symbol)
        .select(REQUIRED_COLUMNS)
        .sort("date")
        .collect()
    )
    return frame.tail(max_bars) if max_bars > 0 else frame



def _aggregate(rows: list[dict], horizon: int) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    frame = pl.DataFrame(rows).filter(pl.col(f"future_return_{horizon}d").is_not_null())
    if frame.is_empty():
        return frame
    return (frame.group_by("variant", "amplitude_scheme", "context_k")
        .agg(
            pl.len().alias("sample_count"),
            pl.col("symbol").n_unique().alias("symbol_count"),
            (pl.col(f"future_return_{horizon}d") > 0).mean().alias("positive_probability"),
            (pl.col(f"future_return_{horizon}d") < 0).mean().alias("negative_probability"),
            pl.col(f"future_return_{horizon}d").mean().alias("average_return"),
        )
        .with_columns(pl.lit(horizon).alias("horizon"))
    )

def main() -> None:
    args = _parse_args()
    source = str(args.data_dir / "kline_daily" / "**" / "*.parquet")
    symbols = _select_symbols(source, args.max_symbols)
    args.output.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    metadata: list[dict] = []
    for symbol in symbols:
        frame = _load_symbol(source, symbol, args.max_bars)
        if frame.height <= args.min_prefix_bars + 1:
            metadata.append({"symbol": symbol, "bars": frame.height, "status": "skipped"})
            continue
        rows_for_symbol: list[dict] = []
        for scheme in ("fine", "coarse"):
            rows_for_symbol.extend(replay_samples(
                frame,
                symbol=symbol,
                config=L2ReplayConfig(
                    min_prefix_bars=args.min_prefix_bars,
                    amplitude_scheme=scheme,  # type: ignore[arg-type]
                ),
            ))
        all_rows.extend(rows_for_symbol)
        metadata.append({
            "symbol": symbol,
            "bars": frame.height,
            "first_date": str(frame["date"][0]),
            "last_date": str(frame["date"][-1]),
            "sample_rows": len(rows_for_symbol),
            "status": "ok",
        })
        print(f"{symbol}: bars={frame.height}, samples={len(rows_for_symbol)}")

    event_rows = event_deduplicate(all_rows)
    if all_rows:
        pl.DataFrame(all_rows).write_parquet(args.output / "samples.parquet")
        pl.DataFrame(event_rows).write_parquet(args.output / "samples_event.parquet")
        for name, rows in (("daily", all_rows), ("event", event_rows)):
            summaries = []
            aggregates = []
            for horizon in (5, 10, 20):
                summaries.extend(summarize_rows(rows, horizon=horizon))
                aggregates.append(_aggregate(rows, horizon))
            pl.DataFrame(summaries).write_csv(args.output / f"summary_{name}.csv")
            pl.concat(aggregates, how="vertical_relaxed").write_csv(args.output / f"aggregate_{name}.csv")
        event_frame = pl.DataFrame(event_rows)
        if event_frame.height >= 2:
            train_parts: list[pl.DataFrame] = []
            test_parts: list[pl.DataFrame] = []
            for _, group in event_frame.group_by("variant", "amplitude_scheme", "context_k", maintain_order=True):
                group = group.sort("date")
                split = max(1, int(group.height * 0.7))
                train_parts.append(group.head(split))
                if group.height > split:
                    test_parts.append(group.tail(group.height - split))
            for name, parts in (("train", train_parts), ("test", test_parts)):
                rows = pl.concat(parts, how="vertical_relaxed").to_dicts() if parts else []
                aggregates = [_aggregate(rows, horizon) for horizon in (5, 10, 20)]
                pl.concat(aggregates, how="vertical_relaxed").write_csv(args.output / f"aggregate_{name}.csv")
        expression_counts = (
            pl.DataFrame(event_rows)
            .group_by("variant", "amplitude_scheme", "context_k", "expression")
            .agg(
                pl.len().alias("occurrence_count"),
                pl.col("symbol").n_unique().alias("symbol_count"),
                pl.col("date").min().alias("first_date"),
                pl.col("date").max().alias("last_date"),
            )
            .sort("occurrence_count", descending=True)
        )
        expression_counts.write_csv(args.output / "expression_recurrence.csv")
    report = {
        "generated_at": date.today().isoformat(),
        "source": source,
        "symbols": metadata,
        "sample_rows": len(all_rows),
        "event_rows": len(event_rows),
        "notes": [
            "Historical features use prefix replay; future rows are used only as labels.",
            "The event table keeps one earliest row per symbol/expression/current L2 segment.",
            "This is an information experiment, not a trading signal.",
        ],
    }
    (args.output / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
