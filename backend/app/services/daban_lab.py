# -*- coding: utf-8 -*-
"""
方新侠「龙头 / 连板 / 情绪周期」体系 —— 打板接力的底层计算 (后端服务内副本)

本文件由研究实验脚本搬进 app/services/, 供 daban_compute.compute() 调用。
文件末尾的 main() 是原来独立跑实验页的入口, 服务端不会调用它; 真正入口是
POST /api/daban-research/rebuild。口径与实验脚本保持一致, 不要只改其中一份。

================================================================================
这份脚本把那套主观心法翻译成一条能跑、能看、能改的基线策略, 目的不是证明它赚钱,
而是把每一笔交易摊开给你看: 为什么买、为什么卖、当时市场什么状态、赚了还是亏了。
你可以据此判断该加什么限制条件。配套页面 index.html 里的每个开关都对应这里的字段。

--------------------------------------------------------------------------------
一、体系条款 -> 代码规则的对照 (哪些能写, 哪些不能写)
--------------------------------------------------------------------------------
  龙头至上 / 只做最强        -> 无法直接写。基线用「全市场涨停事件」当标的池 (首板 / 2板 / 3板
                                可选, 板位只是入场锚点不同), 把「哪只是龙头」留给后续选股因子
                                (本页可看事后几连板)。
  分歧买 / 加速持 / 高潮离    -> 【Phase 0 已证伪, 默认关闭】。系统 phase 是收盘后按
                                连板梯队算的日频标签, 抓不到盘中「分歧」那一刻。
                                页面里做成可选开关, 由你自己试, 不写死进策略。
  退潮即断腕                -> 同上, 默认关闭。
  三日不红即斩              -> 出场模式 r_3red (买入后 3 日不涨就走)。
  单笔亏损 5% 强制止损       -> 出场模式 r_sl (5% 止损 + 最多持有 3 日)。
  格局锁仓 / 持有到趋势结束  -> 出场模式 r_t5 / r_trail (持有 5 日 / 移动止盈)。
  非两百亿不沾              -> 页面「市值下限」滑块 (默认 0 = 不启用)。
  永远不满仓 / 默念10秒      -> 人为纪律, 不可编码, 不进策略。

--------------------------------------------------------------------------------
二、防未来函数 (写死在脚本里, 不靠自觉)
--------------------------------------------------------------------------------
  1. T 日 = 事件库里的首板日 (start_date)。买入一律在 T+1 开盘, 因为 T 日涨停价
     买不到 —— 这是整套口径最关键的一条, 也和 Phase 0 的「可成交」验证一致。
  2. 所有入场特征只取 T 日及之前的全市场统计 (regime_history 当日行), 不取未来。
  3. 每笔交易都附带 boards / days (这只票后来走出了几连板), 但这是【事后标签】,
     只用于复盘展示, 绝不参与任何入场过滤或统计口径, 页面会显著标红提示。
  4. T 日一字板、T+1 一字板都单独标记, 默认从可交易样本里排除 (因为买不到)。
  5. 收益基于前复权 close, 买卖同口径, 未扣手续费/滑点 (页面有提示)。

--------------------------------------------------------------------------------
三、数据 (全部来自本地 tick-stock-panel, 不依赖 pkl, 不依赖历史分钟回填)
--------------------------------------------------------------------------------
  limit_up_research/streaks.parquet  首板事件 (10.8 万条, 每条 start_date 都是首板日)
  kline_daily_enriched/date=*        前复权 OHLC + raw_close + 连板数 + 换手率
  regime_history/part.parquet        每日情绪相位与连板梯队统计 (31 字段)
  instruments/instruments.parquet    股本 (上一轮治本补全过), 用于算市值

用法:
    python3 strategy_lab.py            # 跑回测 + 生成 lab_data.json + index.html
    python3 strategy_lab.py --html     # 复用已有 lab_data.json, 只重新生成页面
================================================================================
"""

import json
import glob
import sys
import os
import bisect
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import warnings

warnings.filterwarnings("ignore", category=RuntimeWarning, message="All-NaN slice encountered")

HERE = os.path.dirname(os.path.abspath(__file__))

