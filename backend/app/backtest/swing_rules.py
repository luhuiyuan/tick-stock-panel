"""A-share swing rules copied from local research repos.

Sources, copied rather than redesigned:

- Grid: akquant ``examples/strategies/02_stock_grid_trading.py``
  (``grid_pct=0.03``, ``trade_lot=100``, initial 10 lots). The anchor is the
  last *filled* price. A rejected order does not move the anchor.
- Turtle: KHunter ``trading/turtle_strategy.py`` short preset
  (10/5/10, add 0.5 ATR, stop 2 ATR, max 4 adds, profit gate 2%,
  add quantity ``current / (add_count + 2)`` floored to 100 shares).
  ATR is an SMA of true range, not an EWM. Donchian levels exclude today.
- Mean reversion: china-astock-quant ``strategies/mean_reversion.py`` and
  ``strategies/_indicators.py`` (Wilder RSI, Bollinger with population std).
- N rebound: N-Rebound ``night_screener.py`` constants and ``check_stock_sina``.
  There is no model file and no round-trip sell in that screener. The exit
  used here is the same structure break the screener already rejects
  (close back through the limit-day open).

The book below is the shared state machine for scans and the scaled matcher.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

GRID_STRATEGY_ID = "grid_trading"
TURTLE_STRATEGY_ID = "turtle_channel"

GRID_DEFAULTS = {
    "grid_pct": 0.03,
    "trade_lot": 100,
    "initial_lots": 10,
}
TURTLE_DEFAULTS = {
    "n_entry": 10,
    "n_exit": 5,
    "atr_period": 10,
    "add_atr": 0.5,
    "exit_atr": 2.0,
    "add_profit_min": 0.02,
    "max_additions": 4,
    "base_position_amount": 20000.0,
    "use_ma_filter": True,
    "upper_shadow_max": 0.04,
}
MEAN_REVERSION_DEFAULTS = {
    "rsi_window": 14,
    "rsi_oversold": 30.0,
    "rsi_overbought": 70.0,
    "bb_window": 20,
    "bb_mult": 2.0,
    "time_stop_days": 10,
}
N_REBOUND_DEFAULTS = {
    "n_days": 7,
    "vol_shrink_ratio": 1.2,
    "upper_shadow_limit": 0.06,
    "max_position_pct": 0.6,
    "min_listed_days": 60,
    "limit_move_pct": 9.5,
}


def _f(params: dict, key: str, defaults: dict):
    value = params.get(key, defaults[key])
    return defaults[key] if value is None else value


def grid_decide(
    close: float,
    last_fill_price: float | None,
    shares: int,
    *,
    grid_pct: float,
    trade_lot: int,
    initial_lots: int,
) -> tuple[str, int]:
    """akquant ``GridTradingStrategy.on_bar`` price rules.

    Returns ``("buy"|"sell"|"hold", share_quantity)``. A sell with too few
    shares stays on hold and does not move the anchor; the caller only updates
    the anchor after a real fill.
    """
    lot = max(int(trade_lot), 1)
    if last_fill_price is None or not np.isfinite(last_fill_price) or last_fill_price <= 0:
        return "buy", max(int(initial_lots), 1) * lot
    if not np.isfinite(close) or close <= 0:
        return "hold", 0
    change = (close - last_fill_price) / last_fill_price
    if change <= -float(grid_pct):
        return "buy", lot
    if change >= float(grid_pct):
        if shares >= lot:
            return "sell", lot
        return "hold", 0
    return "hold", 0


def turtle_add_quantity(shares: int, add_count: int) -> int:
    """KHunter add size: ``int(qty / (add_count + 2)) // 100 * 100``, at least 100."""
    if shares <= 0:
        return 0
    ratio = 1.0 / (int(add_count) + 2)
    sized = int(shares * ratio) // 100 * 100
    return max(sized, 100)


