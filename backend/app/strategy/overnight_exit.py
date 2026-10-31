"""尾盘持股的次日早盘卖出。

买入仍是信号日收盘。卖出只在次日及之后的连续竞价里决定，不用日线开盘价或收盘价代替。

9:30–10:00 按顺序命中即卖：
1. 高开达到 2% 且当前未涨停，第一笔可卖行情卖出。
2. 高开不足 2%（含平开、低开），最高价相对开盘上冲达到 1% 卖出。
3. 低开后，最新价跌破分时均价且仍低于昨收，卖出。
4. 10:00 仍未卖出：未封涨停则按当时价卖出；仍封涨停则留到开板后的下一笔，
   当天没有开板就在 15:00 卖出。卖出涨停是允许的。
5. 跌停封死不成交。全天跌停则留到下一个交易日，重新按同一套规则卖。

分时均价优先用成交额 /（成交量×100）（量的单位是手）。若这样离最新价更远，
改用成交额 / 成交量，兼容把成交量记成股的快照。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Callable

import numpy as np

STRATEGY_ID = "overnight_tail_hold"
EXIT_POLICY = "overnight_morning"
EXIT_SIGNAL_ID = "signal_overnight_morning_exit"
EXIT_REASONS = (
    "gap_sell",
    "push_sell",
    "vwap_stop",
    "deadline",
    "limit_up_open",
    "limit_up_close",
    "limit_down_release",
)

GAP_SELL_PCT = 2.0
PUSH_PCT = 1.0
_LIMIT_EPS = 0.005
_SESSION_OPEN = dt_time(9, 30)
_DEADLINE = dt_time(10, 0)
_DEADLINE_GRACE = dt_time(10, 0, 30)
_SESSION_CLOSE = dt_time(15, 0)


@dataclass(frozen=True)
class SessionBar:
    at: datetime
    open: float | None
    high: float
    low: float
    close: float
    volume: float
    amount: float


@dataclass(frozen=True)
class ExitDecision:
    action: str  # sell | defer | uncovered
    price: float | None
    reason: str
    at: datetime | None = None


@dataclass(frozen=True)
class MorningView:
    session_open: float
    high: float
    low: float
    last: float
    volume: float
    amount: float
    prev_close: float
    limit_up: float
    limit_down: float


def session_vwap(amount: float, volume: float, last: float) -> float | None:
    """分时均价。手和股两种成交量口径里，取离最新价更近的那个。"""
    if not np.isfinite(amount) or not np.isfinite(volume) or amount <= 0 or volume <= 0:
        return None
    per_share = amount / volume
    per_lot = amount / (volume * 100.0)
    if not np.isfinite(last) or last <= 0:
        return per_lot
    if abs(per_lot - last) <= abs(per_share - last):
        return per_lot
    return per_share


def _near_limit(price: float, limit_price: float, *, up: bool) -> bool:
    if not np.isfinite(price) or not np.isfinite(limit_price) or limit_price <= 0:
        return False
    if up:
        return price >= limit_price - _LIMIT_EPS
    return price <= limit_price + _LIMIT_EPS


def _limit_down(view: MorningView) -> bool:
    return _near_limit(view.last, view.limit_down, up=False)


def _sealed_limit_up(view: MorningView) -> bool:
    return _near_limit(view.last, view.limit_up, up=True) and _near_limit(view.low, view.limit_up, up=True)


def _board_opened(view: MorningView) -> bool:
    return (np.isfinite(view.low) and view.low < view.limit_up - _LIMIT_EPS) or (
        np.isfinite(view.last) and view.last < view.limit_up - _LIMIT_EPS
    )


def opportunistic_reason(view: MorningView) -> str | None:
    """9:30–10:00 的三条择机规则。跌停时返回 None，调用方不成交。"""
    if view.session_open <= 0 or view.prev_close <= 0 or not np.isfinite(view.last):
        return None
    if _limit_down(view):
        return None
    gap_pct = (view.session_open / view.prev_close - 1.0) * 100.0
    if gap_pct >= GAP_SELL_PCT and not _near_limit(view.last, view.limit_up, up=True):
        return "gap_sell"
    push_pct = (view.high / view.session_open - 1.0) * 100.0 if view.high > 0 else -1.0
    if gap_pct < GAP_SELL_PCT and push_pct >= PUSH_PCT:
        return "push_sell"
    vwap = session_vwap(view.amount, view.volume, view.last)
    if gap_pct < 0 and vwap is not None and view.last < vwap and view.last < view.prev_close:
        return "vwap_stop"
    return None


def judge_morning(view: MorningView, *, at_deadline: bool) -> str:
    """返回 sell reason、hold_limit、defer 或 none。"""
    if _limit_down(view):
        return "defer" if at_deadline else "none"
    reason = opportunistic_reason(view)
    if reason:
        return reason
    if not at_deadline:
        return "none"
    if _sealed_limit_up(view):
        return "hold_limit"
    return "deadline"


def decide_session(
    bars: list[SessionBar],
    *,
    prev_close: float,
    limit_up: float,
    limit_down: float,
) -> ExitDecision:
    """回放一个交易日的原始分钟线。分钟线没有覆盖到决策时点则 uncovered。"""
    ordered = sorted(bars, key=lambda bar: bar.at)
    session_open: float | None = None
    high = -np.inf
    low = np.inf
    volume = 0.0
    amount = 0.0
    phase = "morning"
    saw_deadline = False
    last_bar: SessionBar | None = None

    def _view(bar: SessionBar) -> MorningView | None:
        if session_open is None:
            return None
        return MorningView(
            session_open=session_open,
            high=float(high),
            low=float(low),
            last=float(bar.close),
            volume=volume,
            amount=amount,
            prev_close=float(prev_close),
            limit_up=float(limit_up),
            limit_down=float(limit_down),
        )

    for index, bar in enumerate(ordered):
        clock = bar.at.time()
        if clock < _SESSION_OPEN or clock > _SESSION_CLOSE:
            continue
        if not np.isfinite(bar.close) or bar.close <= 0:
            continue
        last_bar = bar
        if session_open is None:
            opened = bar.open if bar.open is not None and np.isfinite(bar.open) and bar.open > 0 else bar.close
            session_open = float(opened)
        if np.isfinite(bar.high):
            high = max(high, float(bar.high))
        if np.isfinite(bar.low):
            low = min(low, float(bar.low))
        if np.isfinite(bar.volume) and bar.volume > 0:
            volume += float(bar.volume)
        if np.isfinite(bar.amount) and bar.amount > 0:
            amount += float(bar.amount)
        view = _view(bar)
        if view is None:
            continue
        if phase == "morning":
            if clock > _DEADLINE and not saw_deadline:
                return ExitDecision("uncovered", None, "missing_deadline", bar.at)
            at_deadline = clock >= _DEADLINE
            action = judge_morning(view, at_deadline=at_deadline)
            if action in {"gap_sell", "push_sell", "vwap_stop", "deadline"}:
                return ExitDecision("sell", float(bar.close), action, bar.at)
            if action == "hold_limit":
                phase = "hold_limit"
                saw_deadline = True
                continue
            if action == "defer" and at_deadline:
                phase = "wait"
                saw_deadline = True
            if at_deadline:
                saw_deadline = True
            continue
        if phase == "hold_limit":
            if _board_opened(view):
                phase = "sell_next"
                if index == len(ordered) - 1 and not _limit_down(view):
                    return ExitDecision("sell", float(bar.close), "limit_up_open", bar.at)
                continue
            if clock >= _SESSION_CLOSE and not _limit_down(view):
                return ExitDecision("sell", float(bar.close), "limit_up_close", bar.at)
            continue
        if phase == "sell_next":
            if not _limit_down(view):
                return ExitDecision("sell", float(bar.close), "limit_up_open", bar.at)
            if clock >= _SESSION_CLOSE:
                return ExitDecision("defer", None, "limit_down", bar.at)
            continue
        if phase == "wait" and not _limit_down(view):
            return ExitDecision("sell", float(bar.close), "limit_down_release", bar.at)
        if phase == "wait" and clock >= _SESSION_CLOSE:
            return ExitDecision("defer", None, "limit_down", bar.at)

    if phase == "hold_limit":
        return ExitDecision("uncovered", None, "missing_close", last_bar.at if last_bar else None)
    if phase == "wait":
        return ExitDecision("defer", None, "limit_down", last_bar.at if last_bar else None)
    if not saw_deadline:
        return ExitDecision("uncovered", None, "missing_deadline", last_bar.at if last_bar else None)
    return ExitDecision("defer", None, "limit_down", last_bar.at if last_bar else None)


def live_decision(view: MorningView, *, now: datetime, phase: str, deadline_seen: bool) -> tuple[str, str]:
    """实时快照上的一步。返回 (phase, reason)。reason 为空表示这一笔不卖。

    phase: morning | hold_limit | sell_next | wait
    """
    clock = now.time()
    if phase == "sell_next":
        if _limit_down(view):
            return phase, ""
        return "done", "limit_up_open"
    if phase == "hold_limit":
        if _board_opened(view):
            return "sell_next", ""
        if clock >= _SESSION_CLOSE and not _limit_down(view):
            return "done", "limit_up_close"
        return phase, ""
    if phase == "wait":
        if not _limit_down(view):
            return "done", "limit_down_release"
        return phase, ""
    at_deadline = deadline_seen or clock >= _DEADLINE
    if clock <= _DEADLINE_GRACE:
        action = judge_morning(view, at_deadline=clock >= _DEADLINE)
    elif not deadline_seen:
        # 错过 10:00 的轮询：不再补判冲高，只做截止卖出或涨停留仓。
        if _limit_down(view):
            action = "defer"
        elif _sealed_limit_up(view):
            action = "hold_limit"
        else:
            action = "deadline"
    else:
        action = "none"
    if action in {"gap_sell", "push_sell", "vwap_stop", "deadline"}:
        return "done", action
    if action == "hold_limit":
        return "hold_limit", ""
    if action == "defer" and at_deadline:
        return "wait", ""
    return "morning", ""


def schedule_exits(
    dates: list[str],
    symbols: list[str],
    entry: np.ndarray,
    raw_close: np.ndarray,
    adj_close: np.ndarray,
    bars_for: Callable[[str, str], list[SessionBar] | None],
    limit_prices,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """给每笔可回放的买入写上卖出日和卖出价。没有分钟线的买入清掉。

    卖出价换到和 adj_close 相同的复权口径：分钟原始价 × 当日复权收盘 / 当日原始收盘。
    limit_prices(prev_raw, symbol) -> (limit_up, limit_down)。
    """
    entry_out = np.array(entry, dtype=np.uint8, copy=True)
    exit_out = np.zeros(entry.shape, dtype=np.uint8)
    exit_code = np.full(entry.shape, -1, dtype=np.int16)
    exit_price = np.full(entry.shape, np.nan, dtype=np.float32)
    uncovered = 0
    deferred_days = 0
    covered = 0
    times, assets = entry.shape
    for asset in range(assets):
        symbol = symbols[asset]
        busy_until = -1
        for time_id in range(times):
            if entry_out[time_id, asset] == 0:
                continue
            if time_id <= busy_until:
                entry_out[time_id, asset] = 0
                continue
            placed = False
            cursor = time_id + 1
            while cursor < times:
                day = dates[cursor][:10]
                prev_raw = float(raw_close[cursor - 1, asset])
                if not np.isfinite(prev_raw) or prev_raw <= 0:
                    prev_raw = float(adj_close[cursor - 1, asset])
                bars = bars_for(symbol, day)
                if not bars or not np.isfinite(prev_raw) or prev_raw <= 0:
                    uncovered += 1
                    break
                limit_up, limit_down = limit_prices(prev_raw, symbol)
                decision = decide_session(
                    bars, prev_close=prev_raw, limit_up=limit_up, limit_down=limit_down,
                )
                if decision.action == "uncovered":
                    uncovered += 1
                    break
                if decision.action == "defer":
                    deferred_days += 1
                    cursor += 1
                    continue
                scale = 1.0
                raw_day = float(raw_close[cursor, asset])
                adj_day = float(adj_close[cursor, asset])
                if np.isfinite(raw_day) and raw_day > 0 and np.isfinite(adj_day) and adj_day > 0:
                    scale = adj_day / raw_day
                exit_out[cursor, asset] = 1
                exit_code[cursor, asset] = np.int16(EXIT_REASONS.index(decision.reason))
                exit_price[cursor, asset] = np.float32(float(decision.price) * scale)
                busy_until = cursor
                covered += 1
                placed = True
                break
            if not placed:
                if cursor >= times:
                    uncovered += 1
                entry_out[time_id, asset] = 0
    stats = {
        "covered_entries": covered,
        "uncovered_entries": uncovered,
        "limit_down_defer_days": deferred_days,
        "note": (
            "收益只包含有完整分钟线、并且能按 9:30–10:00 规则卖出的交易。"
            "缺少分钟线的信号已剔除，没有用开盘价或收盘价代替。"
        ),
    }
    return entry_out, exit_out, exit_price, exit_code, stats


def load_day_bars(data_dir, day: str, symbols: set[str]) -> dict[str, list[SessionBar]]:
    """读取当日原始分钟线，不走复权投影。没有分区则返回空。"""
    if not symbols:
        return {}
    path = Path(data_dir) / "kline_minute" / f"date={day}" / "part.parquet"
    if not path.exists():
        return {}
    import polars as pl
    frame = (
        pl.scan_parquet(path.as_posix())
        .filter(pl.col("symbol").is_in(list(symbols)))
        .select(["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"])
        .collect()
    )
    grouped: dict[str, list[SessionBar]] = {}
    for row in frame.iter_rows(named=True):
        grouped.setdefault(str(row["symbol"]), []).append(SessionBar(
            at=row["datetime"],
            open=None if row["open"] is None else float(row["open"]),
            high=float(row["high"]) if row["high"] is not None else float("nan"),
            low=float(row["low"]) if row["low"] is not None else float("nan"),
            close=float(row["close"]) if row["close"] is not None else float("nan"),
            volume=float(row["volume"] or 0),
            amount=float(row["amount"] or 0),
        ))
    return grouped