# ============================ 参数区 (改这里就能改策略) ============================
CONFIG = {
    "data_root": "/workspace/tick-stock-panel",
    "start": "2019-01-01",        # 只回测这之后 (早年情绪标签失真, Phase 0 结论)
    "win_before": 6,              # 画图窗口: T 之前几个交易日
    "win_after": 10,              # 画图窗口: T 之后几个交易日
    "stop_loss": 0.05,            # 单笔强制止损
    "trail_drawdown": 0.03,       # 移动止盈回撤
    "hold_max": 5,                # 最长持有交易日
    "three_red_days": 3,          # 「三日不红」观察天数
    "yizi_amp": 0.002,            # 一字板判定: 振幅小于这个值
    "limit_ratio": 1.08,          # 涨停判定: 相对前收涨幅下限 (兼容 20cm/ST)
    "case_sample": 12000,         # 页面内嵌的分层案例池大小
    "force_extreme": 400,         # 强制纳入收益最好/最差各多少笔
}

# 出场模式: key -> 页面下拉项
EXIT_MODES = {
    "r_t1":    "T+1 收盘卖 (最短持有, Phase 0 基准)",
    "r_t2":    "T+2 收盘卖",
    "r_t3":    "T+3 收盘卖",
    "r_t5":    "T+5 收盘卖 (格局锁仓)",
    "r_sl":    "5% 止损 + 最多持有 3 日 (铁律)",
    "r_3red":  "三日不红即斩 (红了就持到 T+5)",
    "r_trail": "移动止盈 3% + 最多持有 5 日",
}

# 行情字段在张量里的位置
F = {"open": 0, "high": 1, "low": 2, "close": 3, "raw_close": 4,
     "amount": 5, "turnover_rate": 6, "clu": 7}
FLOAT_COLS = ["open", "high", "low", "close", "raw_close", "amount",
              "turnover_rate", "consecutive_limit_ups"]


# ================================ 1. 读数据 ================================

def load_calendar(root, start):
    """交易日历 = 日线分区目录名; 张量按这个索引对齐"""
    dirs = sorted(glob.glob(os.path.join(root, "data/kline_daily_enriched/date=*/")))
    dates = [d.split("date=")[1][:10] for d in dirs]
    dates = [d for d in dates if d >= start]
    files = [os.path.join(root, "data/kline_daily_enriched/date=%s/part.parquet" % d) for d in dates]
    return dates, files


def load_tensor(files, dates):
    """把 (日期 × 股票) 的行情装进 (股票, 日期, 字段) 的 float32 张量, 便于向量化取窗口"""
    def rd(f):
        d = pd.read_parquet(f, columns=["symbol"] + FLOAT_COLS)
        d["date"] = f.split("date=")[1][:10]
        return d

    with ThreadPoolExecutor(max_workers=8) as ex:
        parts = list(ex.map(rd, files))
    df = pd.concat(parts, ignore_index=True)

    syms = sorted(df["symbol"].unique().tolist())
    sym2i = {s: i for i, s in enumerate(syms)}
    d2i = {d: i for i, d in enumerate(dates)}

    si = df["symbol"].map(sym2i).to_numpy(np.int32)
    di = df["date"].map(d2i).to_numpy(np.int32)
    vals = df[FLOAT_COLS].to_numpy(np.float32)

    SC = np.full((len(syms), len(dates), len(FLOAT_COLS)), np.nan, dtype=np.float32)
    SC[si, di, :] = vals
    del df, parts, vals
    return SC, sym2i, syms


def load_events(root, start, valid_syms, board_pos=1):
    """首板事件 (board_pos=1): 直接读 streaks.parquet, 每条 start_date 都是首板日。"""
    s = pd.read_parquet(os.path.join(root, "data/limit_up_research/streaks.parquet"))
    s["t"] = s["start_date"].astype(str)
    s = s[(s["t"] >= start) & s["symbol"].isin(valid_syms)]
    s = s.drop_duplicates(subset=["symbol", "t"]).reset_index(drop=True)
    return s


