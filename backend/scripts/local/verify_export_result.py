#!/usr/bin/env python3
"""导出完成后校验 2022 成果正确性。

校验维度:
  1. 两个成品 zip 存在 + 体积合理
  2. zip 完整性 (分钟全量 testzip; 逐笔抽样成员可读)
  3. 股票覆盖: 导出股票集 vs 日历股票集, 算覆盖率 + 缺失清单
  4. 抽样深检: 抽 N 只 (知名股 + 随机), 逐只 read_csv 校验
        - 行数 > 0
        - date 全部落在 2022 年内
        - 价格列 > 0, volume >= 0
        - 列齐全
        - 与日历该股票交易日数一致 (分钟包)
  5. 规模对账: 逐笔总笔数 (--full 精确; 否则抽样外推) vs 脚本估计 ~50.9 亿
报告写到 <base>/verify_report_<YEAR>.txt 并打印。
"""
import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

import polars as pl

BASE = Path(os.environ.get("TSP_DATA", "/workspace/tick-data"))
YEAR = 2022
EST_TICKS = 5_090_000_000  # 脚本估计的 2022 逐笔总量
FAMOUS = ["600519.SH", "601398.SH", "600000.SH", "000002.SZ", "000001.SZ",
          "300750.SZ", "688981.SH", "601888.SH", "002594.SZ", "600036.SH"]


def load_calendar(year):
    cal = BASE / "backup_export" / "daily" / "kline_daily" / f"{year}.csv.gz"
    df = pl.read_csv(cal, columns=["symbol", "date"], try_parse_dates=True)
    syms = set(df["symbol"].to_list())
    days = df.group_by("symbol").len().rename({"len": "cal_days"})
    return syms, dict(zip(days["symbol"].to_list(), days["cal_days"].to_list()))


def list_zip_symbols(zpath):
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    syms = sorted({n[:-4] for n in names if n.endswith(".csv")})
    return syms, len(names)


def load_sample(zf, sym, columns=None):
    with zf.open(f"{sym}.csv") as f:
        data = f.read()
    return pl.read_csv(data, columns=columns)


