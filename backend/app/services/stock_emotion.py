"""个股情绪分 — 只用近期价格走势给单只股票打「恐惧↔亢奋」连续分。

口径（与离线试算稿一致, 用户已认可「看着还行」）:
  - 5 个指标各归一化到 0..100, 等权平均 => 总分 0..100;
  - 0 = 极度恐惧（大跌）, 100 = 极度亢奋（大涨）, 50 = 中性;
  - 指标: RSI(14) 原生 0..100; ROC(10) ±20% 映射; Stochastic %K(14,3) 原生 0..100;
          BIAS20 ±15% 映射; Bollinger %B(20) ×100;
  - 全部因果滚动计算, 只用当日及以前的数据, 无未来数据、不逐日重放;
  - 前 20 个交易日预热留空（指标尚不可靠, 不伪造中性）。

依赖与复用（零新依赖）:
  - RSI 复用 ``app.backtest.swing_rules.wilder_rsi``（Wilder 平滑, 与离线试算的 ta 库一致）;
  - 归一化 ``score_of`` 与中性常量 ``NEUTRAL`` 复用 ``app.services.emotion_score`` 的纯函数,
    但不使用其大盘维度与档位, 本模块自定个股维度集合与恐惧↔亢奋档位;
  - 其余 4 个指标（ROC / Stochastic %K / BIAS / Bollinger %B）为业界标准公式, 用
    numpy/pandas 几行复刻, 与离线试算稿（ta 库）口径一致。
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from app.backtest.swing_rules import wilder_rsi
from app.services.emotion_score import NEUTRAL, score_of

WARMUP = 20
LOOKBACKS: tuple[int, ...] = (60, 120, 250, 500)
DEFAULT_LOOKBACK = 250

# 档位: (阈值键, 展示名) — 从高到低匹配（0=恐惧, 100=亢奋）
STOCK_LABELS: tuple[tuple[str, str], ...] = (
    ("euphoria", "极度亢奋"),
    ("excited", "亢奋"),
    ("neutral", "中性"),
    ("fear", "恐惧"),
)
STOCK_FALLBACK_LABEL = "极度恐惧"

# 5 个指标子项出厂口径: key -> (label, low, high, unit)
# RSI / Stochastic 已是 0..100, 故 low=0 high=100; 其余按试算映射区间。
STOCK_TERMS: dict[str, tuple[str, float, float, str]] = {
    "rsi14": ("RSI(14)", 0.0, 100.0, ""),
    "roc10": ("ROC(10)", -20.0, 20.0, "%"),
    "stoch_k14": ("Stoch%K(14,3)", 0.0, 100.0, ""),
    "bias20": ("BIAS20", -15.0, 15.0, "%"),
    "boll_b20": ("Boll%B(20)", 0.0, 100.0, ""),
}
STOCK_DIMENSION_ORDER: tuple[str, ...] = tuple(STOCK_TERMS)

DEFAULT_THRESHOLDS: dict[str, float] = {
    "euphoria": 80.0,
    "excited": 60.0,
    "neutral": 40.0,
    "fear": 20.0,
}

STOCK_NOTES: tuple[str, ...] = (
    "情绪分基于个股近期价格走势计算（大跌→恐惧、大涨→亢奋），是对「当前冷热」的标注，不是涨跌预测。",
    "5 个指标等权合成：RSI(14) / ROC(10)±20% / Stochastic%K(14,3) / BIAS20±15% / Bollinger%B(20)；各归一化到 0..100 后平均。",
    "全部因果滚动计算，只用当日及以前的数据，无未来数据、不逐日重放。",
    "前 20 个交易日为指标预热期，分数整体留空（不伪造中性），与离线试算稿一致。",
    "阈值与映射区间可在请求中通过 config 覆盖；本页分数仅供研判，不可直接用于实盘。",
)


def stock_label_of(score: float, thresholds: dict) -> str:
    for key, name in STOCK_LABELS:
        if score >= thresholds.get(key, 0):
            return name
    return STOCK_FALLBACK_LABEL


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _num(value: Any, default: float) -> float:
    v = _finite(value)
    return default if v is None else v


def _indicators(
    close: np.ndarray, high: np.ndarray, low: np.ndarray
) -> dict[str, np.ndarray]:
    """5 个指标（numpy/pandas 标准公式），与离线试算稿（ta 库）口径一致。"""
    n = len(close)
    c = pd.Series(close)
    h = pd.Series(high)
    l = pd.Series(low)
    out: dict[str, np.ndarray] = {}

    # RSI(14): Wilder 平滑（匹配 ta.momentum.RSIIndicator 默认）
    rsi = wilder_rsi(close, 14)
    out["rsi14"] = np.where(np.isfinite(rsi), rsi, np.nan).ravel()

    # ROC(10) = (close[t] / close[t-10] - 1) * 100
    roc = np.full(n, np.nan)
    if n > 10:
        roc[10:] = (close[10:] / close[:-10] - 1.0) * 100.0
    out["roc10"] = roc

    # Stochastic %K(14,3): raw = (C - LL14)/(HH14 - LL14)*100, 再 3 日 SMA 平滑
    ll14 = l.rolling(14, min_periods=14).min()
    hh14 = h.rolling(14, min_periods=14).max()
    raw_k = (c - ll14) / (hh14 - ll14) * 100.0
    out["stoch_k14"] = raw_k.rolling(3, min_periods=1).mean().to_numpy()

    # BIAS20 = close / SMA20 - 1（存为百分比，±15% 映射 0..100）
    sma20 = c.rolling(20, min_periods=20).mean().to_numpy()
    bias = np.full(n, np.nan)
    nz = sma20 > 0
    bias[nz] = (close[nz] / sma20[nz] - 1.0) * 100.0
    out["bias20"] = bias

    # Bollinger %B(20, 2): (C - lower)/(upper - lower), lower=mid-2σ, upper=mid+2σ
    mid = c.rolling(20, min_periods=20).mean().to_numpy()
    std = c.rolling(20, min_periods=20).std(ddof=0).to_numpy()
    boll_b = np.full(n, np.nan)
    ok = (std > 0) & np.isfinite(mid)
    boll_b[ok] = (close[ok] - (mid[ok] - 2.0 * std[ok])) / (4.0 * std[ok])
    out["boll_b20"] = boll_b * 100.0
    return out


def _merge_config(raw: dict | None) -> tuple[dict[str, list[Any]], dict[str, float]]:
    """用户配置 → (terms 覆盖表, 档位阈值覆盖表)。仅覆盖映射区间与阈值, 维度集合不变。"""
    terms = {k: list(v) for k, v in STOCK_TERMS.items()}
    thresholds = dict(DEFAULT_THRESHOLDS)
    if not isinstance(raw, dict):
        return terms, thresholds
    for k, t in terms.items():
        user = raw.get(k)
        if isinstance(user, dict):
            low = _finite(user.get("low"))
            high = _finite(user.get("high"))
            if low is not None:
                t[1] = low
            if high is not None:
                t[2] = high
    user_th = raw.get("thresholds")
    if isinstance(user_th, dict):
        for key in thresholds:
            v = _finite(user_th.get(key))
            if v is not None:
                thresholds[key] = v
    return terms, thresholds


def compute_stock(
    rows: Sequence[Mapping[str, Any]],
    symbol: str,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    config: dict | None = None,
) -> dict[str, Any]:
    """纯函数：从 enriched 日线（已按 symbol 过滤并排序）算个股情绪分序列。

    返回逐日 K 线、5 个指标序列、分项分、总分序列、档位、阈值、口径说明与当前快照。
    与离线试算稿输出口径一致（5 指标等权; 前 WARMUP 天留空）。
    """
    if lookback not in LOOKBACKS:
        raise ValueError(f"lookback 只支持 {LOOKBACKS}")

    window = list(rows[-lookback:]) if len(rows) > lookback else list(rows)
    dates: list[str] = []
    bars: list[dict[str, Any]] = []
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for row in window:
        raw = row.get("close")
        try:
            close = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            close = None
        # 与 se._series / build_structure 同口径：缺失 / NaN / 非正收盘整行丢弃
        if close is None or close != close or close <= 0:
            continue
        date = str(row.get("date"))[:10]
        dates.append(date)
        closes.append(close)
        highs.append(_num(row.get("high"), close))
        lows.append(_num(row.get("low"), close))
        bars.append(
            {
                "date": date,
                "open": _num(row.get("open"), close),
                "high": _num(row.get("high"), close),
                "low": _num(row.get("low"), close),
                "close": close,
                "volume": _num(row.get("volume"), 0.0),
            }
        )
    n = len(dates)
    if n == 0:
        raise ValueError("该标的无可用的收盘数据")

    close_a = np.asarray(closes, dtype=float)
    high_a = np.asarray(highs, dtype=float)
    low_a = np.asarray(lows, dtype=float)
    ind = _indicators(close_a, high_a, low_a)

    terms, thresholds = _merge_config(config)

    def to_json_list(arr: np.ndarray) -> list[float | None]:
        return [None if (isinstance(x, float) and math.isnan(x)) else float(x) for x in arr]

    dims_seq: dict[str, list[float | None]] = {k: [] for k in STOCK_DIMENSION_ORDER}
    scores: list[int | None] = []
    labels: list[str] = []
    for i in range(n):
        if i < WARMUP:  # 预热期整体留空, 与离线试算稿一致
            for k in STOCK_DIMENSION_ORDER:
                dims_seq[k].append(None)
            scores.append(None)
            labels.append(stock_label_of(NEUTRAL, thresholds))
            continue
        acc = 0.0
        cnt = 0
        for k in STOCK_DIMENSION_ORDER:
            v = ind[k][i]
            if isinstance(v, float) and math.isnan(v):
                dims_seq[k].append(None)
                continue
            s = score_of(v, terms[k][1], terms[k][2])
            dims_seq[k].append(s)
            acc += s
            cnt += 1
        if cnt == 0:
            total: int | None = None
            labels.append(stock_label_of(NEUTRAL, thresholds))
        else:
            total = round(acc / cnt)
            labels.append(stock_label_of(total, thresholds))
        scores.append(total)

    last = n - 1
    last_score = scores[last]
    return {
        "symbol": symbol,
        "lookback": lookback,
        "from_date": dates[0],
        "to_date": dates[-1],
        "n_obs": n,
        "dates": dates,
        "bars": bars,
        "metrics": {k: to_json_list(v) for k, v in ind.items()},
        "dimensions": dims_seq,
        "scores": scores,
        "labels": labels,
        "thresholds": thresholds,
        "terms": {
            k: {"label": t[0], "low": t[1], "high": t[2], "unit": t[3]}
            for k, t in terms.items()
        },
        "dimension_order": list(STOCK_DIMENSION_ORDER),
        "current": {
            "date": dates[last],
            "score": last_score,
            "label": stock_label_of(last_score if last_score is not None else NEUTRAL, thresholds),
        },
        "notes": list(STOCK_NOTES),
    }
