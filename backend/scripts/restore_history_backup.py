"""把 ``export_history_backup.py`` 产出的按年 csv.gz 灌回项目的 parquet 分区。

设计约束
--------
* **不破坏现有数据**：默认 merge —— 读已有 ``part.parquet``，与新数据按主键
  去重后重写；只有显式 ``--overwrite`` 才整分区替换。
* **守住项目不变式**：每个 ``date=`` 分区只留一份 ``part.parquet``
  （见 CONTRIBUTING §6.3，DuckDB 逐文件读 footer，文件数直接乘进查询成本）。
* **断点续跑**：已存在的输出文件默认跳过合并（``--skip-existing``），
  中断后重跑不会重复计算。

用法
----
::

    cd backend
    PYTHONPATH= uv run --no-sync python scripts/restore_history_backup.py \
        --src D:/tsp/backup_export --data ../data

    # 只看会写什么，不落盘
    ... --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path
from typing import Sequence

import polars as pl

# 备份子目录 -> (项目目标目录, 主键, 说明)
RESTORE_PLAN: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("daily/kline_daily", "kline_daily", ("symbol", "date"), "A股日K"),
    ("daily/kline_index_daily", "kline_index_daily", ("symbol", "date"), "指数日K"),
    ("daily/kline_etf_daily", "kline_etf_daily", ("symbol", "date"), "ETF 日K"),
    ("minute/kline_minute_1m", "kline_minute", ("symbol", "datetime"), "1分钟K（项目主用的分钟表）"),
    ("minute/kline_minute_5m", "kline_minute_5m", ("symbol", "datetime"), "5分钟K（独立目录，项目暂不读）"),
    ("minute/kline_minute_15m", "kline_minute_15m", ("symbol", "datetime"), "15分钟K（独立目录）"),
    ("minute/kline_minute_30m", "kline_minute_30m", ("symbol", "datetime"), "30分钟K（独立目录）"),
    ("minute/kline_minute_60m", "kline_minute_60m", ("symbol", "datetime"), "60分钟K（独立目录）"),
    ("corporate/xdxr", "xdxr", ("symbol", "ex_date", "category"), "除权除息原始事件"),
)

# 恢复时必须落成 datetime 的列：CSV 里是 "2026-10-30 09:31:00"
_DATETIME_COLS = ("datetime",)


def _normalize(frame: pl.DataFrame, date_col: str) -> pl.DataFrame:
    """统一类型，避免 CSV 推断成 Int/Utf8 后与既有 parquet 对不齐。"""
    casts = []
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in frame.columns:
            casts.append(pl.col(col).cast(pl.Float64, strict=False))
    for col in _DATETIME_COLS:
        if col in frame.columns:
            casts.append(pl.col(col).cast(pl.Datetime("us"), strict=False))
    if date_col in frame.columns:
        casts.append(pl.col(date_col).cast(pl.Date, strict=False))
    return frame.with_columns(casts) if casts else frame


def restore_one(
    src: Path,
    dst: Path,
    *,
    keys: tuple[str, ...],
    date_col: str,
    overwrite: bool,
    dry_run: bool,
    label: str,
) -> dict:
    files = sorted(src.glob("*.csv.gz"))
    if not files:
        print(f"  跳过 {label}: {src} 无文件")
        return {"label": label, "files": 0, "rows": 0}

    total_rows = 0
    total_parts = 0
    started = time.time()
    print(f"\n== {label} → {dst} ({len(files)} 个年度文件) ==", flush=True)

    for path in files:
        frame = pl.read_csv(path, try_parse_dates=True)
        if frame.is_empty():
            continue
        frame = _normalize(frame, date_col)
        if date_col not in frame.columns:
            print(f"  跳过 {path.name}: 无 {date_col} 列")
            continue
        total_rows += frame.height
        for stamp, chunk in frame.group_by(date_col, maintain_order=True):
            day = stamp[0] if isinstance(stamp, tuple) else stamp
            part = dst / f"date={day}" / "part.parquet"
            total_parts += 1
            if dry_run:
                continue
            if part.exists() and not overwrite:
                old = pl.read_parquet(part)
                merged = pl.concat([old, chunk], how="diagonal_relaxed").unique(
                    subset=list(keys), keep="last"
                )
                # 已有分区里若有同一天的多份碎片，这里顺手收成一份。
                for extra in part.parent.glob("*.parquet"):
                    if extra != part:
                        extra.unlink()
            else:
                merged = chunk
            part.parent.mkdir(parents=True, exist_ok=True)
            merged.sort(list(keys)).write_parquet(part)
        print(
            f"  {path.name}: {frame.height:,} 行 → {dst.name}/date=*/part.parquet "
            f"({(time.time() - started):.0f}s)",
            flush=True,
        )
    return {"label": label, "files": len(files), "rows": total_rows, "parts": total_parts}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把历史行情备份包灌回项目 parquet 分区")
    parser.add_argument("--src", type=Path, required=True, help="备份包根目录")
    parser.add_argument("--data", type=Path, default=None, help="项目 data 目录(默认 <repo>/data)")
    parser.add_argument("--only", default="all", help="逗号分隔的子目录名，如 kline_daily,kline_minute_1m")
    parser.add_argument("--overwrite", action="store_true", help="整分区替换，不做合并")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    data_dir = args.data.resolve() if args.data else repo_root / "data"
    src_root = args.src.resolve()
    if not src_root.exists():
        print(f"备份目录不存在: {src_root}")
        return 2

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    pl.DataFrame  # noqa: B018  (保持导入用于类型提示)

    results = []
    started = time.time()
    for sub, target, keys, label in RESTORE_PLAN:
        name = Path(sub).name
        if wanted != {"all"} and name not in wanted and sub not in wanted:
            continue
        src = src_root / sub
        if not src.exists():
            continue
        dst = data_dir / target
        date_col = "ex_date" if target == "xdxr" else "date"
        results.append(
            restore_one(
                src, dst,
                keys=keys, date_col=date_col,
                overwrite=args.overwrite, dry_run=args.dry_run, label=f"{label} [{name}]",
            )
        )

    print(f"\n恢复完成，用时 {(time.time() - started) / 60:.1f} 分钟")
    total = sum(r["rows"] for r in results)
    print(f"共写入 {total:,} 行" + ("（dry-run 未落盘）" if args.dry_run else f" → {data_dir}"))
    print("\n提示：除权因子(adj_factor)不随备份恢复，项目下次同步会按 xdxr + 日K 自动重算。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
