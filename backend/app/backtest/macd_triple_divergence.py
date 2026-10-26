"""MACD triple top/bottom divergence for daily stock screening.

Ported from Allanli1011/cn_stock_scan ``src/indicators/macd.py``
(https://github.com/Allanli1011/cn_stock_scan). That repository has no LICENSE
file, so only the detection rules are included. Price download, charts, the
PA three-push score and the ICT higher-timeframe PDA score are not.

Rules, evaluated on the last three MACD waves as of each bar (no future bar):

- R1 price makes three higher highs (top) or lower lows (bottom)
- R2 DIF at the three golden crosses falls (top) or at the three death
  crosses rises toward zero (bottom)
- R3 the two pullbacks approach zero without crossing it
- R4 absolute histogram area shrinks by at least ``min_area_reduction``
- R5 the third extreme is within ``recency_bars``

Histogram uses the domestic convention ``(DIF - DEA) * 2``. A bar is a strict
hit at 5/5 and a loose hit at 4/5. This is a daily screen, not an intraday
T+0 round trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# 选股预热必须能被进程内 enriched 缓存盖住。该缓存只读约 300 个自然日，
# 本地日线分区上任意完整窗口最少约 195 个交易日。选股请求的是
# warmup + 1 个交易日，因此 150 仍留有余量，不会落到全市场重算指标。
SCREEN_WARMUP_BARS = 150

MACD_TRIPLE_DEFAULTS = {
    "fast": 12,
    "slow": 26,
    "signal": 9,
    "min_area_reduction": 0.10,
    "dif_zero_tolerance": 0.0,
    "dif_approach_zero_ratio": 0.50,
    "min_price_increase_pct": 0.001,
    "recency_bars": 30,
    "allow_loose": False,
}


@dataclass
class Wave:
    direction: str
    start_cross_idx: int
    end_cross_idx: int
    cross_value: float
    extreme_idx: int
    extreme_price: float
    hist_area: float


@dataclass(frozen=True)
class RuleCheck:
    code: str
    name: str
    passed: bool
    detail: str = ""


@dataclass
class DivergenceResult:
    hit: bool
    hit_kind: str
    direction: str
    waves: list[Wave] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    rule_checks: list[RuleCheck] = field(default_factory=list)
    strength: float = 0.0

    @property
    def n_passed(self) -> int:
        return sum(1 for item in self.rule_checks if item.passed)


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def resolve_params(params: dict | None) -> dict:
    """Fill omitted thresholds from the upstream config.yaml defaults."""
    raw = params or {}
    resolved = dict(MACD_TRIPLE_DEFAULTS)
    for key, default in MACD_TRIPLE_DEFAULTS.items():
        if key not in raw or raw[key] is None:
            continue
        if key == "allow_loose":
            resolved[key] = _as_bool(raw[key], bool(default))
        elif isinstance(default, int) and not isinstance(default, bool):
            resolved[key] = int(raw[key])
        else:
            resolved[key] = float(raw[key])
    fast = max(2, int(resolved["fast"]))
    slow = max(fast + 1, int(resolved["slow"]))
    resolved["fast"] = fast
    resolved["slow"] = slow
    resolved["signal"] = max(1, int(resolved["signal"]))
    resolved["recency_bars"] = max(1, int(resolved["recency_bars"]))
    return resolved


def compute_macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """Standard MACD. ``hist = (dif - dea) * 2``, same as the source scanner."""
    values = np.asarray(close, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("compute_macd expects one close series")

    def ema(series: np.ndarray, span: int) -> np.ndarray:
        alpha = 2.0 / (span + 1.0)
        keep = 1.0 - alpha
        out = np.empty(series.shape, dtype=np.float64)
        if series.size == 0:
            return out
        out[0] = series[0]
        for i in range(1, series.size):
            out[i] = alpha * series[i] + keep * out[i - 1]
        return out

    dif = ema(values, fast) - ema(values, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2.0
    return dif, dea, hist


def find_crossovers(dif: np.ndarray, dea: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return golden-cross and death-cross indexes. A touch of zero is not a cross."""
    delta = np.asarray(dif, dtype=np.float64) - np.asarray(dea, dtype=np.float64)
    prev = np.empty_like(delta)
    prev[0] = np.nan
    if delta.size > 1:
        prev[1:] = delta[:-1]
    up = np.flatnonzero((prev <= 0.0) & (delta > 0.0))
    down = np.flatnonzero((prev >= 0.0) & (delta < 0.0))
    return up.astype(np.int64, copy=False), down.astype(np.int64, copy=False)


