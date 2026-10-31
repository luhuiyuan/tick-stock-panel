"""尾盘持股模拟仓的次日早盘卖出计划。

收盘买单成交后记下数量。之后每个交易日 9:30–15:05 用实时原始行情判断。
卖出只走即时单；跌停不挂次日开盘单。策略的自动跟单只允许买入，避免出场信号再卖一次。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

from app.strategy.overnight_exit import (
    EXIT_POLICY,
    STRATEGY_ID,
    MorningView,
    live_decision,
)
from app.strategy import paper

logger = logging.getLogger(__name__)


def _path(data_dir: Path, account_id: str) -> Path:
    return paper._root(data_dir, account_id) / "overnight_exits.json"


def load_plans(data_dir: Path, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> list[dict]:
    path = _path(data_dir, account_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("overnight exit plan load failed: %s", exc)
        return []
    plans = data.get("plans") if isinstance(data, dict) else None
    return list(plans) if isinstance(plans, list) else []


def _save_plans(data_dir: Path, plans: list[dict], account_id: str) -> None:
    from app.services.fs_utils import atomic_write_text
    atomic_write_text(
        _path(data_dir, account_id),
        json.dumps({"plans": plans}, ensure_ascii=False, indent=2),
    )


def note_buy_fill(data_dir: Path, account_id: str, order: dict, day: str) -> None:
    """买入成交后建立卖出计划。调用方已持有 PAPER_LOCK。"""
    if order.get("exit_policy") != EXIT_POLICY or order.get("side") != "buy":
        return
    plans = load_plans(data_dir, account_id)
    if any(item.get("id") == order["id"] for item in plans):
        return
    plans.append({
        "id": order["id"],
        "symbol": order["symbol"],
        "qty": int(order["qty"]),
        "buy_date": day,
        "status": "armed",
        "phase": "morning",
        "deadline_seen": False,
        "session_date": "",
        "reason": None,
    })
    _save_plans(data_dir, plans, account_id)


def _reset_session(plan: dict, today: str) -> None:
    if plan.get("session_date") == today:
        return
    plan["session_date"] = today
    plan["phase"] = "morning"
    plan["deadline_seen"] = False


def _view_from_quote(quote: dict) -> MorningView | None:
    try:
        session_open = float(quote["open"])
        high = float(quote["high"])
        low = float(quote["low"])
        last = float(quote["last"])
        prev = float(quote["prev_close"])
        limit_up = float(quote["limit_up"])
        limit_down = float(quote["limit_down"])
        volume = float(quote.get("volume") or 0)
        amount = float(quote.get("amount") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if min(session_open, last, prev, limit_up, limit_down) <= 0:
        return None
    if not np.isfinite([session_open, high, low, last, prev, limit_up, limit_down]).all():
        return None
    return MorningView(
        session_open=session_open,
        high=high,
        low=low,
        last=last,
        volume=volume,
        amount=amount,
        prev_close=prev,
        limit_up=limit_up,
        limit_down=limit_down,
    )


def _in_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    clock = now.time()
    if clock < datetime.strptime("09:30", "%H:%M").time() or clock > datetime.strptime("15:05", "%H:%M").time():
        return False
    if datetime.strptime("11:30", "%H:%M").time() < clock < datetime.strptime("13:00", "%H:%M").time():
        return False
    return True


def evaluate_account(
    data_dir: Path,
    quotes: dict[str, dict],
    *,
    account_id: str = paper.DEFAULT_ACCOUNT_ID,
    now: datetime,
) -> list[dict]:
    """对已成交的尾盘买入执行早盘卖出。返回成交事件。"""
    if not _in_window(now):
        return []
    today = now.date().isoformat()
    events: list[dict] = []
    with paper.PAPER_LOCK:
        plans = load_plans(data_dir, account_id)
        if not plans:
            return []
        changed = False
        for plan in plans:
            if plan.get("status") != "armed":
                continue
            if str(plan.get("buy_date") or "") >= today:
                continue
            symbol = str(plan.get("symbol") or "")
            quote = quotes.get(symbol)
            if quote is None:
                continue
            _reset_session(plan, today)
            view = _view_from_quote(quote)
            if view is None:
                continue
            limit_up, limit_down = paper.limit_prices(view.prev_close, symbol, "stock")
            view = MorningView(
                session_open=view.session_open,
                high=view.high,
                low=view.low,
                last=view.last,
                volume=view.volume,
                amount=view.amount,
                prev_close=view.prev_close,
                limit_up=limit_up,
                limit_down=limit_down,
            )
            phase, reason = live_decision(
                view,
                now=now,
                phase=str(plan.get("phase") or "morning"),
                deadline_seen=bool(plan.get("deadline_seen")),
            )
            if now.time() >= datetime.strptime("10:00", "%H:%M").time():
                plan["deadline_seen"] = True
            plan["phase"] = phase if phase != "done" else plan.get("phase")
            changed = True
            if phase != "done" or not reason:
                continue
            filled = _sell_plan(data_dir, account_id, plan, view.last, reason, today)
            if filled is None:
                plan["phase"] = "wait"
                continue
            plan["status"] = "done"
            plan["phase"] = "done"
            plan["reason"] = reason
            events.append(filled)
        if changed:
            _save_plans(data_dir, plans, account_id)
    return events


def _sell_plan(
    data_dir: Path,
    account_id: str,
    plan: dict,
    price: float,
    reason: str,
    day: str,
) -> dict | None:
    """调用方已持有 PAPER_LOCK。跌停或滑点后触及跌停则不挂单。"""
    symbol = plan["symbol"]
    held = paper.sell_capacity(data_dir, symbol, account_id)
    if held is None:
        plan["status"] = "done"
        plan["reason"] = "no_shares"
        return None
    available, pending = held
    qty = min(int(plan["qty"]), paper.normalize_qty(available - pending))
    if qty <= 0:
        plan["status"] = "done"
        plan["reason"] = "no_shares"
        return None
    acc = paper.get_account(data_dir, account_id)
    if acc is None:
        return None
    prev = paper._prev_close(data_dir, symbol, "stock", day)
    if prev is not None:
        _up, down = paper.limit_prices(prev, symbol, "stock")
        slipped = paper.apply_slippage(price, "sell", float(acc["slippage_bps"]))
        if slipped <= down + 0.005:
            return None
    order, err = paper.create_order(
        data_dir,
        symbol,
        "sell",
        account_id=account_id,
        qty=qty,
        order_type="market",
        ref_price=price,
        source=f"overnight_exit:{plan['id']}",
    )
    if err or order is None:
        logger.info("overnight exit %s 卖出未下单: %s", symbol, err)
        return None
    order["limit_reject"] = "expire"
    order["reason"] = reason
    paper.save_order(data_dir, order, account_id)
    fill = paper._fill_order(data_dir, order, price, day, account_id)
    if fill is None:
        current = paper.get_order(data_dir, order["id"], account_id)
        if current and current.get("status") == "pending":
            current["status"] = "cancelled"
            current["reason"] = f"{reason}; 未成交，取消以免改挂次日开盘"
            paper.save_order(data_dir, current, account_id)
        return None
    return paper._fill_event(fill, account_id)


def quotes_from_enriched(frame) -> dict[str, dict]:
    """把盘中 enriched 快照转成原始价行情。缺列的行跳过。"""
    required = {"symbol", "raw_close", "raw_high", "raw_low", "limit_up_price", "limit_down_price"}
    if frame is None or getattr(frame, "is_empty", lambda: True)() or not required.issubset(set(frame.columns)):
        return {}
    quotes: dict[str, dict] = {}
    columns = frame.columns
    for row in frame.iter_rows(named=True):
        symbol = row.get("symbol")
        last = row.get("raw_close")
        if not symbol or last is None:
            continue
        close = row.get("close")
        opened = row.get("open")
        raw_open = opened
        if opened is not None and close not in (None, 0) and "close" in columns:
            try:
                raw_open = float(opened) * float(last) / float(close)
            except (TypeError, ValueError, ZeroDivisionError):
                raw_open = opened
        prev = row.get("_prev_close_raw")
        if prev is None and row.get("limit_up_price") not in (None, 0):
            # 涨停价大约是昨收的 1.1/1.2/1.3，不能反推。没有原始昨收时用涨跌停价反算不可靠。
            prev = None
        if prev is None and row.get("prev_close") is not None and close not in (None, 0) and last is not None:
            try:
                prev = float(row["prev_close"]) * float(last) / float(close)
            except (TypeError, ValueError, ZeroDivisionError):
                prev = row.get("prev_close")
        quotes[str(symbol)] = {
            "open": raw_open,
            "high": row.get("raw_high"),
            "low": row.get("raw_low"),
            "last": last,
            "volume": row.get("volume") or 0,
            "amount": row.get("amount") or 0,
            "prev_close": prev,
            "limit_up": row.get("limit_up_price"),
            "limit_down": row.get("limit_down_price"),
        }
    return quotes


def evaluate_enriched(data_dir: Path, frame, *, now: datetime) -> list[dict]:
    quotes = quotes_from_enriched(frame)
    if not quotes:
        return []
    events: list[dict] = []
    for account_id in paper.list_account_ids(data_dir):
        if not any(item.get("status") == "armed" for item in load_plans(data_dir, account_id)):
            continue
        events.extend(evaluate_account(data_dir, quotes, account_id=account_id, now=now))
    return events


def is_overnight_strategy(rule: dict) -> bool:
    return rule.get("match_kind") == "strategy" and rule.get("match_id") == STRATEGY_ID
