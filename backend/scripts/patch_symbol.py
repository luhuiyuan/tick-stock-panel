"""把一只**被整只判失败**的股票补进已打包的 ``<year>.zip`` 与 ``<year>_ticks.zip``。

为什么需要这个脚本
------------------
2024 年那一轮导出里，``601162.SH`` 因为一天（2024-10-10）的逐笔量与日K 差 3 手
被判失败，``run_shard`` 的处理是「整只剔出 results」，于是这只票 242 个交易日全
没进包（5,069 只 → 5,068 只）。当时的阈值是固定 2 手，而那天成交 3,694 万手，
3 手的相对误差只有 8e-8 —— 是阈值不合理，不是数据有问题。

阈值已经改成随成交量放大（``exh._volume_tolerance``），失败粒度也改成「天」。
但**已经打出包的那一年**还缺这只票，重跑整年要 15 小时，补一只票只要几分钟：
抓这一只的全年的逐笔 → 聚合出分钟表 → 作为**新成员追加**进两个 zip。
追加新成员不需要整包重写（只有替换已有成员才需要），所以是秒级操作。

用法
----

    cd backend

    # 真跑（默认路径 D:/tsp/backup_export 与 E:/tsp_backup_ticks）
    PYTHONPATH= .venv/Scripts/python.exe scripts/patch_symbol.py --year 2024 --symbol 601162.SH

    # 只抓不算，看这只票全年能取到多少天（不写盘）
    PYTHONPATH= .venv/Scripts/python.exe scripts/patch_symbol.py --year 2024 --symbol 601162.SH --dry-run

判据：脚本最后会重新打开两个包读回该成员，逐项核对行数、日期集合与包内成员数，
任何一项不符退 1。
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import zipfile
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_history_minute as exh  # noqa: E402

from app.plugins.qatdx import client as qc  # noqa: E402

TICKS_ROOTS = ("E:/tsp_backup_ticks", "D:/tsp_backup_ticks")
MINUTE_COLUMNS = ["symbol", "datetime", "date", "open", "high", "low", "close",
                  "volume", "amount", "buy_volume", "sell_volume"]
TICK_COLUMNS = ["symbol", "date", "time", "price", "volume", "direction"]


def _resolve_ticks_zip(year: int, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    for root in TICKS_ROOTS:
        candidate = Path(root) / f"{year}_ticks.zip"
        if candidate.exists():
            return candidate
    raise SystemExit(f"找不到 {year}_ticks.zip（试过 {TICKS_ROOTS}），用 --ticks-zip 指定")


def _existing_members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _append_member(zip_path: Path, member: str, payload: bytes) -> None:
    """追加一个新成员。已有同名成员时直接报错，避免 zip 里出现重复条目。"""
    if member in _existing_members(zip_path):
        raise SystemExit(f"{zip_path.name} 里已经有 {member}，本脚本只补缺失的票")
    with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED,
                         compresslevel=exh.ZIP_COMPRESSLEVEL, allowZip64=True) as zf:
        zf.writestr(member, payload)


def _read_member(zip_path: Path, member: str) -> pl.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        raw = zf.read(member)
    return pl.read_csv(io.BytesIO(raw))


def _verify(minute_zip: Path, ticks_zip: Path, symbol: str,
            expect_minute: int | None = None, expect_ticks: int | None = None) -> list[str]:
    """回读两个包里的该成员，核对行数、日期集合与包内是否有重名成员。"""
    member = f"{symbol}.csv"
    problems: list[str] = []
    for path in (minute_zip, ticks_zip):
        names = _existing_members(path)
        if member not in names:
            return [f"{path.name} 里没有 {member}"]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            problems.append(f"{path.name} 出现重复成员 {dup[:3]}")
    minute = _read_member(minute_zip, member)
    ticks = _read_member(ticks_zip, member)
    # 分钟包的 datetime 是字符串, 逐笔包的 date 读回来也是字符串: 统一成 ISO 再比。
    mdays = set(minute["datetime"].cast(pl.String).str.slice(0, 10).unique().to_list())
    tdays = set(ticks["date"].cast(pl.String).str.slice(0, 10).unique().to_list())
    if mdays != tdays:
        problems.append(f"分钟日期集合与逐笔不一致（差 {len(tdays - mdays)} / "
                        f"{len(mdays - tdays)} 天）")
    if expect_minute is not None and minute.height != expect_minute:
        problems.append(f"分钟行数 {minute.height} != {expect_minute}")
    if expect_ticks is not None and ticks.height != expect_ticks:
        problems.append(f"逐笔行数 {ticks.height} != {expect_ticks}")
    print(f"回读：分钟 {minute.height:,} 行 / 逐笔 {ticks.height:,} 笔 / "
          f"{len(mdays)} 个交易日", flush=True)
    print(f"包内成员数：分钟 {len(_existing_members(minute_zip))} / "
          f"逐笔 {len(_existing_members(ticks_zip))}", flush=True)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--symbol", required=True, help="如 601162.SH")
    ap.add_argument("--out", type=Path, default=Path("D:/tsp/backup_export"))
    ap.add_argument("--calendar", type=Path, default=None)
    ap.add_argument("--minute-zip", type=Path, default=None)
    ap.add_argument("--ticks-zip", type=Path, default=None)
    ap.add_argument("--endpoints", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true",
                    help="不抓取，只回读两个包里已有的该成员并核对")
    args = ap.parse_args()

    minute_zip = (args.minute_zip or (args.out / "minute" / exh.MINUTE_TABLE
                                      / f"{args.year}.zip")).resolve()
    ticks_zip = _resolve_ticks_zip(args.year, args.ticks_zip).resolve()
    for path in (minute_zip, ticks_zip):
        if not path.exists():
            raise SystemExit(f"包不存在：{path}")

    if args.verify_only:
        problems = _verify(minute_zip, ticks_zip, args.symbol)
        for item in problems:
            print(f"  ! {item}", flush=True)
        return 0 if not problems else 1

    calendar_dir = (args.calendar or (args.out / "daily" / "kline_daily")).resolve()
    calendar = exh.load_year_calendar(calendar_dir, args.year)
    if not calendar:
        raise SystemExit(f"{args.year} 的日历是空的：{calendar_dir}")
    if args.symbol not in calendar:
        raise SystemExit(f"{args.symbol} 不在 {args.year} 的日K 日历里")

    parsed = qc.split_symbol(args.symbol)
    if parsed is None:
        raise SystemExit(f"无法解析代码：{args.symbol}")
    market, code = parsed

    days = tuple((day, int(day.strftime("%Y%m%d")), vol)
                 for day, vol in calendar[args.symbol])
    print(f"{args.symbol} {args.year}: {len(days)} 个交易日"
          f"（{days[0][0]} ~ {days[-1][0]}）", flush=True)

    targets = exh.probe_days(calendar)
    endpoints = exh.pick_endpoints(args.endpoints, probe_ymds=targets)
    ep = exh.Endpoint(endpoints)
    print(f"主站 {len(endpoints)} 台", flush=True)

    job = exh.Job(args.symbol, market, code, days)
    started = time.time()
    bars, ticks, gaps, error = exh._with_retry(lambda: exh.run_job(job, ep), ep)
    if error:
        raise SystemExit(f"抓取失败：{error}")
    print(f"抓取完成 {time.time() - started:.1f}s：{bars.height:,} 根分钟 / "
          f"{ticks.height:,} 笔逐笔", flush=True)
    if gaps:
        print(f"！没有取到的股票日 {len(gaps)} 个：", flush=True)
        for day, reason in gaps:
            print(f"    ~ {day} {reason[:160]}", flush=True)

    want_days = {day for day, _, _ in days}
    got_days = set(ticks["date"].unique().to_list()) if not ticks.is_empty() else set()
    missing = want_days - got_days
    if missing:
        print(f"！日历里有 {len(missing)} 天没有任何逐笔记录（停牌或取不到），"
              f"最小 {min(missing)} 最大 {max(missing)}", flush=True)

    if args.dry_run:
        print("--dry-run：未写盘。", flush=True)
        return 0

    minute_out = bars.with_columns(
        pl.col("datetime").dt.strftime("%Y-%m-%d %H:%M:%S")
    ).select(MINUTE_COLUMNS)
    tick_out = ticks.select(TICK_COLUMNS)

    member = f"{args.symbol}.csv"
    _append_member(minute_zip, member, minute_out.write_csv().encode("utf-8"))
    _append_member(ticks_zip, member, tick_out.write_csv().encode("utf-8"))
    print(f"已追加成员 {member} 到两个包", flush=True)

    # 回读核对：行数、日期集合、包内成员都必须对得上
    problems = _verify(minute_zip, ticks_zip, args.symbol,
                       expect_minute=minute_out.height, expect_ticks=tick_out.height)
    if problems:
        for item in problems:
            print(f"  ! {item}", flush=True)
        return 1

    print(f"核对通过：分钟 {minute_out.height:,} 行 / 逐笔 {tick_out.height:,} 笔 / "
          f"{len(got_days)} 个交易日", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
