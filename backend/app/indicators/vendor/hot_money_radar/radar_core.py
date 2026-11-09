"""Vendored 规则核心 — 逐字提取自 sorahjy/financial-analysis/stock_hot_money_radar.py。

仅保留 match_patterns() 及其依赖闭包(形态匹配 P1-P26、阶段汇总、三角筹码分布、
量价/位置/CMF 辅助函数), 逻辑与原文件逐行一致, 不含 sqlite/实时行情/CLI/回测层。
详见本目录 SOURCE.md。
"""
from __future__ import annotations
import math
import numpy as np
from typing import Any, Dict, List, Optional, Sequence, Tuple

LOOKBACK = 180           # 统一历史窗口；P25判据使用其中近120/60日，P26另取5日前筹码


MIN_BARS = 40            # 少于这么多有效 bar 视为数据不足，不打分


SHORT_WIN = 5            # 近端放量窗口


BASE_WIN = 20            # 量比基线窗口（紧邻近端窗口之前）


HIGH_WIN = 60            # 价格分位窗口


DRIFT_WIN = 20           # 努力与结果背离：看近 20 日累计涨跌幅是否横住


VOL_RATIO_FULL = 2.5     # 量比达到该值给满分（1.0→0 分，线性）


POS_LOW = 0.25           # 收盘价分位 ≤ 此值 → 中低位满分（留足上行空间）


POS_HIGH = 0.70          # 收盘价分位 ≥ 此值 → 已拉升，位置分 0


CMF_WIN = 20             # Chaikin Money Flow 窗口


CMF_FULL = 0.20          # CMF 达到该值给满分（≤0 → 0 分）


CHIP_BUCKETS = 80        # 成本分布价格网格数


CHIP_DECAY = 1.0         # 历史换手衰减系数（n=1：今日换手多少就搬移多少昨日筹码）


CHIP_BAND = 0.07         # 主峰 ±7% 价带内筹码占比 = 单峰密集集中度


CHIP_CONC_LO = 0.25      # 集中度 ≤ 此值 → 0 分


CHIP_CONC_HI = 0.60      # 集中度 ≥ 此值 → 满分


CHIP_PEAK_FULL = 0.35    # 主峰处在近端价格区间低 35% 内 → 低位满分


CHIP_PEAK_ZERO = 0.70    # 主峰处在近端价格区间高 70% 以上 → 低位 0 分


CHIP_PRICE_BELOW_ZERO = -0.08  # 当前价低于主峰太多，说明尚未站回成本区


CHIP_PRICE_ABOVE_FULL = 0.08   # 当前价略高于主峰内仍视为吸筹未充分拉升


CHIP_PRICE_ABOVE_ZERO = 0.22   # 当前价高于主峰过多，视为已启动


CHIP_WINNER_MIN = 0.15         # 获利盘过低，上方套牢压力重


CHIP_WINNER_FULL_LO = 0.25


CHIP_WINNER_FULL_HI = 0.60     # 获利盘中低位最佳：不是全套牢，也不是满获利


CHIP_WINNER_MAX = 0.82


CHIP_WINNER_RISK_HIGH = 0.90       # P26：绝大多数筹码已获利，兑现/派发风险


CHIP_WINNER_RISK_DAYS = 5          # P26：与 N 个交易日前比较


CHIP_WINNER_RISK_PRIOR_MAX = 0.40  # P26：N 日前获利盘仍低于 40%


CHIP_WINNER_RISK_CURRENT_MIN = 0.60  # P26：当前快速跃升到 60% 以上


CHIP_WINNER_RISK_VOLUME_BASE_WIN = 20  # P26：当日量能对比此前 N 日均量


CHIP_WINNER_RISK_VOLUME_MIN_OBS = 15   # P26：此前窗口至少 15 个有效量能点


CHIP_WINNER_RISK_VOLUME_RATIO_MIN = 2.00  # P26：获利盘风险必须有极端放量确认


P26_RET_5_MIN = 0.05              # P26：先有近5日快速拉升，才把获利盘拥挤解释为冲高风险


P26_PEAK_HIGH_DAYS = 10           # P26：价格走弱确认所用近端盘中高点窗口（含信号日）


P26_PEAK_DRAWDOWN_MIN = 0.03      # P26：当前收盘至少较近10日最高价回撤3%


SEALED_AMP = 0.005       # 日内振幅 ≤ 0.5% 视为一字封死板


TURNOVER_COVERAGE = 0.7  # 近端窗口换手率覆盖率达标才用换手率，否则退回成交量


SUSPECT_ACCUM_SCORE = 35.0  # 仅 fired 为空且吸筹分达标 → 疑似吸筹(待确认)


COMPRESS_AMP_RATIO = 0.96  # P23：近20日振幅 < 此倍×近60日振幅


P23_POSITION_60_MAX = 0.36  # P23：近60日收盘位置分位严格小于36%


P23_MA20_FLOOR = 1.005      # P23：确认状态须收盘至少高于MA20 0.5%


P23_DAY_VOLUME_RATIO_MIN = 0.40  # P23：当日量能/此前20日均量下界（含）


P23_DAY_VOLUME_RATIO_MAX = 1.20  # P23：当日量能/此前20日均量上界（含）


PATTERN_CONFIRM_DAYS = 5   # P24：连续确认后只在首次成立日触发一次


OBV_POSITION_MAX = 0.50


OBV_PRICE_RETURN_MAX = 0.03


OBV_RETURN_MIN = 0.10      # P24：30日量纲化OBV净流入下限


OBV_DIV_MIN = 0.25         # P24：OBV净流入(量纲化)−价格涨幅的最小背离


P2_WINDOW = 10             # P2：近X日反复出现真实长下影（优化研究见纪要25）


P2_MIN_COUNT = 2           # P2：X日内至少Y次，且当日自身必须是其中一次；所有池参数相同


P2_LOWER_SHADOW_MIN = 0.020


P2_SHADOW_BODY_RATIO_MIN = 2.0


P2_DAY_VOLUME_RATIO_MIN = 0.40


P2_DAY_VOLUME_RATIO_MAX = 1.00


P3_POSITION_MAX = 0.32


P3_DROP_MIN = 0.042


P3_EVENT_WINDOW = 10


P3_EVENT_VOLUME_RATIO_MAX = 1.00


P3_UNDERCUT_MAX = 0.06


P3_CONFIRM_VOLUME_RATIO_MAX = 1.35


P5_POSITION_MAX = 0.45


P5_SECOND_LOW_RETURN_MIN = -0.02


P5_SECOND_LOW_RETURN_MAX = 0.10


P5_NECK_REBOUND_MIN = 0.05


P5_SECOND_LOW_RECENCY_MAX = 8


P5_RIGHT_LEG_BREAKOUT_WIN = 3


P5_NECK_RATIO_MIN = 1.00


P5_NECK_RATIO_MAX = 1.03


P5_FLOAT_EPSILON = 1e-12


P15_EVENT_HIGH_DAYS = 60


P15_EVENT_LOOKBACK_DAYS = 15


P15_EVENT_LOW_VOLUME_RATIO_MAX = 1.00


P15_EVENT_HIGH_VOLUME_RATIO_MIN = 1.80


P15_EVENT_RET5_MAX = 0.01


P15_PULLBACK_HIGH_DAYS = 10


P15_PULLBACK_MIN = 0.04


P15_DAY_VOLUME_RATIO_MIN = 1.50


PATTERN_EVAL_BARS = LOOKBACK + max(CHIP_WINNER_RISK_DAYS, P15_EVENT_LOOKBACK_DAYS)


P16_POSITION_MIN = 0.80


P16_MAX_VOLUME_DAYS = 40


P16_EVENT_RECENCY_DAYS = 3


P16_PEAK_HIGH_DAYS = 10


P16_PEAK_DRAWDOWN_MIN = 0.03


P17_POSITION_MIN = 0.80


P17_RECENT_DAYS = 10


P17_BASE_DAYS = 6


P17_RUNUP_MIN = 0.16


P17_PULLBACK_MIN = 0.08


P17_PEAK_RECENCY_BARS = 5


P21_BOX_DAYS = 40


P21_RECLAIM_DAYS = 5


P21_UNDERCUT_RATIO_MAX = 0.975


P21_DAY_VOLUME_RATIO_MAX = 0.75


P20_VOLUME_RATIO_MIN = 1.30


P20_DAY_RETURN_MAX = -0.01


P21_MIN_HISTORY_BARS = LOOKBACK + CHIP_WINNER_RISK_DAYS


P4_POSITION_MAX = 0.60


P4_VOLUME_RATIO_MIN = 1.20


P4_VOLUME_RATIO_MAX = 2.50


P4_ABS_DRIFT_MAX = 0.06


P4_CMF_MIN = 0.0


P1_POSITION_60_MAX = 0.40


P1_ABS_RET_20_MAX = 0.04


P1_MA20_FLOOR = 0.995


P1_DAY_VOLUME_RATIO_MIN = 0.40


P1_DAY_VOLUME_RATIO_MAX = 1.00


P1_CONFIRM_DAYS = 2


P1_VERSION = "p1_excess_priority_v1"


P1_REPLACED_ON = "2026-07-14"


P1_BREAKOUT_DAYS = 10


P1_ADDON_POSITION_60_MAX = 0.45


P1_ADDON_ABS_RET_20_MAX = 0.06


P1_ADDON_MA20_FLOOR = 0.995


P1_ADDON_DAY_VOLUME_RATIO_MIN = 0.20


P1_ADDON_DAY_VOLUME_RATIO_MAX = 1.20


P25_POSITION_120_MAX = 0.65


P25_RANGE_60_MAX = 0.35


P25_ABS_RET_60_MAX = 0.16


P25_VOLUME_CONTRACT_MAX = 0.70


P25_VOLUME_SLOPE_60_MAX = -0.005


P25_RET_20_MIN = 0.005


P25_BREAKOUT_20_MIN = -0.04


