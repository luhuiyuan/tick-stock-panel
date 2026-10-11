"""Cross-stock, out-of-sample L2 expression experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import polars as pl

from app.parquet import scan_daily_parquet
from app.research.l2_expression_experiment import (
    L2ReplayConfig,
    event_deduplicate,
    replay_samples,
)

REQUIRED_COLUMNS = ["symbol", "date", "open", "high", "low", "close"]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("../data"))
    parser.add_argument("--max-symbols", type=int, default=16)
    parser.add_argument("--test-symbol-ratio", type=float, default=0.25)
    parser.add_argument("--max-bars", type=int, default=500)
    parser.add_argument("--min-prefix-bars", type=int, default=120)
    parser.add_argument("--min-pattern-counts", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument("--output", type=Path, default=Path("/tmp/tick-stock-l2-cross-stock"))
    return parser.parse_args()


def _source(data_dir: Path) -> str:
    return str(data_dir / "kline_daily" / "**" / "*.parquet")


def _select_symbols(source: str, count: int) -> list[str]:
    result = (
        scan_daily_parquet(source)
        .select("symbol", "date")
        .group_by("symbol")
        .agg(pl.len().alias("bar_count"), pl.col("date").min().alias("first_date"))
        .sort(["bar_count", "symbol"], descending=[True, False])
        .head(count)
        .collect()
    )
    return [str(value) for value in result["symbol"].to_list()]


def _load(source: str, symbol: str, max_bars: int) -> pl.DataFrame:
    frame = (
        scan_daily_parquet(source)
        .filter(pl.col("symbol") == symbol)
        .select(REQUIRED_COLUMNS)
        .sort("date")
        .collect()
    )
    return frame.tail(max_bars) if max_bars > 0 else frame


def _split_symbols(symbols: list[str], test_ratio: float) -> tuple[list[str], list[str]]:
    ranked = sorted(
        symbols,
        key=lambda symbol: hashlib.sha256(symbol.encode("utf-8")).hexdigest(),
    )
    test_count = max(1, min(len(ranked) - 1, round(len(ranked) * test_ratio)))
    return ranked[test_count:], ranked[:test_count]


def _split_rows(rows: list[dict], train_symbols: set[str], cutoffs: dict[str, str]) -> tuple[list[dict], list[dict]]:
    train: list[dict] = []
    test: list[dict] = []
    for row in rows:
        cutoff = cutoffs[row["symbol"]]
        if row["symbol"] in train_symbols and row["date"] <= cutoff:
            train.append(row)
        elif row["symbol"] not in train_symbols and row["date"] > cutoff:
            test.append(row)
    return train, test


def _stats(rows: list[dict], horizon: int) -> dict[str, float | int | None]:
    values = [row[f"future_return_{horizon}d"] for row in rows]
    values = [float(value) for value in values if value is not None]
    if not values:
        return {"sample_count": 0, "positive_probability": None, "negative_probability": None, "average_return": None}
    return {
        "sample_count": len(values),
        "positive_probability": sum(value > 0 for value in values) / len(values),
        "negative_probability": sum(value < 0 for value in values) / len(values),
        "average_return": sum(values) / len(values),
    }


def main() -> None:
    args = _args()
    source = _source(args.data_dir)
    symbols = _select_symbols(source, args.max_symbols)
    train_symbols, test_symbols = _split_symbols(symbols, args.test_symbol_ratio)
    args.output.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    cutoffs: dict[str, str] = {}
    metadata: list[dict] = []
    for symbol in symbols:
        frame = _load(source, symbol, args.max_bars)
        if frame.height <= args.min_prefix_bars + 1:
            metadata.append({"symbol": symbol, "bars": frame.height, "status": "skipped"})
            continue
        cutoffs[symbol] = str(frame["date"][int(frame.height * 0.7)])[:10]
        rows: list[dict] = []
        for scheme in ("fine", "coarse"):
            rows.extend(replay_samples(
                frame,
                symbol=symbol,
                config=L2ReplayConfig(
                    min_prefix_bars=args.min_prefix_bars,
                    amplitude_scheme=scheme,  # type: ignore[arg-type]
                ),
            ))
        all_rows.extend(rows)
        metadata.append({
            "symbol": symbol,
            "bars": frame.height,
            "first_date": str(frame["date"][0])[:10],
            "last_date": str(frame["date"][-1])[:10],
            "cutoff_date": cutoffs[symbol],
            "sample_rows": len(rows),
            "role": "train" if symbol in train_symbols else "test",
            "status": "ok",
        })
        print(f"{symbol}: bars={frame.height}, samples={len(rows)}")

    event_rows = event_deduplicate(all_rows)
    train_rows, test_rows = _split_rows(event_rows, set(train_symbols), cutoffs)
    pl.DataFrame(event_rows).write_parquet(args.output / "samples_event.parquet")
    pl.DataFrame(train_rows).write_parquet(args.output / "samples_train.parquet")
    pl.DataFrame(test_rows).write_parquet(args.output / "samples_test.parquet")

    results: list[dict] = []
    for key, group in pl.DataFrame(train_rows).group_by(
        "variant", "amplitude_scheme", "context_k", maintain_order=True,
    ):
        variant, scheme, context_k = key
        test_group = pl.DataFrame(test_rows).filter(
            (pl.col("variant") == variant)
            & (pl.col("amplitude_scheme") == scheme)
            & (pl.col("context_k") == context_k)
        ).to_dicts()
        counts = (
            group.group_by("expression")
            .agg(pl.len().alias("train_occurrence_count"))
        )
        for minimum in args.min_pattern_counts:
            known = counts.filter(pl.col("train_occurrence_count") >= minimum)
            eligible = (
                pl.DataFrame(test_group)
                .join(known.select("expression"), on="expression", how="inner")
                .to_dicts()
                if test_group and known.height
                else []
            )
            for horizon in (5, 10, 20):
                item = _stats(eligible, horizon)
                item.update({
                    "variant": variant,
                    "amplitude_scheme": scheme,
                    "context_k": context_k,
                    "min_train_pattern_count": minimum,
                    "horizon": horizon,
                    "known_pattern_count": known.height,
                    "test_eligible_count": len(eligible),
                })
                results.append(item)

    # Baseline is the complete unseen-stock/test-period population for the same
    # configuration, not the train population.
    baseline_rows = [row for row in test_rows if row["variant"] == "state" and row["context_k"] == 1]
    for horizon in (5, 10, 20):
        item = _stats(baseline_rows, horizon)
        item.update({
            "variant": "baseline",
            "amplitude_scheme": "n/a",
            "context_k": 1,
            "min_train_pattern_count": 0,
            "horizon": horizon,
            "known_pattern_count": None,
            "test_eligible_count": len(baseline_rows),
        })
        results.append(item)

    pl.DataFrame(results).sort(["horizon", "variant", "amplitude_scheme", "context_k", "min_train_pattern_count"]).write_csv(args.output / "cross_stock_summary.csv")
    pl.DataFrame(metadata).write_json(args.output / "symbols.json")
    report = {
        "generated_at": date.today().isoformat(),
        "selected_symbols": symbols,
        "train_symbols": train_symbols,
        "test_symbols": test_symbols,
        "train_event_rows": len(train_rows),
        "test_event_rows": len(test_rows),
        "all_event_rows": len(event_rows),
        "notes": [
            "Train patterns come only from train symbols before each symbol's cutoff date.",
            "Test rows come only from held-out symbols after each symbol's cutoff date.",
            "Expression hits are evaluated on unseen stocks and unseen later periods.",
        ],
    }
    (args.output / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