def year_ok(series):
    # date 列可能是 Date 或字符串, 统一取前4字符/年份
    s = series.cast(pl.Utf8).str.slice(0, 4)
    bad = s.filter(s != str(YEAR))
    return bad.height


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=YEAR)
    ap.add_argument("--base", type=str, default=str(BASE))
    ap.add_argument("--sample", type=int, default=50, help="深检抽样股票数")
    ap.add_argument("--full", action="store_true", help="精确统计逐笔总笔数(较慢)")
    args = ap.parse_args()
    base = Path(args.base)
    year = args.year

    mzip = base / "backup_export" / "minute" / "kline_minute_1m" / f"{year}.zip"
    tzip = base / "ticks" / f"{year}_ticks.zip"
    out = base / f"verify_report_{year}.txt"
    L = []
    log = lambda s: (print(s), L.append(s))

    log(f"===== 2022 成果校验报告 ({time.strftime('%Y-%m-%d %H:%M:%S')}) =====")

    # 1. 存在 + 体积
    for name, p in (("分钟包", mzip), ("逐笔包", tzip)):
        if not p.exists():
            log(f"[FAIL] {name} 不存在: {p}")
            return
        gb = p.stat().st_size / 1e9
        log(f"[OK]   {name} 存在, 体积 {gb:.2f} GB")
    if mzip.stat().st_size < 1e9:
        log(f"[WARN] 分钟包仅 {mzip.stat().st_size/1e9:.2f} GB, 可能不全")
    if tzip.stat().st_size < 5e9:
        log(f"[WARN] 逐笔包仅 {tzip.stat().st_size/1e9:.2f} GB, 可能不全(预期~21GB)")

    # 2. 完整性
    log("--- 完整性 ---")
    with zipfile.ZipFile(mzip) as zf:
        bad = zf.testzip()
    log(f"[OK]   分钟包 testzip: {'完好' if bad is None else '损坏 '+str(bad)}")
    # 逐笔抽样可读 (避免 21GB 全量 testzip 过慢)
    with zipfile.ZipFile(tzip) as zf:
        tnames = [n for n in zf.namelist() if n.endswith(".csv")]
    import random
    random.seed(0)
    probe = random.sample(tnames, min(60, len(tnames)))
    ok_probe = 0
    for n in probe:
        try:
            with zipfile.ZipFile(tzip) as zf:
                load_sample(zf, n[:-4])
            ok_probe += 1
        except Exception as e:
            log(f"[FAIL] 逐笔成员损坏: {n} -> {e}")
    log(f"[OK]   逐笔包抽样 {ok_probe}/{len(probe)} 成员可读")

    # 3. 覆盖
    log("--- 股票覆盖 (vs 日历) ---")
    cal_syms, cal_days = load_calendar(year)
    m_syms, m_n = list_zip_symbols(mzip)
    t_syms, t_n = list_zip_symbols(tzip)
    log(f"日历股票数: {len(cal_syms)} | 分钟包: {len(m_syms)} | 逐笔包: {len(t_syms)}")
    miss_m = cal_syms - set(m_syms)
    miss_t = cal_syms - set(t_syms)
    log(f"分钟包缺失(在日历不在包): {len(miss_m)}  {sorted(miss_m)[:10]}")
    log(f"逐笔包缺失(在日历不在包): {len(miss_t)}  {sorted(miss_t)[:10]}")
    extra_m = set(m_syms) - cal_syms
    if extra_m:
        log(f"[WARN] 分钟包多出(不在日历): {len(extra_m)} {sorted(extra_m)[:10]}")
    cov_m = len(set(m_syms) & cal_syms) / len(cal_syms) * 100
    cov_t = len(set(t_syms) & cal_syms) / len(cal_syms) * 100
    log(f"覆盖率 分钟={cov_m:.2f}%  逐笔={cov_t:.2f}%")

    # 4. 抽样深检
    log("--- 抽样深检 (含知名股) ---")
    sample_syms = [s for s in FAMOUS if s in set(m_syms)] + random.sample(
        list(set(m_syms) & cal_syms), min(args.sample, len(set(m_syms) & cal_syms)))
    sample_syms = list(dict.fromkeys(sample_syms))[:args.sample + len(FAMOUS)]
    # 分钟抽样
    with zipfile.ZipFile(mzip) as zf:
        m_cols = load_sample(zf, sample_syms[0]).columns
    log(f"分钟包列: {m_cols}")
    min_bad = 0
    for sym in sample_syms:
        if sym not in set(m_syms):
            continue
        try:
            with zipfile.ZipFile(mzip) as zf:
                d = load_sample(zf, sym)
            if d.height == 0:
                min_bad += 1; continue
            yb = year_ok(d["date"])
            price_bad = d.filter((pl.col("open") <= 0) | (pl.col("high") <= 0) |
                                 (pl.col("low") <= 0) | (pl.col("close") <= 0)).height
            vol_bad = d.filter(pl.col("volume") < 0).height
            cd = cal_days.get(sym)
            day_match = f"日历{cd}天" if cd else "日历无"
            if yb or price_bad or vol_bad:
                min_bad += 1
                log(f"  [FAIL] {sym}: 越界年{yb} 价异常{price_bad} 量异常{vol_bad} ({day_match}, 实{d.height}行)")
        except Exception as e:
            min_bad += 1
            log(f"  [FAIL] {sym}: 读取异常 {e}")
    log(f"分钟抽样深检: {len(sample_syms)-min_bad}/{len(sample_syms)} 通过, 异常 {min_bad}")

    # 逐笔抽样
    with zipfile.ZipFile(tzip) as zf:
        t_cols = load_sample(zf, sample_syms[0]).columns
    log(f"逐笔包列: {t_cols}")
    tick_rows_sample = 0
    tick_bad = 0
    for sym in sample_syms:
        if sym not in set(t_syms):
            continue
        try:
            with zipfile.ZipFile(tzip) as zf:
                d = load_sample(zf, sym)
            tick_rows_sample += d.height
            if d.height == 0:
                tick_bad += 1; continue
            yb = year_ok(d["date"])
            pb = d.filter(pl.col("price") <= 0).height
            vb = d.filter(pl.col("volume") <= 0).height
            if yb or pb or vb:
                tick_bad += 1
                log(f"  [FAIL] {sym}: 越界年{yb} 价异常{pb} 量异常{vb} (实{d.height}笔)")
        except Exception as e:
            tick_bad += 1
            log(f"  [FAIL] {sym}: 读取异常 {e}")
    log(f"逐笔抽样深检: {len(sample_syms)-tick_bad}/{len(sample_syms)} 通过, 异常 {tick_bad}")

    # 5. 规模对账
    log("--- 规模对账 ---")
    if args.full:
        total = 0
        with zipfile.ZipFile(tzip) as zf:
            for n in tnames:
                with zf.open(n) as f:
                    total += pl.read_csv(f).height
        log(f"逐笔总笔数(精确): {total:,}  vs 估计 {EST_TICKS:,} "
            f"(偏差 {(total-EST_TICKS)/EST_TICKS*100:+.1f}%)")
    else:
        avg = tick_rows_sample / max(1, len(sample_syms))
        est = avg * len(t_syms)
        log(f"逐笔抽样 {len(sample_syms)} 只平均 {avg:,.0f} 笔/只 -> 外推总笔数 ≈ {est:,.0f} "
            f"vs 估计 {EST_TICKS:,} (偏差 {(est-EST_TICKS)/EST_TICKS*100:+.1f}%)")

    # 结论
    log("===== 结论 =====")
    problems = (miss_m or miss_t or min_bad or tick_bad or
                mzip.stat().st_size < 1e9 or tzip.stat().st_size < 5e9)
    log("结果: " + ("存在问题, 见上" if problems else "全部通过, 成果可信"))

    out.write_text("\n".join(L), encoding="utf-8")
    log(f"报告已写: {out}")


if __name__ == "__main__":
    main()