ACCUM_MODEL_WEIGHTS = {
    "chip": 0.1,                        # 低位筹码集中
    "position": 0.1,                    # 价格中低位
    "cmf_eff": 0.1,                     # CMF 反向有效分：高买压反转风险不加分
    "p1": 0.1,                          # P1 两日企稳 OR 首次10日突破+市场门；双池10日有效
    "p2": 0.05,                         # P2 低位真实长下影 + 温和缩量承接
    "p3": 0.1,                          # P3 缩量收盘大跌后首次收复
    "p5": 0.05,                         # P5 双底右腿确认；临时核心有效，小盘更有效
    "p21": 0.1,                         # P21 深破箱底后缩量收回
    "p23": 0.05,                        # P23 低位压缩后温和量能转强；核心有效
    "p24": 0.05,                        # P24 OBV 底背离：连续确认后的首次成立日
    "p25": 0.05,                        # P25 中低位缩量平台价格转强；双池10/20日正向
    "holder_change": 0.1,               # 股东户数变化：户数降=高分，缺失=中性50
    "repurchase": 0.05,                 # 公司回购：近90日回购=100，否则0
}


DISTRIBUTION_WARNING_PATTERN_CODES = frozenset({
    "P14", "P15", "P16", "P17", "P19", "P20", "P22", "P26",
})


DISTRIBUTION_WARNING_POINTS = {
    code: 1 for code in sorted(DISTRIBUTION_WARNING_PATTERN_CODES)
}


DISTRIBUTION_WARNING_EFFECTIVE_SELL_THRESHOLD = 1


DISTRIBUTION_WARNING_POINTS_THRESHOLD = 1


TRIGGER_WIN = 20          # 突破窗口：放量突破近 20 日收盘高点


TRIGGER_VOL_MULT = 1.3    # 突破当日量 > 1.3× 前 20 日均量


TRIGGER_BASE_MAX_POS = 0.70  # 被突破的前高在近 60 日分位 < 0.70 → 从低/中位突破(非高位追涨)


