"""布林 RSI 均值回归 — 移植 china-astock-quant strategies/mean_reversion.py。

RSI(14) 低于 30 且收盘跌破布林下轨买入。回到中轨或 RSI 高于 70 卖出。
布林标准差用总体标准差 (ddof=0)，RSI 用 Wilder 平滑。持有满 10 根 K 线离场。
"""

import numpy as np

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix
from app.backtest.swing_rules import mean_reversion_masks

META = {
    "id": "boll_rsi_revert",
    "name": "布林RSI均值回归",
    "description": "RSI 低于 30 且收盘在布林下轨之下买入，回到中轨或 RSI 高于 70 卖出",
    "tags": ["均值回归", "布林", "RSI"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 200,
        "amount_min": 5e7,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [
        {"id": "rsi_window", "label": "RSI 周期", "type": "int", "default": 14, "min": 5, "max": 30, "step": 1},
        {"id": "rsi_oversold", "label": "RSI 超卖", "type": "float", "default": 30.0, "min": 5.0, "max": 45.0, "step": 1.0},
        {"id": "rsi_overbought", "label": "RSI 超买", "type": "float", "default": 70.0, "min": 55.0, "max": 90.0, "step": 1.0},
        {"id": "bb_window", "label": "布林周期", "type": "int", "default": 20, "min": 10, "max": 60, "step": 1},
        {"id": "bb_mult", "label": "布林倍数", "type": "float", "default": 2.0, "min": 1.0, "max": 3.0, "step": 0.1},
    ],
    "scoring": {"rsi_14": 1.0},
    "order_by": "score",
    "descending": False,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["boll_rsi_entry"]
EXIT_SIGNALS = ["boll_rsi_exit"]
MAX_HOLD_DAYS = 10


class BollRsiRevertMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close"})

    def required_warmup_bars(self, params: dict) -> int:
        return max(int(params.get("bb_window", 20)), int(params.get("rsi_window", 14))) + 2

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry, exit_ = mean_reversion_masks(market.close, params)
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("boll_rsi_entry",),
            exit_signal_ids=("boll_rsi_exit",),
        )


MATRIX_STRATEGY = BollRsiRevertMatrixStrategy()