def load_name_map(root):
    """symbol -> 名称 (streaks 优先, 缺的用 instruments 补)。"""
    nm = {}
    try:
        s = pd.read_parquet(os.path.join(root, "data/limit_up_research/streaks.parquet"))
        nm.update(dict(zip(s["symbol"], s["name"])))
    except Exception:
        pass
    try:
        inst = pd.read_parquet(os.path.join(root, "data/instruments/instruments.parquet"))
        for sym, nm_ in zip(inst["symbol"], inst["name"]):
            nm.setdefault(sym, nm_)
    except Exception:
        pass
    return nm


def load_events_boardpos(cu, dates, syms, board_pos, valid_syms, start, name_map):
    """
    从连板数张量切出「第 board_pos 个涨停板」事件 (board_pos>=2)。

    cu: (股票, 交易日) 的 consecutive_limit_ups, 1,2,3... 表示这是连板的第几天,
        断板归 0。一张票的某段连续涨停 = 一段 1,2,3,...,L 的递增序列, L 即这段最终连板数。
    所以: 值为 board_pos 的那天 = 第 board_pos 板; 该段峰值 L = 这段最终连板数 (事后标签)。
    """
    cu = np.nan_to_num(cu, nan=0)
    m = cu >= 1
    prev = np.zeros_like(m)
    prev[:, 1:] = m[:, :-1]
    run_start = m & ~prev
    run_id = np.cumsum(run_start.astype(np.int32), axis=1)
    # 每行段 id 从 1 重开, 必须加行级偏移再全局 bincount, 否则不同行的「第1段」会撞车,
    # 把全市场首段格子混在一起计数 (曾算出 8869 这种荒谬的「事后连板数」)。
    row_base = np.arange(m.shape[0], dtype=np.int64)[:, None] * (m.shape[1] + 1)
    run_id_global = run_id.astype(np.int64) + row_base
    rl = np.bincount(run_id_global[m].ravel())     # 每段长度 (每段全局唯一)
    boards_at = np.zeros(m.shape, dtype=np.int64)
    boards_at[m] = rl[run_id_global[m]]

    # board_pos>=6 是「>5板」哨兵: 取连板数 >= 该阈值的那天 (第6板及以后), 不再严格等于
    if board_pos >= 6:
        ev_mask = (cu.astype(np.int32) >= board_pos) & m
    else:
        ev_mask = (cu.astype(np.int32) == board_pos) & m
    si_idx, di_idx = np.where(ev_mask)
    date_ok = np.array([d >= start for d in dates])
    out = []
    for si, di in zip(si_idx.tolist(), di_idx.tolist()):
        sym = syms[si]
        if sym not in valid_syms:
            continue
        if not date_ok[di]:
            continue
        out.append((sym, name_map.get(sym, sym), dates[di], int(boards_at[si, di]), int(boards_at[si, di])))
    ev = pd.DataFrame(out, columns=["symbol", "name", "t", "boards", "days"])
    ev = ev.drop_duplicates(subset=["symbol", "t"]).reset_index(drop=True)
    return ev


def load_regime(root):
    r = pd.read_parquet(os.path.join(root, "data/regime_history/part.parquet"))
    r["date"] = r["date"].astype(str)
    return r.set_index("date")


def load_shares(root):
    d = pd.read_parquet(os.path.join(root, "data/instruments/instruments.parquet"))
    return d.set_index("symbol")["total_shares"].to_dict()


# ================================ 2. 构造交易 ================================

# 张量缓存: 一次读取, 多个板位复用, 避免重复加载 ~ 上百个分区文件
_TENSOR = None