def _new_wave(direction: str, start: int, dif_value: float, price: float, hist_value: float) -> Wave:
    return Wave(
        direction=direction,
        start_cross_idx=start,
        end_cross_idx=start,
        cross_value=float(dif_value),
        extreme_idx=start,
        extreme_price=float(price),
        hist_area=abs(float(hist_value)),
    )


def _accumulate(wave: Wave, index: int, price: float, hist_value: float, *, find_max: bool) -> None:
    wave.end_cross_idx = index
    wave.hist_area += abs(float(hist_value))
    if find_max:
        if price > wave.extreme_price:
            wave.extreme_price = float(price)
            wave.extreme_idx = index
    elif price < wave.extreme_price:
        wave.extreme_price = float(price)
        wave.extreme_idx = index


def build_waves(
    price: np.ndarray,
    hist: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    *,
    last_bar: int,
    direction: str,
) -> list[Wave]:
    """Source wave builder: every start runs to the next opposite cross, or to last_bar."""
    find_max = direction == "up"
    waves: list[Wave] = []
    if starts.size == 0 or last_bar < 0:
        return waves
    for start in starts:
        start_idx = int(start)
        if start_idx > last_bar:
            break
        pos = int(np.searchsorted(ends, start_idx, side="right")) if ends.size else 0
        if pos < ends.size and int(ends[pos]) <= last_bar:
            end_idx = int(ends[pos])
        else:
            end_idx = last_bar
        wave = _new_wave(direction, start_idx, np.nan, float(price[start_idx]), float(hist[start_idx]))
        # cross value is filled by the caller when DIF is known; placeholder replaced below
        for index in range(start_idx + 1, end_idx + 1):
            _accumulate(wave, index, float(price[index]), float(hist[index]), find_max=find_max)
        waves.append(wave)
    return waves


def _rule_flags(
    w1: Wave,
    w2: Wave,
    w3: Wave,
    dif: np.ndarray,
    *,
    direction: str,
    bars_since_last_peak: int,
    min_area_reduction: float,
    dif_zero_tolerance: float,
    dif_approach_zero_ratio: float,
    min_price_increase_pct: float,
    recency_bars: int,
) -> tuple[bool, bool, bool, bool, bool, float]:
    p1, p2, p3 = w1.extreme_price, w2.extreme_price, w3.extreme_price
    c1, c2, c3 = w1.cross_value, w2.cross_value, w3.cross_value
    a1, a2, a3 = w1.hist_area, w2.hist_area, w3.hist_area

    if direction == "top":
        inc_12 = (p2 - p1) / p1 if p1 > 0 else 0.0
        inc_23 = (p3 - p2) / p2 if p2 > 0 else 0.0
        r1 = inc_12 >= min_price_increase_pct and inc_23 >= min_price_increase_pct
        r2 = c1 > c2 > c3
    else:
        dec_12 = (p1 - p2) / p1 if p1 > 0 else 0.0
        dec_23 = (p2 - p3) / p2 if p2 > 0 else 0.0
        r1 = dec_12 >= min_price_increase_pct and dec_23 >= min_price_increase_pct
        r2 = c1 < c2 < c3

    r3 = True
    for left, right in ((w1, w2), (w2, w3)):
        segment = dif[left.end_cross_idx:right.start_cross_idx + 1]
        if segment.size == 0:
            continue
        if direction == "top":
            seg_min = float(segment.min())
            if seg_min < -dif_zero_tolerance:
                r3 = False
                break
            if left.cross_value > 0.0 and seg_min > left.cross_value * dif_approach_zero_ratio:
                r3 = False
                break
        else:
            seg_max = float(segment.max())
            if seg_max > dif_zero_tolerance:
                r3 = False
                break
            if left.cross_value < 0.0 and seg_max < left.cross_value * dif_approach_zero_ratio:
                r3 = False
                break

    red_12 = (a1 - a2) / a1 if a1 > 0 else 0.0
    red_23 = (a2 - a3) / a2 if a2 > 0 else 0.0
    r4 = (a1 > a2 > a3) and red_12 >= min_area_reduction and red_23 >= min_area_reduction
    r5 = bars_since_last_peak <= recency_bars

    if direction == "top":
        price_score = min(max((p3 - p1) / max(p1, 1e-6), 0.0), 0.5) / 0.5
        cross_score = min(max((c1 - c3) / max(c1, 1e-6), 0.0), 0.9) / 0.9
    else:
        price_score = min(max((p1 - p3) / max(p1, 1e-6), 0.0), 0.5) / 0.5
        cross_score = min(max((c3 - c1) / max(abs(c1), 1e-6), 0.0), 0.9) / 0.9
    area_score = min(max((a1 - a3) / max(a1, 1e-6), 0.0), 0.9) / 0.9
    strength = float(np.clip((price_score + cross_score + area_score) / 3.0, 0.0, 1.0))
    return r1, r2, r3, r4, r5, strength


