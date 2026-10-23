"""Minute book for A-share base-position T.

Grid and turtle stay on the daily book. These two strategies replay local
1-minute bars with the rules in ``swing_rules``:

- ``base_t_macd``: TruthHun day-wing MACD turnaround
- ``base_t_band``: stockquant equal-qty band

The first session only buys the base. T+1 means that base cannot be sold
the same day. From the next session, sells consume yesterday's lots only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl

from app.backtest.engine import BacktestEngine, SimResult, TradeRecord
from app.backtest.swing_rules import replay_intraday_day


@dataclass
class _Lot:
    day_id: int
    qty: int
    price: float
    reason: str
    when: date


@dataclass
class _SymbolBook:
    base: int = 0
    lots: list[_Lot] = field(default_factory=list)
    last_price: float = 0.0

    @property
    def shares(self) -> int:
        return sum(lot.qty for lot in self.lots)

    def sellable(self, day_id: int) -> int:
        return sum(lot.qty for lot in self.lots if lot.day_id < day_id)


def _chunks(symbols: list[str], size: int) -> list[list[str]]:
    return [symbols[i:i + size] for i in range(0, len(symbols), size)]


def _hhmm(frame: pl.DataFrame) -> np.ndarray:
    clock = frame["datetime"].dt
    return (clock.hour().cast(pl.Int64) * 100 + clock.minute().cast(pl.Int64)).to_numpy()


def _discover_symbols(repo, day: date) -> list[str]:
    path = repo.store.data_dir / "kline_minute" / f"date={day.isoformat()}" / "part.parquet"
    if not path.exists():
        return []
    frame = pl.scan_parquet(path).select("symbol").unique().collect(engine="streaming")
    return sorted(str(item) for item in frame["symbol"].to_list())


def simulate_base_t(
    repo,
    *,
    kind: str,
    symbols: list[str] | None,
    start: date,
    end: date,
    params: dict | None,
    initial_capital: float,
    buy_cost: float,
    sell_cost: float,
    max_positions: int,
    max_exposure: float,
    include_curves: bool,
    include_trades: bool,
    include_per_symbol: bool,
    include_monte_carlo: bool,
    progress_cb=None,
    cancel_event=None,
) -> SimResult:
    """Replay minute partitions one day at a time and return a portfolio result."""
    rule = "macd" if kind == "base_t_macd" else "band"
    if kind not in {"base_t_macd", "base_t_band"}:
        raise ValueError(f"unknown base-t execution {kind!r}")
    days = [day for day in repo.list_minute_dates(start, end, "stock") if start <= day <= end]
    if not days:
        empty = BacktestEngine._empty_result() if hasattr(BacktestEngine, "_empty_result") else SimResult([], [], [], [], {"error": "回测区间内无分钟K数据"})
        if not getattr(empty, "stats", None):
            empty = SimResult([], [], [], [], {})
        empty.stats["error"] = "回测区间内无分钟K数据, 请先扩展分钟K历史"
        empty.stats["scaled_execution"] = kind
        return empty
    universe = list(symbols or [])
    if not universe:
        universe = _discover_symbols(repo, days[0])
    if not universe:
        result = SimResult([], [], [], [], {"error": "分钟K里没有可回放的股票"})
        result.stats["scaled_execution"] = kind
        return result

    books = {symbol: _SymbolBook() for symbol in universe}
    cash = float(initial_capital)
    equity_values: list[float] = []
    exposure_values: list[float] = []
    equity_curve: list[dict] = []
    drawdown_curve: list[dict] = []
    all_orders: list[dict] = []
    peak = float(initial_capital)
    merged = dict(params or {})
    base_shares = int(merged.get("base_shares") or 0)

    def _equity(mark: dict[str, float]) -> tuple[float, float]:
        market_value = 0.0
        for symbol, book in books.items():
            if book.shares <= 0:
                continue
            price = mark.get(symbol) or book.last_price
            if price > 0:
                market_value += book.shares * price
        return cash + market_value, market_value

    for day_id, day in enumerate(days):
        if cancel_event is not None and cancel_event.is_set():
            break
        if progress_cb is not None:
            progress_cb({"day": day_id + 1, "total": len(days), "date": day.isoformat()})
        mark: dict[str, float] = {}
        for chunk in _chunks(universe, 200):
            frame = repo.get_minute_by_dates(chunk, [day], "stock")
            if frame is None or frame.is_empty():
                continue
            for part in frame.sort(["symbol", "datetime"]).partition_by("symbol", maintain_order=True):
                symbol = str(part["symbol"][0])
                book = books[symbol]
                last_close = float(part["close"][-1])
                if np.isfinite(last_close) and last_close > 0:
                    book.last_price = last_close
                    mark[symbol] = last_close
                if book.base <= 0:
                    if base_shares < 100:
                        continue
                    if sum(1 for item in books.values() if item.base > 0) >= int(max_positions):
                        continue
                    price = float(part["close"][0])
                    if not np.isfinite(price) or price <= 0:
                        continue
                    qty = int(base_shares) // 100 * 100
                    cost = qty * price * (1.0 + buy_cost)
                    _, market_value = _equity(mark)
                    equity_now = cash + market_value
                    if qty < 100 or cash < cost or equity_now <= 0:
                        continue
                    if (market_value + qty * price) / equity_now > float(max_exposure) + 1e-9:
                        continue
                    cash -= cost
                    book.base = qty
                    book.lots.append(_Lot(day_id, qty, price, "base", day))
                    all_orders.append({
                        "date": day, "symbol": symbol, "side": "buy", "qty": qty,
                        "price": price, "reason": "base", "shares_after": qty,
                    })
                    continue
                sellable = book.sellable(day_id)

                def _accept(side: str, qty: int, price: float, _book=book, _day_id=day_id, _day=day, _symbol=symbol) -> bool:
                    nonlocal cash
                    if side == "buy":
                        cost = qty * price * (1.0 + buy_cost)
                        _, market_value = _equity(mark)
                        equity_now = cash + market_value
                        if cash < cost or equity_now <= 0:
                            return False
                        if (market_value + qty * price) / equity_now > float(max_exposure) + 1e-9:
                            return False
                        cash -= cost
                        _book.lots.append(_Lot(_day_id, qty, price, "t", _day))
                    else:
                        if qty > _book.sellable(_day_id):
                            return False
                        left = qty
                        for lot in _book.lots:
                            if left <= 0:
                                break
                            if lot.day_id >= _day_id or lot.qty <= 0:
                                continue
                            take = min(lot.qty, left)
                            lot.qty -= take
                            left -= take
                        if left > 0:
                            return False
                        _book.lots = [lot for lot in _book.lots if lot.qty > 0]
                        cash += qty * price * (1.0 - sell_cost)
                    all_orders.append({
                        "date": _day, "symbol": _symbol, "side": side, "qty": qty,
                        "price": price, "reason": "pending", "shares_after": _book.shares,
                    })
                    return True

                produced = replay_intraday_day(
                    part["open"].to_numpy(),
                    part["high"].to_numpy(),
                    part["low"].to_numpy(),
                    part["close"].to_numpy(),
                    _hhmm(part),
                    kind=rule,
                    base=book.base,
                    sellable=sellable,
                    params=merged,
                    accept=_accept,
                )
                # ``replay`` already committed through accept; copy the reason
                # onto the orders that accept appended as "pending".
                pending = [item for item in all_orders if item["reason"] == "pending" and item["symbol"] == symbol and item["date"] == day]
                for order, produced_order in zip(pending, produced, strict=False):
                    order["reason"] = produced_order["reason"]
                    order["hhmm"] = produced_order["hhmm"]
                    order["shares_after"] = produced_order["shares_after"]

        equity, market_value = _equity(mark)
        peak = max(peak, equity)
        exposure = market_value / equity if equity > 0 else 0.0
        equity_values.append(equity)
        exposure_values.append(exposure)
        if include_curves:
            equity_curve.append({
                "date": day.isoformat(),
                "value": round(equity, 2),
                "cash": round(cash, 2),
                "positions": sum(1 for book in books.values() if book.shares > 0),
                "exposure": round(exposure, 4),
            })
            drawdown_curve.append({
                "date": day.isoformat(),
                "value": round(equity / peak - 1.0 if peak > 0 else 0.0, 4),
            })

    trades = _pair_trades(books, all_orders, buy_cost, sell_cost, days[-1] if days else end, cash)
    cash = _pair_trades.cash  # type: ignore[attr-defined]
    if equity_values:
        equity_values[-1] = cash
        if include_curves and equity_curve:
            equity_curve[-1]["value"] = round(cash, 2)
            equity_curve[-1]["cash"] = round(cash, 2)
            equity_curve[-1]["positions"] = 0
            equity_curve[-1]["exposure"] = 0.0

    stats = BacktestEngine._calc_portfolio_stats_from_values(
        equity_values or [float(initial_capital)],
        exposure_values or [0.0],
        trades,
        float(initial_capital),
        include_monte_carlo=include_monte_carlo,
    )
    stats["statistics_ms"] = 0.0
    stats["scaled_execution"] = kind
    stats["fills"] = [
        {
            "date": item["date"].isoformat(),
            "symbol": item["symbol"],
            "side": item["side"],
            "qty": item["qty"],
            "price": item["price"],
            "reason": item["reason"],
            "shares_after": item.get("shares_after"),
            "hhmm": item.get("hhmm"),
        }
        for item in all_orders
    ]
    stats["execution"] = {
        "scaled_buys": sum(1 for item in all_orders if item["side"] == "buy"),
        "scaled_sells": sum(1 for item in all_orders if item["side"] == "sell"),
        "replayed_days": len(days),
    }
    return SimResult(
        equity_curve=equity_curve if include_curves else [],
        drawdown_curve=drawdown_curve if include_curves else [],
        trades=trades if include_trades else [],
        per_symbol_stats=BacktestEngine._calc_per_symbol(trades) if include_per_symbol else [],
        stats=stats,
    )


def _pair_trades(books, orders, buy_cost: float, sell_cost: float, last_day: date, cash: float) -> list[TradeRecord]:
    """FIFO round trips, then sell whatever base is still open on the last close."""
    open_lots: dict[str, list[dict]] = {}
    trades: list[TradeRecord] = []
    for order in orders:
        symbol = order["symbol"]
        lots = open_lots.setdefault(symbol, [])
        if order["side"] == "buy":
            lots.append({
                "qty": order["qty"], "price": order["price"], "date": order["date"], "reason": order["reason"],
            })
            continue
        remain = int(order["qty"])
        for lot in lots:
            if remain <= 0:
                break
            take = min(int(lot["qty"]), remain)
            if take <= 0:
                continue
            lot["qty"] -= take
            remain -= take
            trades.append(_trade(symbol, lot, order["date"], order["price"], take, order["reason"], buy_cost, sell_cost))
        open_lots[symbol] = [lot for lot in lots if lot["qty"] > 0]

    end_cash = float(cash)
    for symbol, book in books.items():
        if book.shares <= 0 or book.last_price <= 0:
            continue
        for lot in open_lots.get(symbol, []):
            qty = int(lot["qty"])
            if qty <= 0:
                continue
            end_cash += qty * book.last_price * (1.0 - sell_cost)
            trades.append(_trade(
                symbol, lot, last_day, book.last_price, qty, "end", buy_cost, sell_cost,
            ))
    _pair_trades.cash = end_cash  # type: ignore[attr-defined]
    return trades


def _trade(symbol: str, lot: dict, exit_day: date, exit_price: float, qty: int, reason: str, buy_cost: float, sell_cost: float) -> TradeRecord:
    entry_price = float(lot["price"])
    entry_value = qty * entry_price * (1.0 + buy_cost)
    exit_value = qty * exit_price * (1.0 - sell_cost)
    pnl_amount = exit_value - entry_value
    entry_day = lot["date"]
    return TradeRecord(
        symbol=symbol,
        name=symbol,
        entry_date=entry_day,
        exit_date=exit_day,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl_pct=pnl_amount / entry_value if entry_value else 0.0,
        duration=max((exit_day - entry_day).days, 0),
        exit_reason="end" if reason == "end" else "signal",
        shares=float(qty),
        lots=qty / 100.0,
        entry_value=entry_value,
        exit_value=exit_value,
        pnl_amount=pnl_amount,
        entry_signal_date=entry_day.isoformat(),
        exit_signal_date=exit_day.isoformat(),
        entry_signal_id=str(lot.get("reason") or ""),
        exit_signal_id=None if reason == "end" else reason,
    )