def _limit_pct(code: str) -> float:
    """个股涨跌停幅度（粗分；ST 5% 细节忽略，作为噪声接受）。"""
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("8", "4")):  # 北交所
        return 30.0
    return 10.0


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _clip01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def _safe(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _volume_series(bars: List[Dict[str, Any]]) -> Tuple[List[Optional[float]], str]:
    """挑量度量：近端换手率覆盖率达标用换手率（更干净，qfq 不复权 volume 有跳变），否则退回成交量。"""
    turns = [b["turnover"] for b in bars]
    coverage = sum(1 for t in turns if t is not None) / len(turns) if turns else 0.0
    if coverage >= TURNOVER_COVERAGE:
        return turns, "turnover"
    return [b["volume"] for b in bars], "volume"


def _score_volume_ratio(vol: List[Optional[float]]) -> Tuple[Optional[float], Optional[float]]:
    """F1 量比抬升：近 SHORT_WIN 日均量 / 紧邻其前 BASE_WIN 日均量。"""
    if len(vol) < SHORT_WIN + BASE_WIN:
        return None, None
    short = [v for v in vol[-SHORT_WIN:] if v is not None]
    base = [v for v in vol[-(SHORT_WIN + BASE_WIN):-SHORT_WIN] if v is not None]
    short_avg, base_avg = _mean(short), _mean(base)
    if not short_avg or not base_avg or base_avg <= 0:
        return None, None
    ratio = short_avg / base_avg
    score = _clip01((ratio - 1.0) / (VOL_RATIO_FULL - 1.0)) * 100.0
    return score, ratio


def _p26_volume_ratio(vol: Sequence[Optional[float]]) -> Optional[float]:
    """P26 专用量能确认：当日量能 / 此前20日均量；不使用通用的近5日均量口径。"""
    required = CHIP_WINNER_RISK_VOLUME_BASE_WIN + 1
    if len(vol) < required:
        return None
    try:
        current = float(vol[-1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(current) or current <= 0:
        return None
    prior: List[float] = []
    for value in vol[-required:-1]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric) and numeric > 0:
            prior.append(numeric)
    if len(prior) < CHIP_WINNER_RISK_VOLUME_MIN_OBS:
        return None
    base = _mean(prior)
    return current / base if base and base > 0 else None


def _score_position(bars: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    """A2 价格中低位：收盘价在近 HIGH_WIN 日分位越低越好（留出上行空间，已拉升=0）。"""
    closes = [b["close"] for b in bars[-HIGH_WIN:] if b["close"] is not None]
    if len(closes) < 20:
        return None, None
    last = closes[-1]
    pct = sum(1 for c in closes if c <= last) / len(closes)
    score = _clip01((POS_HIGH - pct) / (POS_HIGH - POS_LOW)) * 100.0
    return score, pct


def _amp_ratio(bars: List[Dict[str, Any]]) -> Optional[float]:
    """近20日振幅均值 / 近60日振幅均值（<1 = 波动压缩/酝酿）。振幅=(高−低)/昨收。样本<60 返回 None。"""
    amps: List[float] = []
    for i in range(1, len(bars)):
        pc, h, l = bars[i - 1]["close"], bars[i]["high"], bars[i]["low"]
        if pc and h is not None and l is not None:
            amps.append((h - l) / pc)
    if len(amps) < 60:
        return None
    a_recent, a_base = _mean(amps[-20:]), _mean(amps[-60:])
    return (a_recent / a_base) if a_base else None


def _absorption_score(drift: float) -> float:
    """20 日累计涨跌幅 → 吸筹分。横盘微涨最佳；大跌(杀跌/出货)或大涨(已拉升)都归零。"""
    if drift <= -0.12 or drift >= 0.25:
        return 0.0
    if drift < -0.03:
        return (drift + 0.12) / 0.09 * 100.0     # -12%→0 升到 -3%→100
    if drift <= 0.08:
        return 100.0                              # -3%~+8% 横盘微涨：满分
    return (0.25 - drift) / 0.17 * 100.0          # +8%→100 降到 +25%→0


def _score_absorption(bars: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    """近 DRIFT_WIN 日价格横住程度（结果端），杀跌/暴涨都不算吸筹。"""
    closes = [b["close"] for b in bars if b["close"] is not None]
    if len(closes) < DRIFT_WIN + 1:
        return None, None
    ref = closes[-1 - DRIFT_WIN]
    if not ref:
        return None, None
    drift = closes[-1] / ref - 1.0
    return _absorption_score(drift), drift


def _score_divergence(bars: List[Dict[str, Any]], vol: List[Optional[float]]) -> Tuple[Optional[float], Optional[float]]:
    """信号①努力与结果背离：放量(努力) × 价格横住(结果) 连乘。放量却不涨=有人在吸货。"""
    s_vol, vol_ratio = _score_volume_ratio(vol)
    s_abs, drift = _score_absorption(bars)
    if vol_ratio is None or s_abs is None:
        return None, drift
    effort = _clip01((vol_ratio - 1.0) / (VOL_RATIO_FULL - 1.0))   # 量能抬升强度 0~1
    return effort * (s_abs / 100.0) * 100.0, drift


def _money_flow_mult(bar: Dict[str, Any]) -> Optional[float]:
    """Chaikin 资金流乘数 MFM = ((C−L)−(H−C))/(H−L) ∈ [−1,1]，收越靠上半部越正。"""
    h, l, c = bar["high"], bar["low"], bar["close"]
    if h is None or l is None or c is None or h <= l:
        return None   # 一字板 high==low → 无定义，跳过
    return ((c - l) - (h - c)) / (h - l)


def _score_cmf(bars: List[Dict[str, Any]], vol: List[Optional[float]]) -> Tuple[Optional[float], Optional[float]]:
    """信号②收盘买压：Chaikin Money Flow = Σ(MFM×量)/Σ量，收盘持续靠上半部=买方吸收。"""
    num = den = 0.0
    for b, v in zip(bars[-CMF_WIN:], vol[-CMF_WIN:]):
        m = _money_flow_mult(b)
        if m is None or v is None:
            continue
        num += m * v
        den += v
    if den <= 0:
        return None, None
    cmf = num / den   # [-1, 1]
    return _clip01(cmf / CMF_FULL) * 100.0, cmf


def _bar_avg_price(bar: Dict[str, Any]) -> Optional[float]:
    """用成交额/成交量估算均价；不可用时退回典型价，供三角筹码分布作为峰值。"""
    if "_chip_avg_price" in bar:
        return bar["_chip_avg_price"]
    h, l, c = bar["high"], bar["low"], bar["close"]
    amount, volume = bar.get("amount"), bar.get("volume")
    if amount and volume and amount > 0 and volume > 0 and h is not None and l is not None:
        for avg in (amount / (volume * 100.0), amount / volume):
            if l <= avg <= h:
                return avg
    vals = [x for x in (h, l, c) if x is not None]
    return sum(vals) / len(vals) if vals else None


def _chip_metrics(bars: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """信号③成本分布：换手衰减 + 当日筹码在 low-avg-high 三角铺开，重建流通筹码成本分布。

    返回 concentration(主峰±CHIP_BAND 内筹码占比)、peak_pctile(主峰价位分位)、
    price_to_peak(当前价相对主峰)、winner(获利盘比例)。换手率缺失太多则返回 None。
    """
    lows, highs, peaks, turns = [], [], [], []
    for b in bars:
        avg = _bar_avg_price(b)
        if b["low"] and b["high"] and avg is not None and b["turnover"] is not None and b["high"] > b["low"]:
            lows.append(b["low"]); highs.append(b["high"]); peaks.append(avg); turns.append(b["turnover"])
    if len(lows) < 30:
        return None
    pmin, pmax = min(lows), max(highs)
    if pmax <= pmin:
        return None
    grid = np.linspace(pmin, pmax, CHIP_BUCKETS)
    span = pmax - pmin
    low_array = np.asarray(lows, dtype=float)
    high_array = np.asarray(highs, dtype=float)
    peak_array = np.clip(np.asarray(peaks, dtype=float), low_array, high_array)
    frac_array = np.clip(
        np.asarray(turns, dtype=float) / 100.0 * CHIP_DECAY,
        0.0,
        1.0,
    )

    # 旧实现逐日创建一个很短的 NumPy 数组，180 日窗口会触发约 180 次
    # _triangular_weights 调用。这里一次广播生成所有日的三角分布；最终筹码递推
    #   chips_t = chips_(t-1) * (1-frac_t) + frac_t * day_dist_t
    # 等价为各日分布按“当日换手 × 后续存活率”加权求和。
    prices = grid[None, :]
    lo = low_array[:, None]
    hi = high_array[:, None]
    peak = peak_array[:, None]
    width = hi - lo
    left_width = np.where(peak > lo, peak - lo, 1.0)
    right_width = np.where(peak < hi, hi - peak, 1.0)
    regular = np.where(
        prices <= peak,
        (prices - lo) / left_width,
        (hi - prices) / right_width,
    )
    weights = np.where(
        peak <= lo,
        (hi - prices) / width,
        np.where(peak >= hi, (prices - lo) / width, regular),
    )

    i0 = np.floor((low_array - pmin) / span * (CHIP_BUCKETS - 1)).astype(int)
    i1 = np.floor((high_array - pmin) / span * (CHIP_BUCKETS - 1)).astype(int)
    i0 = np.clip(i0, 0, CHIP_BUCKETS - 1)
    i1 = np.clip(np.maximum(i0, i1), 0, CHIP_BUCKETS - 1)
    bucket_ids = np.arange(CHIP_BUCKETS)[None, :]
    weights = np.maximum(weights, 0.0)
    weights[(bucket_ids < i0[:, None]) | (bucket_ids > i1[:, None])] = 0.0
    row_sums = weights.sum(axis=1)
    empty_rows = np.flatnonzero(row_sums <= 0.0)
    if len(empty_rows):
        empty_bucket_ids = bucket_ids < i0[empty_rows, None]
        empty_bucket_ids |= bucket_ids > i1[empty_rows, None]
        distances = np.abs(grid[None, :] - peak_array[empty_rows, None])
        distances[empty_bucket_ids] = np.inf
        nearest = distances.argmin(axis=1)
        weights[empty_rows, nearest] = 1.0
        row_sums[empty_rows] = 1.0
    weights /= row_sums[:, None]

    coefficients = np.empty(len(frac_array), dtype=float)
    survival = 1.0
    for index in range(len(frac_array) - 1, -1, -1):
        coefficients[index] = frac_array[index] * survival
        survival *= 1.0 - frac_array[index]
    chips = coefficients @ weights
    tot = chips.sum()
    if tot <= 0:
        return None
    chips /= tot
    close = bars[-1]["close"]
    peak_price = float(grid[int(chips.argmax())])
    concentration = float(chips[np.abs(grid - peak_price) <= CHIP_BAND * peak_price].sum())
    winner = float(chips[grid <= close].sum()) if close else 0.0
    peak_pctile = (peak_price - pmin) / span
    price_to_peak = close / peak_price - 1.0 if close and peak_price else None
    return {
        "concentration": concentration,
        "winner": winner,
        "peak_price": peak_price,
        "peak_pctile": peak_pctile,
        "price_to_peak": price_to_peak,
    }


def _score_chip(bars: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[Dict[str, float]]]:
    """信号③低位筹码集中：单峰密集 + 主峰低位 + 价格贴近主峰 + 获利盘中低位。"""
    m = _chip_metrics(bars)
    if m is None:
        return None, None
    conc = _clip01((m["concentration"] - CHIP_CONC_LO) / (CHIP_CONC_HI - CHIP_CONC_LO)) * 100.0
    peak_low = _clip01((CHIP_PEAK_ZERO - m["peak_pctile"]) / (CHIP_PEAK_ZERO - CHIP_PEAK_FULL)) * 100.0
    dist = m.get("price_to_peak")
    if dist is None or dist <= CHIP_PRICE_BELOW_ZERO or dist >= CHIP_PRICE_ABOVE_ZERO:
        price_near_peak = 0.0
    elif dist <= 0:
        price_near_peak = _clip01((dist - CHIP_PRICE_BELOW_ZERO) / (0.0 - CHIP_PRICE_BELOW_ZERO)) * 100.0
    elif dist <= CHIP_PRICE_ABOVE_FULL:
        price_near_peak = 100.0
    else:
        price_near_peak = _clip01((CHIP_PRICE_ABOVE_ZERO - dist) / (CHIP_PRICE_ABOVE_ZERO - CHIP_PRICE_ABOVE_FULL)) * 100.0

    winner = m["winner"]
    if winner <= CHIP_WINNER_MIN or winner >= CHIP_WINNER_MAX:
        winner_mid_low = 0.0
    elif winner < CHIP_WINNER_FULL_LO:
        winner_mid_low = _clip01((winner - CHIP_WINNER_MIN) / (CHIP_WINNER_FULL_LO - CHIP_WINNER_MIN)) * 100.0
    elif winner <= CHIP_WINNER_FULL_HI:
        winner_mid_low = 100.0
    else:
        winner_mid_low = _clip01((CHIP_WINNER_MAX - winner) / (CHIP_WINNER_MAX - CHIP_WINNER_FULL_HI)) * 100.0

    score = 0.45 * conc + 0.25 * peak_low + 0.20 * price_near_peak + 0.10 * winner_mid_low
    m["sub_concentration"] = conc
    m["sub_peak_low"] = peak_low
    m["sub_price_near_peak"] = price_near_peak
    m["sub_winner_mid_low"] = winner_mid_low
    return score, m


def _sealed_and_streak(bars: List[Dict[str, Any]], code: str) -> Tuple[int, int]:
    """近 SHORT_WIN 日一字封死板数量；以及当前连续涨停 streak 长度。"""
    limit = _limit_pct(code) - 0.3
    sealed = 0
    for i in range(max(1, len(bars) - SHORT_WIN), len(bars)):
        chg, prev_close = bars[i]["chg"], bars[i - 1]["close"]
        high, low = bars[i]["high"], bars[i]["low"]
        if chg is None or not prev_close or high is None or low is None:
            continue
        if chg >= limit and (high - low) / prev_close <= SEALED_AMP:
            sealed += 1
    streak = 0
    for b in reversed(bars):
        if b["chg"] is not None and b["chg"] >= limit:
            streak += 1
        else:
            break
    return sealed, streak


def _breakout_trigger(bars: List[Dict[str, Any]], vol: List[Optional[float]]) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """右进左出触发器：从低/中位整理中放量突破近 TRIGGER_WIN 日收盘高点。

    左侧吸筹分给「在不在吸筹」的状态；触发器给「是否刚启动」的买点——只有当价格突破
    一个『位于近 60 日中低位的整理平台』且当日放量，才算右侧确认(避免高位追涨/缩量假突破)。
    """
    if len(bars) < HIGH_WIN + 1:
        return False, None
    closes = [b["close"] for b in bars]
    c = closes[-1]
    prior = [x for x in closes[-1 - TRIGGER_WIN:-1] if x]
    if not c or len(prior) < TRIGGER_WIN // 2:
        return False, None
    ceiling = max(prior)
    broke = c > ceiling
    base_vol = _mean([v for v in vol[-1 - TRIGGER_WIN:-1] if v])
    vol_confirm = bool(base_vol and vol[-1] and vol[-1] > TRIGGER_VOL_MULT * base_vol)
    win = [x for x in closes[-HIGH_WIN - 1:-1] if x]   # 突破前的近 60 日(不含今日)
    base_pctile = sum(1 for x in win if x <= ceiling) / len(win) if win else 1.0
    from_low = base_pctile < TRIGGER_BASE_MAX_POS
    triggered = bool(broke and vol_confirm and from_low)
    return triggered, {"breakout": bool(broke), "vol_confirm": vol_confirm, "base_pctile": round(base_pctile, 2)}


def _kl_pc(bars: List[Dict[str, Any]], i: int) -> Optional[float]:
    return bars[i - 1]["close"] if i > 0 else None


def _kl_amp(bars, i):
    pc, h, l = _kl_pc(bars, i), bars[i]["high"], bars[i]["low"]
    return (h - l) / pc if pc and h is not None and l is not None else None


def _kl_body(bars, i):
    pc, o, c = _kl_pc(bars, i), bars[i]["open"], bars[i]["close"]
    return abs(c - o) / pc if pc and o is not None and c is not None else None


def _kl_upper(bars, i):
    pc, h, o, c = _kl_pc(bars, i), bars[i]["high"], bars[i]["open"], bars[i]["close"]
    return (h - max(o, c)) / pc if pc and None not in (h, o, c) else None


def _kl_lower(bars, i):
    pc, l, o, c = _kl_pc(bars, i), bars[i]["low"], bars[i]["open"], bars[i]["close"]
    return (min(o, c) - l) / pc if pc and None not in (l, o, c) else None


def _ret_k(closes: List[Optional[float]], k: int) -> Optional[float]:
    if len(closes) > k and closes[-1] and closes[-1 - k]:
        return closes[-1] / closes[-1 - k] - 1
    return None


def _ma_last(closes: List[Optional[float]], n: int) -> Optional[float]:
    vals = [c for c in closes[-n:] if c is not None]
    return sum(vals) / len(vals) if len(vals) >= max(2, int(n * 0.6)) else None


def _avg_vol(vol: List[Optional[float]], a: int, b: int) -> Optional[float]:
    seg = [v for v in vol[a:b] if v]
    return sum(seg) / len(seg) if seg else None


def _swing_lows(values: List[float], k: int = 3) -> List[int]:
    """局部低点(摆动低)索引：values[i] 是前后各 k 根内的最小值。

    用于底部形态识别（双底/W底的两个谷）；相邻 k 根内只保留更低的一个，避免平台重复计点。
    """
    lows: List[int] = []
    n = len(values)
    for i in range(k, n - k):
        v = values[i]
        if v is None or v != min(values[i - k:i + k + 1]):
            continue
        if lows and i - lows[-1] <= k:        # 太近：保留更低者
            if v < values[lows[-1]]:
                lows[-1] = i
        else:
            lows.append(i)
    return lows


_PRIOR_CHIP_UNSET = object()


def _build_pattern_context(
    code: str,
    bars: List[Dict[str, Any]],
    prior_chip: Any = _PRIOR_CHIP_UNSET,
    defer_chip: bool = False,
) -> Dict[str, Any]:
    """一次性算齐形态匹配要用的量价上下文（避免每个匹配器各算一遍）。"""
    current_bars = bars[-LOOKBACK:]
    closes = [b["close"] for b in current_bars]
    vol, vol_measure = _volume_series(current_bars)
    score_position, pos = _score_position(current_bars)
    _, vol_ratio = _score_volume_ratio(vol)
    day_volume_ratio = _p26_volume_ratio(vol)
    score_divergence, drift = _score_divergence(current_bars, vol)
    score_cmf, cmf = _score_cmf(current_bars, vol)
    if prior_chip is _PRIOR_CHIP_UNSET:
        prior_end = len(bars) - CHIP_WINNER_RISK_DAYS
        prior_start = max(0, prior_end - LOOKBACK)
        prior_chip_bars = bars[prior_start:prior_end]
    else:
        prior_chip_bars = []
    if defer_chip:
        score_chip, chip = None, None
        prior_chip_value = None if prior_chip is _PRIOR_CHIP_UNSET else prior_chip
    else:
        score_chip, chip = _score_chip(current_bars)
        if prior_chip is _PRIOR_CHIP_UNSET:
            prior_chip_value = (
                _chip_metrics(prior_chip_bars)
                if len(prior_chip_bars) >= MIN_BARS
                else None
            )
        else:
            prior_chip_value = prior_chip
    chip_winner = chip.get("winner") if chip else None
    chip_winner_prior = prior_chip_value.get("winner") if prior_chip_value else None
    chip_winner_change = (
        chip_winner - chip_winner_prior
        if chip_winner is not None and chip_winner_prior is not None
        else None
    )
    sealed, streak = _sealed_and_streak(current_bars, code)
    triggered, trigger = _breakout_trigger(current_bars, vol)
    ma = {n: _ma_last(closes, n) for n in (5, 10, 20, 60)}
    ma_bull = bool(ma[5] and ma[10] and ma[20] and ma[5] > ma[10] > ma[20]
                   and closes[-1] and closes[-1] > ma[5])
    context = {
        "code": code, "closes": closes, "vol": vol,
        "pos": pos, "vol_ratio": vol_ratio, "drift": drift, "cmf": cmf,
        "chip": chip, "sealed": sealed, "streak": streak, "triggered": triggered,
        "vol_measure": vol_measure,
        "score_position": score_position,
        "score_divergence": score_divergence,
        "score_cmf": score_cmf,
        "score_chip": score_chip,
        "trigger": trigger,
        "chip_winner": chip_winner,
        "chip_winner_prior": chip_winner_prior,
        "chip_winner_change": chip_winner_change,
        "day_volume_ratio": day_volume_ratio,
        "p26_volume_ratio": day_volume_ratio,
        "ma5": ma[5], "ma10": ma[10], "ma20": ma[20], "ma60": ma[60], "ma_bull": ma_bull,
    }
    if defer_chip:
        context.update({
            "_chip_deferred": True,
            "_chip_computed": False,
            "_prior_chip_computed": prior_chip is not _PRIOR_CHIP_UNSET,
            "_prior_chip": prior_chip_value,
            "_chip_bars": current_bars,
            "_prior_chip_bars": prior_chip_bars,
        })
    return context


def _ensure_pattern_chip_context(
    ctx: Dict[str, Any],
    include_prior: bool = False,
) -> Optional[Dict[str, float]]:
    """按需补算形态研究的筹码上下文；实时评分仍走 eager 路径。"""
    if not ctx.get("_chip_deferred"):
        return ctx.get("chip")
    if not ctx.get("_chip_computed"):
        score_chip, chip = _score_chip(ctx.get("_chip_bars") or [])
        ctx["score_chip"] = score_chip
        ctx["chip"] = chip
        ctx["chip_winner"] = chip.get("winner") if chip else None
        ctx["_chip_computed"] = True
    if include_prior and not ctx.get("_prior_chip_computed"):
        prior_bars = ctx.get("_prior_chip_bars") or []
        ctx["_prior_chip"] = _chip_metrics(prior_bars) if len(prior_bars) >= MIN_BARS else None
        ctx["_prior_chip_computed"] = True
    if include_prior:
        prior_chip = ctx.get("_prior_chip")
        ctx["chip_winner_prior"] = prior_chip.get("winner") if prior_chip else None
        winner = ctx.get("chip_winner")
        prior = ctx.get("chip_winner_prior")
        ctx["chip_winner_change"] = (
            winner - prior if winner is not None and prior is not None else None
        )
    return ctx.get("chip")


def _p1_low_base_state(bars: List[Dict[str, Any]]) -> bool:
    """P1原始状态：低位窄幅横住、贴近MA20、收阳且温和缩量。"""
    if len(bars) < HIGH_WIN:
        return False
    current_bars = bars[-LOOKBACK:]
    try:
        closes = [float(bar.get("close")) for bar in current_bars]
        open_price = float(current_bars[-1].get("open"))
    except (TypeError, ValueError):
        return False
    if (
        len(closes) < HIGH_WIN
        or any(not math.isfinite(value) or value <= 0 for value in closes[-HIGH_WIN:])
        or not math.isfinite(open_price)
        or open_price <= 0
    ):
        return False

    close = closes[-1]
    position60 = sum(1 for value in closes[-HIGH_WIN:] if value <= close) / HIGH_WIN
    if position60 >= P1_POSITION_60_MAX:
        return False

    ma20 = _mean(closes[-20:])
    reference20 = closes[-21] if len(closes) > 20 else None
    if (
        ma20 is None
        or not reference20
        or abs(close / reference20 - 1.0) > P1_ABS_RET_20_MAX
        or close < P1_MA20_FLOOR * ma20
        or close < open_price
    ):
        return False

    volume, _source = _volume_series(current_bars)
    day_volume_ratio = _p26_volume_ratio(volume)
    return bool(
        day_volume_ratio is not None
        and P1_DAY_VOLUME_RATIO_MIN <= day_volume_ratio <= P1_DAY_VOLUME_RATIO_MAX
    )


def _p1_breakout_addon_state(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算P1突破补充腿的个股侧状态，先做纯突破去重，再叠加过滤条件。

    “昨日是否已突破”只看昨日相对其此前10日收盘的纯突破状态。即使昨日因量能、
    位置或市场门未通过，今天仍不会被误当作一个新的突破 episode。
    """
    empty: Dict[str, Any] = {
        "available": False,
        "current_breakout": False,
        "previous_breakout": False,
        "first_breakout": False,
        "stock_gate": False,
        "position60": None,
        "ret20": None,
        "close_over_ma20": None,
        "day_volume_ratio": None,
        "day_volume_source": None,
        "reason": "insufficient_history",
        "failed_checks": ["history"],
    }
    required = max(HIGH_WIN, P1_BREAKOUT_DAYS + 2, 21)
    current_bars = bars[-LOOKBACK:]
    if len(current_bars) < required:
        return empty

    closes = [_safe(bar.get("close")) for bar in current_bars]
    recent = closes[-HIGH_WIN:]
    if any(value is None or value <= 0 for value in recent):
        invalid = dict(empty)
        invalid["reason"] = "invalid_close"
        invalid["failed_checks"] = ["valid_close"]
        return invalid

    # All closes needed below are inside the validated trailing 60-bar window.
    close = float(closes[-1])
    previous_close = float(closes[-2])
    prior_today = [float(value) for value in closes[-(P1_BREAKOUT_DAYS + 1):-1]]
    prior_yesterday = [float(value) for value in closes[-(P1_BREAKOUT_DAYS + 2):-2]]
    current_breakout = close > max(prior_today)
    previous_breakout = previous_close > max(prior_yesterday)
    first_breakout = bool(current_breakout and not previous_breakout)

    position60 = sum(1 for value in recent if float(value) <= close) / HIGH_WIN
    reference20 = float(closes[-21])
    ret20 = close / reference20 - 1.0
    ma20 = _mean([float(value) for value in closes[-20:]])
    close_over_ma20 = close / ma20 if ma20 and ma20 > 0 else None
    volume, volume_source = _volume_series(current_bars)
    day_volume_ratio = _p26_volume_ratio(volume)

    checks = {
        "first_breakout": first_breakout,
        "position60": position60 < P1_ADDON_POSITION_60_MAX,
        "ret20": abs(ret20) <= P1_ADDON_ABS_RET_20_MAX,
        "ma20": bool(
            close_over_ma20 is not None
            and close_over_ma20 >= P1_ADDON_MA20_FLOOR
        ),
        "volume": bool(
            day_volume_ratio is not None
            and P1_ADDON_DAY_VOLUME_RATIO_MIN
            <= day_volume_ratio
            <= P1_ADDON_DAY_VOLUME_RATIO_MAX
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "available": True,
        "current_breakout": bool(current_breakout),
        "previous_breakout": bool(previous_breakout),
        "first_breakout": first_breakout,
        "stock_gate": not failed,
        "position60": position60,
        "ret20": ret20,
        "close_over_ma20": close_over_ma20,
        "day_volume_ratio": day_volume_ratio,
        "day_volume_source": volume_source,
        "reason": "open" if not failed else "closed",
        "failed_checks": failed,
    }


def _p1_rule_state(
    bars: List[Dict[str, Any]],
    market_gate: bool,
) -> Dict[str, Any]:
    """正式P1复合事件：原两日企稳腿 OR 首次10日突破补充腿。"""
    base_hit = _confirmed_new_state(
        bars, _p1_low_base_state, days=P1_CONFIRM_DAYS
    )
    stock = _p1_breakout_addon_state(bars)
    supplemental_hit = bool(stock["stock_gate"] and market_gate)
    active = bool(base_hit or supplemental_hit)
    if base_hit and supplemental_hit:
        source = "base_and_breakout_addon"
    elif base_hit:
        source = "base_leg"
    elif supplemental_hit:
        source = "breakout_addon"
    else:
        source = None
    signal_date = str(bars[-1].get("date") or "") if bars else None
    return {
        "code": "P1",
        "name": "P1超额优先复合确认",
        "version": P1_VERSION,
        "replaced_on": P1_REPLACED_ON,
        "signal_date": signal_date,
        "active": active,
        "hit": active,
        "source": source,
        "base_leg_hit": bool(base_hit),
        "supplemental_hit": supplemental_hit,
        "market_gate": bool(market_gate),
        "breakout_addon_state": stock,
        "eod_only": True,
        "score_weight": ACCUM_MODEL_WEIGHTS["p1"],
        "production_replacement": True,
    }


def _pat_low_consolidation(
    bars: List[Dict[str, Any]],
    ctx: Dict[str, Any],
    p1_market_gate: bool = False,
) -> bool:                                  # P1 超额优先复合确认
    return bool(_p1_rule_state(bars, p1_market_gate)["active"])


def _pat_low_shadows(bars, ctx):                 # P2 低位真实长下影+温和缩量承接
    if ctx["pos"] is None or ctx["pos"] >= 0.40 or len(bars) < P2_WINDOW + 1:
        return False
    day_volume_ratio = ctx.get("p26_volume_ratio")
    if day_volume_ratio is None:
        volume = ctx.get("vol")
        if volume is None:
            volume, _source = _volume_series(bars)
        day_volume_ratio = _p26_volume_ratio(volume)
    if (
        day_volume_ratio is None
        or day_volume_ratio < P2_DAY_VOLUME_RATIO_MIN
        or day_volume_ratio > P2_DAY_VOLUME_RATIO_MAX
    ):
        return False
    matched: List[bool] = []
    for i in range(len(bars) - P2_WINDOW, len(bars)):
        body, low = _kl_body(bars, i), _kl_lower(bars, i)
        matched.append(bool(
            body is not None
            and low is not None
            and low >= P2_SHADOW_BODY_RATIO_MIN * body
            and low >= P2_LOWER_SHADOW_MIN
        ))
    return matched[-1] and sum(matched) >= P2_MIN_COUNT


def _pat_shakedown_absorb(bars, ctx):            # P3 隐性收集(缩量收盘大跌后首次收复)
    pos, vol, closes = ctx["pos"], ctx["vol"], ctx["closes"]
    if pos is None or pos >= P3_POSITION_MAX or len(bars) < 30:
        return False
    confirm_volume_ratio = ctx.get("p26_volume_ratio")
    if confirm_volume_ratio is None:
        confirm_volume_ratio = _p26_volume_ratio(vol)
    if confirm_volume_ratio is None or confirm_volume_ratio > P3_CONFIRM_VOLUME_RATIO_MAX:
        return False
    for i in range(len(bars) - 2, len(bars) - P3_EVENT_WINDOW - 2, -1):
        pre_drop, drop_close = closes[i - 1], closes[i]
        if (
            not pre_drop or not drop_close
            or drop_close > pre_drop * (1.0 - P3_DROP_MIN)
        ):
            continue
        # 绝大多数交易日并非大跌事件：先做O(1)价格门，再计算该事件日前20日均量。
        event_volume = vol[i]
        event_base = _avg_vol(vol, i - 20, i)
        if (
            not event_volume or not event_base
            or event_volume >= P3_EVENT_VOLUME_RATIO_MAX * event_base
        ):
            continue
        # 真正首次收复：事件后到昨日从未站回跌前收盘；同时保留宽松的6%破底保护。
        if not closes[-1] or not closes[-2] or closes[-1] < pre_drop or closes[-2] >= pre_drop:
            continue
        if any(
            not value
            or value >= pre_drop
            or value < drop_close * (1.0 - P3_UNDERCUT_MAX)
            for value in closes[i + 1:-1]
        ):
            continue
        return True
    return False


def _p4_absorption_state(bars: List[Dict[str, Any]]) -> Optional[bool]:
    """P4核心状态；固定180日窗口，数据不足返回None以便首次事件严格失效。"""
    if len(bars) < LOOKBACK:
        return None
    current_bars = bars[-LOOKBACK:]
    _, pos = _score_position(current_bars)
    volume, _source = _volume_series(current_bars)
    _, volume_ratio = _score_volume_ratio(volume)
    _, drift = _score_absorption(current_bars)
    _, cmf = _score_cmf(current_bars, volume)
    if any(value is None for value in (pos, volume_ratio, drift, cmf)):
        return None
    return bool(
        pos < P4_POSITION_MAX
        and P4_VOLUME_RATIO_MIN < volume_ratio <= P4_VOLUME_RATIO_MAX
        and abs(drift) < P4_ABS_DRIFT_MAX
        and cmf > P4_CMF_MIN
    )


def _p4_current_state(ctx: Dict[str, Any]) -> bool:
    pos, vr, drift, cmf = ctx["pos"], ctx["vol_ratio"], ctx["drift"], ctx["cmf"]
    return bool(
        pos is not None
        and pos < P4_POSITION_MAX
        and vr is not None
        and P4_VOLUME_RATIO_MIN < vr <= P4_VOLUME_RATIO_MAX
        and drift is not None
        and abs(drift) < P4_ABS_DRIFT_MAX
        and cmf is not None
        and cmf > P4_CMF_MIN
    )


def _pat_absorption(bars, ctx):                  # P4 量增价稳(首次观察事件)
    """只报核心状态由昨日不成立到今日成立，避免同一放量平台逐日重复命中。"""
    if len(bars) < LOOKBACK + 1 or not _p4_current_state(ctx):
        return False
    previous_state = _p4_absorption_state(bars[:-1])
    return previous_state is False


def _pat_bottom_formation(bars, ctx):            # P5 双底右腿确认
    """低位W底的右腿确认事件，而不是二底后每天重复成立的状态。

    两个新增要求均为短窗口价格比较：二底须在近8日，且今日由“昨日未突破”切换为突破
    前3日收盘高点。原有“二底回升”下界收紧为触及颈线；不再重建筹码分布。
    """
    pos, closes = ctx["pos"], ctx["closes"]
    if pos is None or pos >= P5_POSITION_MAX:
        return False
    cs = []
    for value in closes[-60:]:
        try:
            close = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(close) or close <= 0:
            return False
        cs.append(close)
    if len(cs) < 40:
        return False
    lows = _swing_lows(cs, k=3)
    if len(lows) < 2:
        return False
    i1, i2 = lows[-2], lows[-1]
    if i2 - i1 < 5:                               # 两底间隔太近不算结构
        return False
    l1, l2 = cs[i1], cs[i2]
    second_low_return = l2 / l1 - 1.0
    if (second_low_return < P5_SECOND_LOW_RETURN_MIN - P5_FLOAT_EPSILON
            or second_low_return > P5_SECOND_LOW_RETURN_MAX + P5_FLOAT_EPSILON):
        return False
    neck = max(cs[i1:i2 + 1])                     # 两底之间的反弹高点=颈线
    if neck / min(l1, l2) - 1 < P5_NECK_REBOUND_MIN - P5_FLOAT_EPSILON:
        return False
    if len(cs) - 1 - i2 > P5_SECOND_LOW_RECENCY_MAX:
        return False                               # 只看二底确认后的近端右腿
    c = cs[-1]
    neck_ratio = c / neck
    if (neck_ratio < P5_NECK_RATIO_MIN - P5_FLOAT_EPSILON
            or neck_ratio > P5_NECK_RATIO_MAX + P5_FLOAT_EPSILON):
        return False
    win = P5_RIGHT_LEG_BREAKOUT_WIN
    current_breakout = c > max(cs[-win - 1:-1])
    previous_breakout = cs[-2] > max(cs[-win - 2:-2])
    return current_breakout and not previous_breakout


def _confirmed_new_state(bars, predicate, days=PATTERN_CONFIRM_DAYS):
    """条件连续成立 ``days`` 天时只触发一次，避免状态型形态每天重复命中。"""
    if len(bars) <= days:
        return False
    # 绝大多数截面当前条件就不成立；短路可避免为每只股票无条件重算6遍滚动状态。
    if not predicate(bars):
        return False
    for offset in range(1, days):
        if not predicate(bars[:-offset]):
            return False
    return not predicate(bars[:-days])


def _p23_compression_state(bars: List[Dict[str, Any]]) -> Optional[bool]:
    """P23核心状态；数据不足返回None，避免把未知误当成“昨日未压缩”。"""
    if len(bars) < HIGH_WIN + 1:                 # 60个振幅至少需要61根bar
        return None
    _, pos = _score_position(bars)
    ratio = _amp_ratio(bars)
    if pos is None or ratio is None:
        return None
    return pos < P23_POSITION_60_MAX and ratio < COMPRESS_AMP_RATIO


def _p23_confirmation_state(
    bars: List[Dict[str, Any]],
) -> Optional[bool]:
    """P23结构确认状态：低位压缩且略站上MA20；数据不足返回None。"""
    core_state = _p23_compression_state(bars)
    if core_state is not True:
        return core_state

    current_bars = bars[-LOOKBACK:]
    closes = [_safe(bar.get("close")) for bar in current_bars[-20:]]
    if len(closes) < 20 or any(value is None or value <= 0 for value in closes):
        return None
    numeric_closes = [float(value) for value in closes if value is not None]
    ma20 = _mean(numeric_closes)
    close_over_ma20 = numeric_closes[-1] / ma20 if ma20 and ma20 > 0 else None
    return bool(close_over_ma20 is not None and close_over_ma20 >= P23_MA20_FLOOR)


def _p23_temperate_volume_state(
    bars: List[Dict[str, Any]],
    ctx: Optional[Dict[str, Any]] = None,
) -> Optional[bool]:
    """P23信号日量能门；不参与结构状态去重，避免量比进出区间造成重复触发。"""
    if ctx is not None and "p26_volume_ratio" in ctx:
        day_volume_ratio = ctx.get("p26_volume_ratio")
    else:
        current_bars = bars[-LOOKBACK:]
        volume, _source = _volume_series(current_bars)
        day_volume_ratio = _p26_volume_ratio(volume)
    if day_volume_ratio is None:
        return None
    return bool(
        P23_DAY_VOLUME_RATIO_MIN <= day_volume_ratio <= P23_DAY_VOLUME_RATIO_MAX
    )


def _pat_compression(bars, ctx):                 # P23 低位压缩温和量能转强
    """结构确认状态由False切为True后再检查当日量能；同一连续状态只报告一次。"""
    if len(bars) < HIGH_WIN + 2:                 # 昨日状态也必须有完整60个振幅
        return False
    current_state = _p23_confirmation_state(bars)
    if current_state is not True:
        return False
    if _p23_confirmation_state(bars[:-1]) is not False:
        return False
    return _p23_temperate_volume_state(bars, ctx=ctx) is True


def _p24_obv_divergence_state(bars):
    """P24 原始状态：中低位、价格未启动但30日OBV有显著净流入。"""
    if len(bars) < HIGH_WIN:
        return False
    _, pos = _score_position(bars)
    closes = [bar.get("close") for bar in bars]
    volumes, _source = _volume_series(bars)
    if pos is None or pos >= OBV_POSITION_MAX or len(closes) < 31:
        return False
    if not closes[-1] or not closes[-31]:
        return False
    obv = 0.0
    for i in range(len(closes) - 30, len(closes)):
        if closes[i] is None or closes[i - 1] is None:
            continue
        direction = 1 if closes[i] > closes[i - 1] else (-1 if closes[i] < closes[i - 1] else 0)
        obv += direction * (volumes[i] or 0)
    valid_volume = [value for value in volumes[-60:] if value]
    mean_volume = (sum(valid_volume) / len(valid_volume)) if valid_volume else 0.0
    if not mean_volume:
        return False
    price_return = closes[-1] / closes[-31] - 1.0
    obv_return = obv / (mean_volume * 30)
    return (
        price_return <= OBV_PRICE_RETURN_MAX
        and obv_return > OBV_RETURN_MIN
        and obv_return - price_return > OBV_DIV_MIN
    )


def _pat_obv_divergence(bars, ctx):              # P24 OBV底背离(价弱量增)
    """严格OBV底背离连续5日确认后仅触发一次，过滤短暂噪声与重复命中。"""
    return _confirmed_new_state(bars, _p24_obv_divergence_state)


def _pat_bottom_base_ignition(bars, ctx):         # P25 低位缩量平台转强
    """120日中低位 + 60日横盘缩量，随后价格小幅转强并接近20日平台。

    用价格确认替代旧版爆量确认；所有股票池口径相同，计算量不增加。
    """
    if len(bars) < 120:
        return False
    closes = [b.get("close") for b in bars]
    if any(value is None or value <= 0 for value in closes[-121:]):
        return False
    ret5 = closes[-1] / closes[-6] - 1.0
    recent_chg = [bars[i].get("chg") for i in range(len(bars) - 3, len(bars))]
    if any(value is None for value in recent_chg):
        return False
    if ret5 > 0.08 or bars[-1]["chg"] >= 6.0 or max(recent_chg) >= 6.0:
        return False

    close = closes[-1]
    position120 = sum(1 for value in closes[-120:] if value <= close) / 120.0
    if position120 > P25_POSITION_120_MAX:
        return False
    highs = [b.get("high") for b in bars[-60:]]
    lows = [b.get("low") for b in bars[-60:]]
    if any(value is None or value <= 0 for value in highs + lows):
        return False
    range60 = max(highs) / min(lows) - 1.0
    abs_ret60 = abs(close / closes[-61] - 1.0)
    if range60 > P25_RANGE_60_MAX or abs_ret60 > P25_ABS_RET_60_MAX:
        return False
    ret20 = close / closes[-21] - 1.0
    if ret20 < P25_RET_20_MIN:
        return False

    volumes = [b.get("volume") for b in bars]
    if any(value is None or value <= 0 for value in volumes[-64:]):
        return False
    early40 = _mean(volumes[-60:-20])
    recent20 = _mean(volumes[-20:])
    if not early40 or not recent20:
        return False
    if recent20 / early40 > P25_VOLUME_CONTRACT_MAX:
        return False

    volume_ma5 = [_mean(volumes[i - 4:i + 1]) for i in range(len(volumes) - 60, len(volumes))]
    axis = list(range(60))
    axis_mean = 29.5
    denom = sum((value - axis_mean) ** 2 for value in axis)
    volume_mean = _mean(volume_ma5)
    slope = sum((x - axis_mean) * y for x, y in zip(axis, volume_ma5)) / denom
    normalized_slope = slope / volume_mean if volume_mean else 0.0
    if normalized_slope > P25_VOLUME_SLOPE_60_MAX:
        return False

    prior20 = [value for value in closes[-21:-1] if value]
    breakout20 = close / max(prior20) - 1.0 if prior20 else -1.0
    return breakout20 >= P25_BREAKOUT_20_MIN


def _p26_peak_drawdown_confirmed(
    close: Optional[float],
    peak_high: Optional[float],
) -> bool:
    """P26峰值回撤边界；研究脚本使用相同的乘法表达式。"""
    return bool(
        close is not None
        and close > 0.0
        and peak_high is not None
        and peak_high > 0.0
        and close <= (1.0 - P26_PEAK_DRAWDOWN_MIN) * peak_high
    )


def _p26_price_reversal_confirmed(
    bars: Sequence[Dict[str, Any]],
    ctx: Optional[Dict[str, Any]] = None,
) -> bool:
    """P26廉价价格门：先快速拉升，再从近端盘中高点明确回撤。"""
    if len(bars) < max(P26_PEAK_HIGH_DAYS, 6):
        return False
    closes = list((ctx or {}).get("closes") or [bar.get("close") for bar in bars])
    ret5 = _ret_k(closes, 5)
    close = _safe(bars[-1].get("close"))
    recent_highs = [
        value
        for value in (
            _safe(bar.get("high")) for bar in bars[-P26_PEAK_HIGH_DAYS:]
        )
        if value is not None and value > 0.0
    ]
    return bool(
        ret5 is not None
        and ret5 >= P26_RET_5_MIN
        and recent_highs
        and _p26_peak_drawdown_confirmed(close, max(recent_highs))
    )


def _pat_chip_winner_risk(bars, ctx):             # P26 获利盘冲高回撤风险
    """获利筹码拥挤/快速转盈、极端放量且冲高回撤，提示兑现或派发风险。"""
    volume_ratio = ctx.get("p26_volume_ratio")
    if volume_ratio is None or volume_ratio < CHIP_WINNER_RISK_VOLUME_RATIO_MIN:
        return False
    # 先检查O(1)/固定10根的价格门，再做昂贵的筹码重建。稳态上升和放量续涨
    # 不应仅因获利盘高而过早卖出；已有冲高、且价格离开峰值后才升级风险。
    if not _p26_price_reversal_confirmed(bars, ctx=ctx):
        return False
    _ensure_pattern_chip_context(ctx, include_prior=True)
    winner = ctx.get("chip_winner")
    prior = ctx.get("chip_winner_prior")
    return bool(
        (winner is not None and winner >= CHIP_WINNER_RISK_HIGH)
        or (
            winner is not None and prior is not None
            and prior < CHIP_WINNER_RISK_PRIOR_MAX
            and winner >= CHIP_WINNER_RISK_CURRENT_MIN
        )
    )


def _pat_test_upper_shadow(bars, ctx):           # P6 试盘长上影破平台又缩回
    if len(bars) < 30:
        return False
    closes = ctx["closes"]
    for i in range(len(bars) - 8, len(bars) - 1):
        up, body = _kl_upper(bars, i), _kl_body(bars, i)
        prior = [bars[j]["high"] for j in range(max(0, i - 20), i) if bars[j]["high"] is not None]
        if (up and body and up > 0.03 and up > 2 * body and prior and bars[i]["high"]
                and bars[i]["high"] > max(prior)
                and closes[-1] and closes[i] and closes[-1] < closes[i]):
            return True
    return False


def _pat_bottom_spike(bars, ctx):                # P7 底部异动放量
    pos, vr = ctx["pos"], ctx["vol_ratio"]
    if pos is None or pos >= 0.40 or vr is None or vr <= 1.5 or len(bars) < 6:
        return False
    amps = [a for a in (_kl_amp(bars, i) for i in range(len(bars) - 5, len(bars))) if a is not None]
    return bool(amps and max(amps) > 0.07)


def _pat_pullback_shakeout(bars, ctx):           # P8 缩量回踩洗盘(挖坑)
    closes, vol, ma20 = ctx["closes"], ctx["vol"], ctx["ma20"]
    if ma20 is None or len(bars) < 12:
        return False
    c = closes[-1]
    if not c or c < ma20:
        return False
    hi = max([x for x in closes[-10:] if x] or [c])
    if not (-0.15 <= c / hi - 1 <= -0.02):
        return False
    down_v = [vol[i] for i in range(len(bars) - 8, len(bars))
              if bars[i]["chg"] is not None and bars[i]["chg"] < 0 and vol[i]]
    up_v = [vol[i] for i in range(len(bars) - 8, len(bars))
            if bars[i]["chg"] is not None and bars[i]["chg"] > 0 and vol[i]]
    if not down_v or not up_v:
        return False
    chip = _ensure_pattern_chip_context(ctx)
    conc_ok = chip is None or chip["concentration"] >= 0.40
    return (sum(down_v) / len(down_v)) < (sum(up_v) / len(up_v)) and conc_ok


def _pat_climb_wash(bars, ctx):                  # P9 边拉边洗
    if not ctx["ma_bull"] or len(bars) < 12:
        return False
    signs = [1 if (bars[i]["chg"] or 0) > 0 else -1 for i in range(len(bars) - 8, len(bars))]
    alt = sum(1 for k in range(1, len(signs)) if signs[k] != signs[k - 1])
    lows = [bars[i]["low"] for i in range(len(bars) - 10, len(bars)) if bars[i]["low"] is not None]
    higher_low = len(lows) >= 8 and min(lows[-5:]) > min(lows[:5])
    return alt >= 4 and higher_low


def _pat_high_turnover_wash(bars, ctx):          # P10 高换手洗盘(筹码峰不发散)
    vr, ma20, closes = ctx["vol_ratio"], ctx["ma20"], ctx["closes"]
    if not (vr is not None and vr > 1.5 and ma20 and closes[-1] and closes[-1] > ma20):
        return False
    chip = _ensure_pattern_chip_context(ctx)
    return bool(chip is not None and chip["concentration"] >= 0.45)


def _pat_breakout(bars, ctx):                    # P11 放量突破启动(右进左出)
    return bool(ctx["triggered"])


def _pat_consecutive_limit(bars, ctx):           # P12 连板拉升
    return ctx["streak"] >= 2


def _pat_first_board(bars, ctx):                 # P13 首板卡位
    if len(bars) < 22:
        return False
    limit = _limit_pct(ctx["code"]) - 0.3
    today = bars[-1]["chg"]
    if today is None or today < limit or ctx["streak"] != 1:
        return False
    for i in range(len(bars) - 21, len(bars) - 1):
        if bars[i]["chg"] is not None and bars[i]["chg"] >= limit:
            return False
    t = bars[-1]["turnover"]
    return t is not None and 10.0 <= t <= 45.0


def _pat_high_vol_stall(bars, ctx):              # P14 高位放量滞涨
    pos, vr, closes = ctx["pos"], ctx["vol_ratio"], ctx["closes"]
    if pos is None or pos < 0.85 or vr is None or vr <= 1.5:
        return False
    r5 = _ret_k(closes, 5)
    up = _kl_upper(bars, len(bars) - 1)
    return r5 is not None and r5 <= 0.02 and up is not None and up > 0.02


def _p15_original_event(
    bars: Sequence[Dict[str, Any]],
    vol: Optional[Sequence[Optional[float]]] = None,
) -> bool:
    """复现旧P15的新高量背离状态，仅作为潜在顶部事件而非卖点。"""
    if len(bars) < P15_EVENT_HIGH_DAYS:
        return False
    event_bars = list(bars[-LOOKBACK:])
    if vol is None:
        event_vol, _ = _volume_series(event_bars)
    else:
        if len(vol) != len(bars):
            return False
        event_vol = list(vol[-LOOKBACK:])
    closes = [_safe(bar.get("close")) for bar in event_bars]
    recent = [value for value in closes[-P15_EVENT_HIGH_DAYS:] if value]
    close = closes[-1]
    if len(recent) < 30 or close is None or close < max(recent):
        return False
    _, volume_ratio = _score_volume_ratio(event_vol)
    if volume_ratio is None:
        return False
    ret5 = _ret_k(closes, 5)
    return bool(
        volume_ratio < P15_EVENT_LOW_VOLUME_RATIO_MAX
        or (
            volume_ratio > P15_EVENT_HIGH_VOLUME_RATIO_MIN
            and ret5 is not None
            and ret5 < P15_EVENT_RET5_MAX
        )
    )


def _pat_vol_price_div(bars, ctx):               # P15 新高量背离放量回撤确认
    """旧P15事件后15日内出现放量回撤，才升级为卖出信号。

    旧P15中约99%的命中来自“缩量收盘新高”，单独看更像供给枯竭后的
    趋势延续。因此新规则保留它作为潜在顶部事件，但把信号日后移到
    价格和成交共同确认转弱的当日，避免未来数据回填到事件日。
    """
    # 需要完整保留“当前180日上下文 + 事件回看15日”，否则较早事件日的
    # 换手率覆盖率量源可能只基于截短历史，导致研究与线上口径漂移。
    required = PATTERN_EVAL_BARS
    day_volume_ratio = _safe(ctx.get("day_volume_ratio"))
    if (
        len(bars) < required
        or day_volume_ratio is None
        or day_volume_ratio <= P15_DAY_VOLUME_RATIO_MIN
    ):
        return False

    close = _safe(bars[-1].get("close"))
    recent_highs = [
        _safe(bar.get("high")) for bar in bars[-P15_PULLBACK_HIGH_DAYS:]
    ]
    if (
        close is None
        or close <= 0.0
        or len(recent_highs) != P15_PULLBACK_HIGH_DAYS
        or any(value is None or value <= 0.0 for value in recent_highs)
    ):
        return False
    peak = max(float(value) for value in recent_highs if value is not None)
    if close > peak * (1.0 - P15_PULLBACK_MIN):
        return False

    for lag in range(1, P15_EVENT_LOOKBACK_DAYS + 1):
        event_end = len(bars) - lag
        if _p15_original_event(bars[:event_end]):
            return True
    return False


def _pat_bearish_max_vol(bars, ctx):             # P16 阴天量回撤确认
    pos, vol = ctx["pos"], ctx["vol"]
    if (
        pos is None
        or pos < P16_POSITION_MIN
        or len(bars) < P16_MAX_VOLUME_DAYS
        or len(vol) < P16_MAX_VOLUME_DAYS
    ):
        return False

    # 40日最大量只说明出现了高位分歧；当前收盘较近10日盘中高点至少回撤3%，
    # 才确认分歧已经演变为价格走弱。高点窗口含当前日，允许识别当日冲高回落。
    close = _safe(bars[-1].get("close"))
    recent_highs = [
        value
        for value in (_safe(bar.get("high")) for bar in bars[-P16_PEAK_HIGH_DAYS:])
        if value is not None and value > 0.0
    ]
    if (
        close is None
        or close <= 0.0
        or not recent_highs
        or close > (1.0 - P16_PEAK_DRAWDOWN_MIN) * max(recent_highs)
    ):
        return False

    mx = max([v for v in vol[-P16_MAX_VOLUME_DAYS:] if v] or [0])
    for i in range(len(bars) - P16_EVENT_RECENCY_DAYS, len(bars)):
        if vol[i] and vol[i] >= mx and bars[i]["chg"] is not None and bars[i]["chg"] < 0:
            return True
    return False


def _pat_inverted_v(bars, ctx):                  # P17 倒V反转
    pos, closes = ctx["pos"], ctx["closes"]
    required = P17_RECENT_DAYS + P17_BASE_DAYS
    if (
        pos is None
        or pos < P17_POSITION_MIN
        or len(bars) < required
        or len(closes) < required
    ):
        return False
    recent_window = closes[-P17_RECENT_DAYS:]
    recent = [c for c in recent_window if c]
    base = [c for c in closes[-required:-P17_RECENT_DAYS] if c]
    if len(recent) < 8 or not base:
        return False
    peak = max(recent)
    last, previous = closes[-1], closes[-2]
    if not last or not previous or not peak:
        return False
    first_peak_index = next(
        index for index, close in enumerate(recent_window) if close == peak
    )
    return bool(
        peak / min(base) - 1 > P17_RUNUP_MIN
        and last / peak - 1 <= -P17_PULLBACK_MIN
        and first_peak_index >= P17_RECENT_DAYS - P17_PEAK_RECENCY_BARS
        and last <= previous
    )


def _pat_bearish_engulf(bars, ctx):              # P18 顶部大阴包阳
    if ctx["pos"] is None or ctx["pos"] < 0.80 or len(bars) < 2:
        return False
    o1, c1 = bars[-2]["open"], bars[-2]["close"]
    o0, c0 = bars[-1]["open"], bars[-1]["close"]
    if None in (o1, c1, o0, c0):
        return False
    return c1 > o1 and c0 < o0 and o0 >= c1 and c0 <= o1


def _pat_dump_bigbear(bars, ctx):                # P19 灌压出货(巨量大阴)
    pos, vr = ctx["pos"], ctx["vol_ratio"]
    if pos is None or pos < 0.70 or vr is None or vr <= 1.8:
        return False
    body = _kl_body(bars, len(bars) - 1)
    o, c, h, l = bars[-1]["open"], bars[-1]["close"], bars[-1]["high"], bars[-1]["low"]
    if None in (o, c, h, l) or h <= l:
        return False
    return body is not None and body > 0.06 and c < o and (c - l) / (h - l) < 0.25


def _ma_breakdown(closes: List[Optional[float]], vol_ratio: Optional[float]) -> bool:
    """P20：跌破MA20 + 第5根bar仍在MA20上方 + 放量 + 当日确认下跌。"""
    ma20 = _ma_last(closes, 20)
    if ma20 is None or len(closes) < 25 or vol_ratio is None:
        return False
    c, previous = closes[-1], closes[-2]
    if not c or not previous or c >= ma20:
        return False
    prior, ma20_prior = closes[-5], _ma_last(closes[:-4], 20)
    day_return = c / previous - 1.0
    return bool(
        prior
        and ma20_prior
        and prior > ma20_prior
        and vol_ratio > P20_VOLUME_RATIO_MIN
        and day_return <= P20_DAY_RETURN_MAX
    )


def _pat_ma_breakdown(bars, ctx):                # P20 均线放量破位
    return _ma_breakdown(ctx["closes"], ctx["vol_ratio"])


def _p21_spring_state(bars: List[Dict[str, Any]]) -> Optional[bool]:
    """P21价格主体：近5日深破前40日箱底至少2.5%，当前收盘重新站回。

    固定按交易日切片，不跳过缺失 low 后再错位补数；数据不足或窗口内有脏值时返回
    ``None`` 并关闭信号。该主体刻意保留为最多5日的有效状态，而不是独立事件：专项
    复测中 False→True 事件化会把双池覆盖压到0.5%以下，且近期小盘超额转负。
    """
    required = P21_BOX_DAYS + P21_RECLAIM_DAYS
    if len(bars) < required:
        return None
    window = bars[-required:]
    box_lows = [_safe(bar.get("low")) for bar in window[:P21_BOX_DAYS]]
    recent_lows = [_safe(bar.get("low")) for bar in window[P21_BOX_DAYS:]]
    close = _safe(window[-1].get("close"))
    if (
        close is None
        or close <= 0.0
        or any(value is None or value <= 0.0 for value in box_lows + recent_lows)
    ):
        return None
    numeric_box_lows = [float(value) for value in box_lows if value is not None]
    numeric_recent_lows = [float(value) for value in recent_lows if value is not None]
    box_low = min(numeric_box_lows)
    recent_low = min(numeric_recent_lows)
    return bool(
        recent_low < box_low * P21_UNDERCUT_RATIO_MAX
        and close > box_low
    )


def _p21_temperate_volume_state(
    bars: List[Dict[str, Any]],
    ctx: Optional[Dict[str, Any]] = None,
) -> Optional[bool]:
    """P21供给枯竭确认：信号日量能不高于此前20日均值的75%。"""
    if ctx is not None and "p26_volume_ratio" in ctx:
        day_volume_ratio = ctx.get("p26_volume_ratio")
    else:
        current_bars = bars[-LOOKBACK:]
        volume, _source = _volume_series(current_bars)
        day_volume_ratio = _p26_volume_ratio(volume)
    if day_volume_ratio is None:
        return None
    return bool(day_volume_ratio <= P21_DAY_VOLUME_RATIO_MAX)


def _pat_spring_reclaim(bars, ctx):              # P21 深破缩量收回(Wyckoff spring)
    """深破2.5%后站回箱底，并以不超过0.75倍量确认抛压枯竭。"""
    if len(bars) < P21_MIN_HISTORY_BARS:
        return False
    return bool(
        _p21_spring_state(bars) is True
        and _p21_temperate_volume_state(bars, ctx=ctx) is True
    )


def _pat_failed_breakout(bars, ctx):             # P22 放量假突破(高位拒绝)
    """今日盘中高点刺破前40日高点、但收盘没站上 + 当日放量(>1.8×前20日均量)。沿用早期形态研究。"""
    if len(bars) < 42:
        return False
    highs = [b["high"] for b in bars if b["high"] is not None]
    if len(highs) < 42:
        return False
    high_40_prior = max(highs[-41:-1])           # 前40日(不含今日)最高
    today_high, c = bars[-1]["high"], ctx["closes"][-1]
    vol = ctx["vol"]
    base = _mean([v for v in vol[-21:-1] if v])
    today_v = vol[-1] if vol else None
    if not high_40_prior or today_high is None or not c or not base or not today_v:
        return False
    day_ratio = today_v / base
    return today_high > high_40_prior and c <= high_40_prior and day_ratio > 1.8


PATTERNS: List[Tuple[str, str, str, str, Any]] = [
    ("P1", "超额优先复合确认", "吸筹", "buy", _pat_low_consolidation),
    ("P2", "低位影线吸筹", "吸筹", "buy", _pat_low_shadows),
    ("P3", "缩量打压首次收复", "吸筹", "buy", _pat_shakedown_absorb),
    ("P4", "量增价稳观察", "吸筹", "observe", _pat_absorption),
    ("P5", "双底右腿确认", "吸筹", "buy", _pat_bottom_formation),
    ("P6", "试盘长上影", "试盘", "hold", _pat_test_upper_shadow),
    ("P7", "底部异动放量", "试盘", "hold", _pat_bottom_spike),
    ("P8", "缩量回踩洗盘", "洗盘", "buy", _pat_pullback_shakeout),
    ("P9", "边拉边洗", "洗盘", "buy", _pat_climb_wash),
    ("P10", "高换手洗盘", "洗盘", "buy", _pat_high_turnover_wash),
    ("P11", "放量突破启动", "突破", "hold", _pat_breakout),
    ("P12", "连板拉升", "拉升", "hold", _pat_consecutive_limit),
    ("P13", "首板卡位", "拉升", "hold", _pat_first_board),
    ("P14", "高位放量滞涨", "出货", "sell", _pat_high_vol_stall),
    ("P15", "新高量背离放量回撤", "出货", "sell", _pat_vol_price_div),
    ("P16", "阴天量", "出货", "sell", _pat_bearish_max_vol),
    ("P17", "倒V反转", "出货", "sell", _pat_inverted_v),
    ("P18", "顶部大阴包阳", "出货", "sell", _pat_bearish_engulf),
    ("P19", "灌压巨量大阴", "出货", "sell", _pat_dump_bigbear),
    ("P20", "均线放量破位", "出货", "sell", _pat_ma_breakdown),
    ("P21", "深破缩量收回", "洗盘", "buy", _pat_spring_reclaim),     # 双池10~40日正向，核心洗盘买点
    ("P22", "放量假突破", "出货", "sell", _pat_failed_breakout),     # 全历史逐日复测：双池2~40日风控有效
    ("P23", "低位压缩温和量能转强", "吸筹", "buy", _pat_compression),  # 10日正向，核心有效买点
    ("P24", "OBV底背离", "吸筹", "buy", _pat_obv_divergence),        # 强OBV背离，连续5日后一次性确认
    ("P25", "中低位缩量平台转强", "吸筹", "buy", _pat_bottom_base_ignition),
    ("P26", "获利盘风险", "出货", "sell", _pat_chip_winner_risk),
]


PATTERN_EFFECTIVE = {"P1", "P2", "P3", "P5", "P11", "P12", "P13", "P14", "P15", "P16", "P17", "P19", "P20", "P21", "P22", "P23", "P24", "P25", "P26"}


def match_patterns(code: str, bars: List[Dict[str, Any]],
                   ctx: Optional[Dict[str, Any]] = None,
                   pool: Optional[str] = None,
                   p1_market_gate: bool = False,
                   p1_override: Optional[bool] = None,
                   patterns: Optional[Sequence[Tuple[str, str, str, str, Any]]] = None,
                   ) -> List[Dict[str, str]]:
    """对一段日线窗口匹配全部形态，返回命中列表（PIT 安全）。"""
    if len(bars) < MIN_BARS:
        return []
    ctx = ctx or _build_pattern_context(code, bars)
    current_bars = bars[-LOOKBACK:]
    fired: List[Dict[str, str]] = []
    selected_patterns = PATTERNS if patterns is None else patterns
    for pcode, name, phase, signal, fn in selected_patterns:
        # 事件型P1/P4需要让昨日状态也能使用完整180日量度来源窗口；P15
        # 需要按过去15日各事件当日的量源复算；P21额外保留正式回测相同的
        # 185根资格窗口；其余形态只看统一LOOKBACK窗口。
        if pcode == "P1":
            pattern_bars = bars[-(LOOKBACK + P1_CONFIRM_DAYS):]
        elif pcode == "P4":
            pattern_bars = bars[-(LOOKBACK + 1):]
        elif pcode == "P15":
            pattern_bars = bars[-(LOOKBACK + P15_EVENT_LOOKBACK_DAYS):]
        elif pcode == "P21":
            pattern_bars = bars[-P21_MIN_HISTORY_BARS:]
        else:
            pattern_bars = current_bars
        try:
            if pcode == "P1" and fn is _pat_low_consolidation:
                matched = (
                    bool(p1_override)
                    if p1_override is not None
                    else fn(
                        pattern_bars,
                        ctx,
                        p1_market_gate if pool != "etf" else False,
                    )
                )
            else:
                matched = fn(pattern_bars, ctx)
            if matched:
                fired.append({"code": pcode, "name": name, "phase": phase, "signal": signal})
        except Exception:
            continue
    return fired


def _distribution_warning_points(fired: Sequence[Dict[str, str]]) -> int:
    """命中有效出货证据的风险积分；按形态代码去重。"""
    codes = {str(pattern.get("code") or "") for pattern in fired}
    return sum(DISTRIBUTION_WARNING_POINTS.get(code, 0) for code in codes)


def _effective_sell_pattern_count(fired: Sequence[Dict[str, str]]) -> int:
    """命中的出货预警形态数；按代码去重（兼容既有字段名）。"""
    codes = {str(pattern.get("code") or "") for pattern in fired}
    return len(codes & DISTRIBUTION_WARNING_PATTERN_CODES)


def _distribution_warning_triggered(fired: Sequence[Dict[str, str]]) -> bool:
    """统一出货预警规则：预警集合内任一形态命中即触发。"""
    return (
        _effective_sell_pattern_count(fired)
        >= DISTRIBUTION_WARNING_EFFECTIVE_SELL_THRESHOLD
        or _distribution_warning_points(fired)
        >= DISTRIBUTION_WARNING_POINTS_THRESHOLD
    )


def _pattern_phase(fired: List[Dict[str, str]], score: Optional[float] = None) -> str:
    """命中形态汇总成主导阶段（优先级：出货预警达标 > 突破 > 买入区 > 拉升 > 试盘）。

    出货预警只统计 DISTRIBUTION_WARNING_PATTERN_CODES：任一形态命中即触发。
    P11 是 hold 风险，P18 不在预警集合内，不会单独触发。
    突破=放量右进买点，最 actionable，仅次于达标的出货风控示警、优先于被动的吸筹/洗盘；
    买入区按类别细分吸筹 / 洗盘（两类都中则合并标注）；剩余 hold 区拉升中 > 试盘；
    无任何形态命中：吸筹分≥SUSPECT_ACCUM_SCORE → 疑似吸筹(待确认)，否则 → 观望(场外不参与)。
    """
    sigs = {p["signal"] for p in fired}
    cats = {p["phase"] for p in fired}
    if _distribution_warning_triggered(fired):
        return "出货预警🔴"
    if "突破" in cats:
        return "▲突破🟠"
    if "buy" in sigs:
        buy_cats = {p["phase"] for p in fired if p["signal"] == "buy"}
        if {"吸筹", "洗盘"} <= buy_cats:
            return "吸筹+洗盘🟡"
        if "洗盘" in buy_cats:
            return "洗盘🟡"
        return "吸筹🟢"
    if "hold" in sigs:                # 此时仅剩 拉升 / 试盘
        return "拉升中🟠" if "拉升" in cats else "试盘🟡"
    if not fired and score is not None and score >= SUSPECT_ACCUM_SCORE:
        return "疑似吸筹(待确认)🟢"
    return "观望⚪"


def _clip_score(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _phase_pattern_strength(fired: List[Dict[str, str]], signals: set) -> float:
    """当前阶段相关形态强度：有效形态权重大，其余形态仍给有限确认。"""
    support = 0.0
    for p in fired:
        if p.get("signal") not in signals:
            continue
        support += 1.25 if p.get("code") in PATTERN_EFFECTIVE else 0.85
    if support <= 0.0:
        return 0.0
    return _clip_score(35.0 + 18.0 * support)


def _phase_invalidations(phase: str) -> List[str]:
    """每个阶段的证伪/止损条件（沿用早期形态研究的 invalidations，让标签可执行）。"""
    if "出货" in phase:
        return ["缩量回踩后重新放量突破前高才解除风险", "跌破高位平台下沿=派发确认"]
    if "突破" in phase:
        return ["次日低开低走或突破位站不稳3日", "突破后回到平台内=假突破"]
    if "拉升" in phase:
        return ["跌破5/10日线且放量", "高位放量滞涨或长上影=见顶"]
    if "试盘" in phase:
        return ["试盘后无量回落、不能再创异动高点", "跌破试盘当日低点"]
    if "吸筹" in phase or "洗盘" in phase:                    # 含疑似吸筹/吸筹+洗盘
        return ["跌破筑底箱体下沿且3-5日不收回", "放量下跌且筹码峰发散", "所属题材退潮"]
    return ["无形态信号：仅观察，待左侧吸筹形态出现再评估"]   # 观望

