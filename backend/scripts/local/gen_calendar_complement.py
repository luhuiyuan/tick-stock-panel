#!/usr/bin/env python3
# 补跑：重新收集符号，找出已写入年份文件中「缺失」的那些，带空结果重试重新抓取并追加。
# 仅处理缺失符号，不会重复已写入的数据。用法: uv run --no-sync python gen_calendar_complement.py [LIMIT]
import glob
import gzip
import os
import sys
import time
from pathlib import Path

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_calendar_all as g  # 复用 collect_symbols / fnum / 常量

OUT_DIR = g.OUT_DIR
START_YEAR = g.START_YEAR
END_YEAR = g.END_YEAR
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0
_HEADER = g._HEADER


def fetch_with_retry(market, code, tries=3):
    best = []
    for t in range(tries):
        try:
            bars = g.qc.stock_bars(market, code, 9, 20000)
        except Exception:  # noqa: BLE001
            bars = []
        if bars:
            return bars
        # 空结果：可能是选中了无数据/抽风的主站，短等后重试(下次可能换主站)
        time.sleep(1.0)
    return best  # 仍为空


def main():
    collected = g.collect_symbols()
    existing = set()
    for p in glob.glob(str(OUT_DIR / "????.csv.gz")):
        d = pl.read_csv(p, columns=["symbol"])
        existing |= set(d["symbol"].to_list())
    missing = [(m, c, s) for (m, c, s) in collected if s not in existing]
    if LIMIT:
        missing = missing[:LIMIT]
    print(f"已写入唯一: {len(existing)}  待补: {len(missing)}", flush=True)

    handles = {}

    def get_handle(year):
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
    for market, code, sym in missing:
        bars = fetch_with_retry(market, code)
        if not bars:
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
                f"{g.fnum(b.get('open'))},{g.fnum(b.get('high'))},"
                f"{g.fnum(b.get('low'))},{g.fnum(b.get('close'))},"
                f"{g.fnum(b.get('vol'))},{g.fnum(b.get('amount'))}\n"
            )
            get_handle(y).write(line)
            total_rows += 1
        done += 1
        if done % 100 == 0 or done == len(missing):
            rate = done / max(time.time() - t0, 1e-6)
            print(f"  {done}/{len(missing)} 新增行={total_rows:,} 失败={fail} ({rate:.1f}/s)", flush=True)

    for h in handles.values():
        h.close()
    print(f"\n补跑完成: 新增 {total_rows:,} 行, 仍失败 {fail} 只", flush=True)


if __name__ == "__main__":
    main()
