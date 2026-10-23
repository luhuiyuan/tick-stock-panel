"""涨停后缩量 N 字 — 移植 N-Rebound night_screener.py 的规则部分。

最近 7 个交易日内涨幅超过 9.5%，之后收盘不破该日开盘价，信号日上影线不超过
6%，成交量不超过涨停日的 1.2 倍，且收盘处在近 60 日高低点的 60% 以下。
不使用该仓库的模型文件。结构被跌破（收盘低于涨停日开盘）时离场。
"""

import numpy as np

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix
from app.backtest.swing_rules import n_rebound_masks

META = {
    "id": "n_limit_pullback",
    "name": "涨停后缩量N字",
    "description": "近 7 日大阳后缩量且不破涨停开盘价，60 日位置不超过 60%",
    "tags": ["N字", "涨停", "缩量"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 100,
        "amount_min": 5e7,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [
        {"id": "n_days", "label": "回看天数", "type": "int", "default": 7, "min": 2, "max": 15, "step": 1},
        {"id": "vol_shrink_ratio", "label": "相对涨停日成交量上限", "type": "float", "default": 1.2, "min": 0.2, "max": 3.0, "step": 0.1},
        {"id": "upper_shadow_limit", "label": "上影线上限", "type": "float", "default": 0.06, "min": 0.0, "max": 0.2, "step": 0.01},
        {"id": "max_position_pct", "label": "60 日位置上限", "type": "float", "default": 0.6, "min": 0.1, "max": 1.0, "step": 0.05},
        {"id": "limit_move_pct", "label": "大阳涨幅阈值%", "type": "float", "default": 9.5, "min": 5.0, "max": 20.0, "step": 0.5},
    ],
    "scoring": {"change_pct": 0.4, "vol_ratio_5d": 0.3, "amount": 0.3},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["n_rebound"]
EXIT_SIGNALS = ["n_rebound_break"]


class NLimitPullbackMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry, exit_ = n_rebound_masks(
            market.open, market.high, market.low, market.close, market.volume, params,
        )
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("n_rebound",),
            exit_signal_ids=("n_rebound_break",),
        )


MATRIX_STRATEGY = NLimitPullbackMatrixStrategy()
