#!/usr/bin/env python3
# 用仓库自带 qatdx 客户端，生成 1990 年以来的全量日K「日历」文件。
# 每只股票抓全部历史日K(上市日->至今)，按年份拆分写到:
#   <out>/daily/kline_daily/<YEAR>.csv.gz
# 不依赖项目 data/ 维表，直接从通达信主站取数。
# 用法: uv run --no-sync python gen_calendar_all.py [LIMIT]
#   LIMIT=0(默认) 抓全部；>0 仅前 N 只(调试用)
import gzip
import os
import re
import sys
import time
from pathlib import Path

import app.plugins.qatdx.client as qc

START_YEAR = 1990
END_YEAR = 2026  # 当前年份(含)
OUT_DIR = Path(os.environ.get("TSP_DATA", "/workspace/tick-data")) / "backup_export/daily/kline_daily"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0

_HEADER = "symbol,date,open,high,low,close,volume,amount"

# 只保留 A 股股票，排除指数(39xxxx/000 上证指数等)/基金(15/16/18/5xxxx)/转债。
_SZ_RE = re.compile(r"^(000|001|002|003|300|301)\d{3}$")  # 深市主板 + 创业板
_SH_RE = re.compile(r"^(600|601|603|605|688|689)\d{3}$")  # 沪市主板 + 科创板


def collect_symbols():
    stocks = []
    for market in (0, 1):  # 0=深, 1=沪
        try:
            lst = qc.security_list(market)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] security_list market={market} 失败: {exc}", flush=True)
            continue
        for r in lst or []:
            code = str(r.get("code") or "").strip()
            if not code or len(code) != 6:
                continue
            if market == 0 and not _SZ_RE.match(code):
                continue
            if market == 1 and not _SH_RE.match(code):
                continue
            sym = qc.to_symbol(market, code)
            if sym:
                stocks.append((market, code, sym))
    return stocks


def fnum(v):
    if v is None:
        return ""
    f = float(v)
    if f == int(f) and abs(f) < 1e15:
        return f"{int(f)}.0"
    return repr(f)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 清理旧的全量/单年日历(只删 4 位年份文件)，避免与本次生成的重复/残留
    removed = 0
    for p in OUT_DIR.glob("????.csv.gz"):
        p.unlink()
        removed += 1
    if removed:
        print(f"已清理旧日历文件 {removed} 个", flush=True)

    stocks = collect_symbols()
    if LIMIT:
        stocks = stocks[:LIMIT]
    print(f"待抓取股票数: {len(stocks)}  (START={START_YEAR}, END={END_YEAR})", flush=True)

    handles = {}  # year -> gzip text handle

    def get_handle(year: int):
        h = handles.get(year)
        if h is None:
            path = OUT_DIR / f"{year}.csv.gz"
            if path.exists():
                h = gzip.open(path, "at", encoding="utf-8", newline="")
            else:
                h = gzip.open(path, "wt", encoding="utf-8", newline="")
                h.write(_HEADER + "\n")
            handles[year] = h
        return h

    total_rows = 0
    done = fail = 0
    t0 = time.time()
    per_year = {}  # year -> 行数
    for market, code, sym in stocks:
        try:
            # 取全部历史日K(category=9)，cap=20800 足够覆盖 1990->2026
            bars = qc.stock_bars(market, code, 9, 20000)
        except Exception:  # noqa: BLE001
            fail += 1
            done += 1
            continue
        for b in bars:
            y = int(b.get("year") or 0)
            if y < START_YEAR or y > END_YEAR:
                continue
            mo = int(b.get("month") or 0)
            d = int(b.get("day") or 0)
            if not (mo and d):
                continue
            line = (
                f"{sym},{y:04d}-{mo:02d}-{d:02d},"
                f"{fnum(b.get('open'))},{fnum(b.get('high'))},"
                f"{fnum(b.get('low'))},{fnum(b.get('close'))},"
                f"{fnum(b.get('vol'))},{fnum(b.get('amount'))}\n"
            )
            get_handle(y).write(line)
            total_rows += 1
            per_year[y] = per_year.get(y, 0) + 1
        done += 1
        if done % 100 == 0 or done == len(stocks):
            rate = done / max(time.time() - t0, 1e-6)
            print(f"  {done}/{len(stocks)} 总行数={total_rows:,} 失败={fail} ({rate:.1f}/s)", flush=True)

    for h in handles.values():
        h.close()

    if total_rows == 0:
        print("没有抓到任何日K，退出", flush=True)
        sys.exit(1)

    print("\n=== 各年份行数 ===", flush=True)
    for y in sorted(per_year):
        print(f"  {y}: {per_year[y]:,} 行", flush=True)
    print(f"\n完成: 共 {total_rows:,} 行，写出 {len(per_year)} 个年份文件到 {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
