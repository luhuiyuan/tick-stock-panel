"""验证布林带「三不碰」三个回避信号的有效性。

对 3 个信号做前瞻表现检验，口径：
  信号命中日 → 次日开盘买入（前复权 open）→ T+1/T+5/T+10 收盘卖出。
同时输出「命中组 vs 同日未命中组」的收益差，用来判断回避是否有道理
（差值越负，说明命中后表现越差，回避越有效）。

用法：
  cd backend
  .venv/bin/python scripts/verify_boll_three_avoid_signals.py [交易日数，默认 260]

说明：
  - 走的是与面板一致的 compute_indicators → compute_signals 真实链路；
  - 用了 boll_width / ma20_bias 因子，因此不能被 signal_forward_performance
    的审核白名单接口直接使用（那套接口只放行已审核因子）；本脚本显式放宽，
    专门用于评估这三个新增信号。
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.indicators.pipeline import compute_indicators, compute_signals  # noqa: E402

TARGETS = [
    "csg_boll_squeeze_warning",
    "csg_boll_mid_breakdown",
    "csg_ma20_bias_over_10",
]
NAMES = {
    "csg_boll_squeeze_warning": "布林缩口观望",
    "csg_boll_mid_breakdown": "有效下穿布林中轨",
    "csg_ma20_bias_over_10": "MA20乖离超10%",
}
HORIZONS = (1, 5, 10)
COLS = [
    "symbol", "date", "open", "high", "low", "close", "raw_open",
    "raw_close", "raw_high", "raw_low", "volume", "amount",
]


def load_from_files(files: list[str]) -> pl.DataFrame:
    parts = []
    for f in files:
        d = pl.read_parquet(f)
        have = [c for c in COLS if c in d.columns]
        parts.append(d.select(have))
    return (
        pl.concat(parts, how="diagonal_relaxed")
        .sort(["symbol", "date"])
        .with_columns(pl.col("date").cast(pl.Date))
    )


def load_frame(days: int) -> pl.DataFrame:
    files = sorted(glob.glob(str(ROOT / "../data/kline_daily_enriched/date=*/part.parquet")))[-days:]
    if not files:
        raise SystemExit("未找到 kline_daily_enriched 数据分区")
    return load_from_files(files)


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 260
    frame = load_frame(days)
    print(f"行情: {frame.height} 行, {frame['symbol'].n_unique()} 只, {frame['date'].n_unique()} 日")

    frame = compute_indicators(frame)
    frame = compute_signals(frame, needed=set(TARGETS), attach_extensions=False)
    avail = [c for c in TARGETS if c in frame.columns]
    if not avail:
        raise SystemExit("信号未注入成功，请检查定义目录与因子依赖")
    ndays = frame["date"].n_unique()

    # 统一口径：次日开盘买入 → T+N 收盘卖出（均为前复权价）
    frame = frame.with_columns(
        [pl.col("open").shift(-1).over("symbol").alias("entry")]
        + [pl.col("close").shift(-(h + 1)).over("symbol").alias(f"exit_{h}") for h in HORIZONS]
    )
    for h in HORIZONS:
        frame = frame.with_columns((pl.col(f"exit_{h}") / pl.col("entry") - 1).alias(f"ret_{h}"))

    bench = frame.group_by("date").agg(
        [pl.col(f"ret_{h}").mean().alias(f"bench_{h}") for h in HORIZONS]
    )

    print()
    print("=== 前瞻表现（次日开盘买入 → T+N 收盘卖出）===")
    print(f"{'信号':<18}{'窗口':<6}{'命中':<8}{'日均':<8}{'平均收益%':<10}{'胜率%':<7}{'市场%':<8}{'超额%':<8}{'超额胜率%'}")
    print("-" * 92)
    for c in avail:
        hit = frame.filter(pl.col(c) == True).join(bench, on="date", how="left")  # noqa: E712
        daily = hit.height / ndays
        for h in HORIZONS:
            v = hit.filter(pl.col(f"ret_{h}").is_finite())
            if v.height == 0:
                continue
            r, b = v[f"ret_{h}"], v[f"bench_{h}"]
            ex = r - b
            print(
                f"{NAMES[c]:<18}{'T+' + str(h):<6}{v.height:<8}{daily:<8.1f}"
                f"{r.mean() * 100:<10.2f}{(r > 0).mean() * 100:<7.1f}"
                f"{b.mean() * 100:<8.2f}{ex.mean() * 100:<8.2f}{(ex > 0).mean() * 100:.1f}"
            )

    print()
    print("=== 命中组 vs 同日未命中组（T+10，差值越负=回避越有效）===")
    print(f"{'信号':<18}{'命中组%':<10}{'未命中组%':<11}{'差值%':<9}{'命中胜率%':<11}{'未命中胜率%'}")
    print("-" * 78)
    h = 10
    valid = frame.filter(pl.col(f"ret_{h}").is_finite())
    for c in avail:
        hit = valid.filter(pl.col(c) == True)[f"ret_{h}"]  # noqa: E712
        miss = valid.filter(pl.col(c) != True)[f"ret_{h}"]  # noqa: E712
        print(
            f"{NAMES[c]:<18}{hit.mean() * 100:<10.2f}{miss.mean() * 100:<11.2f}"
            f"{(hit.mean() - miss.mean()) * 100:<9.2f}"
            f"{(hit > 0).mean() * 100:<11.1f}{(miss > 0).mean() * 100:.1f}"
        )
    print()
    print("全样本命中率:")
    for c in avail:
        rate = frame.filter(pl.col(c) == True).height / frame.height * 100  # noqa: E712
        print(f"  {NAMES[c]}: {rate:.1f}%")

    if "--oos" in sys.argv:
        print()
        print("=== 分段稳健性（早期/中期/最近，T+10 命中组-未命中组，越负越有效）===")
        all_files = sorted(glob.glob(str(ROOT / "../data/kline_daily_enriched/date=*/part.parquet")))
        segments = [
            ("早期", all_files[-540:-800:-1][::-1]),
            ("中期", all_files[-540:-260]),
            ("最近", all_files[-260:]),
        ]
        for c in avail:
            print(f"  --- {NAMES[c]} ---")
            for name, sel in segments:
                if not sel:
                    continue
                seg = compute_indicators(load_from_files(sel))
                seg = compute_signals(seg, needed=set(TARGETS), attach_extensions=False)
                seg = seg.with_columns(
                    [pl.col("open").shift(-1).over("symbol").alias("entry")]
                    + [pl.col("close").shift(-11).over("symbol").alias("exit10")]
                ).with_columns((pl.col("exit10") / pl.col("entry") - 1).alias("ret10"))
                v = seg.filter(pl.col("ret10").is_finite())
                hit = v.filter(pl.col(c) == True)["ret10"]  # noqa: E712
                miss = v.filter(pl.col(c) != True)["ret10"]  # noqa: E712
                if hit.len() == 0:
                    continue
                print(
                    f"    {name:<6} 命中率{hit.len() / v.height * 100:5.1f}% "
                    f"| 命中{hit.mean() * 100:6.2f}% | 未命中{miss.mean() * 100:6.2f}% "
                    f"| 差{(hit.mean() - miss.mean()) * 100:6.2f}%"
                )


if __name__ == "__main__":
    main()
