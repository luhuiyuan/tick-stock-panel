"""Export historical-prefix L0/L1/L2 analog features as JSONL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

from app.parquet import scan_daily_parquet
from app.research.l2_analog_features import replay_feature_snapshots

COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("../data"))
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--max-bars", type=int, default=500)
    parser.add_argument("--min-prefix-bars", type=int, default=80)
    parser.add_argument("--candle-lookback", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = str(args.data_dir / "kline_daily" / "**" / "*.parquet")
    scan = scan_daily_parquet(source)
    available = set(scan.collect_schema().names())
    frame = (
        scan.filter(pl.col("symbol") == args.symbol)
        .select([column for column in COLUMNS if column in available])
        .sort("date")
        .collect()
    )
    if args.max_bars > 0:
        frame = frame.tail(args.max_bars)
    snapshots = replay_feature_snapshots(
        frame,
        symbol=args.symbol,
        min_prefix_bars=args.min_prefix_bars,
        candle_lookback=args.candle_lookback,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for snapshot in snapshots:
            handle.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({
        "symbol": args.symbol,
        "bars": frame.height,
        "snapshots": len(snapshots),
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