def turtle_initial_quantity(price: float, base_amount: float) -> int:
    """KHunter initial size: ``max(int(base_amount / price) // 100 * 100, 100)``."""
    if not np.isfinite(price) or price <= 0:
        return 0
    return max(int(float(base_amount) / price) // 100 * 100, 100)


def _body_high(open_: float, close: float) -> float:
    return max(float(open_), float(close))


def turtle_upper_shadow_ok(open_: float, high: float, close: float, limit: float = 0.04) -> bool:
    """Upper shadow versus the body top. Zero body is treated as 0, matching KHunter."""
    body = _body_high(open_, close)
    if body <= 0:
        return True
    return (float(high) - body) / body <= float(limit)


def turtle_is_bullish(open_: float, close: float, prev_close: float) -> bool:
    return bool(close > open_ and close > prev_close)


def turtle_buy_ok(
    *,
    open_: float,
    high: float,
    close: float,
    prev_close: float,
    up: float,
    ma20: float,
    use_ma_filter: bool = True,
    upper_shadow_max: float = 0.04,
) -> bool:
    """KHunter ``_check_buy_signal`` on one bar (the signal bar, not the fill bar)."""
    if not (np.isfinite(up) and high > up):
        return False
    if not turtle_upper_shadow_ok(open_, high, close, upper_shadow_max):
        return False
    if not np.isfinite(prev_close) or not turtle_is_bullish(open_, close, prev_close):
        return False
    if use_ma_filter and not (np.isfinite(ma20) and close > ma20):
        return False
    return True


def turtle_sell_signal(
    *,
    low: float,
    down: float,
    atr: float,
    entry_price: float,
    exit_atr: float,
    n_exit: int,
) -> tuple[bool, str]:
    """KHunter ``_check_sell_signal``. Channel break is checked before the ATR stop."""
    if np.isfinite(down) and low < down:
        return True, f"跌破{int(n_exit)}日低点"
    if np.isfinite(atr) and entry_price > 0:
        stop = entry_price - float(exit_atr) * atr
        if low <= stop:
            return True, "ATR止损"
    return False, ""


def turtle_add_ok(
    *,
    open_: float,
    high: float,
    close: float,
    prev_close: float,
    atr: float,
    ma20: float,
    entry_price: float,
    last_add_price: float | None,
    add_count: int,
    max_additions: int | None,
    add_atr: float,
    add_profit_min: float,
    use_ma_filter: bool = True,
    upper_shadow_max: float = 0.04,
) -> bool:
    """KHunter add block: profit versus average price, then 0.5 ATR from the last add."""
    if max_additions is not None and add_count >= max_additions:
        return False
    if not (np.isfinite(atr) and entry_price > 0 and np.isfinite(close)):
        return False
    profit = (close - entry_price) / entry_price
    if profit < float(add_profit_min):
        return False
    anchor = last_add_price if last_add_price else entry_price
    if not np.isfinite(high) or high < anchor + float(add_atr) * atr:
        return False
    if not np.isfinite(prev_close) or not turtle_is_bullish(open_, close, prev_close):
        return False
    if use_ma_filter and not (np.isfinite(ma20) and close > ma20):
        return False
    return turtle_upper_shadow_ok(open_, high, close, upper_shadow_max)


def sellable_shares(lots: list[dict], time_id: int) -> int:
    """T+1: shares filled on ``time_id`` cannot be sold on that same bar."""
    return int(sum(int(lot["qty"]) for lot in lots if int(lot["time_id"]) < time_id))


def _as_2d(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array.reshape(-1, 1)
    if array.ndim != 2:
        raise ValueError("price arrays must be 1-D or time x asset")
    return array


def _rolling(values: np.ndarray, window: int, how: str) -> np.ndarray:
    """Rolling reduce with ``min_periods=window``. Any NaN in the window yields NaN."""
    array = _as_2d(values)
    rows, cols = array.shape
    out = np.full((rows, cols), np.nan, dtype=np.float64)
    window = int(window)
    if window <= 0 or rows < window:
        return out
    view = np.lib.stride_tricks.sliding_window_view(array, window, axis=0)
    finite = np.isfinite(view).all(axis=-1)
    if how == "max":
        reduced = np.max(np.where(np.isfinite(view), view, -np.inf), axis=-1)
    elif how == "min":
        reduced = np.min(np.where(np.isfinite(view), view, np.inf), axis=-1)
    elif how == "mean":
        reduced = np.sum(np.where(np.isfinite(view), view, 0.0), axis=-1) / window
    elif how == "std0":
        mean = np.sum(np.where(np.isfinite(view), view, 0.0), axis=-1) / window
        centered = view - mean[..., None]
        reduced = np.sqrt(np.sum(np.where(np.isfinite(view), centered * centered, 0.0), axis=-1) / window)
    else:
        raise ValueError(how)
    reduced = np.where(finite, reduced, np.nan)
    out[window - 1 :] = reduced
    return out


def _shift(values: np.ndarray, periods: int) -> np.ndarray:
    array = _as_2d(values)
    out = np.full(array.shape, np.nan, dtype=np.float64)
    if periods <= 0:
        return array.copy()
    if periods < array.shape[0]:
        out[periods:] = array[:-periods]
    return out


def wilder_rsi(close: np.ndarray, window: int = 14) -> np.ndarray:
    """Wilder RSI via ``ewm(alpha=1/window, adjust=False, min_periods=window)``.

    Same smoothing as china-astock-quant ``strategies/_indicators.py``.
    """
    array = _as_2d(close)
    rows, cols = array.shape
    delta = np.full_like(array, np.nan)
    delta[1:] = array[1:] - array[:-1]
    gain = np.where(np.isfinite(delta), np.maximum(delta, 0.0), np.nan)
    loss = np.where(np.isfinite(delta), np.maximum(-delta, 0.0), np.nan)
    alpha = 1.0 / int(window)
    avg_gain = np.full_like(array, np.nan)
    avg_loss = np.full_like(array, np.nan)
    for col in range(cols):
        g = gain[:, col]
        l = loss[:, col]
        ag = al = None
        started = False
        seen = 0
        for i in range(rows):
            if not np.isfinite(g[i]) or not np.isfinite(l[i]):
                continue
            seen += 1
            if not started:
                ag = float(g[i])
                al = float(l[i])
                started = True
            else:
                ag = (1.0 - alpha) * ag + alpha * float(g[i])
                al = (1.0 - alpha) * al + alpha * float(l[i])
            if seen >= int(window):
                avg_gain[i, col] = ag
                avg_loss[i, col] = al
    out = np.full_like(array, np.nan)
    both_zero = (avg_loss == 0.0) & (avg_gain == 0.0)
    gain_only = (avg_loss == 0.0) & (avg_gain > 0.0)
    normal = np.isfinite(avg_gain) & np.isfinite(avg_loss) & ~both_zero & ~gain_only
    out[both_zero] = 50.0
    out[gain_only] = 100.0
    rs = np.divide(avg_gain, avg_loss, out=np.full_like(array, np.nan), where=normal)
    out[normal] = 100.0 - 100.0 / (1.0 + rs[normal])
    return out


def bollinger_lower_mid(close: np.ndarray, window: int = 20, mult: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """Population Bollinger (``ddof=0``), matching china-astock-quant ``bollinger``."""
    mid = _rolling(close, window, "mean")
    std = _rolling(close, window, "std0")
    lower = mid - float(mult) * std
    return lower, mid


def mean_reversion_masks(close: np.ndarray, params: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Entry: RSI < 30 and close below the lower band. Exit: mid, RSI > 70.

    The 10-bar time stop is left to ``MAX_HOLD_DAYS`` so the product hold
    counter, not a second clock, closes the trade.
    """
    params = params or {}
    array = _as_2d(close)
    rsi = wilder_rsi(array, int(_f(params, "rsi_window", MEAN_REVERSION_DEFAULTS)))
    lower, mid = bollinger_lower_mid(
        array,
        int(_f(params, "bb_window", MEAN_REVERSION_DEFAULTS)),
        float(_f(params, "bb_mult", MEAN_REVERSION_DEFAULTS)),
    )
    oversold = float(_f(params, "rsi_oversold", MEAN_REVERSION_DEFAULTS))
    overbought = float(_f(params, "rsi_overbought", MEAN_REVERSION_DEFAULTS))
    finite = np.isfinite(rsi) & np.isfinite(lower) & np.isfinite(mid) & np.isfinite(array)
    entry = finite & (rsi < oversold) & (array < lower)
    exit_ = finite & ((array >= mid) | (rsi > overbought)) & ~entry
    return entry, exit_


def n_rebound_masks(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    params: dict | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """N-Rebound ``check_stock_sina`` evaluated on every bar.

    Newest limit-day inside ``n_days`` wins. A bar is an exit when that
    structure is no longer valid (including a close through the limit-day open).
    """
    params = params or {}
    opened = _as_2d(open_)
    highs = _as_2d(high)
    lows = _as_2d(low)
    closes = _as_2d(close)
    volumes = _as_2d(volume)
    rows, cols = closes.shape
    n_days = int(_f(params, "n_days", N_REBOUND_DEFAULTS))
    ratio = float(_f(params, "vol_shrink_ratio", N_REBOUND_DEFAULTS))
    shadow_limit = float(_f(params, "upper_shadow_limit", N_REBOUND_DEFAULTS))
    max_pos = float(_f(params, "max_position_pct", N_REBOUND_DEFAULTS))
    listed = int(_f(params, "min_listed_days", N_REBOUND_DEFAULTS))
    move = float(_f(params, "limit_move_pct", N_REBOUND_DEFAULTS))
    high_n = _rolling(highs, listed, "max")
    low_n = _rolling(lows, listed, "min")
    entry = np.zeros((rows, cols), dtype=bool)
    exit_ = np.zeros((rows, cols), dtype=bool)
    prev = _shift(closes, 1)
    for t in range(listed - 1, rows):
        span = high_n[t] - low_n[t]
        position = np.zeros(cols, dtype=np.float64)
        usable = np.isfinite(span) & (span > 0) & np.isfinite(closes[t])
        position[usable] = (closes[t, usable] - low_n[t, usable]) / span[usable]
        shadow = np.full(cols, np.inf)
        positive = np.isfinite(closes[t]) & (closes[t] > 0)
        shadow[positive] = (highs[t, positive] - closes[t, positive]) / closes[t, positive]
        alive = usable & (position <= max_pos) & (shadow <= shadow_limit) & np.isfinite(volumes[t])
        chosen = np.zeros(cols, dtype=bool)
        for k in range(1, n_days + 1):
            zt = t - k
            if zt <= 0:
                break
            base = prev[zt]
            change = np.full(cols, np.nan)
            ok_base = np.isfinite(base) & (base > 0) & np.isfinite(closes[zt])
            change[ok_base] = (closes[zt, ok_base] - base[ok_base]) / base[ok_base] * 100.0
            after = np.ones(cols, dtype=bool)
            gate = opened[zt]
            for j in range(zt + 1, t + 1):
                after &= np.isfinite(closes[j]) & np.isfinite(gate) & (closes[j] >= gate)
            hit = (
                alive
                & ~chosen
                & ok_base
                & (change > move)
                & after
                & np.isfinite(volumes[zt])
                & (volumes[t] <= volumes[zt] * ratio)
            )
            chosen |= hit
        entry[t] = chosen
        exit_[t] = ~chosen
    return entry, exit_


def turtle_indicators(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    params: dict | None = None,
) -> dict[str, np.ndarray]:
    """KHunter ``calculate_indicators``: shifted Donchian, SMA ATR, MA20."""
    params = params or {}
    opened = _as_2d(open_)
    highs = _as_2d(high)
    lows = _as_2d(low)
    closes = _as_2d(close)
    n_entry = int(_f(params, "n_entry", TURTLE_DEFAULTS))
    n_exit = int(_f(params, "n_exit", TURTLE_DEFAULTS))
    atr_period = int(_f(params, "atr_period", TURTLE_DEFAULTS))
    prev = _shift(closes, 1)
    tr = np.fmax(highs - lows, np.fmax(np.abs(highs - prev), np.abs(lows - prev)))
    tr[~np.isfinite(closes) | ~np.isfinite(highs) | ~np.isfinite(lows)] = np.nan
    return {
        "open": opened,
        "high": highs,
        "low": lows,
        "close": closes,
        "prev_close": prev,
        "up": _shift(_rolling(highs, n_entry, "max"), 1),
        "down": _shift(_rolling(lows, n_exit, "min"), 1),
        "atr": _rolling(tr, atr_period, "mean"),
        "ma20": _rolling(closes, 20, "mean"),
    }


@dataclass
class BookFill:
    time_id: int
    asset: int
    side: str
    qty: int
    price: float
    reason: str
    signal_time: int
    shares_after: int


@dataclass
class BookResult:
    entry: np.ndarray
    exit: np.ndarray
    fills: list[BookFill] = field(default_factory=list)
    shares: np.ndarray | None = None
    cash: np.ndarray | None = None


def _one_price_limit(open_, high, low, close, flag: bool) -> bool:
    prices = (float(open_), float(high), float(low), float(close))
    if not all(np.isfinite(price) and price > 0 for price in prices):
        return False
    same = max(prices) - min(prices) <= max(abs(prices[3]) * 1e-4, 0.01)
    return bool(flag) and same


def _can_buy_bar(open_, high, low, close, tradable, limit_up, *, fill_price: float, same_bar_close: bool) -> bool:
    if not tradable or not np.isfinite(fill_price) or fill_price <= 0:
        return False
    if same_bar_close and bool(limit_up):
        return False
    if _one_price_limit(open_, high, low, close, bool(limit_up)):
        return False
    return True


def _can_sell_bar(open_, high, low, close, tradable, limit_down, *, fill_price: float) -> bool:
    if not tradable or not np.isfinite(fill_price) or fill_price <= 0:
        return False
    if _one_price_limit(open_, high, low, close, bool(limit_down)):
        return False
    return True


def _param_int(params: dict, key: str, defaults: dict) -> int:
    return int(_f(params, key, defaults))


def replay_grid(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    params: dict | None = None,
    tradable: np.ndarray | None = None,
    limit_up: np.ndarray | None = None,
    limit_down: np.ndarray | None = None,
    initial_cash: float = 1_000_000.0,
    buy_cost_pct: float = 0.0,
    sell_cost_pct: float = 0.0,
    fill: str = "close_t",
    max_positions: int = 10,
    max_exposure_pct: float = 1.0,
) -> BookResult:
    """Grid book. ``fill='close_t'`` matches akquant (decide and trade on the close).

    ``fill='open_t+1'`` decides on the close and trades the next bar's open.
    The anchor updates only when the order fills.
    """
    params = {**GRID_DEFAULTS, **(params or {})}
    opened = _as_2d(open_)
    highs = _as_2d(high)
    lows = _as_2d(low)
    closes = _as_2d(close)
    rows, cols = closes.shape
    tradable_m = np.ones((rows, cols), dtype=bool) if tradable is None else np.asarray(tradable, dtype=bool).reshape(rows, cols)
    up_m = np.zeros((rows, cols), dtype=bool) if limit_up is None else np.asarray(limit_up, dtype=bool).reshape(rows, cols)
    down_m = np.zeros((rows, cols), dtype=bool) if limit_down is None else np.asarray(limit_down, dtype=bool).reshape(rows, cols)
    entry = np.zeros((rows, cols), dtype=np.uint8)
    exit_ = np.zeros((rows, cols), dtype=np.uint8)
    shares = np.zeros(cols, dtype=np.int64)
    last_price = np.full(cols, np.nan)
    lots: list[list[dict]] = [[] for _ in range(cols)]
    cash = float(initial_cash)
    cash_path = np.full(rows, cash)
    share_path = np.zeros((rows, cols), dtype=np.int64)
    fills: list[BookFill] = []
    pending: dict[int, dict] = {}
    delayed = fill == "open_t+1"
    grid_pct = float(params["grid_pct"])
    trade_lot = int(params["trade_lot"])
    initial_lots = int(params["initial_lots"])

    def _mark(t: int) -> tuple[float, float]:
        market_value = 0.0
        for asset in range(cols):
            if shares[asset] <= 0:
                continue
            price = closes[t, asset]
            if not np.isfinite(price) or price <= 0:
                price = last_price[asset]
            if np.isfinite(price) and price > 0:
                market_value += shares[asset] * float(price)
        equity = cash + market_value
        return market_value, equity

    def _try_fill(t: int, asset: int, order: dict) -> None:
        nonlocal cash
        side = order["side"]
        qty = int(order["qty"])
        same_close = not delayed
        price = float(closes[t, asset] if same_close else opened[t, asset])
        if side == "buy":
            if not _can_buy_bar(
                opened[t, asset], highs[t, asset], lows[t, asset], closes[t, asset],
                bool(tradable_m[t, asset]), bool(up_m[t, asset]),
                fill_price=price, same_bar_close=same_close,
            ):
                return
            if shares[asset] <= 0 and int(np.count_nonzero(shares > 0)) >= int(max_positions):
                return
            _, equity = _mark(t)
            capacity = equity * float(max_exposure_pct) - (_mark(t)[0])
            cost = qty * price * (1.0 + buy_cost_pct)
            if qty < 100 or cost > cash + 1e-6 or cost > capacity + 1e-6:
                return
            cash -= cost
            shares[asset] += qty
            lots[asset].append({"time_id": t, "qty": qty, "price": price})
            last_price[asset] = price
        else:
            if not _can_sell_bar(
                opened[t, asset], highs[t, asset], lows[t, asset], closes[t, asset],
                bool(tradable_m[t, asset]), bool(down_m[t, asset]), fill_price=price,
            ):
                return
            qty = min(qty, sellable_shares(lots[asset], t))
            if qty < 100 or qty > shares[asset]:
                return
            cash += qty * price * (1.0 - sell_cost_pct)
            remain = qty
            for lot in lots[asset]:
                if remain <= 0:
                    break
                take = min(int(lot["qty"]), remain)
                lot["qty"] = int(lot["qty"]) - take
                remain -= take
            lots[asset] = [lot for lot in lots[asset] if int(lot["qty"]) > 0]
            shares[asset] -= qty
            last_price[asset] = price
        fills.append(BookFill(
            time_id=t, asset=asset, side=side, qty=qty, price=price,
            reason=order["reason"], signal_time=int(order["signal_time"]),
            shares_after=int(shares[asset]),
        ))

    for t in range(rows):
        if delayed and pending:
            for asset, order in list(pending.items()):
                _try_fill(t, asset, order)
            pending = {}
        for asset in range(cols):
            price = float(closes[t, asset])
            if not bool(tradable_m[t, asset]) or not np.isfinite(price) or price <= 0:
                continue
            side, qty = grid_decide(
                price,
                None if not np.isfinite(last_price[asset]) else float(last_price[asset]),
                int(shares[asset]),
                grid_pct=grid_pct,
                trade_lot=trade_lot,
                initial_lots=initial_lots,
            )
            if side == "hold":
                continue
            if side == "buy":
                entry[t, asset] = 1
            else:
                exit_[t, asset] = 1
            order = {"side": side, "qty": qty, "reason": "grid", "signal_time": t}
            if delayed:
                pending[asset] = order
            else:
                _try_fill(t, asset, order)
        cash_path[t] = cash
        share_path[t] = shares
    result = BookResult(entry=entry, exit=exit_, fills=fills, shares=share_path, cash=cash_path)
    return result


def replay_turtle(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    params: dict | None = None,
    tradable: np.ndarray | None = None,
    limit_up: np.ndarray | None = None,
    limit_down: np.ndarray | None = None,
    initial_cash: float = 1_000_000.0,
    buy_cost_pct: float = 0.0,
    sell_cost_pct: float = 0.0,
    fill: str = "open_t+1",
    max_positions: int = 10,
    max_exposure_pct: float = 1.0,
) -> BookResult:
    """Turtle book. Default fill is next open, matching KHunter backtest mode.

    Sell is checked before add. Add count and last-add price move only after
    the add order fills. ``last_add_price`` stays the signal-bar close.
    """
    params = {**TURTLE_DEFAULTS, **(params or {})}
    ind = turtle_indicators(open_, high, low, close, params)
    rows, cols = ind["close"].shape
    tradable_m = np.ones((rows, cols), dtype=bool) if tradable is None else np.asarray(tradable, dtype=bool).reshape(rows, cols)
    up_m = np.zeros((rows, cols), dtype=bool) if limit_up is None else np.asarray(limit_up, dtype=bool).reshape(rows, cols)
    down_m = np.zeros((rows, cols), dtype=bool) if limit_down is None else np.asarray(limit_down, dtype=bool).reshape(rows, cols)
    entry = np.zeros((rows, cols), dtype=np.uint8)
    exit_ = np.zeros((rows, cols), dtype=np.uint8)
    shares = np.zeros(cols, dtype=np.int64)
    avg = np.full(cols, np.nan)
    add_count = np.zeros(cols, dtype=np.int64)
    last_add = np.full(cols, np.nan)
    lots: list[list[dict]] = [[] for _ in range(cols)]
    cash = float(initial_cash)
    cash_path = np.full(rows, cash)
    share_path = np.zeros((rows, cols), dtype=np.int64)
    fills: list[BookFill] = []
    pending: dict[int, dict] = {}
    delayed = fill == "open_t+1"
    use_ma = bool(params["use_ma_filter"])
    shadow = float(params["upper_shadow_max"])
    max_adds = params["max_additions"]
    max_adds = None if max_adds is not None and int(max_adds) <= 0 else (None if max_adds is None else int(max_adds))

    def _mark(t: int) -> tuple[float, float]:
        market_value = 0.0
        for asset in range(cols):
            if shares[asset] <= 0:
                continue
            price = ind["close"][t, asset]
            if not np.isfinite(price) or price <= 0:
                price = avg[asset]
            if np.isfinite(price) and price > 0:
                market_value += shares[asset] * float(price)
        return market_value, cash + market_value

    def _try_fill(t: int, asset: int, order: dict) -> None:
        nonlocal cash
        side = order["side"]
        same_close = not delayed
        price = float(ind["close"][t, asset] if same_close else ind["open"][t, asset])
        if side == "buy":
            qty = int(order["qty"] or 0)
            if order["kind"] == "initial":
                qty = turtle_initial_quantity(price, float(params["base_position_amount"]))
            if not _can_buy_bar(
                ind["open"][t, asset], ind["high"][t, asset], ind["low"][t, asset], ind["close"][t, asset],
                bool(tradable_m[t, asset]), bool(up_m[t, asset]),
                fill_price=price, same_bar_close=same_close,
            ):
                return
            if order["kind"] == "initial" and int(np.count_nonzero(shares > 0)) >= int(max_positions):
                return
            market_value, equity = _mark(t)
            capacity = equity * float(max_exposure_pct) - market_value
            cost = qty * price * (1.0 + buy_cost_pct)
            if qty < 100 or cost > cash + 1e-6 or cost > capacity + 1e-6:
                return
            cash -= cost
            old = int(shares[asset])
            shares[asset] = old + qty
            if old <= 0 or not np.isfinite(avg[asset]):
                avg[asset] = price
            else:
                avg[asset] = (avg[asset] * old + price * qty) / shares[asset]
            lots[asset].append({"time_id": t, "qty": qty, "price": price})
            if order["kind"] == "add":
                add_count[asset] = int(order["add_count_after"])
                last_add[asset] = float(order["signal_close"])
        else:
            if not _can_sell_bar(
                ind["open"][t, asset], ind["high"][t, asset], ind["low"][t, asset], ind["close"][t, asset],
                bool(tradable_m[t, asset]), bool(down_m[t, asset]), fill_price=price,
            ):
                return
            qty = min(int(shares[asset]), sellable_shares(lots[asset], t))
            if qty < 100:
                return
            cash += qty * price * (1.0 - sell_cost_pct)
            remain = qty
            for lot in lots[asset]:
                if remain <= 0:
                    break
                take = min(int(lot["qty"]), remain)
                lot["qty"] = int(lot["qty"]) - take
                remain -= take
            lots[asset] = [lot for lot in lots[asset] if int(lot["qty"]) > 0]
            shares[asset] -= qty
            if shares[asset] <= 0:
                shares[asset] = 0
                avg[asset] = np.nan
                add_count[asset] = 0
                last_add[asset] = np.nan
                lots[asset] = []
        fills.append(BookFill(
            time_id=t, asset=asset, side=side, qty=int(qty), price=price,
            reason=str(order["reason"]), signal_time=int(order["signal_time"]),
            shares_after=int(shares[asset]),
        ))

    for t in range(rows):
        if delayed and pending:
            for asset, order in list(pending.items()):
                _try_fill(t, asset, order)
            pending = {}
        for asset in range(cols):
            if not bool(tradable_m[t, asset]):
                continue
            bar_open = float(ind["open"][t, asset])
            bar_high = float(ind["high"][t, asset])
            bar_low = float(ind["low"][t, asset])
            bar_close = float(ind["close"][t, asset])
            prev_close = float(ind["prev_close"][t, asset])
            if not np.isfinite(bar_close) or bar_close <= 0:
                continue
            order = None
            if shares[asset] > 0:
                sell, reason = turtle_sell_signal(
                    low=bar_low,
                    down=float(ind["down"][t, asset]),
                    atr=float(ind["atr"][t, asset]),
                    entry_price=float(avg[asset]) if np.isfinite(avg[asset]) else 0.0,
                    exit_atr=float(params["exit_atr"]),
                    n_exit=int(params["n_exit"]),
                )
                if sell:
                    exit_[t, asset] = 1
                    order = {
                        "side": "sell", "qty": int(shares[asset]), "kind": "exit",
                        "reason": reason, "signal_time": t,
                    }
                elif turtle_add_ok(
                    open_=bar_open, high=bar_high, close=bar_close, prev_close=prev_close,
                    atr=float(ind["atr"][t, asset]), ma20=float(ind["ma20"][t, asset]),
                    entry_price=float(avg[asset]),
                    last_add_price=None if not np.isfinite(last_add[asset]) else float(last_add[asset]),
                    add_count=int(add_count[asset]),
                    max_additions=max_adds,
                    add_atr=float(params["add_atr"]),
                    add_profit_min=float(params["add_profit_min"]),
                    use_ma_filter=use_ma,
                    upper_shadow_max=shadow,
                ):
                    entry[t, asset] = 1
                    order = {
                        "side": "buy",
                        "qty": turtle_add_quantity(int(shares[asset]), int(add_count[asset])),
                        "kind": "add",
                        "reason": "加仓",
                        "signal_time": t,
                        "add_count_after": int(add_count[asset]) + 1,
                        "signal_close": bar_close,
                    }
            elif turtle_buy_ok(
                open_=bar_open, high=bar_high, close=bar_close, prev_close=prev_close,
                up=float(ind["up"][t, asset]), ma20=float(ind["ma20"][t, asset]),
                use_ma_filter=use_ma, upper_shadow_max=shadow,
            ):
                entry[t, asset] = 1
                order = {
                    "side": "buy", "qty": 0, "kind": "initial",
                    "reason": "突破上线", "signal_time": t,
                }
            if order is None:
                continue
            if delayed:
                pending[asset] = order
            else:
                _try_fill(t, asset, order)
        cash_path[t] = cash
        share_path[t] = shares
    return BookResult(entry=entry, exit=exit_, fills=fills, shares=share_path, cash=cash_path)
