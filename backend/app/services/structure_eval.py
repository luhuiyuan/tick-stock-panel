"""客观价格结构评测引擎（方向 A）。

目的：先用**未来数据**算出「真实价格结构」作为标准答案（ground truth），
再用它检验**只用当日及以前数据**的实时判断算法是否正确。没有基准就谈不上
准确率 —— 上一轮诊断发现 `/phase-lab` 把选股信号命中日包装成「主力阶段」，
在 -53% 的单边下跌里仍标 47 天「试盘/洗盘」，正是因为缺这层客观基准。

口径（见 docs 与 SOURCE.md）：
- 标准答案 = 对展示区间**全量** ``log(close)`` 跑离线 ``Pelt(model="l2")``
  得到的全局最优分割；每段按首尾涨跌分 UP / DOWN / SIDEWAYS（默认 ±5%）。
  这是「给定算法 + 参数下的最优分段」，不是绝对真理；pen 越大分段越粗。
  **明确用到了整段未来数据，仅作基准，不可用于实盘。**
- 被检验的实时算法全部 PIT（逐日只用当日及以前数据），输出变点位置 +
  首次报告日，与标准答案对齐后用 TCPDBench 指标打分。
- 主口径是**变点**（F1@容差 / covering / 确认延迟）；段方向只用于着色与
  展示，**不做主观「吸筹/洗盘/出货」阶段判定**。

本模块是纯函数：不依赖 FastAPI、不读数据库、不改全局状态；``rows`` 由 API
层负责从 enriched 日线取出并与 ``build_timeline`` 的展示区间逐日对齐。
"""
from __future__ import annotations

import statistics
from typing import Any, Mapping, Sequence

import numpy as np
import polars as pl

from app.indicators.vendor.price_structure import Pelt, tcpd_metrics

# ---- 默认口径与可选项 ----------------------------------------------------
DEFAULT_PEN = 0.05
PEN_OPTIONS: tuple[float, ...] = (0.02, 0.05, 0.1, 0.3)
DEFAULT_MARGIN = 5
DEFAULT_DIRECTION_THRESHOLD = 0.05
MIN_SIZE = 15
JUMP = 1
SMC_SWING_LENGTH = 3

ALGORITHM_LABELS: dict[str, tuple[str, str]] = {
    "smc_confirmed": (
        "确认摆动点 (SMC)",
        "仓库已有 compute_smc_structure：摆动点需右侧 window 根确认，"
        "以其 confirmed_date 作为首次可见日，天然带确认延迟。",
    ),
    "pit_pelt": (
        "逐日 PIT 最优分割",
        "每天只把 bars[:i+1] 喂同一个 Pelt 分割器，收集当天能确认的变点；"
        "与标准答案唯一差别是看不到未来，用于量出「信息损失」有多大。",
    ),
    "radar_hits": (
        "现有 26 条形态信号",
        "对照项：现有 hot_money_radar 命中日按连续段取起点当变点，"
        "用于展示「语义选股信号 ≠ 价格结构变点」。",
    ),
}

NOTES: tuple[str, ...] = (
    "标准答案 = 对展示区间全量 log(close) 跑离线 Pelt(model=l2, min_size=15, jump=1) "
    "的全局最优分割；它明确用到了整段未来数据，仅作检验基准，不可用于实盘。",
    "标准答案是「给定算法 + pen 参数下的最优分段」，不是绝对真理；pen 越大分段越粗，"
    "切换 pen 后所有分数都会跟着变，只在同一基准下有可比性。",
    "评分以变点为主口径：F1@容差（该找的转折点找到几个）、covering（分段覆盖得多准）、"
    "确认延迟（真实变点被实时算法首次报出时晚了几天）。",
    "三个被检验算法均为 PIT：第 i 天只使用当日及以前的数据；"
    "段方向（UP/DOWN/SIDEWAYS）仅用于着色展示，不做主观「吸筹/洗盘/出货」阶段判定。",
    "确认延迟衡量「真实变点被实时算法首次报出时晚了几天」；radar_hits 是把当天信号日"
    "直接当作变点，其 delay 恒为 0 属于口径使然，不代表识别及时，应结合 F1/covering 一起看。",
    "dp_wave.py（Douglas-Peucker）是同类离线分割，可作交叉验证，不进入本页评分。",
)