def build_trades(cfg, board_pos=1, tensor=None):
    global _TENSOR
    root = cfg["data_root"]
    dates, files = load_calendar(root, cfg["start"])
    if tensor is not None:
        SC, sym2i, syms = tensor
    else:
        if _TENSOR is None:
            SC, sym2i, syms = load_tensor(files, dates)
            _TENSOR = (SC, sym2i, syms)
        else:
            SC, sym2i, syms = _TENSOR
    if board_pos == 1:
        ev = load_events(root, cfg["start"], set(syms))
    else:
        cu = SC[:, :, F["clu"]]
        ev = load_events_boardpos(cu, dates, syms, board_pos, set(syms),
                                 cfg["start"], load_name_map(root))
    reg = load_regime(root)
    shares = load_shares(root)
    d2i = {d: i for i, d in enumerate(dates)}
    bp_label = {1: "首板", 2: "2板", 3: "3板", 4: "4板", 5: "5板", 6: ">5板"}.get(board_pos, "%d板" % board_pos)
    print("[1/5] 行情张量", SC.shape, " %s事件" % bp_label, len(ev), " 交易日", len(dates))

    offs = np.arange(-cfg["win_before"], cfg["win_after"] + 1)
    n_off = len(offs)
    iT = cfg["win_before"]          # T 日在窗口里的位置
    iB = iT + 1                     # T+1 买入日
    n = len(ev)

    t_idx = np.array([d2i.get(t, -1) for t in ev["t"]], dtype=np.int32)
    s_idx = np.array([sym2i.get(s, -1) for s in ev["symbol"]], dtype=np.int32)
    ok_idx = (t_idx >= 0) & (s_idx >= 0) & (t_idx + cfg["win_after"] < len(dates)) \
             & (t_idx - cfg["win_before"] >= 0)

    didx = np.clip(t_idx[:, None] + offs[None, :], 0, len(dates) - 1)
    didx = np.broadcast_to(didx, (n, n_off)).copy()
    sidx = np.broadcast_to(s_idx[:, None], (n, n_off)).copy()
    W = SC[sidx, didx, :]                       # (事件数, 窗口天数, 字段数)
    W[~ok_idx] = np.nan
    del SC

    O = W[:, :, F["open"]]
    H = W[:, :, F["high"]]
    L = W[:, :, F["low"]]
    C = W[:, :, F["close"]]
    RC = W[:, :, F["raw_close"]]
    CLU = W[:, :, F["clu"]]
    TURN = W[:, :, F["turnover_rate"]]

    buy = O[:, iB].astype(np.float64)
    prev_close = C[:, iT - 1]
    t_close = C[:, iT]
    t_raw = RC[:, iT]
    prev_raw = RC[:, iT - 1]

    # --- 可成交性与真实性校验 ---
    is_limit = (CLU[:, iT] >= 1) | (np.divide(t_raw, prev_raw, out=np.full(n, np.nan),
                                            where=prev_raw > 0) > cfg["limit_ratio"])
    amp_t = np.divide(H[:, iT] - L[:, iT], t_close, out=np.full(n, np.nan), where=t_close > 0)
    amp_b = np.divide(H[:, iB] - L[:, iB], C[:, iB], out=np.full(n, np.nan), where=C[:, iB] > 0)
    yizi_t = (amp_t < cfg["yizi_amp"]) & is_limit
    yizi_b = (amp_b < cfg["yizi_amp"]) & \
             (np.divide(C[:, iB], t_close, out=np.full(n, np.nan), where=t_close > 0) > cfg["limit_ratio"])
    gap = np.divide(buy, t_close, out=np.full(n, np.nan), where=t_close > 0) - 1.0

    # --- 数据清洗 (不是为了美化结果, 是剔除不可能发生的记录) ---
    # 1) 上市不足 4 个交易日的新股: 窗口前部无行情, 且上市初期无涨跌幅限制, 不适用涨停策略
    pre_valid = np.sum(np.isfinite(C[:, :iT]) & (C[:, :iT] > 0), axis=1)
    listed = pre_valid >= 4
    # 2) T+1 相对 T 收盘的涨跌幅、隔夜跳空都必须在涨跌停约束内; 超出即复权/数据异常
    r_close = np.divide(C[:, iB], t_close, out=np.full(n, np.nan), where=t_close > 0) - 1.0
    sane = (np.abs(r_close) <= 0.22) & (np.abs(gap) <= 0.22) & (buy > 0.05)

    valid = ok_idx & listed & sane & np.isfinite(buy) & (buy > 0) \
            & np.isfinite(C[:, iB]) & is_limit
    print("[2/5] 有效首板交易", int(valid.sum()),
          " (剔除新股", int((ok_idx & ~listed).sum()),
          " 剔除异常", int((ok_idx & listed & ~sane).sum()),
          " | T日一字", int((valid & yizi_t).sum()),
          " T+1一字", int((valid & yizi_b).sum()), ")")

    # --- 各种出场收益 (全部向量化, 买入价统一为 T+1 开盘) ---
    R = {}
    R["r_t1"] = C[:, iB] / buy - 1
    R["r_t2"] = C[:, iB + 1] / buy - 1
    R["r_t3"] = C[:, iB + 2] / buy - 1
    R["r_t5"] = C[:, iB + 4] / buy - 1

    # 5% 止损 + 最多持有 3 日 (最低价必须是有效正数, 否则按缺失处理)
    def clean_low(a):
        return np.where(np.isfinite(a) & (a > 0), a, np.nan)
    lows3 = clean_low(L[:, iB:iB + 3])
    hit_sl = np.nanmin(lows3, axis=1) <= buy * (1 - cfg["stop_loss"])
    R["r_sl"] = np.where(hit_sl, -cfg["stop_loss"], C[:, iB + 2] / buy - 1)

    # 三日不红即斩: 前 3 日收盘都没站上买入价 -> T+3 收盘走; 否则持到 T+5
    c3 = C[:, iB:iB + 3]
    no_red = np.nanmax(c3, axis=1) <= buy
    r_red = np.where(no_red, C[:, iB + 2] / buy - 1, C[:, iB + 4] / buy - 1)
    hit_sl5 = np.nanmin(clean_low(L[:, iB:iB + 5]), axis=1) <= buy * (1 - cfg["stop_loss"])
    R["r_3red"] = np.where(hit_sl5, -cfg["stop_loss"], r_red)

    # 移动止盈 3% + 最多持有 5 日
    ret_trail = np.full(n, np.nan)
    peak = buy.copy()
    sold = np.zeros(n, dtype=bool)
    for k in range(cfg["hold_max"]):
        c = C[:, iB + k]
        peak = np.maximum(peak, np.nan_to_num(c, nan=0.0))
        trigger = (~sold) & (c <= peak * (1 - cfg["trail_drawdown"]))
        ret_trail[trigger] = np.divide(c[trigger], buy[trigger],
                                       out=np.full(int(trigger.sum()), np.nan),
                                       where=buy[trigger] > 0) - 1
        sold |= trigger
    ret_trail[~sold] = C[~sold, iB + cfg["hold_max"] - 1] / buy[~sold] - 1
    R["r_trail"] = np.where(hit_sl5, -cfg["stop_loss"], ret_trail)

    # --- T 日市场特征 (只取当日, 不含未来) ---
    reg_cols = ["state", "phase", "score", "seal_rate", "promo_rate", "max_consecutive",
                "limit_up", "broken_limit", "first_board", "ge2_count",
                "ladder_completeness", "profit_score", "speculation_score",
                "avg_pct", "median_pct"]
    rd = reg.reindex(ev["t"])[reg_cols].reset_index(drop=True)

    # --- 市值 (亿元) ---
    # 股本是「最新快照」, 必须与前复权价相乘才是历史市值: 送转会让不复权价偏高、
    # 股本同步放大, 用 raw_close × 当前股本会成倍高估 (曾算出 4.7e6 亿的荒谬值)。
    sh = ev["symbol"].map(shares).to_numpy(np.float64)
    mktcap = np.divide(C[:, iT] * sh, 1e8, out=np.full(n, np.nan), where=np.isfinite(sh))

    tr = pd.DataFrame({
        "symbol": ev["symbol"], "name": ev["name"], "t": ev["t"],
        "buy_date": [dates[i] if 0 <= i < len(dates) else "" for i in t_idx + 1],
        "year": ev["t"].str[:4].astype(int), "month": ev["t"].str[5:7].astype(int),
        "mktcap": mktcap, "turn": TURN[:, iT],
        "yizi_t": yizi_t.astype(int), "yizi_b": yizi_b.astype(int),
        "gap": gap, "buy": buy, "t_close": t_close,
        "boards": ev["boards"].to_numpy(), "days": ev["days"].to_numpy(),  # 事后标签
        "bp": np.full(n, int(board_pos)),                                  # 板位: 1=首板 2=2板 3=3板
        "closes": list(np.round(C * 100).astype("float32")),               # 画图用 (分)
        "valid": valid,
    })
    for c in reg_cols:
        tr[c] = rd[c].to_numpy()
    for k, v in R.items():
        tr[k] = v
    tr = tr[tr["valid"]].reset_index(drop=True)
    for k in R:
        tr = tr[np.isfinite(tr[k])]
    tr = tr.reset_index(drop=True)
    print("[3/5] 最终可分析交易", len(tr))
    return tr, cfg, EXIT_MODES


