"""从逐笔备份 zip 重建分钟表 zip。

为什么需要这个脚本
------------------
分钟表的唯一源数据是逐笔：抓取时逐笔在内存里聚合成 1 分钟 bar，再写进 DuckDB
中转库，最后由 ``export_history_minute.py`` 打包成 ``<year>.zip``。逐笔分片打包后
会被删掉，但 ``<year>_ticks.zip`` 是完整保留的 —— 所以只要聚合逻辑有变（修边界、
改方向判定、换实现），都能直接从这个 zip 重算分钟表，**不用回主站重抓**。

典型场景：``_ticks_to_bars`` 早期漏过滤 ``direction == 8``（集合竞价虚拟撮合），
把 14:57~14:59 的试探价夹进了 15:00 那根 bar，实测 19.7% 的股票日 open/low 被拽偏。
修好之后，已经导出的年份用本脚本重算一遍即可，耗时约 40 分钟（对比重抓 13 小时）。

口径与 ``export_history_minute.py`` 的打包产物**完全一致**：成员名 ``<symbol>.csv``，
列 ``symbol,datetime,date,open,high,low,close,volume,amount,buy_volume,sell_volume``，
``datetime`` 用 ``YYYY-MM-DD HH:MM:SS``。

用法
----

    cd backend
    PYTHONPATH= .venv/Scripts/python.exe scripts/rebuild_minute_zip.py --year 2025

    # 先抽样核对（用旧的逐日实现跑对照，逐根比较）
    PYTHONPATH= .venv/Scripts/python.exe scripts/rebuild_minute_zip.py --year 2025 --verify 5

    # 只跑 50 只试速度，产物写到别处
    PYTHONPATH= .venv/Scripts/python.exe scripts/rebuild_minute_zip.py --year 2025 \
        --limit 50 --out D:/tsp/backup_export/_rebuild_test/2025.zip

产物先写 ``.<year>.tmp.zip`` 再原子替换，中途失败不会破坏已有文件。逐笔 zip 里
成员按股票独立处理，单只失败只记名字、不中断整轮。
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import zipfile
from pathlib import Path

import polars as pl

from app.plugins.qatdx.provider import _ticks_frame_to_bars, _ticks_to_bars

MINUTE_TABLE = "kline_minute_1m"
ZIP_COMPRESSLEVEL = 6
TICKS_ROOTS = ("E:/tsp_backup_ticks", "D:/tsp_backup_ticks")
OUT_ROOT = Path("D:/tsp/backup_export")

CSV_SCHEMA = {"symbol": pl.String, "time": pl.String, "direction": pl.Int8}
COLUMNS = ["symbol", "datetime", "date", "open", "high", "low", "close",
           "volume", "amount", "buy_volume", "sell_volume"]


def resolve_ticks_zip(year: int, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    for root in TICKS_ROOTS:
        candidate = Path(root) / f"{year}_ticks.zip"
        if candidate.exists():
            return candidate
    raise SystemExit(f"找不到 {year} 的逐笔备份，请用 --ticks-zip 指定")


def read_member(src: zipfile.ZipFile, name: str) -> pl.DataFrame:
    return pl.read_csv(
        io.BytesIO(src.read(name)),
        schema_overrides=CSV_SCHEMA,
        try_parse_dates=True,
    )


def to_minute_csv(bars: pl.DataFrame) -> bytes:
    out = bars.with_columns(
        pl.col("dt").dt.strftime("%Y-%m-%d %H:%M:%S").alias("datetime"),
        pl.col("dt").dt.date().alias("date"),
    ).select(COLUMNS)
    return out.write_csv().encode("utf-8")


def verify_one(frame: pl.DataFrame, symbol: str) -> tuple[int, int]:
    """用旧的逐日实现重算同一只股票，返回 (新根数, 不一致根数)。"""
    frames = []
    for (day,), group in frame.group_by(["date"], maintain_order=True):
        rows = [
            {"time": t, "price": p, "vol": v, "buyorsell": d}
            for t, p, v, d in zip(group["time"], group["price"],
                                  group["volume"], group["direction"])
        ]
        old = _ticks_to_bars(rows, symbol, day, with_direction=True)
        if old is not None and not old.is_empty():
            frames.append(old)
    if not frames:
        return 0, 0
    old = pl.concat(frames).sort("dt")
    new = _ticks_frame_to_bars(frame, with_direction=True).sort("dt")
    joined = old.join(new, on="dt", how="full", suffix="_new", coalesce=True)
    bad = joined.filter(
        (pl.col("open") != pl.col("open_new"))
        | (pl.col("high") != pl.col("high_new"))
        | (pl.col("low") != pl.col("low_new"))
        | (pl.col("close") != pl.col("close_new"))
        | (pl.col("volume") != pl.col("volume_new"))
        | ((pl.col("amount") - pl.col("amount_new")).abs() > 1e-6)
        | ((pl.col("buy_volume") - pl.col("buy_volume_new")).abs() > 1e-6)
        | ((pl.col("sell_volume") - pl.col("sell_volume_new")).abs() > 1e-6)
    )
    return new.height, bad.height


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从逐笔备份 zip 重建分钟表 zip")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--ticks-zip", type=Path, default=None,
                        help="逐笔备份，默认在 E:/ D:/ 的 tsp_backup_ticks 下找")
    parser.add_argument("--out", type=Path, default=None,
                        help=f"输出的分钟 zip，默认 {OUT_ROOT}/minute/{MINUTE_TABLE}/<year>.zip")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 只（试跑）")
    parser.add_argument("--verify", type=int, default=0,
                        help="抽样 N 只，用旧的逐日实现交叉核对（不写 zip）")
    args = parser.parse_args(argv)

    src_path = resolve_ticks_zip(args.year, args.ticks_zip)
    if not src_path.exists():
        raise SystemExit(f"逐笔备份不存在: {src_path}")
    target = args.out or (OUT_ROOT / "minute" / MINUTE_TABLE / f"{args.year}.zip")
    print(f"逐笔 {src_path}  ({src_path.stat().st_size / 1e9:.2f} GB)")
    print(f"输出 {target}")

    started = time.time()
    with zipfile.ZipFile(src_path) as src:
        members = sorted(src.namelist())
        if args.limit:
            members = members[: args.limit]
        print(f"成员 {len(members)} 只")

        if args.verify:
            total_bars = total_bad = 0
            step = max(1, len(members) // args.verify)
            sample = members[::step][: args.verify]
            print(f"\n交叉核对 {len(sample)} 只（新实现 vs 逐日实现）")
            for name in sample:
                bars, bad = verify_one(read_member(src, name), name.rsplit(".", 1)[0])
                total_bars += bars
                total_bad += bad
                print(f"  {name:<14} {bars:>7,} 根  不一致 {bad}")
            print(f"\n合计 {total_bars:,} 根，不一致 {total_bad} 根")
            return 0 if total_bad == 0 else 1

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{args.year}.tmp.zip")
        tmp.unlink(missing_ok=True)

        written = rows_total = 0
        failed: list[str] = []
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=ZIP_COMPRESSLEVEL, allowZip64=True) as dst:
            for idx, name in enumerate(members, 1):
                symbol = name.rsplit(".", 1)[0]
                try:
                    bars = _ticks_frame_to_bars(read_member(src, name), with_direction=True)
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{symbol}: {type(exc).__name__}: {exc}")
                    continue
                if bars.is_empty():
                    failed.append(f"{symbol}: 无有效成交")
                    continue
                dst.writestr(f"{symbol}.csv", to_minute_csv(bars))
                written += 1
                rows_total += bars.height
                if idx % 200 == 0 or idx == len(members):
                    elapsed = (time.time() - started) / 60
                    eta = elapsed / idx * (len(members) - idx)
                    print(f"  {idx}/{len(members)}  {rows_total:,} 行  "
                          f"已用 {elapsed:.1f}min  预计还需 {eta:.1f}min", flush=True)

        tmp.replace(target)

    size = target.stat().st_size
    print(f"\n完成: {written} 只 / {rows_total:,} 行 / {size / 1e9:.2f} GB / "
          f"耗时 {(time.time() - started) / 60:.1f} 分钟")
    if failed:
        print(f"失败 {len(failed)} 只（前 10 条）：")
        for item in failed[:10]:
            print("  " + item)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