# ---- 基础工具 ------------------------------------------------------------
def _date_text(value: Any) -> str:
    return str(value)[:10]


def _series(rows: Sequence[Mapping[str, Any]]) -> tuple[list[str], np.ndarray]:
    """提取展示区间的日期与收盘价；丢弃缺失/非正收盘的行（保持两者对齐）。"""
    dates: list[str] = []
    closes: list[float] = []
    for row in rows:
        raw = row.get("close")
        try:
            value = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            value = None
        if value is None or value != value or value <= 0:  # NaN / 非正
            continue
        dates.append(_date_text(row.get("date")))
        closes.append(value)
    return dates, np.asarray(closes, dtype=float)


def _log_signal(closes: np.ndarray) -> np.ndarray:
    return np.log(closes).reshape(-1, 1)


def _segment_direction(change: float, threshold: float) -> str:
    if change > threshold:
        return "UP"
    if change < -threshold:
        return "DOWN"
    return "SIDEWAYS"


def _cluster(
    raw: Sequence[dict[str, int]], margin: int
) -> list[dict[str, int]]:
    """把逐日重放产生的漂移变点按 ±margin 聚成唯一变点。

    每个簇取最小的 bkp 作代表位置、最小的 reported 作首次报告日，
    delay = reported - bkp。返回按 bkp 升序。
    """
    if not raw:
        return []
    ordered = sorted(raw, key=lambda item: (item["bkp"], item["reported"]))
    clusters: list[list[dict[str, int]]] = [[ordered[0]]]
    for item in ordered[1:]:
        if item["bkp"] - clusters[-1][-1]["bkp"] <= margin:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    out: list[dict[str, int]] = []
    for group in clusters:
        bkp = min(item["bkp"] for item in group)
        reported = min(item["reported"] for item in group)
        out.append({"bkp": bkp, "reported": reported, "delay": reported - bkp})
    return out


# ---- 标准答案（用未来数据）-----------------------------------------------
def ground_truth(
    dates: Sequence[str],
    closes: np.ndarray,
    *,
    pen: float = DEFAULT_PEN,
    direction_threshold: float = DEFAULT_DIRECTION_THRESHOLD,
) -> dict[str, Any]:
    """离线全局最优分制：变点集合 + 逐段方向。仅用作基准。"""
    n = len(closes)
    if n < MIN_SIZE * 2:
        raise ValueError(f"展示区间不足 {MIN_SIZE * 2} 根，无法做离线分割")
    all_bkps = Pelt(model="l2", min_size=MIN_SIZE, jump=JUMP).fit(
        _log_signal(closes)
    ).predict(pen=pen)
    # ruptures 约定返回值含末端 n_samples；业务层剔除
    bkps = [int(b) for b in all_bkps if 0 < int(b) < n]
    bounds = [0, *bkps, n]
    segments: list[dict[str, Any]] = []
    for a, b in zip(bounds, bounds[1:]):
        change = float(closes[b - 1] / closes[a] - 1.0)
        segments.append(
            {
                "start": a,
                "end": b - 1,
                "from": dates[a],
                "to": dates[b - 1],
                "direction": _segment_direction(change, direction_threshold),
                "change_pct": round(change * 100.0, 4),
            }
        )
    return {
        "bkps": bkps,
        "segments": segments,
        "segment_count": len(segments),
        "engine": "ruptures_lite.Pelt(model=l2, min_size=15, jump=1)",
    }


