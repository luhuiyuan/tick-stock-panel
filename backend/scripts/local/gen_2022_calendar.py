#!/usr/bin/env python3
# 仅用仓库自带 qatdx 客户端，生成 2022 年日K 日历文件 2022.csv.gz。
# 用途：充当 export_history_minute.py 的「日历」前置（决定 2022 哪些股票在市、哪些交易日）。
# 不依赖项目 data/ 维表，直接从通达信主站取数。
import os
import re
import sys
import time
from pathlib import Path

import polars as pl

import app.plugins.qatdx.client as qc

YEAR = 2022
OUT = Path(os.environ.get("TSP_DATA", "/workspace/tick-data")) / "backup_export/daily/kline_daily/2022.csv.gz"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0  # 0 = 全部

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


def main():
    stocks = collect_symbols()
    if LIMIT:
        stocks = stocks[:LIMIT]
    print(f"待抓取股票数: {len(stocks)}", flush=True)

    rows = []
    done = fail = 0
    t0 = time.time()
    for market, code, sym in stocks:
        try:
            # 取最近 1250 根日K（category=9），足够覆盖回 2022-01
            bars = qc.stock_bars(market, code, 9, 1250)
        except Exception:  # noqa: BLE001
            fail += 1
            done += 1
            continue
        for b in bars:
            if int(b.get("year") or 0) != YEAR:
                continue
            try:
                d = f"{int(b['year'])}-{int(b['month']):02d}-{int(b['day']):02d}"
            except Exception:  # noqa: BLE001
                continue
            vol = b.get("vol")
            rows.append({
                "symbol": sym,
                "date": d,
                "open": float(b["open"]) if b.get("open") is not None else None,
                "high": float(b["high"]) if b.get("high") is not None else None,
                "low": float(b["low"]) if b.get("low") is not None else None,
                "close": float(b["close"]) if b.get("close") is not None else None,
                "volume": float(vol) if vol is not None else None,
                "amount": float(b["amount"]) if b.get("amount") is not None else None,
            })
        done += 1
        if done % 100 == 0 or done == len(stocks):
            rate = done / max(time.time() - t0, 1e-6)
            print(f"  {done}/{len(stocks)} 行数={len(rows):,} 失败={fail} ({rate:.1f}/s)", flush=True)

    if not rows:
        print("没有抓到任何 2022 日K，退出", flush=True)
        sys.exit(1)

    df = pl.DataFrame(rows).sort(["symbol", "date"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(OUT, compression="gzip")
    print(f"写出 {OUT}: {df.height:,} 行, {df['symbol'].n_unique()} 只股票", flush=True)


if __name__ == "__main__":
    main()
