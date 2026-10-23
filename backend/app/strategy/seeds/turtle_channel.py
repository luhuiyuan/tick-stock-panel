"""海龟通道 — 移植 KHunter trading/turtle_strategy.py 的短线预设。

10 日高点突破买入，5 日低点或 2 倍 ATR 止损卖出。盈利达到 2% 且再走 0.5 ATR
才加仓，加仓量是当前股数的 1/(次数+2)，最多 4 次。ATR 用简单移动平均。
"""

import numpy as np

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix
from app.backtest.swing_rules import TURTLE_DEFAULTS, replay_turtle

META = {
    "id": "turtle_channel",
    "name": "海龟通道",
    "description": "10/5 唐奇安通道，0.5 ATR 加仓最多 4 次，2 ATR 或跌破通道离场",
    "tags": ["海龟", "趋势", "加仓"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "scaled_execution": "turtle",
    "basic_filter": {
        "price_min": 5,
        "price_max": 200,
        "amount_min": 1e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [
        {"id": "n_entry", "label": "入场通道天数", "type": "int", "default": 10, "min": 5, "max": 60, "step": 1},
        {"id": "n_exit", "label": "出场通道天数", "type": "int", "default": 5, "min": 2, "max": 40, "step": 1},
        {"id": "atr_period", "label": "ATR 天数", "type": "int", "default": 10, "min": 5, "max": 60, "step": 1},
        {"id": "add_atr", "label": "加仓 ATR 倍数", "type": "float", "default": 0.5, "min": 0.1, "max": 2.0, "step": 0.1},
        {"id": "exit_atr", "label": "止损 ATR 倍数", "type": "float", "default": 2.0, "min": 0.5, "max": 5.0, "step": 0.1},
        {"id": "add_profit_min", "label": "加仓最低浮盈", "type": "float", "default": 0.02, "min": 0.0, "max": 0.2, "step": 0.01},
        {"id": "max_additions", "label": "最多加仓次数", "type": "int", "default": 4, "min": 0, "max": 8, "step": 1},
        {"id": "base_position_amount", "label": "首仓金额", "type": "float", "default": 20000.0, "min": 1000.0, "max": 500000.0, "step": 1000.0},
        {"id": "use_ma_filter", "label": "要求收盘在 MA20 上方", "type": "bool", "default": True},
    ],
    "scoring": {"momentum_20d": 0.5, "change_pct": 0.5},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["turtle_buy"]
EXIT_SIGNALS = ["turtle_sell"]


class TurtleChannelMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "high", "low", "open"})

    def required_warmup_bars(self, params: dict) -> int:
        return max(
            int(params.get("n_entry", 10)),
            int(params.get("n_exit", 5)),
            int(params.get("atr_period", 10)),
            20,
        ) + 1

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        merged = {**TURTLE_DEFAULTS, **params}
        book = replay_turtle(
            market.open, market.high, market.low, market.close,
            params=merged,
            tradable=market.tradable,
            limit_up=market.limit_up_locked,
            limit_down=market.limit_down_locked,
            initial_cash=1.0e12,
            fill="open_t+1",
            max_positions=max(len(market.symbols), 1),
            max_exposure_pct=1.0,
        )
        entry = book.entry.astype(bool)
        exit_ = book.exit.astype(bool)
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("turtle_buy",),
            exit_signal_ids=("turtle_sell",),
        )


MATRIX_STRATEGY = TurtleChannelMatrixStrategy()