def check_divergence_rules(
    w1: Wave,
    w2: Wave,
    w3: Wave,
    dif: np.ndarray,
    *,
    direction: str,
    bars_since_last_peak: int,
    min_area_reduction: float,
    dif_zero_tolerance: float,
    dif_approach_zero_ratio: float,
    min_price_increase_pct: float,
    recency_bars: int,
) -> tuple[list[RuleCheck], float]:
    """Five-rule check. Detail text follows the source scanner's wording."""
    flags = _rule_flags(
        w1, w2, w3, dif,
        direction=direction,
        bars_since_last_peak=bars_since_last_peak,
        min_area_reduction=min_area_reduction,
        dif_zero_tolerance=dif_zero_tolerance,
        dif_approach_zero_ratio=dif_approach_zero_ratio,
        min_price_increase_pct=min_price_increase_pct,
        recency_bars=recency_bars,
    )
    r1, r2, r3, r4, r5, strength = flags
    p1, p2, p3 = w1.extreme_price, w2.extreme_price, w3.extreme_price
    c1, c2, c3 = w1.cross_value, w2.cross_value, w3.cross_value
    a1, a2, a3 = w1.hist_area, w2.hist_area, w3.hist_area
    checks: list[RuleCheck] = []

    if direction == "top":
        inc_12 = (p2 - p1) / p1 if p1 > 0 else 0.0
        inc_23 = (p3 - p2) / p2 if p2 > 0 else 0.0
        r1_detail = (
            f"{p1:.2f}→{p2:.2f}→{p3:.2f} (+{inc_12 * 100:.2f}%/+{inc_23 * 100:.2f}%)"
            if r1 else
            f"价格未创新高 {p1:.2f}→{p2:.2f}→{p3:.2f} (+{inc_12 * 100:.2f}%/+{inc_23 * 100:.2f}%)"
        )
        r1_name = "价格三推创新高"
        r2_name = "DIF 金叉值递减"
    else:
        dec_12 = (p1 - p2) / p1 if p1 > 0 else 0.0
        dec_23 = (p2 - p3) / p2 if p2 > 0 else 0.0
        r1_detail = (
            f"{p1:.2f}→{p2:.2f}→{p3:.2f} (-{dec_12 * 100:.2f}%/-{dec_23 * 100:.2f}%)"
            if r1 else
            f"价格未创新低 {p1:.2f}→{p2:.2f}→{p3:.2f} (-{dec_12 * 100:.2f}%/-{dec_23 * 100:.2f}%)"
        )
        r1_name = "价格三推创新低"
        r2_name = "DIF 死叉值递增"
    checks.append(RuleCheck("R1", r1_name, r1, r1_detail))
    checks.append(RuleCheck("R2", r2_name, r2, f"{c1:+.3f}→{c2:+.3f}→{c3:+.3f}"))

    problems: list[str] = []
    for nth, (left, right) in enumerate(((w1, w2), (w2, w3)), start=1):
        segment = dif[left.end_cross_idx:right.start_cross_idx + 1]
        if segment.size == 0:
            continue
        if direction == "top":
            seg_min = float(segment.min())
            if seg_min < -dif_zero_tolerance:
                problems.append(f"回调{nth}破零(min={seg_min:.3f})")
            if left.cross_value > 0.0:
                threshold = left.cross_value * dif_approach_zero_ratio
                if seg_min > threshold:
                    problems.append(f"回调{nth}未逼近(min={seg_min:.3f}需≤{threshold:.3f})")
        else:
            seg_max = float(segment.max())
            if seg_max > dif_zero_tolerance:
                problems.append(f"反弹{nth}破零(max={seg_max:.3f})")
            if left.cross_value < 0.0:
                threshold = left.cross_value * dif_approach_zero_ratio
                if seg_max < threshold:
                    problems.append(f"反弹{nth}未逼近(max={seg_max:.3f}需≥{threshold:.3f})")
    checks.append(RuleCheck(
        "R3",
        "DIF 回调逼近零轴",
        r3,
        "两次回调均逼近零未破" if r3 else "; ".join(problems),
    ))

    red_12 = (a1 - a2) / a1 if a1 > 0 else 0.0
    red_23 = (a2 - a3) / a2 if a2 > 0 else 0.0
    if r4:
        r4_detail = f"{a1:.2f}→{a2:.2f}→{a3:.2f} 衰减{red_12 * 100:.0f}%/{red_23 * 100:.0f}%"
    elif not (a1 > a2 > a3):
        r4_detail = f"非严格递减 {a1:.2f}/{a2:.2f}/{a3:.2f}"
    else:
        r4_detail = (
            f"衰减不足 {red_12 * 100:.0f}%/{red_23 * 100:.0f}% "
            f"(需≥{min_area_reduction * 100:.0f}%)"
        )
    checks.append(RuleCheck("R4", "柱面积严格衰减", r4, r4_detail))
    which = "顶" if direction == "top" else "底"
    checks.append(RuleCheck(
        "R5",
        "第三推时效",
        r5,
        f"第三{which}距今 {bars_since_last_peak} 根 (≤{recency_bars})",
    ))
    return checks, strength