# ================================ 3. 全量条件扫描 ================================

def cond_masks(tr):
    """每个限制条件单独作用下的布尔掩码 (用于全量效果表)"""
    m = {}
    ph = tr["phase"].fillna("").astype(str)
    st = tr["state"].fillna("").astype(str)
    for p in ["ice", "repair", "ignite", "rally", "climax", "ebb"]:
        m["只在 %s 相位交易" % p] = ph == p
    m["只在冷相位(冰点/退潮)交易"] = ph.isin(["ice", "ebb"])
    m["相位门: 点火+主升才买 (方案原设, Phase0证伪)"] = ph.isin(["ignite", "rally"])
    m["排除 T 日一字板"] = tr["yizi_t"] == 0
    m["排除 T+1 一字板(买不到)"] = tr["yizi_b"] == 0
    m["大盘状态=strong"] = st == "strong"
    m["大盘状态≠weak"] = st != "weak"
    for v in [10, 50, 100, 200]:
        m["市值 ≥ %d 亿 (非两百亿不沾)" % v] = tr["mktcap"] >= v
    for v in [0.20, 0.30, 0.40]:
        m["晋级率 ≥ %.0f%%" % (v * 100)] = tr["promo_rate"] >= v
    for v in [0.60, 0.70, 0.80]:
        m["封板率 ≥ %.0f%%" % (v * 100)] = tr["seal_rate"] >= v
    for v in [30, 50, 80]:
        m["涨停家数 ≥ %d" % v] = tr["limit_up"] >= v
    for v in [5, 7, 9]:
        m["最高连板 ≤ %d" % v] = tr["max_consecutive"] <= v
    for v in [0.01, 0.03, 0.05]:
        m["隔夜高开 ≤ %.0f%%" % (v * 100)] = tr["gap"] <= v
    for v in [2021, 2022, 2023]:
        m["只看 %d 年及以后" % v] = tr["year"] >= v
    m["首板日炸板数 ≤ 20"] = tr["broken_limit"] <= 20
    return m


