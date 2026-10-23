"""首板 — 当日涨停且连板数为 1。

连板数沿用本系统 consecutive_limit_ups（昨日未涨停则为 1）。不另写一套涨停价。
外部仓库没有单独的首板回合实现；卖出用已确认方案：跌破 MA5、最多持有 3 天、
止损 5%。一字板和收盘封板仍由撮合拒绝买入。
"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
)

META = {
    "id": "first_limit_up",
    "name": "首板",
    "description": "当日第一板涨停，跌破 MA5 或持有满 3 天离场",
    "tags": ["涨停", "首板"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 100,
        "amount_min": 5e7,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {"amount": 0.5, "change_pct": 0.5},
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["first_limit_up"]
EXIT_SIGNALS = ["ma5_breakdown"]
STOP_LOSS = -0.05
MAX_HOLD_DAYS = 3


class FirstLimitUpMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "consecutive_limit_ups"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        del params
        consecutive = market.field("consecutive_limit_ups")
        listed = np.cumsum(np.isfinite(market.close), axis=0) >= 60
        entry = market.limit_up_locked.astype(bool) & np.isfinite(consecutive) & (consecutive == 1) & listed
        ma5 = matrix_feature(market, "ma5")
        exit_ = np.isfinite(ma5) & (market.close < ma5) & ~entry
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("first_limit_up",),
            exit_signal_ids=("ma5_breakdown",),
        )


MATRIX_STRATEGY = FirstLimitUpMatrixStrategy()