def _waves_with_cross_values(
    price: np.ndarray,
    hist: np.ndarray,
    dif: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    *,
    last_bar: int,
    direction: str,
) -> list[Wave]:
    waves = build_waves(
        price, hist, starts, ends, last_bar=last_bar, direction=direction,
    )
    for wave in waves:
        wave.cross_value = float(dif[wave.start_cross_idx])
    return waves


def detect_triple_divergence(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    direction: str = "top",
    params: dict | None = None,
) -> DivergenceResult:
    """Last-bar detector. Same wave selection as cn_stock_scan on this prefix."""
    if direction not in {"top", "bottom"}:
        raise ValueError("direction must be 'top' or 'bottom'")
    cfg = resolve_params(params)
    close = np.asarray(close, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    if close.ndim != 1 or high.shape != close.shape or low.shape != close.shape:
        raise ValueError("close/high/low must be one aligned series")
    slow = int(cfg["slow"])
    if close.size < slow * 2:
        return DivergenceResult(
            hit=False, hit_kind="miss", direction=direction,
            reasons=[f"仅 {close.size} 根 K 线, 需 ≥ {slow * 2}"],
        )
    dif, dea, hist = compute_macd(close, int(cfg["fast"]), slow, int(cfg["signal"]))
    up, down = find_crossovers(dif, dea)
    if direction == "top":
        waves = _waves_with_cross_values(
            high, hist, dif, up, down, last_bar=close.size - 1, direction="up",
        )
    else:
        waves = _waves_with_cross_values(
            low, hist, dif, down, up, last_bar=close.size - 1, direction="down",
        )
    if len(waves) < 3:
        return DivergenceResult(
            hit=False, hit_kind="miss", direction=direction,
            reasons=[f"仅 {len(waves)} 段 {direction}-wave; 需 ≥ 3"],
        )
    w1, w2, w3 = waves[-3:]
    checks, strength = check_divergence_rules(
        w1, w2, w3, dif,
        direction=direction,
        bars_since_last_peak=close.size - 1 - w3.extreme_idx,
        min_area_reduction=float(cfg["min_area_reduction"]),
        dif_zero_tolerance=float(cfg["dif_zero_tolerance"]),
        dif_approach_zero_ratio=float(cfg["dif_approach_zero_ratio"]),
        min_price_increase_pct=float(cfg["min_price_increase_pct"]),
        recency_bars=int(cfg["recency_bars"]),
    )
    failed = [item for item in checks if not item.passed]
    reasons = [item.detail for item in failed]
    if not failed:
        kind, hit, used = "strict", True, strength
    elif len(failed) == 1:
        kind, hit, used = "loose", False, strength * 0.5
    else:
        kind, hit, used = "miss", False, 0.0
    return DivergenceResult(
        hit=hit, hit_kind=kind, direction=direction,
        waves=[w1, w2, w3], rule_checks=checks, reasons=reasons, strength=used,
    )


def _hit_from_flags(flags: tuple[bool, ...], strength: float, *, allow_loose: bool) -> tuple[int, float]:
    passed = sum(bool(item) for item in flags)
    if passed == 5:
        return 1, strength
    if passed == 4 and allow_loose:
        return 1, strength * 0.5
    return 0, 0.0


def scan_both(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    params: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Point-in-time hits for every bar. Bar t uses only close/high/low through t.

    Overlapping waves are kept. A DIF touch of zero does not close a wave, and
    the next cross in the same direction starts another wave that still ends at
    the next opposite cross. That is the source builder, not a single-wave state.
    """
    cfg = resolve_params(params)
    close = np.asarray(close, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    size = close.size
    bottom = np.zeros(size, dtype=np.uint8)
    top = np.zeros(size, dtype=np.uint8)
    bottom_score = np.zeros(size, dtype=np.float32)
    top_score = np.zeros(size, dtype=np.float32)
    slow = int(cfg["slow"])
    if size < slow * 2:
        return bottom, top, bottom_score, top_score

    dif, dea, hist = compute_macd(close, int(cfg["fast"]), slow, int(cfg["signal"]))
    up, down = find_crossovers(dif, dea)
    allow_loose = bool(cfg["allow_loose"])
    rule_kwargs = {
        "min_area_reduction": float(cfg["min_area_reduction"]),
        "dif_zero_tolerance": float(cfg["dif_zero_tolerance"]),
        "dif_approach_zero_ratio": float(cfg["dif_approach_zero_ratio"]),
        "min_price_increase_pct": float(cfg["min_price_increase_pct"]),
        "recency_bars": int(cfg["recency_bars"]),
    }
    states = (
        ("top", up, down, high, True, top, top_score),
        ("bottom", down, up, low, False, bottom, bottom_score),
    )
    minimum = slow * 2 - 1
    last_bar_only = _as_bool((params or {}).get("last_bar_only"), False)
    last_index = size - 1
    for direction, starts, ends, price, find_max, hits, scores in states:
        if starts.size == 0:
            continue
        completed: list[Wave] = []
        opened: list[Wave] = []
        start_pos = 0
        end_pos = 0
        r3_key = None
        r3_ok = False
        for index in range(size):
            is_end = end_pos < ends.size and int(ends[end_pos]) == index
            is_start = start_pos < starts.size and int(starts[start_pos]) == index
            if is_end:
                for wave in opened:
                    _accumulate(wave, index, float(price[index]), float(hist[index]), find_max=find_max)
                completed.extend(opened)
                opened = []
                end_pos += 1
            elif is_start:
                for wave in opened:
                    _accumulate(wave, index, float(price[index]), float(hist[index]), find_max=find_max)
                opened.append(_new_wave(
                    direction, index, float(dif[index]), float(price[index]), float(hist[index]),
                ))
                start_pos += 1
            else:
                for wave in opened:
                    _accumulate(wave, index, float(price[index]), float(hist[index]), find_max=find_max)
            total = len(completed) + len(opened)
            # 选股只需要最新交易日。波段状态仍逐根累积，规则只在最后一根上求值。
            if last_bar_only and index != last_index:
                continue
            if index < minimum or total < 3:
                continue
            w1, w2, w3 = _last_three(completed, opened)
            key = (
                w1.start_cross_idx, w1.end_cross_idx,
                w2.start_cross_idx, w2.end_cross_idx,
                w3.start_cross_idx,
            )
            if key != r3_key:
                r3_key = key
                r3_ok = _r3_ok(
                    w1, w2, w3, dif, direction,
                    float(cfg["dif_zero_tolerance"]),
                    float(cfg["dif_approach_zero_ratio"]),
                )
            flags = _flags_with_cached_r3(
                w1, w2, w3, r3_ok, dif,
                direction=direction,
                bars_since_last_peak=index - w3.extreme_idx,
                **rule_kwargs,
            )
            hit, strength = _hit_from_flags(flags[:5], flags[5], allow_loose=allow_loose)
            hits[index] = hit
            scores[index] = np.float32(strength)
    return bottom, top, bottom_score, top_score


def _last_three(completed: list[Wave], opened: list[Wave]) -> tuple[Wave, Wave, Wave]:
    total = len(completed) + len(opened)
    picked = []
    for offset in (total - 3, total - 2, total - 1):
        if offset < len(completed):
            picked.append(completed[offset])
        else:
            picked.append(opened[offset - len(completed)])
    return picked[0], picked[1], picked[2]


def _r3_ok(
    w1: Wave, w2: Wave, w3: Wave, dif: np.ndarray, direction: str, tolerance: float, ratio: float,
) -> bool:
    return _rule_flags(
        w1, w2, w3, dif,
        direction=direction,
        bars_since_last_peak=0,
        min_area_reduction=0.0,
        dif_zero_tolerance=tolerance,
        dif_approach_zero_ratio=ratio,
        min_price_increase_pct=0.0,
        recency_bars=10**9,
    )[2]


def _flags_with_cached_r3(
    w1: Wave,
    w2: Wave,
    w3: Wave,
    r3: bool,
    dif: np.ndarray,
    *,
    direction: str,
    bars_since_last_peak: int,
    min_area_reduction: float,
    dif_zero_tolerance: float,
    dif_approach_zero_ratio: float,
    min_price_increase_pct: float,
    recency_bars: int,
) -> tuple[bool, bool, bool, bool, bool, float]:
    """R3 is cached by the caller. The other rules use the wave as of this bar."""
    flags = _rule_flags(
        w1, w2, w3, dif,
        direction=direction,
        bars_since_last_peak=bars_since_last_peak,
        min_area_reduction=min_area_reduction,
        dif_zero_tolerance=dif_zero_tolerance,
        dif_approach_zero_ratio=dif_approach_zero_ratio,
        min_price_increase_pct=min_price_increase_pct,
        recency_bars=recency_bars,
    )
    return flags[0], flags[1], r3, flags[3], flags[4], flags[5]


def scan_frame(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    params: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Scan a time x asset panel. Missing bars are skipped per asset, not filled."""
    close = np.asarray(close, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    if close.shape != high.shape or close.shape != low.shape or close.ndim != 2:
        raise ValueError("scan_frame expects aligned time x asset arrays")
    rows, cols = close.shape
    bottom = np.zeros((rows, cols), dtype=np.uint8)
    top = np.zeros((rows, cols), dtype=np.uint8)
    bottom_score = np.zeros((rows, cols), dtype=np.float32)
    top_score = np.zeros((rows, cols), dtype=np.float32)
    minimum = int(resolve_params(params)["slow"]) * 2
    for asset in range(cols):
        valid = (
            np.isfinite(close[:, asset])
            & np.isfinite(high[:, asset])
            & np.isfinite(low[:, asset])
        )
        indexes = np.flatnonzero(valid)
        if indexes.size < minimum:
            continue
        b_hit, t_hit, b_score, t_score = scan_both(
            close[indexes, asset], high[indexes, asset], low[indexes, asset], params,
        )
        bottom[indexes, asset] = b_hit
        top[indexes, asset] = t_hit
        bottom_score[indexes, asset] = b_score
        top_score[indexes, asset] = t_score
    return bottom, top, bottom_score, top_score
