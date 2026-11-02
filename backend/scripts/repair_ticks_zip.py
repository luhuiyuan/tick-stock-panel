"""按日补齐 ``<year>_ticks.zip`` 里缺失的股票日。

为什么需要这个脚本
------------------
``export_history_minute.fetch_ticks`` 早期遇到主站返回空页时直接 ``break``，把
「主站抖了一下」当成「当天没数据」，既不重试也不记失败——2025 年因此静默丢了
460 个股票日（全部落在 2025-01-10 一天，沪深各板块都有），分片还被标记完成、
年完成标记也写了。抓取逻辑已经改成「取不全就抛错」，但**已经导出的年份**还得补。

逐笔 zip 是完整源数据，分钟表可以从它重算，所以补的只有逐笔这一层：把缺失的
股票日重新抓一遍，合并回成员里，再让 ``rebuild_minute_zip.py`` 重算分钟表。

zip 成员不能原地替换，整包重写一次（本机实测解压重压约 34 MB/s，11 GB 的包
约 1 小时）。产物先写 ``.<year>_ticks.tmp.zip`` 再原子替换，中途失败不破坏原文件。

用法
----

    cd backend

    # 只看缺哪些，不连网、不写盘
    PYTHONPATH= .venv/Scripts/python.exe scripts/repair_ticks_zip.py --year 2025 --dry-run

    # 真跑
    PYTHONPATH= .venv/Scripts/python.exe scripts/repair_ticks_zip.py --year 2025

跑完的判据：脚本最后会重新扫一遍新包，缺失数必须是 0；否则退出码为 1。
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_history_minute as exh  # noqa: E402

from app.plugins.qatdx import client as qc  # noqa: E402

ZIP_COMPRESSLEVEL = 6
TICKS_ROOTS = ("E:/tsp_backup_ticks", "D:/tsp_backup_ticks")
CALENDAR_DIR = Path("D:/tsp/backup_export/daily/kline_daily")
TICK_SCHEMA = {"symbol": pl.String, "date": pl.String, "time": pl.String,
               "price": pl.Float64, "volume": pl.Float64, "direction": pl.Int8}
COLUMNS = ["symbol", "date", "time", "price", "volume", "direction"]


def resolve_ticks_zip(year: int, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    for root in TICKS_ROOTS:
        candidate = Path(root) / f"{year}_ticks.zip"
        if candidate.exists():
            return candidate
    raise SystemExit(f"找不到 {year}_ticks.zip（试过 {TICKS_ROOTS}），用 --zip 指定")


def _scan_chunk(zpath: Path, names: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    with zipfile.ZipFile(zpath) as zf:
        for name in names:
            # 只投影 date 一列走 polars（Rust 解析），逐行 Python 切成 32 亿行要半小时。
            frame = pl.read_csv(io.BytesIO(zf.read(name)), columns=["date"],
                                schema_overrides={"date": pl.String})
            symbol = name[:-4] if name.endswith(".csv") else name
            out[symbol] = set(frame["date"].unique().to_list())
    return out


def scan_dates(zpath: Path, threads: int = 4) -> dict[str, set[str]]:
    """每个成员里出现了哪些交易日。成员间互不依赖，按线程切片并发解压。"""
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    merged: dict[str, set[str]] = {}
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for part in pool.map(lambda chunk: _scan_chunk(zpath, chunk),
                             [names[i::threads] for i in range(threads)]):
            merged.update(part)
    return merged


def find_missing(calendar: dict[str, list[tuple[date, float]]],
                 present: dict[str, set[str]]) -> dict[str, list[tuple[date, float]]]:
    """日历上有成交、zip 里没有的（股票, 交易日）。成交量为 0 的日子不算缺失。"""
    missing: dict[str, list[tuple[date, float]]] = {}
    for symbol, days in calendar.items():
        have = present.get(symbol, set())
        lost = [(day, vol) for day, vol in days if str(day) not in have and vol > 0]
        if lost:
            missing[symbol] = lost
    return missing


def _rows_to_frame(symbol: str, ticks: dict[date, list[dict]]) -> pl.DataFrame:
    days: list[str] = []
    times: list[str] = []
    prices: list[float] = []
    volumes: list[float] = []
    directions: list[int] = []
    for day in sorted(ticks):
        for row in ticks[day]:
            days.append(str(day))
            times.append(str(row.get("time") or ""))
            prices.append(float(row.get("price") or 0.0))
            volumes.append(float(row.get("vol") or 0.0))
            directions.append(exh._direction(row))
    return pl.DataFrame(
        {"symbol": [symbol] * len(days), "date": days, "time": times,
         "price": prices, "volume": volumes, "direction": directions},
        schema_overrides={"symbol": pl.String, "date": pl.String, "time": pl.String,
                          "price": pl.Float64, "volume": pl.Float64, "direction": pl.Int8},
    )


def merge_member(existing: pl.DataFrame, patch: pl.DataFrame) -> pl.DataFrame:
    """把补来的行按日期插进原成员，**保持原有行序**。

    不能直接 ``sort``：同一分钟里有十几笔成交，排序必须稳定，而 polars 的 sort
    默认不稳定，打乱同行序就等于打乱成交流水。
    """
    dates = patch["date"].unique().sort().to_list()
    parts: list[pl.DataFrame] = []
    rest = existing
    for day in dates:
        parts.append(rest.filter(pl.col("date") < day))
        parts.append(patch.filter(pl.col("date") == day))
        rest = rest.filter(pl.col("date") > day)
    parts.append(rest)
    return pl.concat([p for p in parts if not p.is_empty()], how="vertical").select(COLUMNS)


def fetch_missing(missing: dict[str, list[tuple[date, float]]], endpoints: list,
                  workers: int) -> tuple[dict[str, dict[date, list[dict]]], list[str]]:
    ep = exh.Endpoint(endpoints)
    got: dict[str, dict[date, list[dict]]] = {}
    failed: list[str] = []
    total = sum(len(v) for v in missing.values())
    done = 0
    started = time.time()

    def work(symbol: str):
        parsed = qc.split_symbol(symbol)
        if parsed is None:
            raise RuntimeError(f"无法解析代码 {symbol}")
        days = tuple((day, int(day.strftime("%Y%m%d")), vol) for day, vol in missing[symbol])
        return symbol, exh.fetch_ticks(ep, parsed[0], parsed[1], days)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, s): s for s in missing}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                name, ticks = future.result()
                got[name] = ticks
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{symbol}: {type(exc).__name__}: {exc}")
            done += len(missing[symbol])
            if len(got) + len(failed) == len(missing) or (len(got) + len(failed)) % 50 == 0:
                print(f"  抓取 {done:,}/{total:,} 股·天  成功 {len(got)} 只 / 失败 {len(failed)} 只"
                      f"  {time.time() - started:.0f}s", flush=True)
    return got, failed


def rewrite(zpath: Path, patch: dict[str, dict[date, list[dict]]]) -> tuple[Path, int, int]:
    """整包重写：补过的成员合并后写回，其余成员原样搬过去。"""
    target = zpath.with_name(f".{zpath.stem}.tmp.zip")
    if target.exists():
        target.unlink()
    total = 0
    repaired = 0
    started = time.time()
    with zipfile.ZipFile(zpath) as zin, zipfile.ZipFile(
        target, "w", zipfile.ZIP_DEFLATED, compresslevel=ZIP_COMPRESSLEVEL, allowZip64=True
    ) as zout:
        infos = zin.infolist()
        for index, info in enumerate(infos, 1):
            name = info.filename
            payload = zin.read(name)
            symbol = name[:-4] if name.endswith(".csv") else None
            if symbol in patch:
                existing = pl.read_csv(io.BytesIO(payload), schema_overrides=TICK_SCHEMA)
                merged = merge_member(existing, _rows_to_frame(symbol, patch[symbol]))
                payload = merged.write_csv().encode("utf-8")
                total += merged.height
                repaired += 1
            else:
                total += max(payload.count(b"\n") - 1, 0)  # 减掉表头
            zout.writestr(name, payload)
            if index % 500 == 0 or index == len(infos):
                elapsed = time.time() - started
                eta = elapsed / index * (len(infos) - index)
                print(f"  重写 {index:,}/{len(infos):,} 成员  {elapsed / 60:.1f}min"
                      f"  预计还需 {eta / 60:.1f}min", flush=True)
    return target, total, repaired


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="补齐逐笔 zip 里缺失的股票日")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--zip", type=Path, default=None, help="逐笔 zip 路径（默认按年份在 E/D 盘找）")
    parser.add_argument("--calendar", type=Path, default=CALENDAR_DIR, help="日K 备份目录")
    parser.add_argument("--workers", type=int, default=4, help="抓取并发（主站限流，别超 4）")
    parser.add_argument("--endpoints", type=int, default=8, help="主站数量")
    parser.add_argument("--dry-run", action="store_true", help="只报缺失，不连网不写盘")
    args = parser.parse_args(argv)

    zpath = resolve_ticks_zip(args.year, args.zip)
    print(f"逐笔包 {zpath}（{zpath.stat().st_size / 1e9:.2f} GB）", flush=True)

    calendar = exh.load_year_calendar(args.calendar, args.year)
    if not calendar:
        raise SystemExit(f"{args.year}: 无日K日历，无法判断该有哪些交易日")
    present = scan_dates(zpath)
    missing = find_missing(calendar, present)
    total = sum(len(v) for v in missing.values())
    print(f"日历 {sum(len(v) for v in calendar.values()):,} 股·天 / {len(calendar)} 只"
          f"；包内 {len(present)} 只", flush=True)
    if not total:
        print("没有缺失，无需修复。", flush=True)
        return 0
    print(f"缺失 {total:,} 股·天，涉及 {len(missing)} 只", flush=True)
    by_day = sorted({str(d) for v in missing.values() for d, _ in v})
    print(f"缺失日期：{', '.join(by_day[:10])}{' …' if len(by_day) > 10 else ''}", flush=True)

    if args.dry_run:
        print("\n--dry-run：未连网、未写盘。")
        return 0

    targets = exh.probe_days(calendar)
    endpoints = exh.pick_endpoints(args.endpoints, probe_ymds=targets)
    print(f"主站 {len(endpoints)} 台（已验证 {'/'.join(map(str, targets))} 有逐笔）", flush=True)

    got, failed = fetch_missing(missing, endpoints, args.workers)
    if failed:
        print(f"\n有 {len(failed)} 只抓不下来，先不重写包（重跑会自动重试）：", flush=True)
        for line in failed[:10]:
            print(f"  {line}", flush=True)
        return 1

    print(f"\n重写整包（{len(got)} 只待合并）…", flush=True)
    target, rows, repaired = rewrite(zpath, got)

    # 回读新包再扫一遍：缺失必须是 0，否则不动原文件。
    after = find_missing(calendar, scan_dates(target))
    left = sum(len(v) for v in after.values())
    if left:
        print(f"新包仍缺 {left:,} 股·天，保留原包不动：{target}", flush=True)
        return 1

    backup = zpath.with_suffix(".zip.bak")
    if backup.exists():
        backup.unlink()
    shutil.move(str(zpath), str(backup))
    target.replace(zpath)
    backup.unlink()
    print(f"\n完成：{repaired} 只成员合并 / 全包 {rows:,} 行 / "
          f"{zpath.stat().st_size / 1e9:.2f} GB / 余量校验缺失 0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