# ---- 三个被检验的实时算法（全部 PIT）-------------------------------------
def smc_confirmed(
    rows: Sequence[Mapping[str, Any]], dates: Sequence[str], margin: int
) -> tuple[list[dict[str, int]], str | None]:
    """仓库已有的确认摆动点：confirmed_date 即首次可见日，天然带确认延迟。"""
    index = {d: i for i, d in enumerate(dates)}
    try:
        from app.indicators.smc_adapter import compute_smc_structure

        frame = pl.DataFrame(
            {
                "date": [_date_text(r.get("date")) for r in rows],
                "open": [float(r.get("open") or 0.0) for r in rows],
                "high": [float(r.get("high") or 0.0) for r in rows],
                "low": [float(r.get("low") or 0.0) for r in rows],
                "close": [float(r.get("close") or 0.0) for r in rows],
                "volume": [float(r.get("volume") or 0.0) for r in rows],
            }
        )
        swings = compute_smc_structure(frame, swing_length=SMC_SWING_LENGTH)["swing_points"]
    except Exception as exc:  # noqa: BLE001 — 上游结构算法不可用时如实降级
        return [], f"SMC 摆动点不可用，已跳过：{type(exc).__name__}: {exc}"

    raw: list[dict[str, int]] = []
    for point in swings:
        j = index.get(_date_text(point.get("date")))
        r = index.get(_date_text(point.get("confirmed_date")))
        if j is None or r is None or r < j:
            continue
        raw.append({"bkp": j, "reported": r, "delay": r - j})
    return _cluster(raw, margin), None


def pit_pelt_raw(closes: np.ndarray, *, pen: float) -> dict[int, int]:
    """逐日 PIT 重放的原始结果：``{变点位置: 首次被报告的日索引}``。

    第 i 天的分割只用 ``closes[:i+1]``，因此 ``reported >= bkp`` 恒成立，
    且修改 ``closes[reported+1:]`` 不影响任何 ``reported`` 更早的条目 —— 这是
    「无未来数据泄漏」的可测定义。测试直接断言这一性质。
    """
    n = len(closes)
    first_seen: dict[int, int] = {}
    logp = _log_signal(closes)
    for i in range(MIN_SIZE, n):
        bkps = Pelt(model="l2", min_size=MIN_SIZE, jump=JUMP).fit(
            logp[: i + 1]
        ).predict(pen=pen)
        for raw_bkp in bkps:
            j = int(raw_bkp)
            if j >= i + 1 or j <= 0:  # 剔除末端与 0
                continue
            if j not in first_seen or i < first_seen[j]:
                first_seen[j] = i
    return first_seen


def pit_pelt(
    closes: np.ndarray, *, pen: float, margin: int
) -> list[dict[str, int]]:
    """逐日把 bars[:i+1] 喂同一个 Pelt，收集当天能确认的变点。

    与标准答案唯一差别是看不到未来：第 i 天的分割只用 closes[:i+1]。
    """
    return _cluster(
        [
            {"bkp": j, "reported": r, "delay": r - j}
            for j, r in pit_pelt_raw(closes, pen=pen).items()
        ],
        margin,
    )


def radar_hits(
    hit_dates: Sequence[str] | None, dates: Sequence[str], margin: int
) -> tuple[list[dict[str, int]], str | None]:
    """现有 26 条规则命中日按连续段取起点，作为对照变点。"""
    if not hit_dates:
        return [], "未提供形态命中日，radar_hits 未参与评分"
    index = {d: i for i, d in enumerate(dates)}
    hits = sorted({index[d] for d in hit_dates if d in index})
    hit_set = set(hits)
    # 0 是序列起点、不是变点，排除；只保留每个连续命中段的首日
    starts = [i for i in hits if i > 0 and (i - 1) not in hit_set]
    return _cluster(
        [{"bkp": i, "reported": i, "delay": 0} for i in starts], margin
    ), None


