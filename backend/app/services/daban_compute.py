# -*- coding: utf-8 -*-
"""打板接力：全量交易的计算逻辑，由后端 `POST /api/daban-research/rebuild` 调用。

口径与原来的独立实验脚本完全一致，只是从「离线导出 json」改成「后端现算现返回」：
  * 6 个板位 (1..5、>=6) 各自的涨停事件，一律 T+1 开盘买入，算 7 种出场收益；
  * stats = 全部板位合并后的全量交易，一行一笔，**没有任何抽样**；
  * ohlc = 每笔的 17 根真实 K 线窗口，行号与 stats 严格同序（导出前逐根校验过）。

本模块只返回内存结果，不写任何文件；落盘缓存由 daban_research service 负责。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.services import daban_lab as S

# 过滤字段 (前端所有限制开关都只用这些, 加上出场模式选择)
FILTER_COLS = ["ph", "st", "yr", "mktcap", "promo", "seal", "lu",
               "mc", "brk", "gap", "yizi_t", "yizi_b"]
RET_COLS = ["r_t1", "r_t2", "r_t3", "r_t5", "r_sl", "r_3red", "r_trail"]
# bp = 板位, di = 事件日在 calendar 的下标, sym = 股票在 symbols 表的下标,
# boards = 事后连板数 (只用于展示, 不能拿来选股)
STATS_COLS = FILTER_COLS + ["bp"] + RET_COLS + ["di", "sym", "boards"]

PHASE_NAMES = ["冰点", "修复", "点火", "主升", "高潮", "退潮"]
STATE_NAMES = ["强", "震荡", "弱"]
BOARD_POS = [1, 2, 3, 4, 5, 6]
BP_NAMES = {1: "首板", 2: "2板", 3: "3板", 4: "4板", 5: "5板", 6: ">5板"}


def I(x, scale=1, default=-9999):
    v = x * scale
    return int(round(v)) if np.isfinite(v) else default


def build_stats(tr, phases, states, d_i_full, sym_i):
    """全量统计行: 过滤字段 + 7 收益 + 板位 + 事件日下标 + 股票下标, 不带 K 线。"""
    ph = tr["phase"].fillna("").astype(str)
    st = tr["state"].fillna("").astype(str)
    ph_i = ph.map({p: i for i, p in enumerate(phases)}).fillna(-1).astype(int).to_numpy()
    st_i = st.map({s: i for i, s in enumerate(states)}).fillna(-1).astype(int).to_numpy()

    cols = {
        "ph": ph_i, "st": st_i,
        "yr": tr["year"].to_numpy(),
        "mktcap": tr["mktcap"].to_numpy() * 10,
        "promo": tr["promo_rate"].to_numpy() * 1000,
        "seal": tr["seal_rate"].to_numpy() * 1000,
        "lu": tr["limit_up"].to_numpy(),
        "mc": tr["max_consecutive"].to_numpy(),
        "brk": tr["broken_limit"].to_numpy(),
        "gap": tr["gap"].to_numpy() * 10000,
        "yizi_t": tr["yizi_t"].to_numpy(),
        "yizi_b": tr["yizi_b"].to_numpy(),
        "bp": tr["bp"].to_numpy(),
        "di": np.array([d_i_full.get(str(t), -1) for t in tr["t"]], dtype=np.int64),
        "sym": tr["symbol"].map(sym_i).to_numpy(),
        "boards": tr["boards"].to_numpy(),
    }
    for k in RET_COLS:
        cols[k] = tr[k].to_numpy() * 10000

    rows = []
    n = len(tr)
    for i in range(n):
        rows.append([I(cols[c][i]) for c in STATS_COLS])
    return rows


def add_ohlc(rows, cfg, root, dates, files):
    """从原始日线张量还原每一笔的 OHLC 窗口, 并与已有 closes 逐根校验。"""
    cached = getattr(S, "_TENSOR", None)
    if cached is not None:
        SC, sym2i, syms = cached
    else:
        SC, sym2i, syms = S.load_tensor(files, dates)
    d2i = {d: i for i, d in enumerate(dates)}
    wb, wa = cfg["win_before"], cfg["win_after"]
    offs = np.arange(-wb, wa + 1)
    n = len(rows)

    t_idx = np.array([d2i.get(t, -1) for t in rows["t"]], dtype=np.int32)
    s_idx = np.array([sym2i.get(s, -1) for s in rows["symbol"]], dtype=np.int32)
    didx = np.clip(t_idx[:, None] + offs[None, :], 0, len(dates) - 1)
    didx = np.broadcast_to(didx, (n, len(offs))).copy()
    sidx = np.broadcast_to(np.clip(s_idx, 0, len(syms) - 1)[:, None], (n, len(offs))).copy()

    W = SC[sidx, didx, :4].astype(np.float64)
    bad = (t_idx < 0) | (s_idx < 0)
    W[bad] = np.nan
    W = np.where(np.isfinite(W) & (W > 0), W, np.nan)
    ohlc100 = np.round(W * 100)
    closes = np.array(list(rows["closes"]), dtype=np.float64)

    dev = np.abs(ohlc100[:, :, 3] - closes)
    bad_dev = int(np.sum(np.isfinite(dev) & (dev > 1)))
    print("[校验] 张量 close 与权威 closes 偏差 >1 分的根数:", bad_dev,
          " / 总根数", int(np.isfinite(closes).sum()))
    if bad_dev > max(20, n // 500):
        raise RuntimeError("疑似 K 线窗口错位, 拒绝把不真实的 K 线摆上页面")

    C = closes.copy()
    O = ohlc100[:, :, 0]
    H = ohlc100[:, :, 1]
    L = ohlc100[:, :, 2]
    with np.errstate(invalid="ignore"):
        H = np.fmax(H, np.fmax(O, C))
        L = np.fmin(L, np.fmin(O, C))
    valid = np.isfinite(C) & (C > 0) & np.isfinite(O) & (O > 0)
    illogic = int(np.sum(valid & np.isfinite(H) & np.isfinite(L) & (H < L)))
    print("[校验] K 线影线不自洽的根数:", illogic)
    if illogic > 0:
        raise RuntimeError("K 线影线异常, 拒绝导出")

    flat = np.stack([O, H, L, C], axis=2)
    flat = np.where(np.isfinite(flat) & (flat > 0), flat, -1)
    return flat.astype(np.int32).reshape(n, -1)


def compute(data_root: str) -> tuple[dict, np.ndarray]:
    """按当前本地日线重算全部板位的全量交易。返回 (payload, ohlc)。

    data_root 是仓库根目录 (其下为 data/)，后端运行时从 repo.store.data_dir 推导。
    """
    cfg = dict(S.CONFIG)
    cfg["data_root"] = data_root
    # 每次重算都重新读日线，不能复用上一次的张量缓存，否则行情更新后结果不刷新。
    S._TENSOR = None

    tr_list = []
    pos_counts = {}
    for bp in BOARD_POS:
        tr, cfg, modes = S.build_trades(cfg, board_pos=bp)
        tr_list.append(tr)
        pos_counts[bp] = len(tr)
        print("[打板] %s 全量交易 %d 笔" % (BP_NAMES[bp], len(tr)))
    tr = pd.concat(tr_list, ignore_index=True)
    print("[打板] 合并全量交易", len(tr), "笔; 出场模式", list(modes))

    scan = S.scan_all(tr, modes)
    # 连续交易日历，不能用事件首板日集合，否则 K 线窗口会错位。
    cal, files = S.load_calendar(data_root, cfg["start"])

    symbols = sorted(tr["symbol"].unique().tolist())
    sym_i = {s: i for i, s in enumerate(symbols)}
    names = tr.drop_duplicates("symbol").set_index("symbol")["name"].to_dict()
    name_list = [names.get(s, s) for s in symbols]
    d_i_full = {d: i for i, d in enumerate(cal)}

    phases = ["ice", "repair", "ignite", "rally", "climax", "ebb"]
    states = ["strong", "range", "weak"]
    print("[打板] 组装全量统计行 ...")
    stats = build_stats(tr, phases, states, d_i_full, sym_i)

    print("[打板] 还原全量真实 OHLC ...")
    ohlc_arr = add_ohlc(tr, cfg, data_root, cal, files)
    if len(ohlc_arr) != len(stats):
        raise RuntimeError("K 线与统计行数不一致, 行号会错位")

    ohlc_n = (cfg["win_before"] + cfg["win_after"] + 1) * 4
    # baseline 与 scan / stats 全部出自本次同一次计算, 不读旧的 lab_data.json。
    baseline = {m: S.summarize(tr[m]) for m in modes}
    caveats = [
        "买不到就买不到: 涨停当天买不到, 一律按次日开盘价买入; 收益按前复权收盘价算, 未扣手续费与滑点。",
        "「最终几连板 / 持续几天」是买完之后才走出来的结果, 只用于复盘展示, 绝不能拿来当买入条件 —— 那样等于用未来数据作弊。",
        "情绪相位是收盘后按连板梯队算的日频标签, 抓不到盘中「分歧 / 加速」那一刻; Phase 0 已验证「点火/主升才买」这道门没有超额。相位在这里只作分档观察, 不预设它能赚钱。",
        "「隔夜高开」是次日集合竞价就能看到的价格, 高开太多就不挂单, 可执行、不是未来函数。止损按跌破买入价 5% 时以 -5% 成交近似, 未计跳空滑点。",
        "市值是「前复权价 × 最新总股本」的估算 (股本只有最新快照), 只用于分档过滤, 不是精确历史市值。",
        "一字板不再剔除、始终计入: 其中部分是涨停当天与次日一字封死、实际根本买不到的 (卡片标「一字买不进」), 数字已含它们, 看胜率时心里有数。已剔除上市不足 4 日的新股与涨跌停约束外的异常记录。",
        "按日统计图按「涨停当天」归集全量笔数 (不是买入日), 数据与总账同源; 某段时间笔数很少时, 平均收益容易被个别极端案例带偏, 看柱子高低一起判断。",
        "下面列表就是全部 %d 笔交易本身, 没有抽样、没有挑选 —— 点某一天就看到那一天的全部笔数, 一条不少。" % len(tr),
    ]
    payload = {
        "meta": {
            "title": "打板接力研究",
            "n_all": int(len(tr)),
            "start": cfg["start"],
            "exit_modes": modes,
            "phases": phases, "phase_names": PHASE_NAMES,
            "states": states, "state_names": STATE_NAMES,
            "pos_names": [BP_NAMES[b] for b in BOARD_POS],
            "pos_counts": [pos_counts[b] for b in BOARD_POS],
            "symbols": symbols, "name_list": name_list,
            "calendar": cal,
            "win_before": cfg["win_before"], "win_after": cfg["win_after"],
            "stats_cols": STATS_COLS,
            "ohlc_n": ohlc_n,
            "stop_loss": cfg["stop_loss"],
            "caveats": caveats,
        },
        "baseline": baseline,
        "scan": scan,
        "stats": stats,
    }
    return payload, ohlc_arr
