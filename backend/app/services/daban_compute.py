# -*- coding: utf-8 -*-
"""打板接力：全量交易的计算逻辑，由后端 `POST /api/daban-research/rebuild` 调用。

口径与原来的独立实验脚本完全一致，只是从「离线导出 json」改成「后端现算现返回」：
  * 6 个板位 (1..5、>=6) 各自的涨停事件，一律 T+1 开盘买入，算 7 种出场收益；
  * stats = 全部板位合并后的全量交易，一行一笔，**没有任何抽样**；
  * ohlc = 每笔的 17 根真实 K 线窗口，行号与 stats 严格同序（导出前逐根校验过）。

本模块只返回内存结果，不写任何文件；落盘缓存由 daban_research service 负责。
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from app.services import daban_lab as S

# 过滤字段 (前端所有限制开关都只用这些, 加上出场模式选择)
FILTER_COLS = ["ph", "st", "yr", "mktcap", "promo", "seal", "lu",
               "mc", "brk", "gap", "yizi_t", "yizi_b", "zaban"]
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
        "zaban": tr["zaban"].to_numpy(),
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


# ---- 分类标签 (P2): 给每一笔已发生的涨停打标, 行号与 stats 严格同序 ----
# 位掩码: 每笔一个 int64, 第 bit 位为 1 表示命中该标签。前端用算术判定读位
# (Math.floor(mask / 2**bit) % 2), 不受 JS 位运算 32 位限制; JSON number 的
# 精确整数上限 2**53, 所以 bit 硬上限 52。前端勾选多个标签取交集(AND)。
# timing 标明判定时点, 避免把事后标签误当成盘前买入信号:
#   pre    = 只用 T-1 及以前的日线 (事前, 可升信号候选)
#   event  = 含 T 日涨停/封板/情绪等事后信息 (仅复盘切片)
#   buy    = T+1 开盘集合竞价可见 (隔夜高开特征; 实际买入按 T 日涨停价, 不用它作买价)
#   review = 含 T 日「放量涨停 / 突破 / 实体吞没」等决定性动作的完整形态
#            (第二轮补齐, 用于复盘分类, 绝不是买入信号 —— 买入在 T 日涨停价)
#
# 前 30 条是「前置片段」: 只用 T-1 及以前日线或已有事件字段, 覆盖原文口径的一部分。
# 30..36 是「完整版」: 按 01/02 册的建议口径 / 原文定量逐条补齐原先缺的动作,
# 与前置片段标签并存, 便于对比「前置 vs 完整」。知识库硬约束是「只用 T 日及以前」,
# T 日本身合法(见 09 册第 7 节); 之前压到 T-1 只是为了能当盘前信号, 不是分类要求。
TAG_DEFS = [
    # --- 位置与趋势 (9) ---
    {"id": "t_oversold", "bit": 0, "name": "大跌后涨停", "group": "位置与趋势", "timing": "pre",
     "rule": "T-1 收盘较 T-21 收盘跌 ≥15%"},
    {"id": "t_lowpos", "bit": 1, "name": "位置偏低", "group": "位置与趋势", "timing": "pre",
     "rule": "T-1 收盘距近 60 日最低价 ≤20%"},
    {"id": "t_newhigh", "bit": 2, "name": "突破阶段新高", "group": "位置与趋势", "timing": "pre",
     "rule": "T-1 收盘 ≥ 前 20 日最高收盘"},
    {"id": "t_mabull", "bit": 3, "name": "均线多头", "group": "位置与趋势", "timing": "pre",
     "rule": "T-1 的 MA5 > MA10 > MA20"},
    # --- 形态与量能 (13) ---
    {"id": "t_platform", "bit": 4, "name": "横盘后涨停", "group": "形态与量能", "timing": "pre",
     "rule": "T-1 前 5 日振幅 ≤6% 且 5 日涨跌幅绝对值 ≤5%"},
    {"id": "t_shrink", "bit": 5, "name": "缩量窄幅洗盘", "group": "形态与量能", "timing": "pre",
     "rule": "近 5 日均额 / 前 15 日均额 ≤0.7 且 5 日振幅 ≤8%"},
    {"id": "t_uppersh", "bit": 6, "name": "长上影试盘", "group": "形态与量能", "timing": "pre",
     "rule": "T-1 上影线 ≥ 2 倍实体且实体 >0"},
    {"id": "t_3yang", "bit": 7, "name": "连阳蓄势", "group": "形态与量能", "timing": "pre",
     "rule": "T-1 起连续 3 根阳线、单日涨幅 ≤3% 且高点逐级抬高"},
    {"id": "t_volbreak", "bit": 8, "name": "放量突破", "group": "形态与量能", "timing": "pre",
     "rule": "T-1 成交额 ≥ 前 5 日均额 1.8 倍且收盘破前 5 日最高价"},
    # --- 事件属性 (8) ---
    {"id": "t_yizi", "bit": 9, "name": "一字板", "group": "事件属性", "timing": "event",
     "rule": "T 日或 T+1 为一字板 (实际买不进)"},
    {"id": "t_broken30", "bit": 10, "name": "炸板潮", "group": "事件属性", "timing": "event",
     "rule": "T 日全市场炸板家数 ≥30 (封板分歧大)"},
    {"id": "t_cold", "bit": 11, "name": "冷相位", "group": "事件属性", "timing": "event",
     "rule": "当日情绪相位为冰点或退潮"},
    {"id": "t_smallcap", "bit": 12, "name": "小市值", "group": "事件属性", "timing": "event",
     "rule": "估算总市值 <50 亿"},
    {"id": "t_gap3", "bit": 13, "name": "隔夜高开>3%", "group": "事件属性", "timing": "buy",
     "rule": "T+1 开盘较 T 日收盘高开 >3%"},
    # --- 第二批 (bit 14..29): 全部可只用 T-1 及以前日线 / 现有事件字段判定 ---
    {"id": "t_dd20", "bit": 14, "name": "深度回撤", "group": "位置与趋势", "timing": "pre",
     "rule": "T-1 收盘距近 20 日最高收盘回撤 ≥15% (01-01)"},
    {"id": "t_bias24", "bit": 15, "name": "乖离超卖", "group": "位置与趋势", "timing": "pre",
     "rule": "T-1 收盘较 MA24 低 ≥15% (01-07)"},
    {"id": "t_highpos", "bit": 16, "name": "接近年内高位", "group": "位置与趋势", "timing": "pre",
     "rule": "T-1 收盘距近 60 日最高收盘回撤 ≤5% (01-06)"},
    {"id": "t_mafit", "bit": 17, "name": "均线粘合", "group": "位置与趋势", "timing": "pre",
     "rule": "MA5/MA10/MA20 三者极差 ≤ T-1 收盘的 2% (01-14)"},
    {"id": "t_pullback", "bit": 18, "name": "回踩均线", "group": "位置与趋势", "timing": "pre",
     "rule": "T-1 最低价触及 MA20 ±2%、收盘站上 MA20 且 MA20 上行 (01-16)"},
    {"id": "t_vcp", "bit": 19, "name": "波动压缩", "group": "形态与量能", "timing": "pre",
     "rule": "前 5 日平均振幅 / 前 20 日平均振幅 ≤0.6 (07-17)"},
    {"id": "t_lowersh", "bit": 20, "name": "长下影大长腿", "group": "形态与量能", "timing": "pre",
     "rule": "T-1 下影线 ≥ 2 倍实体且实体 >0 (02-14)"},
    {"id": "t_engulf", "bit": 21, "name": "阳包阴", "group": "形态与量能", "timing": "pre",
     "rule": "T-1 阳线实体完全包住 T-2 阴线实体 (02-01)"},
    {"id": "t_lastdrop", "bit": 22, "name": "最后一跌", "group": "形态与量能", "timing": "pre",
     "rule": "T-1 单日跌幅 ≥5% (01-11 简化)"},
    {"id": "t_smallyang", "bit": 23, "name": "超跌小阳堆积", "group": "形态与量能", "timing": "pre",
     "rule": "20 日跌 ≥10% 且近 5 日 ≥4 根阳线、单日涨跌幅均 ≤3% (01-18)"},
    {"id": "t_priceupvol", "bit": 24, "name": "价升量缩", "group": "形态与量能", "timing": "pre",
     "rule": "近 5 日涨 ≥3% 且 5 日均额 / 前 15 日均额 ≤0.8 (06-12)"},
    {"id": "t_4yang1yin", "bit": 25, "name": "四连阳一阴", "group": "形态与量能", "timing": "pre",
     "rule": "前 5 日恰 4 阳 1 阴且阴线成交额 < 5 日均额 (02-15)"},
    {"id": "t_nshape", "bit": 26, "name": "N字回马枪", "group": "形态与量能", "timing": "pre",
     "rule": "近 20 日内曾涨停(连板天数≥1)且 T-1 较该涨停日收盘回调 ≥5% (02-03)"},
    {"id": "t_highseal", "bit": 27, "name": "高封板率", "group": "事件属性", "timing": "event",
     "rule": "当日全市场封板率 ≥80%"},
    {"id": "t_lumany", "bit": 28, "name": "涨停潮", "group": "事件属性", "timing": "event",
     "rule": "当日全市场涨停家数 ≥100 (中位约 60 家)"},
    {"id": "t_weakmkt", "bit": 29, "name": "大盘偏弱", "group": "事件属性", "timing": "event",
     "rule": "当日大盘状态为「弱」或「偏弱」"},
    # --- 第三批 (bit 30..36): 完整口径, 按 01/02 册建议口径/原文定量逐条补齐 ---
    # 含 T 日决定性动作 -> timing=review, 只能复盘切片, 不能当买入信号。
    {"id": "t_full_platform", "bit": 30, "name": "平台突破板·完整", "group": "完整形态·复盘", "timing": "review",
     "rule": "01-02 完整口径: 前 5 日振幅 ≤8% 且 5 日涨跌 ≤5%(平台) + T 日放量涨停 amt_ratio ≥2(原文定量放大 1 倍以上) + C_T 突破平台上沿 max(H_{T-5..T-1})"},
    {"id": "t_full_newhigh", "bit": 31, "name": "前高突破板·完整", "group": "完整形态·复盘", "timing": "review",
     "rule": "01-03 完整口径: C_T > max(H_{T-20..T-1}) 且前 20 日内存在至少一个局部高点(H 同时高于左右相邻日)"},
    {"id": "t_full_ma_cross", "bit": 32, "name": "一阳穿多线·完整", "group": "完整形态·复盘", "timing": "review",
     "rule": "01-15 完整口径: T 日一根涨停阳线 O_T < min(MA5,MA10,MA20) 且 C_T > max(MA5,MA10,MA20) (均线取 T-1 及以前)"},
    {"id": "t_full_engulf", "bit": 33, "name": "反包涨停·完整", "group": "完整形态·复盘", "timing": "review",
     "rule": "02-01 完整口径: T-1 收阴 C_{T-1}<O_{T-1} + T 日实体吞没 C_T > max(O_{T-1},C_{T-1}) + H_T > H_{T-1} + 放量 amt_ratio ≥1.2"},
    {"id": "t_full_nshape", "bit": 34, "name": "双响炮N字·完整", "group": "完整形态·复盘", "timing": "review",
     "rule": "02-03 完整口径: 近 2–10 日内曾涨停(days_since_lu) + 回调缩量 amt_shrink ≤0.7 + T 日 C_T 突破前高(前置涨停日收盘)"},
    # 只用 T-1 及以前补齐缺失的量能/影线子条件 -> 仍是事前, 严格等价, 可升信号候选。
    {"id": "t_full_uppersh", "bit": 35, "name": "长上影试盘·完整", "group": "完整形态·事前", "timing": "pre",
     "rule": "02-13 完整口径: 上影 ≥2×实体(原文定量) + 下影 ≤0.2×实体(无下影或很短) + 量能放大 amount_{T-1}/mean(amount_{T-6..T-2}) ≥1.5"},
    {"id": "t_full_longleg", "bit": 36, "name": "大长腿·完整", "group": "完整形态·事前", "timing": "pre",
     "rule": "02-14 完整口径: (H_{T-1}−L_{T-1})/L_{T-1} ≥10%(原文定量) + 下影 ≥2×实体 + T-1 收实体大阳线"},
    # --- 第四批 (bit 37..52): 72 条待实现里挑 16 条「日线可算 + 原文/建议口径明确」的先落地 ---
    # 位号到 52 为止(2**53-1 = MAX_SAFE_INTEGER), 再扩需改多 word 位掩码, 见第 13 册。
    # 分组按来源册组织; timing 按「是否读 T 日」分 pre / review, review 只能复盘切片。
    # 位置突破·复盘 (01 册, 含 T 日 C_T 突破动作)
    {"id": "t_box_break", "bit": 37, "name": "箱体突破板", "group": "位置突破·复盘", "timing": "review",
     "rule": "01-04 口径: T-2..T-11 十根箱体宽度 (max(H)-min(L))/min(L) ≤15%(原文区间宽度≤15%、持续≥10日) + T 日 C_T 突破箱体上沿 max(H_{T-2..T-11})"},
    {"id": "t_wbottom", "bit": 38, "name": "W底突破板", "group": "位置突破·复盘", "timing": "review",
     "rule": "01-12 口径: 近10日与次10日两区间最低价价差 ≤3%(原文两低点价差≤3%、间隔≥5日) + T 日 C_T 突破两低点间颈线 max(H_{T-5..T-13})"},
    # K线组合·事前 (02 册, 只用 T-1 及以前)
    {"id": "t_hm_single", "bit": 39, "name": "单板回马枪", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-05 分档维度取「单板」一档: 近20日曾涨停且前置连板峰值=1 + T-1 较该涨停日收盘回调 ≥5%; 双板/三板档待后续(标部分实现)"},
    {"id": "t_hm_platform", "bit": 40, "name": "平台型回马枪", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-06 分档维度取「平台型」一档: 近20日曾涨停 + 回调期5日振幅 ≤9% 且缩量 amt_shrink ≤0.9 + T-1 较峰值回调 ≥3%; V/W型档待后续(标部分实现)"},
    {"id": "t_morning_star", "bit": 41, "name": "早晨之星", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-11 建议口径: T-3 大阴(较 T-4 跌 ≥3%) + T-2 十字星(|C−O|/C ≤1%) + T-1 收复 C_{T-1} > (O_{T-3}+C_{T-3})/2"},
    {"id": "t_hammer", "bit": 42, "name": "倒锤头/锤头线", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-12 建议口径: T-1 上影 ≥2×实体 + 下影 ≤0.2×实体 + 缩量 amt_shrink ≤0.8"},
    # 涨停日盘面·复盘 (03 册, 含 T 日 K 线/成交额)
    {"id": "t_t_board", "bit": 43, "name": "T字板", "group": "涨停日盘面·复盘", "timing": "review",
     "rule": "03-02 口径: T 日开=高=收(涨停价, 容差0.5%) + 盘中有下影 (C_T−L_T)/C_T ≥1%; 含 T 日 K 线, 只能复盘切片"},
    {"id": "t_shrink_board", "bit": 44, "name": "缩量板", "group": "涨停日盘面·复盘", "timing": "review",
     "rule": "03-03 原文定量: amount_T / amount_{T-1} ≤0.7 (对比基准是前一日, 不是5日均量)"},
    {"id": "t_entity_board", "bit": 45, "name": "实体换手板", "group": "涨停日盘面·复盘", "timing": "review",
     "rule": "03-04 代理口径: 非一字 + T 日实体大阳 (C_T−O_T)/O_T ≥3% + 日内振幅 (H_T−L_T)/L_T ≥5% + 下影 (C_T−L_T)/C_T ≤9%; 原文换手率10%–25%因本地 turnover_rate 早年几乎全空无法算, 标部分实现(P)"},
    {"id": "t_high_blow", "bit": 46, "name": "高位爆量板", "group": "涨停日盘面·复盘", "timing": "review",
     "rule": "03-23 分档维度取「高位爆量」一档: amt_ratio ≥5(p90) 且 T-1 收盘 ≥ 近60日最高收盘的95%; 缩量/温和放量档待后续(标部分实现)"},
    # 量柱节奏·事前 (05 册, 只用 T-1 及以前)
    {"id": "t_longyin_shortzhu", "bit": 47, "name": "长阴短柱", "group": "量柱节奏·事前", "timing": "pre",
     "rule": "05-06 建议口径: T-1 跌 ≥4% + amount_{T-1}/mean(amount_{T-6..T-2}) ≤0.7"},
    {"id": "t_changyang_aizhu", "bit": 48, "name": "长阳矮柱", "group": "量柱节奏·事前", "timing": "pre",
     "rule": "05-07 定量口径(原文仅定性): T-1 涨 ≥5% + amount_{T-1}/mean(amount_{T-6..T-2}) ≤0.9 (按分位数定阈值)"},
    {"id": "t_beiliang_shensuo", "bit": 49, "name": "倍量伸缩", "group": "量柱节奏·事前", "timing": "pre",
     "rule": "05-08 定量口径(原文定性「倍量→缩量→倍量」): A_{T-1}/A_{T-2} ≥1.5 且 A_{T-2}/A_{T-3} ≤0.7 且 A_{T-3}/A_{T-4} ≥1.5"},
    {"id": "t_shrink_pullback", "bit": 50, "name": "缩量回调", "group": "量柱节奏·事前", "timing": "pre",
     "rule": "05-09 定量口径(原文定性「回调量逐日萎缩」): T-1/T-2/T-3 成交额三日递减 且 C_{T-1} < C_{T-4}(价跌); 放量下跌为反向档待后续(标部分实现)"},
    # 实证画像·复盘 (08 册, 含 T 日成交额放大)
    {"id": "t_accelerate", "bit": 51, "name": "连涨加速型", "group": "实证画像·复盘", "timing": "review",
     "rule": "08-01 原文实证口径: T-1 涨幅 8%–16% + T 日成交额放大 amt_ratio 2–5 倍; 含 T 日量能, 复盘画像"},
    {"id": "t_flat_burst", "bit": 52, "name": "横盘突发型", "group": "实证画像·复盘", "timing": "review",
     "rule": "08-02 原文口径: 涨停前5日涨跌幅 |r5| ≤2%(几乎横盘) + T 日突然放量 amt_ratio ≥2; 含 T 日量能, 复盘画像"},
    # 第五批 (bit 53..60): 首批落到 word1 的标签, 见第 14 册
    {"id": "t_main_accel", "bit": 53, "name": "主升加速板", "group": "位置与趋势", "timing": "event",
     "rule": "01-05 建议口径: C_{T-1} > MA20 > MA60 (均线截至 T-1) + ret5_pre ≥8% + 板位 bp ≥2; bp 要等 T 日涨停才成立, 故为当日而非事前"},
    {"id": "t_absorb_break", "bit": 54, "name": "吸筹后突破", "group": "形态与量能", "timing": "pre",
     "rule": "01-17 建议口径三段式: T-10..T-6 五日涨 2%–10% → T-5..T-1 缩量整理 amt_shrink ≤0.7 且 5 日振幅 ≤6%"},
    {"id": "t_golden_pillar", "bit": 55, "name": "黄金柱", "group": "量柱节奏·事前", "timing": "pre",
     "rule": "06-04 放宽口径: T-4 为阳线 + 量 ≥2×前5日均量, 且 T-3..T-1 中至少 2 日「价升+量缩」; 原文「三日全价升量缩」严格口径仅 147 笔 (<300 门槛), 故放宽并标部分实现(P)"},
    {"id": "t_double_sword", "bit": 56, "name": "双剑霸天地", "group": "K线组合·事前", "timing": "pre",
     "rule": "06-10 建议口径: 相邻两日, 一日上影 ≥2×实体、一日下影 ≥2×实体 (原文「分别」不限顺序, 取并集); 实体为 0 的十字星不计"},
    {"id": "t_fake_yin", "bit": 57, "name": "假阴真阳", "group": "K线组合·事前", "timing": "pre",
     "rule": "06-11 原文定量: T-1 收盘 < 开盘 (阴线外观) 但 收盘 > C_{T-2} (实际收涨)"},
    {"id": "t_pos_low", "bit": 58, "name": "低位板", "group": "位置分档·事前", "timing": "pre",
     "rule": "01-19 分位分档: dd60 = 距 60 日最高价回撤 ≥ p67 (0.1781), 即回撤最大的 1/3"},
    {"id": "t_pos_mid", "bit": 59, "name": "中位板", "group": "位置分档·事前", "timing": "pre",
     "rule": "01-19 分位分档: p33 (0.0598) ≤ dd60 < p67 (0.1781)"},
    {"id": "t_pos_high", "bit": 60, "name": "高位板", "group": "位置分档·事前", "timing": "pre",
     "rule": "01-19 分位分档: dd60 < p33 (0.0598), 即最贴近 60 日最高价的 1/3"},
    # 第六批 (bit 61..70): 06 册量学体系剩余可判定条目, 见第 15 册
    {"id": "t_diliangqun", "bit": 61, "name": "地量群", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-02 建议口径: 近 100 日成交额 10% 分位以下连续 ≥5 日 (百日低量群)"},
    {"id": "t_aijiangjun", "bit": 62, "name": "矮将军", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-03 口径: T-2..T-101 存在连续 ≥5 日低量群 + T-1 为小倍阳 (量 1–2 倍前日) 且为小阳 (0<涨幅≤3%)"},
    {"id": "t_guozuofeng", "bit": 63, "name": "过左峰", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-06 口径: C_{T-1} > max(H_{T-2..T-61})×1.01 (有效突破) 且该 60 日窗口内存在局部极大 (左峰确实成立, 不是单边上涨)"},
    {"id": "t_jiyin_ciyang", "bit": 64, "name": "极阴次阳", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-13 口径: T-2/T-3 连续下跌且两日累计跌幅 ≥5% (极阴) + T-1 收阳且 C_{T-1} ≥ (O_{T-2}+C_{T-2})/2 (反包过半)"},
    {"id": "t_sanyuan", "bit": 65, "name": "三元连动", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-14 原文定量: T-1/T-2/T-3 三日各自「价升且量缩」(即 06-12 价升量缩连续 3 日成立)"},
    {"id": "t_xianchang", "bit": 66, "name": "现场直憋", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-17 建议口径: 10 日振幅 (maxH−minL)/minL ≤8% + 10 日均额 / 前 60 日均额 ≤0.7 (窄幅+低量群)"},
    {"id": "t_huangjin_x", "bit": 67, "name": "黄金十字架", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-20 代理口径 (原文「左支右撑」靠人工画平衡线): 左支点 = max(H_{T-11..T-40}) + T-1..T-10 最低价回踩支点 ±2% + C_{T-1} 站上支点; 水平位近似, 标部分实现(P)"},
    {"id": "t_binglin", "bit": 68, "name": "兵临城下", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-22 建议口径: T-1/T-2/T-3 三连阳且单日涨幅 ≤3% + 高点逐日抬高 + 20 日最高价不是 H_{T-2} 本身 (上方仍有压力) 且 C_{T-1} 未突破该最高价"},
    {"id": "t_niugu_sanjue", "bit": 69, "name": "牛股三绝", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-23 原文三子条件取交集: 近 20 日内同时存在「跳空不补」「倍量不穿」「高量不破」; 并集口径覆盖 58% 无区分度, 故取交集"},
    {"id": "t_dayang_shuangxiu", "bit": 70, "name": "大阳双休", "group": "量学基因·事前", "timing": "pre",
     "rule": "06-24 原文定量: T-3 涨幅 ≥5% (大阳) + T-2/T-1 两根小实体 (|涨跌| ≤2%) 且收盘均不低于 C_{T-3} (在实顶之上调整)"},

    # --- 第七批: 01 册形态复合 (bit 71..74) ---
    {"id": "t_sanhu_keng", "bit": 71, "name": "散户坑/黄金坑", "group": "位置与趋势", "timing": "pre",
     "rule": "01-08 建议口径: 平台 T-20..T-9 振幅 ≤12% + 坑 T-8..T-2 最低价跌破平台下沿 1% 且坑内均额 ≤平台均额 80% + C_{T-1} 收回平台下沿之上; 「突破日放量」属 T 日, 已前移到 T-1 判回升"},
    {"id": "t_caihong_qiao", "bit": 72, "name": "彩虹桥", "group": "位置与趋势", "timing": "pre",
     "rule": "01-09 建议口径: 近 20 日分 4 段每段最低价逐段抬高 + 四段最高价极差 ≤5% (受制于同一阻力线) + C_{T-1} ≥ 阻力线 95%"},
    {"id": "t_keng_qiao_die", "bit": 73, "name": "坑+桥叠加", "group": "位置与趋势", "timing": "pre",
     "rule": "01-10 原文「同时满足 01-08 与 01-09」。注意: 两个形态在同一个 20 日窗口内逻辑互斥 (坑要求跌破下沿、桥要求低点抬升), 故改为时间错开 —— 坑在 T-20..T-11 (对比 T-39..T-23 平台) + 桥在 T-10..T-1, 才可能叠加"},
    {"id": "t_qixing_zhengli", "bit": 74, "name": "旗形整理", "group": "位置与趋势", "timing": "pre",
     "rule": "01-13 建议口径: 旗杆 T-20..T-11 涨 ≥15% + 旗面 T-10..T-1 高点与低点同步下移 (向下倾斜) + 回调不破旗杆一半 + 旗面均额 ≤旗杆段 80%"},

    # --- 第七批: 02-02 反包细分型, 三档互斥 (bit 75..77) ---
    {"id": "t_fanbao_zhaban", "bit": 75, "name": "反包·炸板型", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-02 ① 在阳包阴(bit21)基础上: T-2 上影 ≥2 倍实体 且 T-2 未涨停 (CLU=0, 炸板未封收长上影); 与 bit76/77 互斥"},
    {"id": "t_fanbao_duanban", "bit": 76, "name": "反包·断板型", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-02 ② 在阳包阴(bit21)基础上: T-3 首板涨停 (CLU=1) + T-2 断板 (CLU=0) 即「涨停-断板-反包」; 与 bit75/77 互斥, 优先判定"},
    {"id": "t_fanbao_shizi", "bit": 77, "name": "反包·十字星/假阴型", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-02 ③ 在阳包阴(bit21)基础上: T-2 为十字星 (|C−O|/C ≤1%) 或假阴真阳 (C<O 但 C>C_{T-3}); 与 bit75/76 互斥"},

    # --- 第七批: 02 册形态 (bit 78) ---
    {"id": "t_v_fanzhuan_yiyin", "bit": 78, "name": "V反转+一阴切线", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-16 建议口径: 平台 T-15..T-6 振幅 ≤15% + T-5..T-4 两日跌 ≥3% (缩量下杀, V 左半) + T-3..T-2 反弹 ≥4% (V 右半) + T-1 收阴洗盘但仍在平台下沿之上; 原文末步「涨停突破」属 T 日, 改成 T-1 一阴切线"},

    # --- 第八批: 07 册境外量化形态 (bit 79..86), 共用 build_tags 内几何 helper ---
    {"id": "t_sym_triangle", "bit": 79, "name": "对称三角形", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-02 近21日分3段: 高点逐段降低 + 低点逐段抬高 (收敛) + 近段波幅 < 远段波幅 (尖端收敛); 向上解决属 T 日, 前置为 T-1 形态"},
    {"id": "t_cup_handle", "bit": 80, "name": "杯柄形态", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-06 近30日: 杯底(T-19..T-8)比左右杯沿低 ≥10% + 两杯沿高度差 ≤6% + 柄(T-2..T-0)浅回撤不破杯底且 ≥右沿88% + T-1 逼近右沿 ≥95%; 收紧自原文首轮16.8%过松口径"},
    {"id": "t_vcp", "bit": 81, "name": "VCP波动收缩", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-08 近24日分3段: 每段(H_max−L_min)/L_min 逐段变小 + 每段均额逐段递减 (供给耗尽); Minervini 连续收缩形态的前置判定"},
    {"id": "t_double_bottom", "bit": 82, "name": "双底W底", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-12 近28日: 两底(T-27..T-18 与 T-14..T-5)低点差 ≤3% + 颈线(T-19..T-12)高于左底 ≥10% + T-1 逼近/突破颈线 ≥100%; CV≈0.43 待阈值回溯"},
    {"id": "t_falling_wedge", "bit": 83, "name": "下降楔形", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-14 近20日分2段: 高点与低点均逐段下移 + 近段波幅 < 远段波幅 (区间收窄), 通常向上解决; 前置形态判定"},
    {"id": "t_higher_lows_vol", "bit": 84, "name": "更高低点放量", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-16 近21日分3段: 低点逐段抬高 + 近段均额 > 远段2倍 (每次抬高伴随放量, 强度递进确认); CV≈0.42 待阈值回溯"},
    {"id": "t_vol_compress", "bit": 85, "name": "波动压缩", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-17 vol_compress=近5日振幅/近20日振幅 ≤ 全样本p20(0.379); 原文称占扫描结果65%-70%的最高频形态, 此处取最紧的20%分档; 分位切点固定不随重算漂移"},
    {"id": "t_pullback_continue", "bit": 86, "name": "回踩续涨", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-19 上升趋势(C_{T-1}>MA20 且 MA20 上行) + 近5日最低价回踩至 MA20×1.01 内不破 + T-1 收阳且量 > T-2 的1.2倍 (放量反包); 收紧自首轮39.8%过松口径, CV≈0.42 待阈值回溯"},

    # --- 第九批: 07册剩余可达标形态 + 08册原文明确数字因子 + 02册回马枪 (bit 87..90) ---
    {"id": "t_asc_triangle", "bit": 87, "name": "上升三角形", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-01 近21日分3段: 低点逐段抬高 + 高点基本水平(三段最高差≤6%) + 近段波幅<远段波幅(收敛); 与对称三角形bit79的区别是阻力水平而非下倾; 年度CV≈0.34稳定"},
    {"id": "t_long_lower_shadow", "bit": 88, "name": "长下影线", "group": "量化因子·事前", "timing": "pre",
     "rule": "08-08 原文已给数字: T-1 下影线(min(O,C)-L) ≥ 实体|O-C| 的2倍 且收阳/十字星; 与bit3(no低档)重叠约43%但仍有近6000笔独立命中; 年度CV≈0.23稳定"},
    {"id": "t_momentum_accel", "bit": 89, "name": "动量加速", "group": "量化因子·事前", "timing": "pre",
     "rule": "08-07 原文量价齐升: T-1成交额/前5日均额≥3 且 近5日涨幅(c0/C_{T-6}-1)≥10%; 收紧自首轮VR≥2/mom≥5%的21.5%过松口径, 年度CV≈0.19稳定"},
    {"id": "t_huilima_platform", "bit": 90, "name": "回马枪平台突破", "group": "K线组合·事前", "timing": "pre",
     "rule": "02-08 平台突破型二拉: T-8..T-20 内曾现单日涨幅≥9.5%涨停 + T-2..T-6 回调平台振幅≤12% + T-1 收盘突破平台高点且开盘未高开过猛(<平台高×1.05); 年度CV≈0.38"},

    # --- 第十批: 归一化阈值改造旧形态 + 盘整三要素 + 小阳线 (bit 91..93) ---
    {"id": "t_breakaway_gap", "bit": 91, "name": "突破缺口", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-10/07-18 突破缺口归一化改造: T-1 向上跳空(O>H_{T-2} 且 L>H_{T-2}) + 缺口幅度/TR14≥0.8 + 量比(A0/A1)≥2; 归一化解决第17册绝对阈值口径 CV0.74 的 regime 漂移, 改造后 CV≈0.29"},
    {"id": "t_consolidation", "bit": 92, "name": "盘整三要素", "group": "境外形态·事前", "timing": "pre",
     "rule": "07-23 原文三要素同时成立: ①支撑阻力清晰(近15日上下沿各触及≥2次) ②窄幅按个股波动率归一(amp15/amp60≤p30=0.509固定切点) ③相对低量(近15均额/前45均额≤0.8)且无突量(15日最大额/均额≤2); CV≈0.39"},
    {"id": "t_small_yang", "bit": 93, "name": "小阳线蓄势", "group": "量化因子·事前", "timing": "pre",
     "rule": "08-08 子条件: 近3日全部为小阳线(0<实体涨幅≤5%), 原文3日内阳线占比≥80%的具体化; 与bit7连阳蓄势重叠25%但更严格(限定小阳); CV≈0.20稳定"},
]

# 位号唯一、连续 (当前 94 条占 bit 0..93)。
# 每个 word 用 53 位: JS number 精确整数上限是 2**53-1, 53 个位全置 1 恰好等于它,
# 所以前端可以「除以 2**bit 取整再判奇偶」精确判位, 不需要 BigInt。
# 每笔记录存 TAG_WORDS 个 int64, 第 bit 位落在 word = bit // 53 的 (bit % 53) 位。
# 前 53 条正好塞满 word0 (值域与改造前逐位一致, 可做严格等价回归),
# bit53 起的第五批标签自动铺到 word1 —— 多 word 改造真正生效的证据。
TAG_BITS_PER_WORD = 53
TAG_WORDS = max(1, (max(d["bit"] for d in TAG_DEFS) + 1 + TAG_BITS_PER_WORD - 1) // TAG_BITS_PER_WORD)

# 取多少根历史日线: 第六批的地量群要看「百日」低量, 所以从 60 扩到 100。
# 前 60 列的位置含义完全不变 (第 0 列仍是 T-1, 第 59 列仍是 T-60),
# 因此 bit0..bit60 的判定逻辑不用改; 但凡是「对整行取 max/min」的地方都必须
# 显式切成 [:, :60], 否则会悄悄变成 100 日口径 —— 下面 lo60 / mx60 两处就是。
HIST = 100

# 01-19 位置三档的分位切点: 全量 dd60 的 p33 / p67 (2026-11-16 缓存上算得)。
# 固定不随重算浮动, 否则同一笔记录在不同日期会被打上不同档位, 无法跨期比较。
DD60_P33 = 0.0598
DD60_P67 = 0.1781

# 07-17 波动压缩的分位切点: 全样本 (amp5/amp20) 的 p20 = 0.379 (2026-11-16 探查算得)。
# 与 DD60 分档同理, 固定不随重算浮动, 否则同一笔记录在不同日期会落入不同档。
COMPRESS_P20 = 0.379

# 07-23 盘整三要素的窄幅切点: 全样本 (amp15/amp60) 的 p30 = 0.509 (第十批探查算得)。
# 归一化到个股自身波动, 解决绝对振幅阈值的 regime 漂移; 切点固定不随重算浮动。
CONSOLIDATE_P30 = 0.509

assert len(TAG_DEFS) == 94
assert sorted(d["bit"] for d in TAG_DEFS) == list(range(94))
assert max(d["bit"] for d in TAG_DEFS) <= TAG_BITS_PER_WORD * TAG_WORDS - 1


def build_tags(tr, cal, SC, sym2i) -> np.ndarray:
    """分类标签位掩码, 形状 (n, TAG_WORDS) 的 int64 矩阵, 行号与 build_stats / add_ohlc 严格同序。

    多 word 的原因: 标签总数超过 53 条后单 int64 装不下, 而前端要能精确判位。
    这里每 53 位一个 word, word0 覆盖 bit 0..52 —— 改造前的 53 条标签全部落在 word0,
    所以 word0 的数值与改造前逐位一致, 可做严格等价回归。

    分两类:
      * 前 30 条「前置片段」+ 2 条「完整·事前」: 只用 T-1 及以前日线;
      * 5 条「完整·复盘」: 额外读 T 日 K 线与成交额, 补齐放量涨停/突破/吞没等
        决定性动作。打标对象是「已经发生的涨停记录」, 用 T 日信息不构成未来函数
        (09 册第 7 节禁止的是 T+1 及以后); 但这些标签绝不能当盘前买入信号。
    事件属性类从 tr 直接取。数据不足 / 停牌缺失一律判为不命中, 不猜、不补。
    """
    n = len(tr)
    d2i = {d: i for i, d in enumerate(cal)}
    t_idx = np.array([d2i.get(str(t), -1) for t in tr["t"]], dtype=np.int32)
    s_idx = np.array([sym2i.get(s, -1) for s in tr["symbol"]], dtype=np.int32)
    # 板位 (1..6, 6 = >5板): 01-05「主升加速板」原文口径要求 bp ≥2, 故必须进 build_tags
    bp_all = tr["bp"].to_numpy()
    masks = np.zeros((n, TAG_WORDS), dtype=np.int64)

    def _bit_val(bit: int) -> tuple[int, int]:
        """位号 -> (word 下标, 该 word 内的位偏移)。"""
        return divmod(bit, TAG_BITS_PER_WORD)

    # --- 事件属性 (bit 9..13, 27..29), 不依赖张量 ---
    event_bits = [
        (9, (tr["yizi_t"].to_numpy() == 1) | (tr["yizi_b"].to_numpy() == 1)),
        (10, tr["broken_limit"].to_numpy() >= 30),
        (11, tr["phase"].fillna("").astype(str).isin(["ice", "ebb"]).to_numpy()),
        (12, tr["mktcap"].to_numpy() < 50),
        (13, tr["gap"].to_numpy() > 0.03),
        (27, tr["seal_rate"].to_numpy() >= 0.8),
        (28, tr["limit_up"].to_numpy() >= 100),
        (29, tr["state"].fillna("").astype(str).isin(["weak", "lean_weak"]).to_numpy()),
    ]
    for bit, cond in event_bits:
        w, off = _bit_val(bit)
        masks[:, w] |= np.where(np.asarray(cond, dtype=bool), 1 << off, 0).astype(np.int64)

    # --- 前置形态 (bit 0..8, 14..26), 分块取 HIST 根历史, 避免一次性占几百 MB ---
    offs = np.arange(1, HIST + 1, dtype=np.int32)          # T-1 .. T-HIST
    n_sym, n_date = SC.shape[0], len(cal)
    chunk = 20000
    for a in range(0, n, chunk):
        b = min(n, a + chunk)
        m = b - a
        ti = t_idx[a:b][:, None]
        si = s_idx[a:b][:, None]
        di = ti - offs[None, :]
        ok = (ti >= 0) & (si >= 0) & (di >= 0)
        dic = np.clip(di, 0, n_date - 1)
        sic = np.broadcast_to(np.clip(si, 0, n_sym - 1), (m, HIST))
        W = SC[sic, dic, :].astype(np.float64)
        W[~np.broadcast_to(ok[:, :, None], W.shape)] = np.nan
        C, H, L, O, A, CLU = (W[:, :, 3], W[:, :, 1], W[:, :, 2],
                              W[:, :, 0], W[:, :, 5], W[:, :, 7])
        c0 = C[:, 0]

        # --- T 日单根 (第三批「完整·复盘」标签用) ---
        # 打标对象是「已发生的涨停记录」, T 日 K 线/成交额是当天收盘就有的信息,
        # 不是未来函数 (09 册第 7 节禁止的是 T+1 及以后)。但带这些条件的标签
        # 只能用于复盘切片, 绝不能当盘前买入信号 —— 前端标 timing=review。
        okT = ((ti >= 0) & (si >= 0) & (ti < n_date)).ravel()
        siT = np.clip(si, 0, n_sym - 1).ravel()
        diT = np.clip(ti, 0, n_date - 1).ravel()
        WT = SC[siT, diT, :].astype(np.float64)          # (m, 8)
        WT[~okT] = np.nan
        CT, HT, LT, OT, AT = WT[:, 3], WT[:, 1], WT[:, 2], WT[:, 0], WT[:, 5]

        def nanmean(x, cnt_min):
            """忽略缺失求均值, 返回 (均值, 有效根数, 根数是否够)。数据不足一律判不命中。"""
            finite = np.isfinite(x)
            cnt = finite.sum(axis=1)
            return (np.where(finite, x, 0).sum(axis=1) / np.maximum(cnt, 1),
                    cnt, cnt >= cnt_min)

        # ---- 第八批几何 helper: 07 册境外形态共用 (第16册要求先建基建再批量落地) ----
        def seg_ext(x, edges, mode="max"):
            """按 [a,b) 列分段取极值 -> (m, 段数)。列 k = T-(k+1), 列 0 = T-1。"""
            f = np.max if mode == "max" else np.min
            return np.column_stack([f(x[:, a:b], axis=1) for a, b in edges])

        def seg_avg(x, edges):
            """按 [a,b) 列分段求忽略缺失的均值 -> (m, 段数); 全缺段记 NaN。"""
            out = []
            for a, b in edges:
                fin = np.isfinite(x[:, a:b])
                cnt = fin.sum(axis=1)
                out.append(np.where(fin, x[:, a:b], 0).sum(axis=1) / np.maximum(cnt, 1))
            return np.column_stack(out)

        full21 = np.isfinite(C[:, :21]).all(axis=1)
        full24 = np.isfinite(C[:, :24]).all(axis=1)
        Hf = np.where(np.isfinite(H), H, -np.inf)
        Lf = np.where(np.isfinite(L), L, np.inf)
        Cf = np.where(np.isfinite(C), C, -np.inf)
        rng5 = (Hf[:, :5].max(axis=1) - Lf[:, :5].min(axis=1)) / c0

        # bit0 大跌后涨停: T-1 相对 T-21 跌幅 ≥15%
        r20 = c0 / C[:, 20] - 1
        t0 = full21 & np.isfinite(r20) & (r20 <= -0.15)
        # bit1 位置偏低: 距 60 日最低 ≤20%
        lo60 = Lf[:, :60].min(axis=1)
        t1 = full21 & np.isfinite(lo60) & (lo60 > 0) & ((c0 / lo60 - 1) <= 0.20)
        # bit2 突破阶段新高: T-1 收盘 ≥ 前 20 日最高收盘
        mx20 = np.where(np.isfinite(C[:, 1:21]), C[:, 1:21], -np.inf).max(axis=1)
        t2 = full21 & np.isfinite(mx20) & (c0 >= mx20)
        # bit3 均线多头
        ma5, ma10, ma20 = C[:, :5].mean(axis=1), C[:, :10].mean(axis=1), C[:, :20].mean(axis=1)
        t3 = full21 & (ma5 > ma10) & (ma10 > ma20)
        # bit4 横盘后涨停: 5 日振幅 ≤6% 且 5 日涨跌幅 ≤5%
        r5 = c0 / C[:, 5] - 1
        t4 = full21 & np.isfinite(rng5) & (rng5 <= 0.06) & (np.abs(r5) <= 0.05)
        # bit5 缩量窄幅洗盘: 近 5 日均额 / 前 15 日均额 ≤0.7 且 5 日振幅 ≤8%
        aok = np.isfinite(A[:, :20]).all(axis=1) & (A[:, 5:20].mean(axis=1) > 0)
        a5, a20 = A[:, :5].mean(axis=1), A[:, 5:20].mean(axis=1)
        t5 = aok & (a5 / a20 <= 0.7) & np.isfinite(rng5) & (rng5 <= 0.08)
        # bit6 长上影试盘
        body = np.abs(c0 - O[:, 0])
        upper = H[:, 0] - np.maximum(O[:, 0], c0)
        t6 = np.isfinite(upper) & np.isfinite(body) & (body > 0) & (upper >= 2 * body)
        # bit7 连阳蓄势 (红三兵)
        yang = (C[:, 0] > O[:, 0]) & (C[:, 1] > O[:, 1]) & (C[:, 2] > O[:, 2])
        small = (np.abs(C[:, 0] / C[:, 1] - 1) <= 0.03) & (np.abs(C[:, 1] / C[:, 2] - 1) <= 0.03) & (np.abs(C[:, 2] / C[:, 3] - 1) <= 0.03)
        higher = (H[:, 0] > H[:, 1]) & (H[:, 1] > H[:, 2])
        t7 = (np.isfinite(C[:, :4]).all(axis=1) & np.isfinite(O[:, :3]).all(axis=1)
              & np.isfinite(H[:, :3]).all(axis=1) & yang & small & higher)
        # bit8 放量突破
        av = A[:, 1:6].mean(axis=1)
        mh = Hf[:, 1:6].max(axis=1)
        t8 = aok & (av > 0) & (A[:, 0] / av >= 1.8) & (c0 > mh)

        # ---- 第二批: 位置与趋势 (bit 14..18) ----
        # bit14 深度回撤: 距近 20 日最高收盘 ≥15%
        mx20all = Cf[:, :20].max(axis=1)
        t14 = full21 & np.isfinite(mx20all) & (mx20all > 0) & (c0 / mx20all - 1 <= -0.15)
        # bit15 乖离超卖: 收盘较 MA24 低 ≥15%
        ma24 = C[:, :24].sum(axis=1) / 24
        t15 = full24 & np.isfinite(ma24) & (ma24 > 0) & (c0 / ma24 - 1 <= -0.15)
        # bit16 接近年内高位: 距近 60 日最高收盘 ≤5%
        mx60 = Cf[:, :60].max(axis=1)
        t16 = full21 & np.isfinite(mx60) & (mx60 > 0) & (c0 / mx60 - 1 >= -0.05)
        # bit17 均线粘合: 三均线极差 ≤ 收盘的 2%
        ma_max = np.maximum(np.maximum(ma5, ma10), ma20)
        ma_min = np.minimum(np.minimum(ma5, ma10), ma20)
        t17 = full21 & np.isfinite(ma_max) & (ma_min > 0) & ((ma_max - ma_min) / c0 <= 0.02)
        # bit18 回踩均线: 最低价触及 MA20 ±2%、收盘站上且 MA20 上行
        ma20_prev = C[:, 1:21].mean(axis=1)
        t18 = (full21 & np.isfinite(L[:, 0]) & (ma20 > ma20_prev)
               & (np.abs(L[:, 0] / ma20 - 1) <= 0.02) & (c0 > ma20))

        # ---- 第二批: 形态与量能 (bit 19..26) ----
        # bit19 波动压缩: 前 5 日平均振幅 / 前 20 日平均振幅 ≤0.6
        with np.errstate(invalid="ignore", divide="ignore"):
            amp = (H - L) / C
        amp5, _, ok5 = nanmean(amp[:, :5], 4)
        amp20, _, ok20 = nanmean(amp[:, :20], 16)
        with np.errstate(invalid="ignore", divide="ignore"):
            t19 = ok5 & ok20 & (amp20 > 0) & (amp5 / amp20 <= 0.6)
        # bit20 长下影大长腿
        lower = np.minimum(O[:, 0], c0) - L[:, 0]
        t20 = np.isfinite(lower) & np.isfinite(body) & (body > 0) & (lower >= 2 * body)
        # bit21 阳包阴: T-1 阳线实体完全包住 T-2 阴线实体
        engulf = ((C[:, 0] > O[:, 0]) & (C[:, 1] < O[:, 1])
                  & (O[:, 0] <= C[:, 1]) & (C[:, 0] >= O[:, 1]))
        t21 = (np.isfinite(C[:, :2]).all(axis=1)
               & np.isfinite(O[:, :2]).all(axis=1) & engulf)
        # bit22 最后一跌: T-1 单日跌幅 ≥5%
        prev_ret = c0 / C[:, 1] - 1
        t22 = np.isfinite(prev_ret) & (prev_ret <= -0.05)
        # bit23 超跌小阳堆积: 20 日跌 ≥15% + 近 5 日 ≥4 阳且单日均 ≤3%
        y5 = (C[:, :5] > O[:, :5]).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            small5 = (np.abs(C[:, :5] / C[:, 1:6] - 1) <= 0.03).all(axis=1)
        t23 = (full21 & (r20 <= -0.10) & (y5 >= 4) & small5
               & np.isfinite(C[:, :6]).all(axis=1) & np.isfinite(O[:, :5]).all(axis=1))
        # bit24 价升量缩: 近 5 日涨 ≥3% 且 5 日均额 / 前 15 日均额 ≤0.8
        t24 = aok & np.isfinite(r5) & (r5 >= 0.03) & (a5 / a20 <= 0.8)
        # bit25 四连阳一阴: 恰 4 阳 1 阴且阴线成交额 < 5 日均额
        yin_amt = np.where(C[:, :5] <= O[:, :5], A[:, :5], 0).sum(axis=1)
        t25 = (full21 & np.isfinite(A[:, :5]).all(axis=1) & (y5 == 4)
               & (a5 > 0) & (yin_amt < a5))
        # bit26 N 字回马枪: 近 20 日内曾涨停(连板≥1)且 T-1 较该涨停日收盘回调 ≥5%
        clu_hist = np.where(np.isfinite(CLU[:, 1:20]), CLU[:, 1:20], 0)
        had_lu = (clu_hist >= 1).any(axis=1)
        peak_c = np.where(clu_hist >= 1, C[:, 1:20], -np.inf).max(axis=1)
        t26 = (full21 & had_lu & np.isfinite(peak_c) & (peak_c > 0)
               & (c0 / peak_c - 1 <= -0.05))

        # ---- 第三批: 完整口径 (bit 30..36), 按 01/02 册建议口径逐条补齐 ----
        # 量能基准: amt_ratio = amount_T / mean(amount_{T-5..T-1}) (09 册定义)
        amt_base = A[:, :5].mean(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            amt_ratio = AT / np.where(amt_base > 0, amt_base, np.nan)

        # bit30 平台突破板·完整 (01-02): 平台 + T 日放量 ≥2 倍 + 收盘破平台上沿
        plat_top = Hf[:, :5].max(axis=1)
        t30 = (full21 & np.isfinite(rng5) & (rng5 <= 0.08) & np.isfinite(r5) & (np.abs(r5) <= 0.05)
               & np.isfinite(amt_ratio) & (amt_ratio >= 2)
               & np.isfinite(CT) & np.isfinite(plat_top) & (CT > plat_top))

        # bit31 前高突破板·完整 (01-03): C_T 破前 20 日最高价 + 前 20 日存在局部高点
        ph20 = Hf[:, :20].max(axis=1)
        with np.errstate(invalid="ignore"):
            loc = (np.isfinite(H[:, 1:19]) & np.isfinite(H[:, 0:18]) & np.isfinite(H[:, 2:20])
                   & (H[:, 1:19] > H[:, 0:18]) & (H[:, 1:19] > H[:, 2:20]))
        has_local_high = loc.any(axis=1)
        t31 = (full21 & np.isfinite(CT) & np.isfinite(ph20) & (CT > ph20) & has_local_high)

        # bit32 一阳穿多线·完整 (01-15): 涨停阳线由三均线下方穿到上方
        ma_min = np.minimum(np.minimum(ma5, ma10), ma20)
        ma_max = np.maximum(np.maximum(ma5, ma10), ma20)
        t32 = (full21 & np.isfinite(OT) & np.isfinite(CT) & (CT > OT)
               & (OT < ma_min) & (CT > ma_max))

        # bit33 反包涨停·完整 (02-01): T-1 阴线 + T 日实体吞没 + 创新高 + 放量
        t33 = (np.isfinite(c0) & np.isfinite(O[:, 0]) & (c0 < O[:, 0])
               & np.isfinite(CT) & (CT > np.maximum(O[:, 0], c0))
               & np.isfinite(HT) & np.isfinite(H[:, 0]) & (HT > H[:, 0])
               & np.isfinite(amt_ratio) & (amt_ratio >= 1.2))

        # bit34 双响炮N字·完整 (02-03): 2–10 日内曾涨停 + T-1 未涨停(回调)
        #                              + 缩量 + T 日收盘破前置涨停日高点
        clu_recent = np.where(np.isfinite(CLU[:, 1:10]), CLU[:, 1:10], 0)
        had_lu_2_10 = (clu_recent >= 1).any(axis=1)
        peak_recent = np.where(clu_recent >= 1, C[:, 1:10], -np.inf).max(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            # 严格对齐 09 册 amt_shrink = mean(amount_{T-5..T-1}) / mean(amount_{T-25..T-6})
            amt20 = A[:, 5:25].mean(axis=1)
            amt_shrink = amt_base / np.where(amt20 > 0, amt20, np.nan)
        not_lu_t1 = np.nan_to_num(CLU[:, 0], nan=0.0) < 1
        t34 = (full21 & had_lu_2_10 & not_lu_t1 & np.isfinite(peak_recent) & (peak_recent > 0)
               & np.isfinite(amt_shrink) & (amt_shrink <= 0.7)
               & np.isfinite(CT) & (CT > peak_recent))

        # bit35 长上影试盘·完整 (02-13, 事前): 上影 ≥2×实体 + 短下影 + 量能放大
        with np.errstate(invalid="ignore", divide="ignore"):
            amt6 = A[:, 1:6].mean(axis=1)
            amt_up1 = A[:, 0] / np.where(amt6 > 0, amt6, np.nan)
        t35 = (np.isfinite(upper) & np.isfinite(lower) & np.isfinite(body) & (body > 0)
               & (upper >= 2 * body) & (lower <= 0.2 * body)
               & np.isfinite(amt_up1) & (amt_up1 >= 1.5))

        # bit36 大长腿·完整 (02-14, 事前): 日内振幅 ≥10% + 长下影 + 实体大阳线
        with np.errstate(invalid="ignore", divide="ignore"):
            day_amp = (H[:, 0] - L[:, 0]) / np.where(L[:, 0] > 0, L[:, 0], np.nan)
        t36 = (np.isfinite(day_amp) & (day_amp >= 0.10)
               & np.isfinite(lower) & np.isfinite(body) & (body > 0) & (lower >= 2 * body)
               & (c0 > O[:, 0]))

        # ---- 第四批 (bit 37..52): 72 条待实现里 16 条「日线可算」维度 ----
        # 中间量口径与第 13 册记录的探查脚本完全一致: 忽略停牌缺失求均值, 数据不足判不命中。
        def _nm(x):
            f = np.isfinite(x)
            c = f.sum(axis=1)
            return np.where(f, x, 0).sum(axis=1) / np.where(c > 0, c, np.nan)

        with np.errstate(invalid="ignore", divide="ignore"):
            a5_p4, a20_p4 = _nm(A[:, :5]), _nm(A[:, 5:25])
            amt_ratio_p4 = AT / np.where(a5_p4 > 0, a5_p4, np.nan)
            amt_r1_p4 = AT / A[:, 0]
            amt_rel1_p4 = A[:, 0] / _nm(A[:, 1:6])
            amt_shrink_p4 = a5_p4 / np.where(a20_p4 > 0, a20_p4, np.nan)
            ret1_p4 = c0 / C[:, 1] - 1
            highpos_p4 = c0 / np.where(mx60 > 0, mx60, np.nan) >= 0.95

        # bit37 01-04 箱体突破板 (复盘): 10 根箱体宽度 ≤15% + C_T 破上沿
        box_hi = Hf[:, 1:11].max(axis=1)
        box_lo = Lf[:, 1:11].min(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            boxw = (box_hi - box_lo) / np.where(box_lo > 0, box_lo, np.nan)
        t37 = (full21 & np.isfinite(boxw) & (boxw <= 0.15)
               & np.isfinite(CT) & (CT > box_hi))

        # bit38 01-12 W底突破板 (复盘): 两区间低点价差 ≤3% + C_T 破颈线
        lo_near, lo_far = Lf[:, :10].min(axis=1), Lf[:, 10:20].min(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            w_gap = np.abs(lo_near / np.where(lo_far > 0, lo_far, np.nan) - 1)
        neck = Hf[:, 4:12].max(axis=1)
        t38 = (full21 & np.isfinite(w_gap) & (w_gap <= 0.03)
               & np.isfinite(neck) & np.isfinite(CT) & (CT > neck))

        # 02 册两条「回马枪」共用的前置涨停窗口 (T-2..T-21)
        clu_p4 = np.where(np.isfinite(CLU[:, 1:21]), CLU[:, 1:21], 0)
        had_lu_p4 = (clu_p4 >= 1).any(axis=1)
        peak_p4 = np.where(clu_p4 >= 1, Cf[:, 1:21], -np.inf).max(axis=1)

        # bit39 02-05 单板回马枪 (事前): 前置连板峰值=1 的单板档 + 回调 ≥5%
        t39 = (full21 & had_lu_p4 & (clu_p4.max(axis=1) == 1)
               & np.isfinite(peak_p4) & (peak_p4 > 0) & (c0 / peak_p4 - 1 <= -0.05))
        # bit40 02-06 平台型回马枪 (事前): 曾涨停 + 回调期横盘 + 缩量 + 小幅回调
        t40 = (full21 & had_lu_p4 & np.isfinite(rng5) & (rng5 <= 0.09)
               & np.isfinite(amt_shrink_p4) & (amt_shrink_p4 <= 0.9)
               & np.isfinite(peak_p4) & (peak_p4 > 0) & (c0 / peak_p4 - 1 <= -0.03))
        # bit41 02-11 早晨之星 (事前): 大阴 + 十字星 + 收复
        t41 = ((C[:, 2] < O[:, 2]) & (np.abs(C[:, 2] / C[:, 3] - 1) >= 0.03)
               & (np.abs(C[:, 1] - O[:, 1]) / np.where(C[:, 1] > 0, C[:, 1], np.nan) <= 0.01)
               & (C[:, 0] > (O[:, 2] + C[:, 2]) / 2)
               & np.isfinite(C[:, :4]).all(axis=1) & np.isfinite(O[:, :3]).all(axis=1))
        # bit42 02-12 倒锤头/锤头线 (事前): 长上影 + 短下影 + 缩量
        t42 = (np.isfinite(body) & (body > 0) & (upper >= 2 * body)
               & (lower <= 0.2 * body) & np.isfinite(amt_shrink_p4) & (amt_shrink_p4 <= 0.8))

        # bit43 03-02 T字板 (复盘): 开=高=收(涨停价) + 盘中有下影
        t43 = (np.isfinite(CT) & (CT > 0)
               & (np.abs(OT - CT) / CT <= 0.005) & (np.abs(HT - CT) / CT <= 0.005)
               & ((CT - LT) / CT >= 0.01))
        # bit44 03-03 缩量板 (复盘): amount_T / amount_{T-1} ≤0.7 (原文定量)
        t44 = np.isfinite(amt_r1_p4) & (amt_r1_p4 <= 0.7)
        # bit45 03-04 实体换手板 (复盘, 代理): 非一字 + 实体大阳 + 日内震荡
        t45 = (np.isfinite(OT) & (OT > 0) & np.isfinite(CT) & (CT > OT)
               & ((CT - OT) / OT >= 0.03) & np.isfinite(HT) & np.isfinite(LT) & (LT > 0)
               & ((HT - LT) / LT >= 0.05) & ((CT - LT) / CT <= 0.09))
        # bit46 03-23 高位爆量板 (复盘, 分档取一档): amt_ratio ≥5 + 位置偏高
        t46 = full21 & np.isfinite(amt_ratio_p4) & (amt_ratio_p4 >= 5.0) & highpos_p4

        # bit47 05-06 长阴短柱 (事前): T-1 跌 ≥4% + 量比 ≤0.7
        t47 = (np.isfinite(ret1_p4) & (ret1_p4 <= -0.04)
               & np.isfinite(amt_rel1_p4) & (amt_rel1_p4 <= 0.7))
        # bit48 05-07 长阳矮柱 (事前): T-1 涨 ≥5% + 量比 ≤0.9
        t48 = (np.isfinite(ret1_p4) & (ret1_p4 >= 0.05)
               & np.isfinite(amt_rel1_p4) & (amt_rel1_p4 <= 0.9))
        # bit49 05-08 倍量伸缩 (事前): 倍量 → 缩量 → 倍量
        t49 = (np.isfinite(A[:, :4]).all(axis=1) & (A[:, 1] > 0) & (A[:, 2] > 0) & (A[:, 3] > 0)
               & (A[:, 0] / A[:, 1] >= 1.5) & (A[:, 1] / A[:, 2] <= 0.7)
               & (A[:, 2] / A[:, 3] >= 1.5))
        # bit50 05-09 缩量回调 (事前): 三日量递减 + 价跌
        t50 = (np.isfinite(A[:, :3]).all(axis=1) & (A[:, 0] < A[:, 1]) & (A[:, 1] < A[:, 2])
               & np.isfinite(C[:, 3]) & (c0 < C[:, 3]))

        # bit51 08-01 连涨加速型 (复盘): T-1 涨 8%–16% + T 日放量 2–5 倍
        t51 = (np.isfinite(ret1_p4) & (ret1_p4 >= 0.08) & (ret1_p4 <= 0.16)
               & np.isfinite(amt_ratio_p4) & (amt_ratio_p4 >= 2.0) & (amt_ratio_p4 <= 5.0))
        # bit52 08-02 横盘突发型 (复盘): 前 5 日横盘 |r5| ≤2% + T 日放量 ≥2
        t52 = (full21 & np.isfinite(r5) & (np.abs(r5) <= 0.02)
               & np.isfinite(amt_ratio_p4) & (amt_ratio_p4 >= 2.0))

        # ---- 第五批 (bit 53..60): 首批落到 word1 的标签 ----
        # 中间量口径与第 14 册记录的生产同源探查完全一致 (含 body>0 保护)。
        with np.errstate(invalid="ignore", divide="ignore"):
            # bit53 01-05 主升加速板 (当日): C_{T-1} > MA20 > MA60 + ret5 ≥8% + bp ≥2
            ma20_p5, ma60_p5 = _nm(C[:, :20]), _nm(C[:, :60])
            t53 = (np.isfinite(c0) & np.isfinite(ma20_p5) & np.isfinite(ma60_p5) & (ma60_p5 > 0)
                   & (c0 > ma20_p5) & (ma20_p5 > ma60_p5)
                   & np.isfinite(r5) & (r5 >= 0.08) & (bp_all[a:b] >= 2))

            # bit54 01-17 吸筹后突破 (事前): 先涨 5 日 (T-10..T-6) → 再缩量整理 5 日 (T-5..T-1)
            r5a_p5 = C[:, 5] / C[:, 10] - 1
            lo5_p5, hi5_p5 = Lf[:, :5].min(axis=1), Hf[:, :5].max(axis=1)
            rng5b_p5 = (hi5_p5 - lo5_p5) / np.where(lo5_p5 > 0, lo5_p5, np.nan)
            t54 = (np.isfinite(r5a_p5) & (r5a_p5 >= 0.02) & (r5a_p5 <= 0.10)
                   & np.isfinite(amt_shrink_p4) & (amt_shrink_p4 <= 0.7)
                   & np.isfinite(rng5b_p5) & (rng5b_p5 <= 0.06))

            # bit55 06-04 黄金柱 (事前, 放宽): T-4 阳线 + 量 ≥2×前5日均量 + 后3日至少2日价升量缩
            av5_p5 = _nm(A[:, 4:9])
            hj_amt_p5 = A[:, 3] / np.where(av5_p5 > 0, av5_p5, np.nan)
            up_step_p5 = np.stack([C[:, 2] > C[:, 3], C[:, 1] > C[:, 2], C[:, 0] > C[:, 1]])
            dn_vol_p5 = np.stack([A[:, 2] < A[:, 3], A[:, 1] < A[:, 2], A[:, 0] < A[:, 1]])
            seq3_p5 = (up_step_p5 & dn_vol_p5).sum(axis=0)
            t55 = (np.isfinite(hj_amt_p5) & (hj_amt_p5 >= 2.0)
                   & np.isfinite(C[:, 3]) & np.isfinite(O[:, 3]) & (C[:, 3] > O[:, 3])
                   & (seq3_p5 >= 2))

        # bit56 06-10 双剑霸天地 (事前): 相邻两日一长上影一长下影, 原文「分别」不限顺序
        def _shadow_p5(k: int):
            """第 k 根 (0=T-1) 的上/下影与实体之比; 实体为 0 的十字星判为不可用。"""
            bd = np.abs(C[:, k] - O[:, k])
            okb = np.isfinite(bd) & (bd > 0)
            with np.errstate(invalid="ignore", divide="ignore"):
                up = (H[:, k] - np.maximum(O[:, k], C[:, k])) / np.where(okb, bd, np.nan)
                dn = (np.minimum(O[:, k], C[:, k]) - L[:, k]) / np.where(okb, bd, np.nan)
            return up, dn, okb

        u2_p5, d2_p5, ok2_p5 = _shadow_p5(1)
        u1_p5, d1_p5, ok1_p5 = _shadow_p5(0)
        t56 = (ok1_p5 & ok2_p5
               & (((u2_p5 >= 2) & (d1_p5 >= 2)) | ((d2_p5 >= 2) & (u1_p5 >= 2))))

        # bit57 06-11 假阴真阳 (事前): T-1 收<开 (阴线外观) 但 收>昨收 (实际收涨)
        t57 = (np.isfinite(C[:, 0]) & np.isfinite(O[:, 0]) & np.isfinite(C[:, 1])
               & (C[:, 0] < O[:, 0]) & (C[:, 0] > C[:, 1]))

        # bit58..60 01-19 位置分档 (事前): dd60 = 距 60 日最高价回撤, 按固定分位切三档
        hi60_p5 = Hf[:, :60].max(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            dd60_p5 = (hi60_p5 - c0) / np.where(hi60_p5 > 0, hi60_p5, np.nan)
        dd_ok = np.isfinite(dd60_p5)
        t58 = dd_ok & (dd60_p5 >= DD60_P67)                        # 回撤最大 1/3 → 低位
        t59 = dd_ok & (dd60_p5 >= DD60_P33) & (dd60_p5 < DD60_P67)
        t60 = dd_ok & (dd60_p5 < DD60_P33)                         # 最贴近高点 1/3 → 高位

        # ---- 第六批 (bit 61..70): 06 册量学体系, 窗口从 60 扩到 HIST=100 ----
        # 06-02 需要「百日」低量, 这是本模块唯一用到 60 根以上历史的地方。
        # 阈值都是逐笔现算的分位数/比值, 没有新增固定常数, 换数据不会失配。
        with np.errstate(invalid="ignore", divide="ignore"):
            # 06-02 地量群: 近 100 日成交额 10% 分位以下连续 ≥5 日
            a_cnt_p6 = np.isfinite(A).sum(axis=1)
            with warnings.catch_warnings():
                # 全缺失行 nanquantile 会返回 nan 并报 RuntimeWarning, 这些行下面一律判不命中
                warnings.simplefilter("ignore", RuntimeWarning)
                q10_p6 = np.nanquantile(np.where(np.isfinite(A), A, np.nan), 0.10, axis=1)
            lowq_p6 = (A <= q10_p6[:, None]) & np.isfinite(A)
            run_p6 = np.zeros(m, dtype=np.int32)
            best_run_p6 = np.zeros(m, dtype=np.int32)
            for j in range(HIST):
                run_p6 = np.where(lowq_p6[:, j], run_p6 + 1, 0)
                best_run_p6 = np.maximum(best_run_p6, run_p6)
            t61 = (a_cnt_p6 >= 90) & np.isfinite(q10_p6) & (best_run_p6 >= 5)

            # 06-03 矮将军: 低量群必须在 T-1 之前 (给「小倍阳」让位), 故从第 2 列扫描
            run2_p6 = np.zeros(m, dtype=np.int32)
            best2_p6 = np.zeros(m, dtype=np.int32)
            for j in range(1, HIST):
                run2_p6 = np.where(lowq_p6[:, j], run2_p6 + 1, 0)
                best2_p6 = np.maximum(best2_p6, run2_p6)
            xb_p6 = A[:, 0] / np.where(A[:, 1] > 0, A[:, 1], np.nan)   # 小倍阳: 1–2 倍前日量
            t62 = ((a_cnt_p6 >= 90) & np.isfinite(q10_p6) & (best2_p6 >= 5)
                   & np.isfinite(xb_p6) & (xb_p6 >= 1.0) & (xb_p6 <= 2.0)
                   & np.isfinite(C[:, 0]) & np.isfinite(O[:, 0]) & (C[:, 0] > O[:, 0])
                   & ((C[:, 0] / O[:, 0] - 1) <= 0.03))

            # 06-06 过左峰: C_{T-1} 有效突破 60 日左峰, 且窗口内确有局部极大
            peak60_p6 = Hf[:, 1:61].max(axis=1)
            loc60_p6 = (np.isfinite(H[:, 1:60]) & np.isfinite(H[:, 0:59]) & np.isfinite(H[:, 2:61])
                        & (H[:, 1:60] > H[:, 0:59]) & (H[:, 1:60] > H[:, 2:61]))
            t63 = (np.isfinite(peak60_p6) & (peak60_p6 > 0) & np.isfinite(c0)
                   & (c0 > peak60_p6 * 1.01) & loc60_p6.any(axis=1))

            # 06-13 极阴次阳: 两连阴累跌 ≥5% + T-1 阳线反包过半
            ret2_p6 = C[:, 1] / C[:, 2] - 1
            ret3_p6 = C[:, 2] / C[:, 3] - 1
            cum2_p6 = C[:, 1] / C[:, 3] - 1
            t64 = (np.isfinite(ret2_p6) & np.isfinite(ret3_p6) & (ret2_p6 <= 0) & (ret3_p6 <= 0)
                   & (cum2_p6 <= -0.05) & np.isfinite(C[:, 0]) & np.isfinite(O[:, 0])
                   & (C[:, 0] > O[:, 0]) & np.isfinite(O[:, 1]) & np.isfinite(C[:, 1])
                   & (C[:, 0] >= (O[:, 1] + C[:, 1]) / 2))

            # 06-14 三元连动: 直接复用 06-04 那段的 seq3 (T-1/T-2/T-3 三日价升量缩计数)
            t65 = seq3_p5 >= 3

            # 06-17 现场直憋: 10 日窄幅 ≤8% + 10 日均额 / 前 60 日均额 ≤0.7
            hi10_p6 = Hf[:, :10].max(axis=1)
            lo10_p6 = Lf[:, :10].min(axis=1)
            rng10_p6 = (hi10_p6 - lo10_p6) / np.where(lo10_p6 > 0, lo10_p6, np.nan)
            a60_p6 = _nm(A[:, 10:70])
            shr_p6 = _nm(A[:, :10]) / np.where(a60_p6 > 0, a60_p6, np.nan)
            t66 = (np.isfinite(rng10_p6) & (rng10_p6 <= 0.08)
                   & np.isfinite(shr_p6) & (shr_p6 <= 0.7))

            # 06-20 黄金十字架 (代理): 左支点 + 回踩 ±2% + 收盘站上
            pivot_p6 = Hf[:, 10:40].max(axis=1)
            t67 = (np.isfinite(pivot_p6) & (pivot_p6 > 0) & np.isfinite(lo10_p6)
                   & (np.abs(lo10_p6 / pivot_p6 - 1) <= 0.02)
                   & np.isfinite(c0) & (c0 >= pivot_p6))

            # 06-22 兵临城下: 三连小阳 + 高点逐日抬高 + 仍压在 20 日左峰下方
            r1_p6 = C[:, 0] / C[:, 1] - 1
            r2_p6 = C[:, 1] / C[:, 2] - 1
            r3_p6 = C[:, 2] / C[:, 3] - 1
            s3_p6 = ((r1_p6 > 0) & (r1_p6 <= 0.03) & (r2_p6 > 0) & (r2_p6 <= 0.03)
                     & (r3_p6 > 0) & (r3_p6 <= 0.03))
            up3_p6 = (H[:, 0] > H[:, 1]) & (H[:, 1] > H[:, 2])
            peak20_p6 = Hf[:, 1:21].max(axis=1)
            t68 = (s3_p6 & up3_p6 & np.isfinite(peak20_p6) & np.isfinite(H[:, 1])
                   & (H[:, 1] < peak20_p6) & np.isfinite(c0) & (c0 <= peak20_p6))

            # 06-23 牛股三绝 (三子条件取交集, 并集覆盖 58% 无区分度):
            #   cm_p6[:, j] = min(L_{T-1}..L_{T-(j+1)}), 即「该日之后到 T-1 的最低价」
            cm_p6 = np.minimum.accumulate(Lf[:, :20], axis=1)
            vmax20_p6 = np.where(np.isfinite(A[:, 1:21]), A[:, 1:21], -np.inf).max(axis=1)
            gap_p6 = np.zeros(m, dtype=bool)
            bei_p6 = np.zeros(m, dtype=bool)
            hi_p6 = np.zeros(m, dtype=bool)
            for j in range(19):
                # 跳空发生在 T-(j+1), 缺口下沿 = H_{T-(j+2)}; 之后最低价未跌回下沿
                gap_p6 |= ((L[:, j + 1] > H[:, j + 2]) & np.isfinite(H[:, j + 2])
                           & (cm_p6[:, j] > H[:, j + 2]))
                rr = A[:, j + 1] / A[:, j + 2]
                bei_p6 |= (np.isfinite(rr) & (rr >= 2.0) & (C[:, j + 1] > O[:, j + 1])
                           & np.isfinite(L[:, j + 1]) & (cm_p6[:, j] > L[:, j + 1]))
                hi_p6 |= (np.isfinite(A[:, j + 1]) & np.isfinite(vmax20_p6)
                          & (A[:, j + 1] >= vmax20_p6) & (C[:, j + 1] > O[:, j + 1])
                          & np.isfinite(L[:, j + 1]) & (cm_p6[:, j] > L[:, j + 1]))
            t69 = gap_p6 & bei_p6 & hi_p6

            # 06-24 大阳双休: T-3 大阳, T-2/T-1 两根小实体且都站在大阳收盘之上
            t70 = (np.isfinite(r3_p6) & (r3_p6 >= 0.05)
                   & np.isfinite(r2_p6) & (np.abs(r2_p6) <= 0.02)
                   & np.isfinite(r1_p6) & (np.abs(r1_p6) <= 0.02)
                   & np.isfinite(C[:, 2]) & np.isfinite(C[:, 1]) & np.isfinite(C[:, 0])
                   & (C[:, 1] >= C[:, 2]) & (C[:, 0] >= C[:, 2]))

            # ================= 第七批: 01 册形态复合 (bit 71..74) =================
            # bit71 散户坑/黄金坑: 平台窄幅 -> 末端缩量挖坑跌破下沿 -> T-1 收回下沿之上
            p_lo71 = Lf[:, 9:21].min(axis=1)
            p_hi71 = Hf[:, 9:21].max(axis=1)
            p_amp71 = np.where(p_lo71 > 0, (p_hi71 - p_lo71) / p_lo71, np.nan)
            p_amt71, _, p_amt71_ok = nanmean(A[:, 9:21], 10)
            k_lo71 = Lf[:, 1:9].min(axis=1)
            k_amt71, _, k_amt71_ok = nanmean(A[:, 1:9], 4)
            t71 = (np.isfinite(C[:, :21]).all(axis=1)
                   & np.isfinite(p_amp71) & (p_amp71 <= 0.12)
                   & np.isfinite(k_lo71) & (k_lo71 < p_lo71 * 0.99)
                   & p_amt71_ok & k_amt71_ok & (p_amt71 > 0) & (k_amt71 <= p_amt71 * 0.80)
                   & np.isfinite(c0) & (c0 > p_lo71))

            # bit72 彩虹桥: 近 20 日 4 段最低价逐段抬高 + 最高价受制于同一阻力线 + 逼近阻力
            s_lo72 = [Lf[:, i * 5:(i + 1) * 5].min(axis=1) for i in range(4)]
            s_hi72 = [Hf[:, i * 5:(i + 1) * 5].max(axis=1) for i in range(4)]
            res_hi72 = np.maximum.reduce(s_hi72)
            res_lo72 = np.minimum.reduce(s_hi72)
            t72 = (np.isfinite(C[:, :20]).all(axis=1)
                   & (s_lo72[0] > s_lo72[1]) & (s_lo72[1] > s_lo72[2]) & (s_lo72[2] > s_lo72[3])
                   & np.isfinite(res_hi72) & np.isfinite(res_lo72) & (res_lo72 > 0)
                   & (res_hi72 / res_lo72 - 1 <= 0.05)
                   & np.isfinite(c0) & (c0 >= res_hi72 * 0.95))

            # bit73 坑+桥叠加: 两个形态在同一 20 日窗口内互斥, 故时间错开
            e_lo73 = Lf[:, 22:39].min(axis=1)
            e_amt73, _, e_amt73_ok = nanmean(A[:, 22:39], 12)
            pit_lo73 = Lf[:, 10:20].min(axis=1)
            pit_amt73, _, pit_amt73_ok = nanmean(A[:, 10:20], 6)
            pit73 = (np.isfinite(C[:, :39]).all(axis=1)
                     & np.isfinite(e_lo73) & (e_lo73 > 0)
                     & np.isfinite(pit_lo73) & (pit_lo73 < e_lo73 * 0.99)
                     & e_amt73_ok & pit_amt73_ok & (e_amt73 > 0) & (pit_amt73 <= e_amt73 * 0.80))
            b_lo0_73 = Lf[:, 0:5].min(axis=1)
            b_lo1_73 = Lf[:, 5:10].min(axis=1)
            b_hi73 = Hf[:, 0:10].max(axis=1)
            b_hi_lo73 = Hf[:, 0:10].min(axis=1)
            bridge73 = (np.isfinite(C[:, :10]).all(axis=1) & (b_lo0_73 > b_lo1_73)
                        & np.isfinite(b_hi73) & np.isfinite(b_hi_lo73) & (b_hi_lo73 > 0)
                        & (b_hi73 / b_hi_lo73 - 1 <= 0.08))
            t73 = pit73 & bridge73

            # bit74 旗形整理: 旗杆急涨 -> 旗面高低点同步下移 + 缩量 + 回调不破旗杆一半
            pole74 = np.where(np.isfinite(C[:, 20]) & (C[:, 20] > 0), C[:, 10] / C[:, 20] - 1, np.nan)
            vn74, _, _ = nanmean(A[:, 0:10], 5)
            vf74, _, vf74_ok = nanmean(A[:, 10:20], 5)
            t74 = (np.isfinite(C[:, :21]).all(axis=1)
                   & np.isfinite(pole74) & (pole74 >= 0.15)
                   & (Hf[:, 0:5].max(axis=1) < Hf[:, 5:10].max(axis=1))
                   & (Lf[:, 0:5].min(axis=1) < Lf[:, 5:10].min(axis=1))
                   & np.isfinite(c0) & (c0 >= C[:, 10] * 0.5)
                   & vf74_ok & np.isfinite(vn74) & (vf74 > 0) & (vn74 <= vf74 * 0.80))

            # ============ 第七批: 02-02 反包细分型, 三档互斥 (bit 75..77) ============
            # 复用 bit21 的阳包阴判定 (t21), 保证基础集合与生产逐笔一致。
            # 优先判定顺序 ②断板 -> ③十字星/假阴 -> ①炸板, 后判者排除前者, 三档不相交。
            body2_75 = np.abs(C[:, 1] - O[:, 1])
            up2_75 = H[:, 1] - np.maximum(O[:, 1], C[:, 1])
            t76 = t21 & (CLU[:, 2] == 1) & (CLU[:, 1] == 0)
            doji77 = np.isfinite(C[:, 1]) & (np.abs(C[:, 1] - O[:, 1]) / C[:, 1] <= 0.01)
            fake77 = (C[:, 1] < O[:, 1]) & (C[:, 1] > C[:, 2])
            t77 = t21 & ~t76 & (doji77 | fake77)
            t75 = (t21 & ~t76 & ~t77 & np.isfinite(body2_75) & np.isfinite(up2_75)
                   & (body2_75 > 0) & (up2_75 >= 2 * body2_75) & (CLU[:, 1] == 0))

            # ================= 第七批: 02-16 V 反转 + 一阴切线 (bit 78) =================
            q_hi78 = Hf[:, 5:15].max(axis=1)
            q_lo78 = Lf[:, 5:15].min(axis=1)
            q_amp78 = np.where(q_lo78 > 0, (q_hi78 - q_lo78) / q_lo78, np.nan)
            kill78 = np.where(np.isfinite(C[:, 5]) & (C[:, 5] > 0), C[:, 3] / C[:, 5] - 1, np.nan)
            rec78 = np.where(np.isfinite(C[:, 3]) & (C[:, 3] > 0), C[:, 1] / C[:, 3] - 1, np.nan)
            t78 = (np.isfinite(C[:, :16]).all(axis=1)
                   & np.isfinite(q_amp78) & (q_amp78 <= 0.15) & (q_lo78 > 0)
                   & np.isfinite(kill78) & (kill78 <= -0.03)
                   & np.isfinite(rec78) & (rec78 >= 0.04)
                   & np.isfinite(c0) & (c0 < O[:, 0]) & (c0 > q_lo78))

            # ================= 第八批: 07 册境外量化形态 (bit 79..86) =================
            # 全部 pre 口径, 只用 T-1 及以前日线; 复用 seg_ext/seg_avg 几何 helper。
            Af8 = np.where(np.isfinite(A), A, np.nan)

            # bit79 对称三角形: 近21日3段, 高点逐段降低 + 低点逐段抬高 + 近段波幅<远段
            e79 = [(0, 7), (7, 14), (14, 21)]
            hi79 = seg_ext(Hf, e79, "max")
            lo79 = seg_ext(Lf, e79, "min")
            conv79 = ((hi79[:, 0] < hi79[:, 1]) & (hi79[:, 1] < hi79[:, 2])
                      & (lo79[:, 0] > lo79[:, 1]) & (lo79[:, 1] > lo79[:, 2]))
            narrow79 = (hi79[:, 0] - lo79[:, 0]) < (hi79[:, 2] - lo79[:, 2])
            t79 = (np.isfinite(C[:, :21]).all(axis=1) & conv79 & narrow79 & (lo79[:, 0] > 0))

            # bit80 杯柄: 杯底低于左右沿≥10% + 两沿差≤6% + 浅柄 + 逼近右沿≥95%
            rimL80 = Hf[:, 20:30].max(axis=1)
            bottom80 = Lf[:, 8:20].min(axis=1)
            rimR80 = Hf[:, 3:8].max(axis=1)
            hlo80 = Lf[:, 0:3].min(axis=1)
            cup80 = ((bottom80 < rimL80 * 0.90) & (bottom80 < rimR80 * 0.90)
                     & (np.abs(rimL80 - rimR80) / np.maximum(rimL80, 1e-9) <= 0.06))
            handle80 = (hlo80 >= bottom80) & (hlo80 < rimR80) & (hlo80 >= rimR80 * 0.88)
            t80 = (np.isfinite(C[:, :30]).all(axis=1) & cup80 & handle80
                   & np.isfinite(c0) & (c0 >= rimR80 * 0.95))

            # bit81 VCP 波动收缩: 近24日3段, 每段振幅递减 + 均额递减
            e81 = [(0, 8), (8, 16), (16, 24)]
            sh81 = seg_ext(Hf, e81, "max")
            sl81 = seg_ext(Lf, e81, "min")
            amp81 = (sh81 - sl81) / np.maximum(sl81, 1e-9)
            vol81 = seg_avg(Af8, e81)
            t81 = (np.isfinite(C[:, :24]).all(axis=1)
                   & (amp81[:, 0] < amp81[:, 1]) & (amp81[:, 1] < amp81[:, 2])
                   & (vol81[:, 0] < vol81[:, 1]) & (vol81[:, 1] < vol81[:, 2]))

            # bit82 双底: 左底(T-27..T-18)与右底(T-14..T-5)差≤3% + 颈线高于左底≥10% + 逼近颈线≥100%
            lowL82 = Lf[:, 18:28].min(axis=1)
            lowR82 = Lf[:, 5:15].min(axis=1)
            neck82 = Hf[:, 12:20].max(axis=1)
            twin82 = (np.abs(lowL82 - lowR82) / np.maximum(lowL82, 1e-9) <= 0.03) & (neck82 > lowL82 * 1.10)
            t82 = (np.isfinite(C[:, :28]).all(axis=1) & twin82 & (lowL82 > 0)
                   & np.isfinite(c0) & (c0 >= neck82 * 1.00))

            # bit83 下降楔形: 近20日2段, 高低点同步下移 + 近段波幅<远段
            hi0_83 = Hf[:, 0:10].max(axis=1); hi1_83 = Hf[:, 10:20].max(axis=1)
            lo0_83 = Lf[:, 0:10].min(axis=1); lo1_83 = Lf[:, 10:20].min(axis=1)
            wedge83 = (hi0_83 < hi1_83) & (lo0_83 < lo1_83) & ((hi0_83 - lo0_83) < (hi1_83 - lo1_83))
            t83 = (np.isfinite(C[:, :20]).all(axis=1) & wedge83 & (lo0_83 > 0))

            # bit84 更高低点放量: 近21日3段, 低点逐段抬高 + 近段均额>远段2倍
            e84 = [(0, 7), (7, 14), (14, 21)]
            lo84 = seg_ext(Lf, e84, "min")
            vol84 = seg_avg(Af8, e84)
            t84 = (np.isfinite(C[:, :21]).all(axis=1)
                   & (lo84[:, 0] > lo84[:, 1]) & (lo84[:, 1] > lo84[:, 2])
                   & (vol84[:, 0] > vol84[:, 2] * 2.0) & (lo84[:, 0] > 0))

            # bit85 波动压缩: amp5/amp20 ≤ 固定 p20 (0.379)
            a5_85 = (Hf[:, 0:5].max(axis=1) - Lf[:, 0:5].min(axis=1)) / np.maximum(c0, 1e-9)
            a20_85 = (Hf[:, 0:20].max(axis=1) - Lf[:, 0:20].min(axis=1)) / np.maximum(c0, 1e-9)
            compress85 = np.where((a20_85 > 0) & np.isfinite(a5_85) & np.isfinite(a20_85),
                                  a5_85 / a20_85, np.nan)
            t85 = (np.isfinite(C[:, :20]).all(axis=1)
                   & np.isfinite(compress85) & (compress85 <= COMPRESS_P20))

            # bit86 回踩续涨: 上升趋势 + 近5日回踩至 MA20×1.01 内 + T-1 放量阳线反包
            ma20_86 = np.where(np.isfinite(C[:, :20]).all(axis=1), C[:, :20].mean(axis=1), np.nan)
            ma20p_86 = np.where(np.isfinite(C[:, 5:25]).all(axis=1), C[:, 5:25].mean(axis=1), np.nan)
            up86 = np.isfinite(ma20_86) & np.isfinite(ma20p_86) & (c0 > ma20_86) & (ma20_86 > ma20p_86)
            touch86 = Lf[:, 0:5].min(axis=1) <= ma20_86 * 1.01
            yang86 = np.isfinite(O[:, 0]) & (c0 > O[:, 0])
            ar86 = np.where(np.isfinite(A[:, 1]) & (A[:, 1] > 0), A[:, 0] / A[:, 1], np.nan)
            t86 = (np.isfinite(C[:, :25]).all(axis=1) & up86 & touch86 & (c0 >= ma20_86)
                   & yang86 & np.isfinite(ar86) & (ar86 > 1.2))

            # ================= 第九批: 07册剩余形态 + 08册因子 + 02册回马枪 (bit 87..90) =================
            # 全部 pre 口径, 只用 T-1 及以前日线; 复用 seg_ext/seg_avg 几何 helper。
            Af9 = np.where(np.isfinite(A), A, np.nan)

            # bit87 上升三角形: 近21日3段, 低点逐段抬高 + 高点基本水平(差≤6%) + 近段波幅<远段
            e87 = [(0, 7), (7, 14), (14, 21)]
            hi87 = seg_ext(Hf, e87, "max")
            lo87 = seg_ext(Lf, e87, "min")
            rise87 = (lo87[:, 0] > lo87[:, 1]) & (lo87[:, 1] > lo87[:, 2])
            flat87 = (np.maximum.reduce(hi87, axis=1) / np.maximum(np.minimum.reduce(hi87, axis=1), 1e-9) - 1) <= 0.06
            narrow87 = (hi87[:, 0] - lo87[:, 0]) < (hi87[:, 2] - lo87[:, 2])
            t87 = (np.isfinite(C[:, :21]).all(axis=1) & rise87 & flat87 & narrow87 & (lo87[:, 0] > 0))

            # bit88 长下影线: 下影(min(O,C)-L) ≥ 实体|O-C| 的2倍 且收阳/十字星 (原文08-08已给数字)
            body88 = np.abs(c0 - O[:, 0])
            lower88 = np.minimum(O[:, 0], c0) - L[:, 0]
            t88 = (np.isfinite(C[:, :1]).all(axis=1) & np.isfinite(body88) & (body88 > 0)
                   & np.isfinite(lower88) & (lower88 >= body88 * 2) & (c0 >= O[:, 0]))

            # bit89 动量加速: T-1额/前5日均额≥3 且 近5日涨幅≥10% (收紧自首轮21.5%过松口径)
            vbase89 = np.where(np.isfinite(A[:, 5:10]).all(axis=1), A[:, 5:10].mean(axis=1), np.nan)
            vr89 = np.where(np.isfinite(vbase89) & (vbase89 > 0), A[:, 0] / vbase89, np.nan)
            mom89 = np.where(np.isfinite(C[:, 5]) & (C[:, 5] > 0), c0 / C[:, 5] - 1, np.nan)
            t89 = (np.isfinite(C[:, :10]).all(axis=1) & np.isfinite(vr89) & (vr89 >= 3)
                   & np.isfinite(mom89) & (mom89 >= 0.10))

            # bit90 回马枪平台突破: T-8..T-20曾涨停(单日≥9.5%) + T-2..T-6平台振幅≤12% + T-1突破平台高
            ret90 = np.where(np.isfinite(C[:, 1:21]) & (C[:, 1:21] > 0),
                             C[:, :20] / C[:, 1:21] - 1, np.nan)
            had_limit90 = np.nanmax(ret90, axis=1) >= 0.095
            ph90 = Hf[:, 2:7].max(axis=1)
            pl90 = Lf[:, 2:7].min(axis=1)
            pamp90 = np.where(pl90 > 0, (ph90 - pl90) / pl90, np.nan)
            brk90 = (c0 > ph90) & (O[:, 0] < ph90 * 1.05)
            t90 = (np.isfinite(C[:, :21]).all(axis=1) & had_limit90
                   & np.isfinite(pamp90) & (pamp90 <= 0.12) & brk90)

            # ================= 第十批: 归一化改造 + 盘整三要素 + 小阳线 (bit 91..93) =================
            # bit91 突破缺口: 归一化改造, 解决第17册绝对阈值 CV0.74 的 regime 漂移
            # 缺口幅度/TR14≥0.8 (TR 用近15日收盘相邻变动均值近似) + 量比 A0/A1≥2
            tr14_91 = np.nanmean(np.abs(np.diff(np.where(np.isfinite(C[:, :15]), C[:, :15], np.nan), axis=1)), axis=1)
            gap_size91 = np.where(np.isfinite(H[:, 1]) & (H[:, 1] > 0), (O[:, 0] - H[:, 1]) / H[:, 1], np.nan)
            gap_ratio91 = np.where(np.isfinite(tr14_91) & (tr14_91 > 0) & (c0 > 0),
                                   gap_size91 * c0 / tr14_91, np.nan)
            gapvol91 = np.where(np.isfinite(A[:, 1]) & (A[:, 1] > 0), A[:, 0] / A[:, 1], np.nan)
            t91 = (np.isfinite(C[:, :15]).all(axis=1) & (O[:, 0] > H[:, 1]) & (L[:, 0] > H[:, 1])
                   & np.isfinite(gap_ratio91) & (gap_ratio91 >= 0.8)
                   & np.isfinite(gapvol91) & (gapvol91 >= 2.0))

            # bit92 盘整三要素: ①支撑阻力清晰(近15上下沿各触及≥2) ②窄幅归一(amp15/amp60≤p30) ③低量无突量
            amp60_92 = (Hf[:, :60].max(axis=1) - Lf[:, :60].min(axis=1)) / np.maximum(c0, 1e-9)
            amp60_92 = np.where(np.isfinite(amp60_92) & (amp60_92 > 0), amp60_92, np.nan)
            bh92 = Hf[:, 0:15].max(axis=1); bl92 = Lf[:, 0:15].min(axis=1)
            amp15_92 = np.where(bl92 > 0, (bh92 - bl92) / bl92, np.nan)
            ratio92 = np.where(np.isfinite(amp60_92) & (amp60_92 > 0), amp15_92 / amp60_92, np.nan)
            tt92 = (Hf[:, 0:15] >= bh92[:, None] * 0.99).sum(axis=1) >= 2
            tb92 = (Lf[:, 0:15] <= bl92[:, None] * 1.01).sum(axis=1) >= 2
            v15_92 = np.where(np.isfinite(A[:, 0:15]).all(axis=1), A[:, 0:15].mean(axis=1), np.nan)
            v4560_92 = np.where(np.isfinite(A[:, 15:60]).all(axis=1), A[:, 15:60].mean(axis=1), np.nan)
            vmax15_92 = np.where(np.isfinite(A[:, 0:15]).all(axis=1), A[:, 0:15].max(axis=1), np.nan)
            lowvol92 = np.where(np.isfinite(v4560_92) & (v4560_92 > 0), v15_92 / v4560_92, np.nan)
            novol92 = np.where(np.isfinite(v15_92) & (v15_92 > 0), vmax15_92 / v15_92, np.nan)
            t92 = (np.isfinite(C[:, :60]).all(axis=1) & np.isfinite(ratio92) & (ratio92 <= CONSOLIDATE_P30)
                   & tt92 & tb92 & np.isfinite(lowvol92) & (lowvol92 <= 0.8)
                   & np.isfinite(novol92) & (novol92 <= 2.0))

            # bit93 小阳线蓄势: 近3日全部小阳线 (0<实体涨幅≤5%)
            small93 = np.where(np.isfinite(O) & (O > 0), (C - O) / O, np.nan)
            small3_93 = ((small93[:, 0:3] > 0) & (small93[:, 0:3] <= 0.05)).sum(axis=1)
            t93 = np.isfinite(C[:, :3]).all(axis=1) & (small3_93 == 3)

        # 显式 (bit, 条件) 列表: 位号不连续, 不能用 enumerate 隐式对齐
        shape_bits = [
            (0, t0), (1, t1), (2, t2), (3, t3), (4, t4), (5, t5), (6, t6), (7, t7), (8, t8),
            (14, t14), (15, t15), (16, t16), (17, t17), (18, t18),
            (19, t19), (20, t20), (21, t21), (22, t22), (23, t23), (24, t24), (25, t25), (26, t26),
            (30, t30), (31, t31), (32, t32), (33, t33), (34, t34), (35, t35), (36, t36),
            (37, t37), (38, t38), (39, t39), (40, t40), (41, t41), (42, t42),
            (43, t43), (44, t44), (45, t45), (46, t46), (47, t47), (48, t48),
            (49, t49), (50, t50), (51, t51), (52, t52),
            (53, t53), (54, t54), (55, t55), (56, t56), (57, t57),
            (58, t58), (59, t59), (60, t60),
            (61, t61), (62, t62), (63, t63), (64, t64), (65, t65),
            (66, t66), (67, t67), (68, t68), (69, t69), (70, t70),
            (71, t71), (72, t72), (73, t73), (74, t74),
            (75, t75), (76, t76), (77, t77), (78, t78),
            (79, t79), (80, t80), (81, t81), (82, t82),
            (83, t83), (84, t84), (85, t85), (86, t86),
            (87, t87), (88, t88), (89, t89), (90, t90),
            (91, t91), (92, t92), (93, t93),
        ]
        sub = np.zeros((m, TAG_WORDS), dtype=np.int64)
        for bit, cond in shape_bits:
            w, off = _bit_val(bit)
            sub[:, w] |= np.where(np.asarray(cond, dtype=bool), 1 << off, 0).astype(np.int64)
        masks[a:b] |= sub
    return masks


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

    # 分类标签: 张量已在 build_trades 里加载并缓存, 这里复用, 不重复读分区文件。
    print("[打板] 计算分类标签 ...")
    if S._TENSOR is None:
        raise RuntimeError("行情张量未加载, 无法计算分类标签")
    SC, sym2i, _syms = S._TENSOR
    tag_masks = build_tags(tr, cal, SC, sym2i)
    if len(tag_masks) != len(stats):
        raise RuntimeError("标签行数与统计行数不一致, 行号会错位")

    ohlc_n = (cfg["win_before"] + cfg["win_after"] + 1) * 4
    n_no_ret = int(tr["r_t1"].isna().sum())
    caveats = [
        "打板口径: 涨停判定按「盘中最高价触及涨停价」(含炸板未封), 买价 = T 日涨停价(触板即可挂单), T+1 收盘卖; 收益按前复权收盘价算, 未扣手续费与滑点。触板未必封住、封住未必买得到, 回测按「触板即成交」理想化估算, 炸板股按涨停价买入(当天即浮亏)。",
        "列表是 %s 起本地日线里能算出的全部「触板」记录(盘中最高价触及涨停价, 含炸板未封; 事件池明显大于收盘封板口径): 不再按 T+10 画图窗口裁掉首尾, 也不再剔除上市不足 4 日新股、涨跌停约束外的异常记录。6 档板位按连续触板第几天切开, 默认全部展示。" % cfg["start"],
        "「最终几连板 / 持续几天」是买完之后才走出来的结果, 只用于复盘展示, 绝不能拿来当买入条件 —— 那样等于用未来数据作弊。",
        "情绪相位是收盘后按连板梯队算的日频标签, 抓不到盘中「分歧 / 加速」那一刻; Phase 0 已验证「点火/主升才买」这道门没有超额。相位在这里只作分档观察, 不预设它能赚钱。早年部分交易日没有相位/涨停家数标签, 这些字段留空, 加限制时按「不符合」处理。",
        "「隔夜高开」是 T+1 集合竞价能看到的客观特征 (= T+1 开盘 / T 日收盘 - 1), 仅用于复盘切片与限制项; 买入按 T 日涨停价, 与它无关。止损按跌破买入价 5% 时以 -5% 成交近似, 未计跳空滑点。",
        "市值是「前复权价 × 最新总股本」的估算 (股本只有最新快照), 只用于分档过滤, 不是精确历史市值。",
        "一字板不再剔除、始终计入: 其中部分是涨停当天与次日一字封死、实际根本买不到的 (卡片标「一字买不进」), 数字已含它们, 看胜率时心里有数。",
        "末尾 %d 个交易日的涨停因走不完最长 5 日持有期, 已在计算时整条剔除, 不进列表、不进笔数、不进标签 —— 避免拿 clip 补齐的重复 K 线算假收益。剩余极少数「收益未走完」是个股持有期内停牌/数据缺失导致卖出日无价, 不参与胜率/平均收益统计。" % cfg["hold_max"],
        "按日统计图按「涨停当天」归集全量笔数 (不是买入日)。某段时间笔数很少时, 平均收益容易被个别极端案例带偏, 看柱子高低一起判断。",
        "下面列表就是全部 %d 笔涨停记录本身, 没有抽样、没有挑选 —— 点某一天就看到那一天的全部笔数, 一条不少。" % len(tr),
        "分类标签是给已发生的涨停记录做事后切片, 不是买入信号: 勾选多个取交集(AND)。标「事前」的只用 T-1 及以前日线; 标「当日」「买入时」「复盘」的含 T 日或 T+1 信息, 只用于复盘观察, 不能拿来选股。",
        "标「复盘」的完整形态标签(平台突破板/前高突破板/一阳穿多线/反包涨停/双响炮N字)按原文口径补齐了 T 日放量涨停、突破、实体吞没等决定性动作, 与前 30 条「前置片段」标签并存, 便于对比。它们用 T 日信息给已发生的记录分类 —— 这不是未来函数(涨停当天收盘就成立), 但买入发生在 T+1 开盘, 所以绝不能当盘前选股信号。",
    ]
    payload = {
        "meta": {
            "title": "打板接力研究",
            "n_all": int(len(tr)),
            "n_no_ret": n_no_ret,
            "start": cfg["start"],
            "limit_basis": cfg.get("limit_basis", "touch"),
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
            "tag_defs": TAG_DEFS,
            # 每笔标签掩码的 word 数: 前端据此知道 tags[k] 是长度为 tag_words 的数组
            "tag_words": TAG_WORDS,
            "tag_bits_per_word": TAG_BITS_PER_WORD,
        },
        "stats": stats,
        "tags": tag_masks.tolist(),
    }
    return payload, ohlc_arr