def summarize(r):
    r = np.asarray(r, dtype=np.float64)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return {"n": 0, "win": 0, "mean": 0, "median": 0, "total": 0}
    return {
        "n": int(len(r)),
        "win": float((r > 0).mean()),
        "mean": float(r.mean()),
        "median": float(np.median(r)),
        "total": float(r.sum()),
        "p10": float(np.quantile(r, 0.10)),
        "p90": float(np.quantile(r, 0.90)),
    }


def scan_all(tr, exit_modes):
    masks = cond_masks(tr)
    out = {}
    for mode in exit_modes:
        base = summarize(tr[mode])
        rows = [{"cond": "【无限制 · 全量基线】", **base}]
        for name, mask in masks.items():
            s = summarize(tr.loc[mask, mode])
            s["cond"] = name
            s["d_mean"] = s["mean"] - base["mean"] if s["n"] else 0
            s["d_win"] = s["win"] - base["win"] if s["n"] else 0
            rows.append(s)
        out[mode] = {"base": base, "rows": rows}
    return out


# ================================ 4. 分层案例池 ================================

def sample_cases(tr, cfg):
    """分层抽样: 极端盈亏必留 + 每个「交易日×板位」保底 1 条 + 其余按 板位×相位×收益桶 均匀抽。

    按「交易日×板位」保底是为了前端「点某天的柱子」时, 基本都能看到那天的真实 K 线,
    否则案例池只覆盖约 10% 的交易, 点十次有八次是空列表。
    """
    n = len(tr)
    k = min(cfg["case_sample"], n)
    r = tr["r_t1"].to_numpy()
    force = cfg["force_extreme"]
    bp = tr["bp"].to_numpy()
    rng = np.random.default_rng(20261112)

    # 强制极端: 每个板位各自最赚/最亏各 force 笔, 避免某板位被淹没
    keep = set()
    for b in np.unique(bp):
        ri = np.where(bp == b)[0]
        if len(ri) == 0:
            continue
        order = ri[np.argsort(r[ri])]
        keep |= set(order[-force:].tolist()) | set(order[:force:].tolist())

    # 按日保底: 每个 (交易日, 板位) 至少 1 条
    dates = tr["t"].fillna("").astype(str).to_numpy()
    cell = {}
    for i in range(n):
        cell.setdefault((dates[i], int(bp[i])), []).append(i)
    day_budget = max(0, k - len(keep) - 200)
    day_pick = set()
    for key in sorted(cell):
        cand = [i for i in cell[key] if i not in keep and i not in day_pick]
        if not cand:
            continue
        if len(day_pick) >= day_budget:
            break
        day_pick.add(int(rng.choice(cand)))

    # 其余按 板位×相位×收益桶 均匀抽, 让各相位、各盈亏档都有样本
    bucket = pd.cut(tr["r_t1"], [-np.inf, -0.09, -0.05, -0.02, 0, 0.02, 0.05, 0.09, np.inf],
                    labels=False)
    key = bp.astype(str) + "|" + tr["phase"].fillna("na").astype(str) + "|" + bucket.astype(str)
    groups = {}
    for i, g in enumerate(key.to_numpy()):
        groups.setdefault(g, []).append(i)

    idx = set(keep) | day_pick
    room = max(0, k - len(idx))
    per_group = max(1, room // max(1, len(groups))) if groups else 0
    if per_group:
        for v in groups.values():
            cand = [i for i in v if i not in idx]
            if not cand:
                continue
            idx |= set(int(x) for x in rng.choice(cand, size=min(len(cand), per_group), replace=False))

    if len(idx) > k:
        # 超预算只裁「均匀抽」那部分, 极端案例和按日保底不动
        rest = [i for i in idx if i not in keep and i not in day_pick]
        idx = keep | day_pick | set(int(x) for x in rng.choice(
            rest, size=max(0, k - len(keep) - len(day_pick)), replace=False).tolist())
    return tr.iloc[sorted(idx)].reset_index(drop=True), n


def pack(tr_all, sample, cfg, exit_modes, scan):
    """把案例池压成紧凑整数数组, 页面里全部用整数运算"""
    symbols = sorted(tr_all["symbol"].unique().tolist())
    sym_i = {s: i for i, s in enumerate(symbols)}
    names = tr_all.drop_duplicates("symbol").set_index("symbol")["name"].to_dict()
    name_list = [names.get(s, s) for s in symbols]
    phases = ["ice", "repair", "ignite", "rally", "climax", "ebb"]
    states = ["strong", "range", "weak"]
    dates = sorted(tr_all["t"].unique().tolist())
    d_i = {d: i for i, d in enumerate(dates)}

    def I(x, scale=1, default=-9999):
        v = x * scale
        return int(round(v)) if np.isfinite(v) else default

    cols = [
        "sym", "t", "buy_date", "ph", "st", "yr", "mo",
        "mktcap", "promo", "seal", "lu", "mc", "brk", "score",
        "gap", "buy", "yizi_t", "yizi_b", "turn",
        "r_t1", "r_t2", "r_t3", "r_t5", "r_sl", "r_3red", "r_trail",
        "boards", "days",
    ] + ["c%d" % j for j in range(cfg["win_before"] + cfg["win_after"] + 1)]

    rows = []
    for _, z in sample.iterrows():
        cl = z["closes"]
        row = [
            sym_i[z["symbol"]], d_i[z["t"]], d_i.get(z["buy_date"], -1),
            phases.index(z["phase"]) if z["phase"] in phases else -1,
            states.index(z["state"]) if z["state"] in states else -1,
            int(z["year"]), int(z["month"]),
            I(z["mktcap"], 10), I(z["promo_rate"], 1000), I(z["seal_rate"], 1000),
            I(z["limit_up"]), I(z["max_consecutive"]), I(z["broken_limit"]), I(z["score"]),
            I(z["gap"], 10000), I(z["buy"], 100),
            int(z["yizi_t"]), int(z["yizi_b"]), I(z["turn"], 100),
        ]
        for mode in exit_modes:
            row.append(I(z[mode], 10000))
        row += [int(z["boards"]), int(z["days"])]
        row += [int(round(v)) if np.isfinite(v) else -1 for v in cl]
        rows.append(row)

    return {
        "meta": {
            "title": "龙头 / 首板策略复盘实验台",
            "n_all": int(len(tr_all)),
            "n_sample": int(len(sample)),
            "start": cfg["start"],
            "exit_modes": exit_modes,
            "win_before": cfg["win_before"], "win_after": cfg["win_after"],
            "stop_loss": cfg["stop_loss"], "trail_drawdown": cfg["trail_drawdown"],
            "phases": phases, "states": states,
            "symbols": symbols, "name_list": name_list, "dates": dates,
            "cols": cols,
            "caveats": [
                "T 日涨停价买不到, 一律按 T+1 开盘价买入; 收益按前复权收盘价计算, 未扣手续费与滑点。",
                "【事后标签】「最终连板数 / 持续天数」是这笔交易之后才走出来的结果, 只用于复盘, 绝不能当买入条件 —— 用它选股等于未来函数。",
                "情绪相位是收盘后按连板梯队算的日频标签, 抓不到盘中「分歧」那一刻; Phase 0 已验证「点火/主升才买」这道门没有超额 (打板口径 10/11 年为负)。",
                "「隔夜高开」是 T+1 集合竞价 9:25 就能看到的价格, 高开太多就不挂单 —— 可执行, 不是未来函数。止损按跌破买入价 5% 时以 -5% 成交近似, 未计跳空滑点。",
                "市值为「前复权价 × 最新总股本」的历史市值 (股本只有最新快照, 没有历史时序), 只用于分档过滤, 不是精确历史市值。",
                "默认排除 T 日与 T+1 的一字板 (买不到); 页面里可以打开看看忽略可成交性会虚增多少收益。已剔除上市不足 4 日的新股与涨跌停约束外的异常记录。",
                "全量条件效果表基于 %d 笔交易; 页面交互统计基于 %d 笔分层样本 (已强制纳入盈亏最极端的案例)。" % (len(tr_all), len(sample)),
            ],
            "baseline": {m: summarize(tr_all[m]) for m in exit_modes},
        },
        "scan": scan,
        "rows": rows,
    }


# ================================ 5. 主流程 ================================

def main():
    if "--html" in sys.argv and os.path.exists(os.path.join(HERE, "lab_data.json")):
        data = json.load(open(os.path.join(HERE, "lab_data.json"), encoding="utf-8"))
    else:
        tr, cfg, modes = build_trades(CONFIG)
        print("[4/5] 全量条件扫描 ...")
        scan = scan_all(tr, modes)
        sample, n_all = sample_cases(tr, CONFIG)
        data = pack(tr, sample, CONFIG, modes, scan)
        with open(os.path.join(HERE, "lab_data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        print("[5/5] lab_data.json 写出, 案例池", len(sample), "笔")

    tpl = os.path.join(HERE, "page_template.html")
    if os.path.exists(tpl):
        html = open(tpl, encoding="utf-8").read()
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        html = html.replace("/*__LAB_DATA__*/", "const DATA = " + payload + ";")
        out = os.path.join(HERE, "index.html")
        open(out, "w", encoding="utf-8").write(html)
        print("页面已生成:", out, " 大小 %.1f MB" % (os.path.getsize(out) / 1e6))
    else:
        print("未找到 page_template.html, 仅输出数据")


if __name__ == "__main__":
    main()
