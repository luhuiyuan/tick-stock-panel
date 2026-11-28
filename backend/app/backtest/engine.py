"""回测引擎的共享数据加载及 Tivelo 撮合与统计适配。"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pyarrow as pa
from tivelo_backtest.legacy.simulator import (
    LegacySimulator,
)
from tivelo_backtest.legacy.simulator import (
    MatcherConfig as TiveloMatcherConfig,
)
from tivelo_backtest.legacy.simulator import (
    SimResult as SimResult,
)
from tivelo_backtest.legacy.simulator import (
    SimulationOptions as SimulationOptions,
)
from tivelo_backtest.legacy.simulator import (
    TradeRecord as TiveloTradeRecord,
)
from tivelo_backtest.legacy.simulator import (
    _apply_position_policy as _apply_position_policy,
)
from tivelo_backtest.legacy.simulator import (
    _matrix_entry_score as _matrix_entry_score,
)
from tivelo_backtest.legacy.simulator import (
    _resolve_signal_id as _resolve_signal_id,
)

from app.backtest.chan_center import CenterBook, CenterPolicy, validate_center_execution
from app.backtest.matrix import (
    MarketDataMatrix,
    MarketMatrix,
    MatrixCacheProfile,
    load_market_data_matrix_from_daily_panel,
    load_market_data_matrix_from_parquet,
)
from app.backtest.matrix import (
    build_market_matrix as build_market_matrix,
)
from app.backtest.price_basis import BacktestPriceBasis, BackwardAdjustment
from app.config import settings
from app.enriched_generation import EnrichedGenerationUnavailableError
from app.parquet import parquet_glob, scan_enriched_parquet
from app.tickflow.repository import KlineRepository

if TYPE_CHECKING:
    from app.backtest.snapshot import SnapshotDailySource

logger = logging.getLogger(__name__)


def history_load_start(engine, start: date, asset_type: str, bars: int, *, minimum_days: int = 120) -> date:
    """保留既有最小预取范围; 长假或分区稀疏时按真实交易日向前取足。"""
    fallback = start - timedelta(days=max(minimum_days, int(bars * 1.6)))
    data_dir = getattr(getattr(getattr(engine, "repo", None), "store", None), "data_dir", None)
    if bars <= 1 or data_dir is None:
        return fallback
    from app.services.mining_preflight import enriched_partition_dates

    dates = enriched_partition_dates(data_dir, asset_type, end=start - timedelta(days=1))
    return min(fallback, dates[max(0, len(dates) - bars)]) if dates else fallback






# ================================================================
# 数据结构
# ================================================================











# ================================================================
# PanelCache — 避免重复 scan_parquet + compute_all
# ================================================================

class _CacheEntry:
    __slots__ = ("df", "ts")

    def __init__(self, df: pl.DataFrame, ts: float):
        self.df = df
        self.ts = ts


class _InFlight:
    """同 key 正在计算的占位: leader 算完通过 done 唤醒所有跟随者复用结果。"""

    __slots__ = ("done", "df", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.df: pl.DataFrame | None = None
        self.error: BaseException | None = None


class PanelCache:
    """LRU + TTL 数据面板缓存。"""

    def __init__(self, max_size: int = 2, ttl_seconds: int = 180):
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        # 跨请求单例, SSE 回测在各自 daemon 线程并发访问 OrderedDict。
        # 无锁的 move_to_end/del/popitem check-then-act 会抛 "OrderedDict mutated"。
        # 用实例锁守护所有 OrderedDict 变更; compute_fn (重扫盘) 放锁外避免串行化。
        self._lock = threading.Lock()
        # single-flight: 同 key 只让一个线程 compute, 其余等其结果复用。
        # 否则优化器/walk-forward 的 max_workers 个线程冷启动同时 miss, 会并行加载 N 份同一面板。
        self._inflight: dict[str, _InFlight] = {}
        # 轻量遥测: 累加真实扫盘耗时与命中/复用次数, 用于量化 IO 占比 (是否值得进一步优化)。
        # compute_seconds 只计 leader 的实际 compute_fn 耗时, follower 复用不计 —— 反映真实 IO。
        self._compute_seconds = 0.0
        self._compute_count = 0   # 实际扫盘次数
        self._hit_count = 0       # 缓存命中 (未扫盘) 次数
        self._reuse_count = 0     # single-flight 跟随者复用次数

    def get_or_compute(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        columns: list[str] | None,
        compute_fn,
        asset_type: str = "stock",
        generation: str | None = None,
    ) -> pl.DataFrame:
        key = self._make_key(
            symbols, start, end, columns, asset_type, generation
        )
        now = time.monotonic()

        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                if now - entry.ts < self._ttl:
                    self._cache.move_to_end(key)
                    self._hit_count += 1
                    return entry.df
                del self._cache[key]  # 过期, 丢弃后重算
            # single-flight: 同 key 若已有线程在算, 登记为跟随者; 否则本线程当 leader。
            flight = self._inflight.get(key)
            leader = flight is None
            if leader:
                flight = _InFlight()
                self._inflight[key] = flight

        if not leader:
            # 跟随者: 等 leader 算完直接复用, 不重复 compute (消除缓存踩踏)。
            flight.done.wait()
            with self._lock:
                self._reuse_count += 1
            if flight.error is not None:
                raise flight.error
            return flight.df

        # leader: compute 放锁外 (不同 key 仍可并发, 保留原设计优点)。
        t_compute = time.perf_counter()
        try:
            df = compute_fn(symbols, start, end, columns, asset_type)
        except BaseException as e:
            # 失败不缓存: 摘除 inflight 让后续线程重试, 并把异常透传给已在等的跟随者。
            with self._lock:
                self._compute_seconds += time.perf_counter() - t_compute  # 失败也花了 IO, 计入
                self._compute_count += 1
                self._inflight.pop(key, None)
            flight.error = e
            flight.done.set()
            raise
        with self._lock:
            self._compute_seconds += time.perf_counter() - t_compute
            self._compute_count += 1
            self._cache[key] = _CacheEntry(df=df, ts=now)
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            self._inflight.pop(key, None)
        flight.df = df
        flight.done.set()
        return df

    def stats(self) -> dict:
        """遥测快照: 累计扫盘耗时/次数与命中/复用次数。首尾快照取差即区间内 IO 开销。"""
        with self._lock:
            return {
                "compute_seconds": round(self._compute_seconds, 4),
                "compute_count": self._compute_count,
                "hit_count": self._hit_count,
                "reuse_count": self._reuse_count,
            }

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    @staticmethod
    def _make_key(
        symbols: list[str] | None,
        start: date,
        end: date,
        columns: list[str] | None,
        asset_type: str = "stock",
        generation: str | None = None,
    ) -> str:
        if symbols is None:
            h = "all"
        else:
            h = hashlib.md5(",".join(sorted(symbols)).encode()).hexdigest()[:12]
        cols = "all" if columns is None else hashlib.md5(",".join(sorted(columns)).encode()).hexdigest()[:8]
        return f"{asset_type}:{generation or 'unmanaged'}:{h}:{start}:{end}:{cols}"


# 等待进行中 enriched 发布的上限与轮询间隔。孤儿标记由 get_enriched_generation
# 在读取时直接自愈, 因此这里等到的 EnrichedGenerationUnavailableError 意味着
# 发布方确实存活 —— 对回测/优化这类长任务, 有界等待优于立即失败。仅用于
# worker 任务路径 (矩阵加载), 实时热路径不得调用 data_generation_await。
_GENERATION_WAIT_TIMEOUT_S = 300.0
_GENERATION_POLL_S = 1.0


# ================================================================
# BacktestEngine
# ================================================================

@dataclass
class MatcherConfig(TiveloMatcherConfig):
    cooldown_loss_streak: int | None = None
    cooldown_days: int | None = None
    center_policy: CenterPolicy | None = None


@dataclass
class TradeRecord(TiveloTradeRecord):
    center_reference: dict | None = None


class ProjectSimulator(LegacySimulator):
    """Tivelo handles ordinary orders; fork-specific fills and ledgers stay local."""

    @staticmethod
    def _resolve_exit_prices(matrix, config):
        prices = LegacySimulator._resolve_exit_prices(matrix, config)
        override = getattr(matrix, "exit_price", None)
        if override is None:
            return prices
        if matrix.price_scale is not None:
            raise ValueError("自定义退出价暂不支持动态价格比例")
        return np.where(np.isfinite(override), override, prices)

    def simulate_independent_market_matrix(self, matrix, *args, **kwargs):
        if getattr(matrix, "center_features", None) is not None:
            raise ValueError("中枢震荡依赖实际持仓状态, 仅支持仓位模式")
        return super().simulate_independent_market_matrix(matrix, *args, **kwargs)

    def _simulate_portfolio_matrix(
        self,
        matrix: MarketMatrix,
        config: MatcherConfig,
        progress_cb: "Callable[[dict], None] | None",
        cancel_event: "threading.Event | None",
        options: SimulationOptions | None = None,
    ) -> SimResult:
        custom = (getattr(matrix, "center_features", None) is not None
                  or getattr(matrix, "exit_price", None) is not None
                  or (config.cooldown_loss_streak and config.cooldown_days))
        if not custom:
            return super()._simulate_portfolio_matrix(matrix, config, progress_cb, cancel_event, options)
        if matrix.price_scale is not None:
            raise ValueError("自定义退出价、中枢持仓及亏损冷却暂不支持动态价格比例")
        options = options or SimulationOptions()
        time_count, asset_count = matrix.shape
        center_features = getattr(matrix, "center_features", None)
        center_book = None
        if center_features is not None:
            validate_center_execution(config)
            center_book = CenterBook(center_features, policy=config.center_policy or CenterPolicy())
        entry_prices = self._resolve_entry_prices(matrix, config)
        exit_prices = matrix.open if config.exit_fill == "open_t+1" else matrix.close
        if getattr(matrix, "exit_price", None) is not None:
            exit_prices = np.where(np.isfinite(matrix.exit_price), matrix.exit_price, exit_prices)
        buy_cost_pct = config.buy_cost_pct()
        sell_cost_pct = config.sell_cost_pct()
        cash = float(config.initial_capital)
        peak = cash
        max_positions = max(int(config.max_positions), 0)
        max_exposure_pct = min(max(float(config.max_exposure_pct), 0.0), 1.0)
        positions: dict[int, dict] = {}
        last_close = np.full(asset_count, np.nan, dtype=np.float64)
        trades: list[TradeRecord] = []
        equity_curve: list[dict] = []
        drawdown_curve: list[dict] = []
        equity_values: list[float] = []
        exposure_values: list[float] = []
        execution_stats = {
            "buy_invalid_price": 0,
            "buy_suspended": 0,
            "buy_limit_up": 0,
            "buy_no_slot": 0,
            "buy_cash": 0,
            "buy_lot_size": 0,
            "buy_same_day_reentry": 0,
            "buy_exposure": 0,
            "buy_score_filter": 0,
            "sell_invalid_price": 0,
            "sell_suspended": 0,
            "sell_limit_down": 0,
            "pending_exit": 0,
        }

        # Source fork: sell-first blocks T through T + cooldown_days - 1.
        consec_losses = 0
        cooldown_until = -1
        execution_stats["buy_cooldown"] = 0

        minute_cache: dict = {}
        if config.minute_fill:
            trigger_times, trigger_assets = np.nonzero(matrix.entry | matrix.exit)
            trigger_dates = {matrix.timestamp_labels[int(t)][:10] for t in trigger_times}
            trigger_symbols = {matrix.symbols[int(a)] for a in trigger_assets}
            if trigger_dates and trigger_symbols:
                loaded = self._minute_data(
                    list(trigger_symbols), trigger_dates, config.asset_type,
                )
                minute_cache = {key: value for key, value in loaded.items() if value is not None and len(value) > 0}

        def _count(key: str) -> None:
            execution_stats[key] = execution_stats.get(key, 0) + 1

        def _valid_price(value) -> bool:
            return bool(np.isfinite(value) and value > 0)

        def _signal_id(code: int, signal_ids: tuple[str, ...]) -> str | None:
            return signal_ids[code] if 0 <= code < len(signal_ids) else None

        def _signal_date(signal_time: int, fallback: str) -> str:
            return matrix.timestamp_labels[signal_time][:10] if signal_time >= 0 else fallback

        def _market_value() -> float:
            total = 0.0
            for asset, pos in positions.items():
                mark = last_close[asset]
                if not _valid_price(mark):
                    mark = pos["entry_price"]
                total += pos["shares"] * mark
            return total

        def _refill_price(time_id: int, asset_id: int, side: str, daily_price: float) -> float:
            if not config.minute_fill or not minute_cache:
                return daily_price
            key = (matrix.symbols[asset_id], matrix.timestamp_labels[time_id][:10])
            minute_rows = minute_cache.get(key)
            if minute_rows is None:
                return daily_price
            reference = float(matrix.reference_price[time_id, asset_id])
            precise = self._resolve_minute_fill(
                minute_rows,
                reference if _valid_price(reference) else None,
                side,
            )
            return precise if precise is not None else daily_price

        def _minute_trigger_price(time_id: int, asset_id: int) -> float | None:
            if not config.minute_fill or not minute_cache:
                return None
            key = (matrix.symbols[asset_id], matrix.timestamp_labels[time_id][:10])
            minute_rows = minute_cache.get(key)
            if minute_rows is None:
                return None
            reference = float(matrix.reference_price[time_id, asset_id])
            return self._resolve_minute_exit_trigger(
                minute_rows,
                reference if _valid_price(reference) else None,
            )

        def _one_price_limit(time_id: int, asset_id: int, direction: str) -> bool:
            if not matrix.tradable[time_id, asset_id]:
                return False
            prices = (
                float(matrix.open[time_id, asset_id]),
                float(matrix.high[time_id, asset_id]),
                float(matrix.low[time_id, asset_id]),
                float(matrix.close[time_id, asset_id]),
            )
            if not all(_valid_price(value) for value in prices):
                return False
            same_price = max(prices) - min(prices) <= max(abs(prices[3]) * 1e-4, 0.01)
            flag = matrix.limit_up_locked if direction == "up" else matrix.limit_down_locked
            return bool(flag[time_id, asset_id]) and same_price

        def _intraday_entry(time_id: int, asset_id: int) -> bool:
            """该格建仓是否发生在盘中: 分钟策略的逐格价格覆盖 (entry_price) 有限,
            或 minute_fill 细化可用。粗粒度: 只要有分钟数据即视为盘中 — 方向保守
            (多计入峰值 → 早退出), 不会像漏计那样高估收益。"""
            if matrix.entry_price is not None and bool(np.isfinite(matrix.entry_price[time_id, asset_id])):
                return True
            return config.minute_fill and bool(minute_cache)

        def _can_buy(time_id: int, asset_id: int) -> tuple[bool, str]:
            if not matrix.tradable[time_id, asset_id]:
                return False, "buy_suspended"
            if center_book is not None and not _valid_price(matrix.volume[time_id, asset_id]):
                return False, "buy_suspended"
            if not _valid_price(entry_prices[time_id, asset_id]):
                return False, "buy_invalid_price"
            if config.entry_fill == "close_t" and bool(matrix.limit_up_locked[time_id, asset_id]):
                # close_t 按信号日收盘价成交: 收盘封板(含一字)在收盘价的买队排队
                # 现实中排不进, 一律拦截 — 旧实现只拦一字板, 尾盘封板股按涨停价
                # "成交"导致回测收益虚高。open_t+1 按次日开盘成交, 不受此限。
                return False, "buy_limit_up"
            if _one_price_limit(time_id, asset_id, "up"):
                return False, "buy_limit_up"
            return True, ""

        def _can_sell(time_id: int, asset_id: int, override: float | None = None) -> tuple[bool, str]:
            if not matrix.tradable[time_id, asset_id]:
                return False, "sell_suspended"
            if center_book is not None and not _valid_price(matrix.volume[time_id, asset_id]):
                return False, "sell_suspended"
            price = override if override is not None else exit_prices[time_id, asset_id]
            if not _valid_price(price):
                return False, "sell_invalid_price"
            if (
                override is None
                and config.exit_fill == "close_t"
                and bool(matrix.limit_down_locked[time_id, asset_id])
            ):
                # close_t 按当日收盘价卖出: 收盘封死跌停(含一字)的卖队排队现实中
                # 排不出去, 拦截并经 pending_exit 递延到下一可卖日 — 与独立路径/
                # legacy 路径同口径 (本路径此前只拦一字板, 尾盘封板股按跌停价
                # "卖出"导致亏损被低估)。
                return False, "sell_limit_down"
            if _one_price_limit(time_id, asset_id, "down"):
                return False, "sell_limit_down"
            return True, ""

        def _mark_pending(
            asset_id: int,
            reason: str,
            signal_date: str,
            signal_id: str | None = None,
            next_open: bool = False,
        ) -> None:
            pos = positions[asset_id]
            if not pos.get("pending_exit_reason"):
                pos["pending_exit_reason"] = reason
                pos["pending_exit_signal_date"] = signal_date
                pos["pending_exit_signal_id"] = signal_id
                _count("pending_exit")
            if next_open:
                pos["pending_exit_next_open"] = True
            pos["blocked_exit_days"] += 1

        def _sell(
            time_id: int,
            asset_id: int,
            reason: str,
            signal_date: str,
            sold_today: set[int],
            override: float | None = None,
        ) -> None:
            nonlocal cash, consec_losses, cooldown_until
            pos = positions.pop(asset_id)
            exit_price = float(override) if override is not None else _refill_price(
                time_id, asset_id, "sell", float(exit_prices[time_id, asset_id])
            )
            exit_value = pos["shares"] * exit_price * (1 - sell_cost_pct)
            cash += exit_value
            pnl_amount = exit_value - pos["entry_value"]
            pnl_pct = pnl_amount / pos["entry_value"] if pos["entry_value"] > 0 else 0.0
            if config.cooldown_loss_streak is not None and config.cooldown_loss_streak > 0 and config.cooldown_days is not None:
                if pnl_amount < 0:
                    consec_losses += 1
                    if consec_losses >= config.cooldown_loss_streak:
                        cooldown_until = time_id + config.cooldown_days
                        consec_losses = 0
                else:
                    consec_losses = 0
            sold_today.add(asset_id)
            trades.append(TradeRecord(
                symbol=matrix.symbols[asset_id],
                name=matrix.names[asset_id],
                entry_date=pos["entry_date"],
                exit_date=matrix.timestamp_labels[time_id][:10],
                entry_price=round(float(pos["entry_price"]), 4),
                exit_price=round(exit_price, 4),
                pnl_pct=round(float(pnl_pct), 6),
                duration=int(pos["hold_days"]),
                exit_reason=reason,
                shares=round(float(pos["shares"]), 4),
                lots=round(float(pos["lots"]), 2),
                position_pct=round(float(pos["position_pct"]), 6),
                entry_value=round(float(pos["entry_value"]), 2),
                exit_value=round(float(exit_value), 2),
                pnl_amount=round(float(pnl_amount), 2),
                entry_score=round(float(pos["entry_score"]), 2),
                entry_signal_date=pos["entry_signal_date"],
                exit_signal_date=signal_date,
                blocked_exit_days=int(pos["blocked_exit_days"]),
                entry_signal_id=pos["entry_signal_id"],
                exit_signal_id=(
                    pos.get("pending_exit_signal_id")
                    or _signal_id(int(matrix.exit_signal_code[time_id, asset_id]), matrix.exit_signal_ids)
                ) if reason == "signal" or (center_book is not None and reason == "center_stop") else None,
                center_reference=pos.get("center_reference"),
            ))
            if center_book is not None:
                center_book.on_exit(asset_id, center_features.days[time_id])

        def _try_sell(
            time_id: int,
            asset_id: int,
            reason: str,
            signal_date: str,
            sold_today: set[int],
            override: float | None = None,
        ) -> bool:
            signal_id = (
                positions[asset_id].get("pending_exit_signal_id")
                or _signal_id(int(matrix.exit_signal_code[time_id, asset_id]), matrix.exit_signal_ids)
                if reason == "signal" or (center_book is not None and reason == "center_stop") else None
            )
            minute_trigger = config.exit_fill == "signal_next_minute" and reason == "signal"
            if minute_trigger and override is None:
                pos = positions[asset_id]
                if pos.get("pending_exit_next_open"):
                    override = float(matrix.open[time_id, asset_id])
                else:
                    override = _minute_trigger_price(time_id, asset_id)
                    if override is None:
                        _mark_pending(asset_id, reason, signal_date, signal_id, next_open=True)
                        _count("sell_minute_trigger_fallback")
                        return False
            ok, blocked = _can_sell(time_id, asset_id, override)
            if not ok:
                _mark_pending(
                    asset_id,
                    reason,
                    signal_date,
                    signal_id,
                    next_open=minute_trigger,
                )
                _count(blocked)
                return False
            _sell(time_id, asset_id, reason, signal_date, sold_today, override)
            return True

        for time_id, date_label in enumerate(matrix.timestamp_labels):
            date_text = date_label[:10]
            center_buys, center_sells = {}, {}
            if center_book is not None and time_id > 0:
                center_buys, center_sells = center_book.on_close(
                    time_id - 1, matrix.close[time_id - 1], positions,
                )
            if time_id % 20 == 0:
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("回测被用户取消 (第 %d/%d 天)", time_id, time_count)
                    break
                if progress_cb is not None:
                    try:
                        progress_cb({
                            "day": time_id + 1,
                            "total": time_count,
                            "date": date_text,
                            "equity": round(cash + _market_value(), 2),
                        })
                    except Exception:
                        pass

            sold_today: set[int] = set()
            for pos in positions.values():
                pos["hold_days"] += 1

            for asset_id in list(positions):
                pos = positions.get(asset_id)
                if pos is None or pos.get("pending_exit_reason") or pos["entry_date"] == date_text:
                    continue
                if not matrix.tradable[time_id, asset_id] or pos["entry_price"] <= 0:
                    continue
                open_price = float(matrix.open[time_id, asset_id])
                low_price = float(matrix.low[time_id, asset_id])
                high_price = float(matrix.high[time_id, asset_id])
                entry_price = float(pos["entry_price"])
                peak_price = float(pos["max_high"])
                risk_lines: list[tuple[float, str]] = []
                if config.stop_loss_pct is not None:
                    risk_lines.append((entry_price * (1 - abs(config.stop_loss_pct)), "stop_loss"))
                if config.trailing_stop_pct is not None:
                    risk_lines.append((peak_price * (1 - abs(config.trailing_stop_pct)), "trailing_stop"))
                activate = config.trailing_take_profit_activate_pct
                drawdown = config.trailing_take_profit_drawdown_pct
                if activate is not None and drawdown is not None and peak_price > entry_price:
                    if peak_price / entry_price - 1 >= abs(float(activate)):
                        risk_lines.append((peak_price * (1 - abs(float(drawdown))), "trailing_take_profit"))
                valid_lines = [(line, reason) for line, reason in risk_lines if _valid_price(line)]
                if valid_lines:
                    stop_price, reason = max(valid_lines, key=lambda item: item[0])
                    override = None
                    if _valid_price(open_price) and open_price <= stop_price:
                        override = open_price
                    elif _valid_price(low_price) and low_price <= stop_price:
                        override = stop_price
                    if override is not None:
                        _try_sell(time_id, asset_id, reason, date_text, sold_today, override)
                        continue
                if config.take_profit_pct is not None:
                    take_profit = entry_price * (1 + abs(float(config.take_profit_pct)))
                    if _valid_price(open_price) and open_price >= take_profit:
                        _try_sell(time_id, asset_id, "take_profit", date_text, sold_today, open_price)
                    elif _valid_price(high_price) and high_price >= take_profit:
                        _try_sell(time_id, asset_id, "take_profit", date_text, sold_today, take_profit)

            for asset_id in list(positions):
                pos = positions.get(asset_id)
                if pos is None:
                    continue
                reason = ""
                signal_date = date_text
                if pos.get("pending_exit_reason"):
                    reason = str(pos["pending_exit_reason"])
                    signal_date = str(pos.get("pending_exit_signal_date") or date_text)
                elif center_book is not None and asset_id in center_sells:
                    order = center_sells[asset_id]
                    reason = order.reason
                    signal_date = matrix.timestamp_labels[time_id - 1][:10]
                    pos["pending_exit_signal_id"] = order.signal_id
                    if order.exit_evidence is not None:
                        pos["center_reference"]["exit_evidence"] = order.exit_evidence
                elif matrix.exit[time_id, asset_id]:
                    reason = "signal"
                    signal_date = _signal_date(int(matrix.exit_signal_time[time_id, asset_id]), date_text)
                elif config.max_hold_days is not None and pos["hold_days"] >= config.max_hold_days:
                    reason = "max_hold"
                elif time_id == time_count - 1 and center_book is None:
                    reason = "end"
                if reason:
                    _try_sell(time_id, asset_id, reason, signal_date, sold_today)

            in_cooldown = (
                config.cooldown_loss_streak is not None
                and config.cooldown_loss_streak > 0
                and config.cooldown_days is not None
                and time_id < cooldown_until
            )
            if in_cooldown and time_id < time_count - 1 and max_positions > 0:
                execution_stats["buy_cooldown"] += int(np.count_nonzero(matrix.entry[time_id]))
            if time_id < time_count - 1 and max_positions > 0 and not in_cooldown:
                candidates: list[tuple[int, float]] = []
                for asset_id in np.flatnonzero(matrix.entry[time_id]):
                    asset = int(asset_id)
                    if center_book is not None and asset not in center_buys:
                        continue
                    if asset in positions:
                        continue
                    if asset in sold_today:
                        _count("buy_same_day_reentry")
                        continue
                    ok, blocked = _can_buy(time_id, asset)
                    if not ok:
                        _count(blocked)
                        continue
                    if center_book is not None:
                        blocked = center_book.policy.reject_entry(
                            center_buys[asset], float(entry_prices[time_id, asset]),
                        )
                        if blocked:
                            _count(blocked)
                            continue
                    score = _matrix_entry_score(matrix, time_id, asset)
                    if center_book is not None and center_buys[asset].rank_score is not None:
                        score = center_buys[asset].rank_score
                    if config.score_min is not None and score < config.score_min:
                        _count("buy_score_filter")
                        continue
                    if config.score_max is not None and score > config.score_max:
                        _count("buy_score_filter")
                        continue
                    candidates.append((asset, score))
                candidates.sort(key=lambda item: item[1], reverse=True)
                slots = max_positions - len(positions)
                if slots <= 0:
                    execution_stats["buy_no_slot"] += len(candidates)
                elif candidates:
                    scan_candidates = center_book is not None and (
                        center_book.policy.entry_gate or center_book.policy.risk_sizing
                    )
                    selected = candidates if scan_candidates else candidates[:slots]
                    allocation_slots = min(len(selected), slots)
                    market_value_before = _market_value()
                    equity_before = cash + market_value_before
                    target_value = equity_before * max_exposure_pct / max_positions
                    exposure_capacity = equity_before * max_exposure_pct - market_value_before
                    if equity_before <= 0 or exposure_capacity <= 0 or max_exposure_pct <= 0:
                        execution_stats["buy_exposure"] += len(selected)
                    else:
                        weights = np.repeat(1 / allocation_slots, len(selected))
                        if config.position_sizing == "score_weight" and not (
                            center_book is not None and center_book.policy.risk_sizing
                        ):
                            raw_weights = np.array([max(item[1], 0.0) for item in selected])
                            weight_sum = raw_weights[:allocation_slots].sum()
                            if weight_sum > 0:
                                weights = raw_weights / weight_sum
                        policy_scale = 1.0
                        if config.position_sizing == "policy" and config.position_policy_compute is not None:
                            policy_returns: dict[str, np.ndarray] = {}
                            for asset_id, _score in selected:
                                # 窗口截止到入场信号日 (与评分同时刻) — open_t+1 成交日
                                # 的 K 线对决策是未来数据, 不得进入。
                                source_time = int(matrix.entry_signal_time[time_id, int(asset_id)])
                                if source_time < 0:
                                    source_time = time_id
                                col = matrix.close[max(0, source_time - 59):source_time + 1, int(asset_id)].astype(np.float64)
                                finite = col[np.isfinite(col)]
                                if finite.size >= 2:
                                    sym = str(matrix.symbols[int(asset_id)])
                                    policy_returns[sym] = np.diff(finite) / finite[:-1]
                            weights, policy_scale = _apply_position_policy(
                                config,
                                [(str(matrix.symbols[int(a)]), float(s)) for a, s in selected],
                                policy_returns,
                                trades,
                                cash,
                                equity_before,
                                peak,
                                len(positions),
                                execution_stats,
                            )
                        total_budget = min(cash, exposure_capacity, target_value * allocation_slots)
                        if policy_scale < 1.0:
                            total_budget = max(total_budget * policy_scale, 0.0)
                        for (asset_id, entry_score), weight in zip(selected, weights):
                            if len(positions) >= max_positions:
                                _count("buy_no_slot")
                                break
                            market_value = _market_value()
                            equity = cash + market_value
                            capacity = equity * max_exposure_pct - market_value
                            if center_book is not None and center_book.policy.risk_sizing:
                                capacity = equity_before * max_exposure_pct - (
                                    market_value_before + sum(
                                        p["entry_value"] for p in positions.values()
                                        if p["entry_date"] == date_text
                                    )
                                )
                            allocation = min(total_budget * float(weight), target_value, cash, capacity)
                            if center_book is not None and center_book.policy.risk_sizing:
                                allocation = min(target_value, equity_before * center_book.policy.position_cap,
                                                 cash, capacity)
                            if allocation <= 0:
                                _count("buy_exposure")
                                continue
                            entry_price = _refill_price(
                                time_id, asset_id, "buy", float(entry_prices[time_id, asset_id])
                            )
                            shares = np.floor(allocation / (entry_price * (1 + buy_cost_pct)) / 100) * 100
                            risk_per_share = None
                            sizing_risk = None
                            if center_book is not None and center_book.policy.risk_sizing:
                                risk_per_share = (entry_price * (1 + buy_cost_pct)
                                                  - (center_buys[asset_id].risk_low or center_buys[asset_id].low)
                                                  * (1 - sell_cost_pct))
                                if not np.isfinite(risk_per_share) or risk_per_share <= 0:
                                    _count("buy_center_risk")
                                    continue
                                sizing_risk = risk_per_share
                                policy = center_book.policy
                                if policy.risk_floor_pct or policy.atr_risk_multiple:
                                    floor_distance = max(entry_price * policy.risk_floor_pct,
                                                         center_buys[asset_id].atr * policy.atr_risk_multiple)
                                    floor_loss = (floor_distance + entry_price * buy_cost_pct
                                                  + max(0, entry_price - floor_distance) * sell_cost_pct)
                                    sizing_risk = max(sizing_risk, floor_loss)
                                risk_shares = np.floor(
                                    equity_before * center_book.policy.risk_fraction / sizing_risk / 100
                                ) * 100
                                shares = min(shares, risk_shares)
                            entry_value = shares * entry_price * (1 + buy_cost_pct)
                            if shares <= 0:
                                _count("buy_lot_size")
                                continue
                            if entry_value > cash + 1e-6:
                                _count("buy_cash")
                                continue
                            if entry_value > capacity + 1e-6:
                                _count("buy_exposure")
                                continue
                            cash -= entry_value
                            positions[asset_id] = {
                                "entry_date": date_text,
                                "entry_signal_date": _signal_date(
                                    int(matrix.entry_signal_time[time_id, asset_id]), date_text
                                ),
                                "entry_signal_id": _signal_id(
                                    int(matrix.entry_signal_code[time_id, asset_id]), matrix.entry_signal_ids
                                ),
                                "entry_price": entry_price,
                                "entry_value": entry_value,
                                "shares": shares,
                                "lots": shares / 100,
                                "position_pct": entry_value / equity_before if equity_before > 0 else 0.0,
                                "entry_score": entry_score,
                                "max_high": entry_price,
                                "hold_days": 0,
                                "pending_exit_reason": None,
                                "pending_exit_signal_date": None,
                                "pending_exit_signal_id": None,
                                "pending_exit_next_open": False,
                                "blocked_exit_days": 0,
                            }
                            if center_book is not None:
                                order = center_buys[asset_id]
                                center_book.on_fill(asset_id, order)
                                positions[asset_id]["entry_signal_id"] = order.signal_id
                                positions[asset_id]["center_reference"] = {
                                    "low": order.low, "high": order.high,
                                    "phase": "123" if order.signal_id.startswith("center_bsp_") else "range",
                                }
                                if order.evidence is not None:
                                    positions[asset_id]["center_reference"].update({
                                        "risk_low": order.risk_low,
                                        "lesson24": dict(vars(order.evidence)),
                                        "evidence_tier": "daily_pen_auxiliary",
                                    })
                                if risk_per_share is not None:
                                    positions[asset_id]["center_reference"].update({
                                        "entry_equity": equity_before,
                                        "risk_budget_amount": equity_before * center_book.policy.risk_fraction,
                                        "planned_risk_amount": shares * risk_per_share,
                                    })
                                    if center_book.policy.risk_floor_pct or center_book.policy.atr_risk_multiple:
                                        positions[asset_id]["center_reference"]["sizing_risk_amount"] = shares * sizing_risk

            for asset_id, pos in positions.items():
                high_price = float(matrix.high[time_id, asset_id])
                # close_t 且收盘价成交的当日建仓: 当日 high 在建仓之前, 不进移动
                # 止损峰值; 盘中成交 (override/minute_fill) 与 open 成交仍计入。
                if (
                    _valid_price(high_price)
                    and not (
                        config.entry_fill == "close_t"
                        and not _intraday_entry(time_id, asset_id)
                        and pos["entry_date"] == date_text
                    )
                ):
                    pos["max_high"] = max(float(pos["max_high"]), high_price)
            valid_closes = np.isfinite(matrix.close[time_id]) & (matrix.close[time_id] > 0)
            last_close[valid_closes] = matrix.close[time_id, valid_closes]

            market_value = _market_value()
            equity = cash + market_value
            peak = max(peak, equity)
            drawdown = (equity - peak) / peak if peak > 0 else 0.0
            exposure = market_value / equity if equity > 0 else 0.0
            equity_value = round(float(equity), 2)
            exposure_value = round(float(exposure), 4)
            equity_values.append(equity_value)
            exposure_values.append(exposure_value)
            if options.include_curves:
                equity_curve.append({
                    "date": date_text,
                    "value": equity_value,
                    "cash": round(float(cash), 2),
                    "positions": len(positions),
                    "exposure": exposure_value,
                })
                drawdown_curve.append({
                    "date": date_text,
                    "value": round(float(drawdown), 4),
                })

        # 期末仍持仓 (含被封板/停牌递延的): 不再只计入 pending_exit_positions 而
        # 静默丢弃交易记录 — 按该标的最后一个价格有效日强制落账,
        # blocked_exit_days 保留"曾受阻"痕迹 (与独立路径的期末兜底同口径,
        # n_trades/win_rate 与权益口径不再分裂)。先取 pending 计数再清仓。
        # 中枢账本的持仓留到期末按市值记账，不在这里强制平仓。
        pending_exit_count = sum(1 for pos in positions.values() if pos.get("pending_exit_reason"))
        if center_book is None:
            for asset_id in list(positions):
                last_time = None
                for t in range(time_count - 1, -1, -1):
                    if _valid_price(exit_prices[t, asset_id]):
                        last_time = t
                        break
                if last_time is None:
                    continue
                _sell(
                    last_time, asset_id,
                    str(positions[asset_id].get("pending_exit_reason") or "end"),
                    matrix.timestamp_labels[last_time][:10],
                    set(),
                )

        statistics_started = time.perf_counter()
        stats = self._calc_portfolio_stats_from_values(
            equity_values,
            exposure_values,
            trades,
            config.initial_capital,
            include_monte_carlo=options.include_monte_carlo,
        )
        stats["statistics_ms"] = round(
            (time.perf_counter() - statistics_started) * 1000,
            1,
        )
        stats["execution"] = execution_stats
        stats["pending_exit_positions"] = pending_exit_count
        if center_book is not None:
            stats["open_positions"] = len(positions)
            stats["end_policy"] = "mark_open_positions"
            stats["center_rules"] = center_book.policy.snapshot()
            stats["open_position_details"] = [
                {"symbol": matrix.symbols[a], "name": matrix.names[a],
                 **{key: pos[key] for key in (
                     "entry_date", "entry_signal_date", "entry_signal_id", "entry_price",
                     "entry_value", "shares", "position_pct", "center_reference",
                 )}}
                for a, pos in positions.items()
            ]
        stats["market_matrix_shape"] = [time_count, asset_count]
        stats["market_matrix_bytes"] = matrix.nbytes
        return SimResult(
            equity_curve=equity_curve if options.include_curves else [],
            drawdown_curve=drawdown_curve if options.include_curves else [],
            trades=trades if options.include_trades else [],
            per_symbol_stats=(
                self._calc_per_symbol(trades)
                if options.include_per_symbol_stats
                else []
            ),
            stats=stats,
        )


class BacktestEngine:
    """回测引擎 — 数据加载 + 撮合模拟 + 统计计算。"""

    def __init__(self, repo: KlineRepository, *, snapshot: SnapshotDailySource | None = None) -> None:
        self.repo = repo
        self.snapshot = snapshot
        self._cache = PanelCache()
        self._evidence = None
        self._entry_data = None

    # ── 数据加载 ──────────────────────────────────────

    def data_generation(self, asset_type: str = "stock") -> str | None:
        from app.services.daily_health import assert_usable

        root = getattr(getattr(self.repo, "store", None), "data_dir", None)
        if root is not None:
            assert_usable(root, asset_type)
        loader = getattr(self.repo, "get_matrix_data_generation", None)
        return loader(asset_type) if callable(loader) else None

    def data_generation_await(
        self,
        asset_type: str = "stock",
        *,
        cancel_event: threading.Event | None = None,
        timeout_s: float = _GENERATION_WAIT_TIMEOUT_S,
    ) -> str | None:
        """获取 generation; 发布进行中时在超时窗口内轮询, 可被取消事件打断。"""
        deadline = time.monotonic() + timeout_s
        from app.services.daily_health import DailyIntegrityError

        while True:
            try:
                return self.data_generation(asset_type)
            except DailyIntegrityError:
                raise
            except EnrichedGenerationUnavailableError:
                if cancel_event is not None and cancel_event.is_set():
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_GENERATION_POLL_S)

    def assert_data_generation(
        self,
        asset_type: str,
        expected: str | None,
    ) -> None:
        if expected is None:
            return
        current = self.data_generation(asset_type)
        if current != expected:
            raise EnrichedGenerationUnavailableError(
                "enriched data changed while the snapshot was being read"
            )

    def _range_cache_generation(self, asset_type: str, end: date, expected: str | None) -> str | None:
        loader = getattr(self.repo, "get_enriched_generation_snapshot", None)
        if not callable(loader) or expected is None:
            return expected
        snapshot = loader(asset_type)
        if snapshot.generation != expected:
            raise EnrichedGenerationUnavailableError(
                "enriched data changed while the snapshot was being read"
            )
        return snapshot.through(end)

    def load_panel(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        columns: list[str] | None = None,
        asset_type: str = "stock",
        *,
        expected_generation: str | None = None,
    ) -> pl.DataFrame:
        """加载 enriched 数据面板，带缓存。asset_type='etf' 时读 ETF enriched。"""
        # 快照世代路径也给一次重试 (issue #517): 盘中刷新每轮换新 generation,
        # 读后校验失败时重读「现役世代」而不是死等已作废的旧世代。
        attempts = 2
        expected = expected_generation
        for attempt in range(attempts):
            generation = (
                expected
                if expected is not None
                else self.data_generation(asset_type)
            )
            try:
                # Cache identity follows the consumed prefix. The global generation
                # still guards the read, including in-progress/partial publications.
                cache_generation = self._range_cache_generation(asset_type, end, generation)
                panel = self._cache.get_or_compute(
                    symbols,
                    start,
                    end,
                    columns,
                    self._load_panel_inner,
                    asset_type=asset_type,
                    generation=cache_generation,
                )
                self.assert_data_generation(asset_type, generation)
            except EnrichedGenerationUnavailableError:
                if attempt + 1 >= attempts:
                    raise
                expected = None
                continue
            return panel
        raise EnrichedGenerationUnavailableError(
            "unable to read a stable enriched data snapshot"
        )

    def load_panel_for_backtest(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        feature_plan,
        asset_type: str = "stock",
        *,
        progress_cb: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        czsc_max_workers: int = 1,
        evidence=None,
        price_basis: BacktestPriceBasis = "qfq",
        raw_execution_quotes: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        """按解析后的依赖加载窄基础列并计算回测所需特征。"""
        columns = sorted(feature_plan.base_columns)
        generation = None
        if self.snapshot is not None:
            df = self.snapshot.read_panel(self.repo, symbols, start, end, columns)
        else:
            generation = self.data_generation_await(asset_type) if price_basis == "hfq" else None
            df = self.load_panel(
                symbols,
                start,
                end,
                columns=columns,
                asset_type=asset_type,
                **({"expected_generation": generation} if generation is not None else {}),
            )
        if df.is_empty():
            return df
        if raw_execution_quotes is not None:
            from app.backtest.dynamic_daily import attach_exchange_quotes, current_replay

            df = attach_exchange_quotes(df, raw_execution_quotes)
            if price_basis == "dynamic":
                df = current_replay().continuous_panel(df, self.repo.store.data_dir, asset_type)
        if self.snapshot is None and price_basis == "hfq":
            df = BackwardAdjustment(self.repo.store.data_dir, asset_type).panel(df)
            self.assert_data_generation(asset_type, generation)

        from app.backtest.fundamentals import (
            load_fundamental_snapshot,
        )

        fundamental_names = sorted(
            getattr(feature_plan, "fundamental_columns", frozenset())
            or frozenset()
        )
        financials = load_fundamental_snapshot(
            self.repo.store.data_dir if self.repo is not None else None,
        ) if fundamental_names else None
        instruments = (
            self.repo.get_instruments_asset(asset_type)
            if self.repo is not None
            else pl.DataFrame()
        )
        historical_shares = (
            self.repo.get_historical_shares()
            if asset_type == "stock" and self.repo is not None and not instruments.is_empty()
            else None
        )
        if evidence is not None:
            from app.factors.ext_factors import attach_ext_columns

            # 扩展时序值也要固定在本次原始快照内；检查期间不重新读取磁盘。
            inputs = self._entry_data
            locked_inputs = inputs is not None and (inputs.version is not None or inputs.configs())
            df = attach_ext_columns(
                df, include_snapshot=False, data_dir=self.repo.store.data_dir,
                **({"inputs": inputs} if locked_inputs else {}),
            )

        def prepare(raw):
            return self._prepare_panel_features(
                raw, feature_plan, instruments, historical_shares, financials,
                attach_extensions=evidence is None,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                czsc_max_workers=czsc_max_workers,
            )

        if evidence is not None:
            evidence.capture_panel(df, prepare, {
                "instruments": instruments, "historical_shares": historical_shares,
                "financial_announcements": financials,
            })
        return prepare(df)

    @staticmethod
    def _prepare_panel_features(
        df, feature_plan, instruments, historical_shares, financials, *, attach_extensions=True,
        progress_cb=None, cancel_event=None, czsc_max_workers: int = 1,
    ):
        from app.backtest.fundamentals import attach_fundamental_factors
        from app.indicators.pipeline import (
            compute_indicators,
            compute_limit_signals,
            compute_signals,
        )

        fundamental_names = sorted(getattr(feature_plan, "fundamental_columns", ()) or ())
        if fundamental_names:
            df = attach_fundamental_factors(df, financials, fundamental_names)
        matrix_native = feature_plan.execution_backend == "matrix_native"
        if not matrix_native:
            df = compute_indicators(df, needed=set(feature_plan.indicator_columns))
            df = compute_signals(
                df,
                needed=set(feature_plan.signal_columns),
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                czsc_max_workers=czsc_max_workers,
                **({"attach_extensions": False} if not attach_extensions else {}),
            )
        if not instruments.is_empty():
            df = compute_limit_signals(
                df,
                instruments,
                needed={"signal_limit_up", "signal_limit_down"}
                if matrix_native
                else set(feature_plan.signal_columns),
                historical_shares=historical_shares,
            )
            join_cols = ["symbol"] if "symbol" in instruments.columns else []
            join_cols.extend(
                col for col in sorted(feature_plan.instrument_columns)
                if col in instruments.columns and col not in df.columns
            )
            if len(join_cols) > 1:
                df = df.join(
                    instruments.select(join_cols).unique(subset=["symbol"]),
                    on="symbol",
                    how="left",
                )

        required_columns = set(feature_plan.base_columns) | set(feature_plan.instrument_columns)
        required_columns |= set(feature_plan.signal_columns)
        if not matrix_native:
            required_columns |= set(feature_plan.indicator_columns)
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            raise ValueError(f"回测字段依赖未生成: {sorted(missing_columns)}")

        float_cols = [c for c in df.columns if df[c].dtype.is_float()]
        if float_cols:
            df = df.with_columns([
                pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite())
                .then(None)
                .otherwise(pl.col(c))
                .alias(c)
                for c in float_cols
            ])
        return df

    def load_market_data_matrix_for_backtest(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        feature_plan,
        asset_type: str = "stock",
        *,
        cache_profile: MatrixCacheProfile | None = None,
        coverage_start: date | None = None,
        coverage_end: date | None = None,
        expected_generation: str | None = None,
        cancel_event: threading.Event | None = None,
        price_basis: BacktestPriceBasis = "qfq",
    ) -> MarketDataMatrix:
        """Load a matrix-native backtest directly from projected parquet batches."""
        if feature_plan.execution_backend != "matrix_native":
            raise ValueError("direct market matrix loading requires matrix_native backend")
        from app.tickflow.repository import enriched_dirname

        parquet_root = self.repo.store.data_dir / enriched_dirname(asset_type)
        instruments = self.repo.get_instruments_asset(asset_type)
        if self._evidence is not None:
            self._evidence.record_frame("instruments", instruments)
        field_columns = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        # 扩展时序数值字段不进 parquet 解析 (enriched 只落 14 存储列), 矩阵构建后
        # 按 (日期, 标的) 对齐现场注入 — 与财务因子 attach 同构。快照模式/字符串
        # 字段/未注册名在此拦截 (快照回看历史 = 未来函数)。
        from app.factors.ext_factors import numeric_timeseries_ext_columns

        ext_wanted = frozenset(c for c in field_columns if c.startswith("ext_"))
        if ext_wanted:
            invalid_ext = ext_wanted - numeric_timeseries_ext_columns(
                self.repo.store.data_dir, inputs=self._entry_data,
            )
            if invalid_ext:
                raise ValueError(
                    f"扩展字段不可用于矩阵回测 (字段已失效，或不是时序模式的数值字段): "
                    f"{sorted(invalid_ext)}。请在策略设置中移除或替换相关评分因子、叠加条件；"
                    "若策略源码直接引用该字段，请同步修改源码，或恢复对应的时序扩展数据。"
                )
            field_columns = field_columns - ext_wanted
        if self.snapshot is not None:
            panel = self.snapshot.read_panel(self.repo, symbols, start, end, field_columns)
            market = load_market_data_matrix_from_daily_panel(
                panel, start, end, field_columns=field_columns, instruments=instruments,
                cancel_event=cancel_event,
            )
            return self._attach_market_fields(market, feature_plan, ext_wanted)

        cache_root = (
            self.repo.store.data_dir / ".backtest_matrix_cache"
            if settings.backtest_matrix_disk_cache_enabled
            else None
        )
        cache_fields = (
            cache_profile.field_columns
            if cache_profile is not None
            else frozenset(field_columns)
        )
        # 缓存档包含其他策略的依赖; 所有扩展列均须留到当前策略的现场注入阶段,
        # 否则无关策略残留的旧扩展字段也会被当作 parquet 必需列, 拖垮整个共享缓存。
        cache_fields = frozenset(name for name in cache_fields if not name.startswith("ext_"))
        cache_max_bytes = (
            cache_profile.max_disk_bytes
            if cache_profile is not None
            else settings.backtest_matrix_cache_max_mb * 1024 * 1024
        )
        source_generation = (
            expected_generation
            if expected_generation is not None
            else self.data_generation_await(asset_type, cancel_event=cancel_event)
        )
        # 快照世代路径也给第二次机会 (issue #517): 循环内撞换代时下方 except
        # 已用 data_generation_await 刷新世代, 这里只需不再限定单次尝试。
        attempts = 2
        for attempt in range(attempts):
            try:
                cache_generation = self._range_cache_generation(
                    asset_type, coverage_end or end, source_generation,
                )
                market = load_market_data_matrix_from_parquet(
                    parquet_root,
                    start,
                    end,
                    field_columns=field_columns,
                    symbols=symbols,
                    instruments=instruments,
                    cache_root=cache_root,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    cache_field_columns=cache_fields,
                    cache_max_bytes=cache_max_bytes,
                    profile_generation=(
                        cache_profile.generation if cache_profile is not None else "request"
                    ),
                    source_generation=cache_generation,
                    cancel_event=cancel_event,
                )
                if price_basis == "hfq":
                    market = BackwardAdjustment(self.repo.store.data_dir, asset_type).matrix(market)
                self.assert_data_generation(asset_type, source_generation)
                return self._attach_market_fields(market, feature_plan, ext_wanted)
            except EnrichedGenerationUnavailableError:
                if attempt + 1 >= attempts:
                    raise
                source_generation = self.data_generation_await(
                    asset_type, cancel_event=cancel_event
                )
            except pa.ArrowException as exc:
                raise ValueError(f"direct market matrix parquet scan failed: {exc}") from exc
        raise EnrichedGenerationUnavailableError(
            "unable to read a stable enriched matrix snapshot"
        )

    def _attach_market_fields(self, market, feature_plan, ext_wanted):
        from app.backtest.fundamentals import attach_matrix_fundamental_fields

        fundamental_names = sorted(
            getattr(feature_plan, "fundamental_columns", frozenset())
            or frozenset()
        )
        if fundamental_names:
            # 财务因子不落 enriched 存储: 矩阵加载后按公告日门控附加字段。
            market = attach_matrix_fundamental_fields(
                market,
                self.repo.store.data_dir if self.repo is not None else None,
                fundamental_names,
                evidence=self._evidence,
            )
        if ext_wanted:
            from app.factors.ext_factors import attach_matrix_ext_fields

            market = attach_matrix_ext_fields(
                market,
                self.repo.store.data_dir if self.repo is not None else None,
                ext_wanted,
                inputs=self._entry_data,
            )
        return market

    def cache_stats(self) -> dict:
        """暴露 PanelCache 遥测快照 (扫盘耗时/次数/命中/复用), 供上层量化 IO 占比。"""
        return self._cache.stats()

    def clear_panel_cache(self) -> None:
        self._cache.invalidate()

    def _load_panel_inner(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        columns: list[str] | None = None,
        asset_type: str = "stock",
    ) -> pl.DataFrame:
        t0 = time.perf_counter()

        # 近期区间优先复用 repository 的预计算 enriched 历史缓存 (仅 stock: 该缓存为股票专用)。
        try:
            if columns is None and asset_type == "stock" and self.repo is not None and hasattr(self.repo, "get_enriched_range"):
                cached = self.repo.get_enriched_range(start, end, symbols=symbols, columns=columns)
                if cached is not None and not cached.is_empty():
                    elapsed = (time.perf_counter() - t0) * 1000
                    logger.info("load_panel(cache): %.0fms, %d rows, %d columns", elapsed, len(cached), len(cached.columns))
                    return cached
        except Exception as e:  # noqa: BLE001
            logger.debug("backtest load panel cache miss: %s", e)

        from app.tickflow.repository import enriched_dirname
        enriched_glob = parquet_glob(self.repo.store.data_dir / enriched_dirname(asset_type))

        try:
            lf = scan_enriched_parquet(enriched_glob)
            if symbols is not None:
                lf = lf.filter(pl.col("symbol").is_in(symbols))
            if columns is not None:
                available = set(lf.collect_schema().names())
                selected = [c for c in columns if c in available]
                if "symbol" not in selected and "symbol" in available:
                    selected.insert(0, "symbol")
                if "date" not in selected and "date" in available:
                    selected.insert(1, "date")
                lf = lf.select(selected)
            df = (
                lf.filter(
                    (pl.col("date") >= start)
                    & (pl.col("date") <= end)
                )
                .sort(["symbol", "date"])
                .collect(streaming=True)
            )
        except Exception as e:
            logger.warning("backtest load panel failed: %s", e)
            return pl.DataFrame()

        if df.is_empty():
            return df

        if columns is not None:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("load_panel: %.0fms, %d rows, %d columns", elapsed, len(df), len(df.columns))
            return df

        from app.indicators.pipeline import compute_all
        # 按 asset_type 取维表: ETF 回测须用 ETF 维表, 否则名称 JOIN 失败(全 null)、
        # 涨停信号算在错误的 instruments 上。
        instruments = self.repo.get_instruments_asset(asset_type)
        df = compute_all(df, instruments=instruments)
        if not instruments.is_empty() and "name" not in df.columns:
            inst_cols = [c for c in ["symbol", "name"] if c in instruments.columns]
            if len(inst_cols) == 2:
                df = df.join(
                    instruments.select(inst_cols).unique(subset=["symbol"]),
                    on="symbol",
                    how="left",
                )

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("load_panel: %.0fms, %d rows", elapsed, len(df))
        return df

    # ── 撮合模拟 ──────────────────────────────────────

    # 分钟K cache 存储的数值列及固定顺序 (_resolve_minute_fill 按此顺序整数索引)。
    _MINUTE_NUMERIC_COLS = ["open", "high", "low", "close", "volume", "amount"]

    @staticmethod
    def _load_minute_for_fills(
        repo,
        symbols: list[str],
        dates_needed: set,
        asset_type: str,
        *, evidence=None,
    ) -> dict:
        """按触发日加载分钟K, 返回 {(symbol, date_str): float64 2D ndarray}。

        dates_needed: 需要分钟数据的日期集合 (set of date strings "YYYY-MM-DD")

        按触发日分批读取对应分区文件 (get_minute_by_dates), 而非扫描整个区间
        (get_minute_range)。内存与回测区间长度解耦, 只随触发日数量增长 ——
        触发日稀疏时避免读取区间内大量无关日期导致爆内存。

        cache 值为 float64 紧凑 2D 数组 (列顺序见 _MINUTE_NUMERIC_COLS), 而非
        完整 DataFrame, 避免每条记录携带 polars 元数据开销导致内存膨胀。
        """
        if not symbols or not dates_needed:
            return {}
        from datetime import date as _date
        sorted_date_strs = sorted(dates_needed)
        date_objs = [_date.fromisoformat(s) for s in sorted_date_strs]

        cache: dict = {}
        # 分批读取: 每批 50 个交易日, 处理完拼进 cache, 避免单批过大。
        BATCH = 50
        numeric_cols = BacktestEngine._MINUTE_NUMERIC_COLS
        from app.services.minute_adjust import MinutePriceBasisError

        reader = getattr(repo, "get_minute_for_backtest", None) or repo.get_minute_by_dates
        for i in range(0, len(date_objs), BATCH):
            batch = date_objs[i:i + BATCH]
            try:
                df = reader(symbols, batch, asset_type=asset_type)
            except MinutePriceBasisError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("minute fill data load failed (batch %d-%d): %s", i, i + len(batch), e)
                continue
            if df.is_empty():
                continue
            if evidence is not None:
                evidence.record_frame(f"minute_fill/{asset_type}/{batch[0]}/{batch[-1]}", df)
            # 按 (symbol, 日期) 分组, 每组转紧凑 float64 数组存入 cache
            df = df.sort(["symbol", "datetime"]).with_columns(
                pl.col("datetime").dt.strftime("%Y-%m-%d").alias("_d_str")
            )
            for sub in df.partition_by(["symbol", "_d_str"], as_dict=False):
                if sub.is_empty():
                    continue
                sym = sub["symbol"][0]
                d_str = sub["_d_str"][0]
                cols = [c for c in numeric_cols if c in sub.columns]
                cache[(sym, d_str)] = sub.select(
                    [pl.col(c).cast(pl.Float64) for c in cols]
                ).to_numpy()
        return cache

    def _execution(self) -> LegacySimulator:
        # The host owns minute I/O and evidence; the simulator sees only arrays.
        return ProjectSimulator(
            minute_loader=lambda symbols, dates, asset_type: self._load_minute_for_fills(
                self.repo,
                symbols,
                dates,
                asset_type,
                evidence=self._evidence,
            )
        )

    def simulate(
        self,
        panel: pl.DataFrame,
        entries: pl.Series | None,
        exits: pl.Series | None,
        config: MatcherConfig,
        entry_signal_ids: list[str] | None = None,
        exit_signal_ids: list[str] | None = None,
    ) -> SimResult:
        return self._execution().simulate(
            panel, entries, exits, config, entry_signal_ids, exit_signal_ids
        )

    def simulate_independent_candidates(
        self,
        panel: pl.DataFrame,
        entries: pl.Series | None,
        exits: pl.Series | None,
        config: MatcherConfig,
        progress_cb: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        entry_signal_ids: list[str] | None = None,
        exit_signal_ids: list[str] | None = None,
    ) -> SimResult:
        return self._execution().simulate_independent_candidates(
            panel,
            entries,
            exits,
            config,
            progress_cb,
            cancel_event,
            entry_signal_ids,
            exit_signal_ids,
        )

    _resolve_entry_prices = staticmethod(LegacySimulator._resolve_entry_prices)

    def _simulate_independent_matrix(
        self,
        matrix: MarketMatrix,
        raw_candidates: int,
        config: MatcherConfig,
        progress_cb: Callable[[dict], None] | None,
        cancel_event: threading.Event | None,
        options: SimulationOptions | None = None,
    ) -> SimResult:
        return self._execution()._simulate_independent_matrix(
            matrix, raw_candidates, config, progress_cb, cancel_event, options
        )

    def simulate_independent_market_matrix(
        self,
        matrix: MarketMatrix,
        raw_candidates: int,
        config: MatcherConfig,
        progress_cb: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        options: SimulationOptions | None = None,
    ) -> SimResult:
        return self._execution().simulate_independent_market_matrix(
            matrix, raw_candidates, config, progress_cb, cancel_event, options
        )

    def simulate_independent_candidates_legacy(
        self,
        panel: pl.DataFrame,
        entries: pl.Series | None,
        exits: pl.Series | None,
        config: MatcherConfig,
        progress_cb: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        entry_signal_ids: list[str] | None = None,
        exit_signal_ids: list[str] | None = None,
    ) -> SimResult:
        return self._execution().simulate_independent_candidates_legacy(
            panel,
            entries,
            exits,
            config,
            progress_cb,
            cancel_event,
            entry_signal_ids,
            exit_signal_ids,
        )

    _resolve_minute_fill = staticmethod(LegacySimulator._resolve_minute_fill)

    _resolve_minute_exit_trigger = staticmethod(LegacySimulator._resolve_minute_exit_trigger)

    def simulate_portfolio(
        self,
        panel: pl.DataFrame,
        entries: pl.Series | None,
        exits: pl.Series | None,
        config: MatcherConfig,
        progress_cb: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        entry_signal_ids: list[str] | None = None,
        exit_signal_ids: list[str] | None = None,
    ) -> SimResult:
        return self._execution().simulate_portfolio(
            panel,
            entries,
            exits,
            config,
            progress_cb,
            cancel_event,
            entry_signal_ids,
            exit_signal_ids,
        )

    def simulate_market_matrix(
        self,
        matrix: MarketMatrix,
        config: MatcherConfig,
        progress_cb: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        options: SimulationOptions | None = None,
    ) -> SimResult:
        return self._execution().simulate_market_matrix(
            matrix, config, progress_cb, cancel_event, options
        )

    def _simulate_portfolio_matrix(
        self,
        matrix: MarketMatrix,
        config: MatcherConfig,
        progress_cb: Callable[[dict], None] | None,
        cancel_event: threading.Event | None,
        options: SimulationOptions | None = None,
    ) -> SimResult:
        return self._execution()._simulate_portfolio_matrix(
            matrix, config, progress_cb, cancel_event, options
        )

    def simulate_portfolio_legacy(
        self,
        panel: pl.DataFrame,
        entries: pl.Series | None,
        exits: pl.Series | None,
        config: MatcherConfig,
        progress_cb: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        entry_signal_ids: list[str] | None = None,
        exit_signal_ids: list[str] | None = None,
    ) -> SimResult:
        return self._execution().simulate_portfolio_legacy(
            panel,
            entries,
            exits,
            config,
            progress_cb,
            cancel_event,
            entry_signal_ids,
            exit_signal_ids,
        )

    _reconcile_terminal_curve = staticmethod(LegacySimulator._reconcile_terminal_curve)

    _build_curves = staticmethod(LegacySimulator._build_curves)

    _sortino_ratio = staticmethod(LegacySimulator._sortino_ratio)

    _mc_drawdown_percentiles = staticmethod(LegacySimulator._mc_drawdown_percentiles)

    _per_trade_block = staticmethod(LegacySimulator._per_trade_block)

    _calc_stats = staticmethod(LegacySimulator._calc_stats)

    _calc_per_symbol = staticmethod(LegacySimulator._calc_per_symbol)

    _calc_independent_candidate_result = staticmethod(
        LegacySimulator._calc_independent_candidate_result
    )

    _calc_portfolio_stats = staticmethod(LegacySimulator._calc_portfolio_stats)

    _calc_portfolio_stats_from_values = staticmethod(
        LegacySimulator._calc_portfolio_stats_from_values
    )

    _date_str = staticmethod(LegacySimulator._date_str)

    _empty_result = staticmethod(LegacySimulator._empty_result)

    cross_section_rank = staticmethod(LegacySimulator.cross_section_rank)

    cross_section_qcut = staticmethod(LegacySimulator.cross_section_qcut)