# ---- 评分 ----------------------------------------------------------------
def score_algorithm(
    gt_bkps: Sequence[int],
    confirmations: Sequence[dict[str, int]],
    n: int,
    margin: int,
) -> dict[str, Any]:
    """把某算法的输出对标准答案打分。"""
    pred = [c["bkp"] for c in confirmations]
    annotations = {1: list(gt_bkps)}
    f1, precision, recall = tcpd_metrics.f_measure(
        annotations, pred, margin=margin, return_PR=True
    )
    covering = tcpd_metrics.covering(annotations, pred, n)

    delays: list[int] = []
    for t in gt_bkps:
        candidates = [c["delay"] for c in confirmations if abs(c["bkp"] - t) <= margin]
        if candidates:
            delays.append(min(candidates))
    detected = len(delays)
    total = len(gt_bkps)
    return {
        "f1": round(float(f1), 6),
        "precision": round(float(precision), 6),
        "recall": round(float(recall), 6),
        "covering": round(float(covering), 6),
        "delay_mean": round(float(statistics.mean(delays)), 4) if delays else None,
        "delay_median": round(float(statistics.median(delays)), 4) if delays else None,
        "detected": detected,
        "gt_count": total,
        "detection_rate": round(detected / total, 6) if total else None,
        "pred_count": len(pred),
    }


# ---- 组装 ----------------------------------------------------------------
def evaluate(
    rows: Sequence[Mapping[str, Any]],
    symbol: str,
    *,
    pen: float = DEFAULT_PEN,
    margin: int = DEFAULT_MARGIN,
    direction_threshold: float = DEFAULT_DIRECTION_THRESHOLD,
    radar_hit_dates: Sequence[str] | None = None,
) -> dict[str, Any]:
    """纯函数：组装标准答案 + 三个 PIT 实时算法 + 三项打分。

    ``rows`` 必须是**展示区间**的日线（与 ``build_timeline`` 的 timeline 逐日
    对齐），不含暖机段；标准答案用整段，实时算法逐日只用前缀。
    """
    if pen not in PEN_OPTIONS:
        raise ValueError(f"pen 只支持 {PEN_OPTIONS}")
    if margin < 0:
        raise ValueError("margin 不能为负")
    dates, closes = _series(rows)
    n = len(closes)
    gt = ground_truth(dates, closes, pen=pen, direction_threshold=direction_threshold)

    smc, smc_note = smc_confirmed(rows, dates, margin)
    rad, rad_note = radar_hits(radar_hit_dates, dates, margin)
    algorithms = [
        {"name": "smc_confirmed", "confirmations": smc},
        {"name": "pit_pelt", "confirmations": pit_pelt(closes, pen=pen, margin=margin)},
        {"name": "radar_hits", "confirmations": rad},
    ]
    scores: list[dict[str, Any]] = []
    for algo in algorithms:
        name = algo["name"]
        label, description = ALGORITHM_LABELS[name]
        scores.append(
            {
                "name": name,
                "label": label,
                "description": description,
                **score_algorithm(gt["bkps"], algo["confirmations"], n, margin),
            }
        )

    notes = list(NOTES)
    for note in (smc_note, rad_note):
        if note:
            notes.append(note)

    return {
        "symbol": symbol,
        "pen": pen,
        "margin": margin,
        "direction_threshold": direction_threshold,
        "pen_options": list(PEN_OPTIONS),
        "from_date": dates[0] if dates else None,
        "to_date": dates[-1] if dates else None,
        "n_obs": n,
        "dates": dates,
        "ground_truth": gt,
        "algorithms": [
            {
                "name": a["name"],
                "label": ALGORITHM_LABELS[a["name"]][0],
                "bkps": [c["bkp"] for c in a["confirmations"]],
                "confirmations": a["confirmations"],
            }
            for a in algorithms
        ],
        "scores": scores,
        "notes": notes,
    }
