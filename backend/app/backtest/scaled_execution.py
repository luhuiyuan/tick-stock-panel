"""Portfolio book for grid and turtle.

Old matrix matchers stay untouched. Only strategies that set
``META["scaled_execution"]`` to ``grid`` or ``turtle`` land here, and the
order rules are the shared book in ``swing_rules`` (akquant grid, KHunter turtle).
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np

from app.backtest.engine import (
    BacktestEngine,
    MatcherConfig,
    SimResult,
    SimulationOptions,
    TradeRecord,
)
from app.backtest.matrix import MarketMatrix
from app.backtest.swing_rules import replay_grid, replay_turtle


def _as_date(label: str) -> date:
    return datetime.strptime(label[:10], "%Y-%m-%d").date()


def _price_matrix(matrix: MarketMatrix) -> dict[str, np.ndarray]:
    return {
        "open_": np.asarray(matrix.open, dtype=np.float64),
        "high": np.asarray(matrix.high, dtype=np.float64),
        "low": np.asarray(matrix.low, dtype=np.float64),
        "close": np.asarray(matrix.close, dtype=np.float64),
        "tradable": np.asarray(matrix.tradable, dtype=bool),
        "limit_up": np.asarray(matrix.limit_up_locked, dtype=bool),
        "limit_down": np.asarray(matrix.limit_down_locked, dtype=bool),
    }


def simulate_scaled(
    matrix: MarketMatrix,
    config: MatcherConfig,
    kind: str,
    params: dict | None = None,
    progress_cb=None,
    cancel_event=None,
    options: SimulationOptions | None = None,
) -> SimResult:
    """Run the copied grid or turtle book and return a normal portfolio result."""
    options = options or SimulationOptions()
    if kind not in {"grid", "turtle"}:
        raise ValueError(f"unknown scaled execution {kind!r}")
    prices = _price_matrix(matrix)
    rows, cols = prices["close"].shape
    if rows == 0 or cols == 0:
        return BacktestEngine._empty_result()
    fill = "open_t+1" if config.entry_fill == "open_t+1" else "close_t"
    common = dict(
        params=params or {},
        tradable=prices["tradable"],
        limit_up=prices["limit_up"],
        limit_down=prices["limit_down"],
        initial_cash=float(config.initial_capital),
        buy_cost_pct=float(config.buy_cost_pct()),
        sell_cost_pct=float(config.sell_cost_pct()),
        fill=fill,
        max_positions=int(config.max_positions),
        max_exposure_pct=float(config.max_exposure_pct),
    )
    if kind == "grid":
        book = replay_grid(prices["open_"], prices["high"], prices["low"], prices["close"], **common)
    else:
        # KHunter backtests the signal bar and trades the next open. close_t is
        # only used when the user explicitly asks the matcher for same-bar fills.
        book = replay_turtle(prices["open_"], prices["high"], prices["low"], prices["close"], **common)
    if cancel_event is not None and cancel_event.is_set():
        empty = BacktestEngine._empty_result()
        empty.stats["error"] = "cancelled"
        return empty

    labels = [str(label)[:10] for label in matrix.timestamp_labels]
    close = prices["close"]
    equity_values: list[float] = []
    exposure_values: list[float] = []
    equity_curve: list[dict] = []
    drawdown_curve: list[dict] = []
    peak = float(config.initial_capital)
    for t in range(rows):
        market_value = 0.0
        for asset in range(cols):
            qty = int(book.shares[t, asset]) if book.shares is not None else 0
            if qty <= 0:
                continue
            price = close[t, asset]
            if not np.isfinite(price) or price <= 0:
                continue
            market_value += qty * float(price)
        equity = float(book.cash[t]) + market_value if book.cash is not None else float(config.initial_capital)
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0 if peak > 0 else 0.0
        exposure = market_value / equity if equity > 0 else 0.0
        equity_values.append(equity)
        exposure_values.append(exposure)
        if options.include_curves:
            equity_curve.append({
                "date": labels[t],
                "value": round(equity, 2),
                "cash": round(float(book.cash[t]), 2),
                "positions": int(np.count_nonzero(book.shares[t] > 0)),
                "exposure": round(exposure, 4),
            })
            drawdown_curve.append({"date": labels[t], "value": round(float(drawdown), 4)})

    trades = _trades_from_fills(
        matrix, book.fills, labels, float(config.buy_cost_pct()), float(config.sell_cost_pct()),
    )
    end_trades, end_cash = _liquidate_remaining(
        matrix, book, labels,
        float(config.buy_cost_pct()),
        float(config.sell_cost_pct()),
        float(book.cash[-1]) if book.cash is not None else float(config.initial_capital),
    )
    trades.extend(end_trades)
    if equity_values:
        equity_values[-1] = end_cash
        if options.include_curves and equity_curve:
            equity_curve[-1]["value"] = round(end_cash, 2)
            equity_curve[-1]["cash"] = round(end_cash, 2)
            equity_curve[-1]["positions"] = 0
            equity_curve[-1]["exposure"] = 0.0
            peak_equity = max(row["value"] for row in equity_curve)
            drawdown_curve[-1]["value"] = round(end_cash / peak_equity - 1.0 if peak_equity else 0.0, 4)

    stats = BacktestEngine._calc_portfolio_stats_from_values(
        equity_values,
        exposure_values,
        trades,
        float(config.initial_capital),
        include_monte_carlo=options.include_monte_carlo,
    )
    stats["statistics_ms"] = 0.0
    stats["scaled_execution"] = kind
    stats["scaled_fill"] = fill
    stats["fills"] = [
        {
            "date": labels[item.time_id],
            "symbol": matrix.symbols[item.asset],
            "side": item.side,
            "qty": item.qty,
            "price": item.price,
            "reason": item.reason,
            "shares_after": item.shares_after,
        }
        for item in book.fills
    ]
    stats["execution"] = {
        "scaled_buys": sum(1 for item in book.fills if item.side == "buy"),
        "scaled_sells": sum(1 for item in book.fills if item.side == "sell"),
    }
    return SimResult(
        equity_curve=equity_curve if options.include_curves else [],
        drawdown_curve=drawdown_curve if options.include_curves else [],
        trades=trades if options.include_trades else [],
        per_symbol_stats=BacktestEngine._calc_per_symbol(trades) if options.include_per_symbol_stats else [],
        stats=stats,
    )


def _trades_from_fills(matrix, fills, labels, buy_cost: float, sell_cost: float) -> list[TradeRecord]:
    open_lots: list[list[dict]] = [[] for _ in matrix.symbols]
    trades: list[TradeRecord] = []
    for fill in fills:
        asset = fill.asset
        if fill.side == "buy":
            open_lots[asset].append({
                "qty": fill.qty,
                "price": fill.price,
                "time_id": fill.time_id,
                "reason": fill.reason,
            })
            continue
        remain = fill.qty
        for lot in open_lots[asset]:
            if remain <= 0:
                break
            take = min(int(lot["qty"]), remain)
            if take <= 0:
                continue
            lot["qty"] = int(lot["qty"]) - take
            remain -= take
            trades.append(_trade(
                matrix, asset, labels, int(lot["time_id"]), fill.time_id,
                float(lot["price"]), float(fill.price), take, fill.reason,
                str(lot["reason"]), buy_cost, sell_cost,
            ))
        open_lots[asset] = [lot for lot in open_lots[asset] if int(lot["qty"]) > 0]
    return trades


def _liquidate_remaining(matrix, book, labels, buy_cost: float, sell_cost: float, cash: float) -> tuple[list[TradeRecord], float]:
    """Force-close whatever the sample still holds, matching the portfolio end rule."""
    if book.shares is None or book.shares.shape[0] == 0:
        return [], cash
    last = book.shares.shape[0] - 1
    # Rebuild lots the same way as the trade list, then sell what is left.
    open_lots: list[list[dict]] = [[] for _ in matrix.symbols]
    for fill in book.fills:
        asset = fill.asset
        if fill.side == "buy":
            open_lots[asset].append({"qty": fill.qty, "price": fill.price, "time_id": fill.time_id, "reason": fill.reason})
        else:
            remain = fill.qty
            for lot in open_lots[asset]:
                if remain <= 0:
                    break
                take = min(int(lot["qty"]), remain)
                lot["qty"] = int(lot["qty"]) - take
                remain -= take
            open_lots[asset] = [lot for lot in open_lots[asset] if int(lot["qty"]) > 0]
    trades: list[TradeRecord] = []
    close = np.asarray(matrix.close, dtype=np.float64)
    for asset, lots in enumerate(open_lots):
        if not lots:
            continue
        price = float(close[last, asset])
        if not np.isfinite(price) or price <= 0:
            for t in range(last, -1, -1):
                candidate = float(close[t, asset])
                if np.isfinite(candidate) and candidate > 0:
                    price = candidate
                    break
        if not np.isfinite(price) or price <= 0:
            continue
        for lot in lots:
            qty = int(lot["qty"])
            if qty <= 0:
                continue
            cash += qty * price * (1.0 - sell_cost)
            trades.append(_trade(
                matrix, asset, labels, int(lot["time_id"]), last,
                float(lot["price"]), price, qty, "end", str(lot["reason"]),
                buy_cost, sell_cost,
            ))
    return trades, cash


def _trade(
    matrix, asset: int, labels, entry_time: int, exit_time: int,
    entry_price: float, exit_price: float, qty: int, exit_reason: str, entry_reason: str,
    buy_cost: float, sell_cost: float,
) -> TradeRecord:
    entry_value = qty * entry_price * (1.0 + buy_cost)
    exit_value = qty * exit_price * (1.0 - sell_cost)
    pnl_amount = exit_value - entry_value
    entry_day = _as_date(labels[entry_time])
    exit_day = _as_date(labels[exit_time])
    return TradeRecord(
        symbol=matrix.symbols[asset],
        name=matrix.names[asset] if asset < len(matrix.names) else matrix.symbols[asset],
        entry_date=entry_day,
        exit_date=exit_day,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl_pct=pnl_amount / entry_value if entry_value else 0.0,
        duration=max((exit_day - entry_day).days, 0),
        exit_reason=exit_reason,
        shares=float(qty),
        lots=qty / 100.0,
        entry_value=entry_value,
        exit_value=exit_value,
        pnl_amount=pnl_amount,
        entry_signal_date=labels[entry_time],
        exit_signal_date=labels[exit_time],
        entry_signal_id=entry_reason,
        exit_signal_id=exit_reason if exit_reason not in {"end"} else None,
    )
