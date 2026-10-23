"""网格交易 — 移植 akquant examples/strategies/02_stock_grid_trading.py。

初始买 10 手。之后相对上一笔真实成交价每下跌 grid_pct 加一手，每上涨
grid_pct 减一手。锚只在成交后移动；买不进或卖不出时下一根 K 线按原锚再试。
"""

import numpy as np

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix
from app.backtest.swing_rules import GRID_DEFAULTS, replay_grid

META = {
    "id": "grid_trading",
    "name": "网格交易",
    "description": "相对上一笔成交价每跌 3% 加一手、每涨 3% 减一手，初始 10 手",
    "tags": ["网格", "高抛低吸"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "scaled_execution": "grid",
    "basic_filter": {
        "price_min": 2,
        "price_max": 300,
        "amount_min": 5e7,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [
        {"id": "grid_pct", "label": "网格间距", "type": "float", "default": 0.03, "min": 0.005, "max": 0.2, "step": 0.005},
        {"id": "trade_lot", "label": "每格股数", "type": "int", "default": 100, "min": 100, "max": 10000, "step": 100},
        {"id": "initial_lots", "label": "初始手数", "type": "int", "default": 10, "min": 1, "max": 50, "step": 1},
    ],
    "scoring": {"change_pct": 1.0},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["grid_buy"]
EXIT_SIGNALS = ["grid_sell"]


class GridTradingMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 5

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        merged = {**GRID_DEFAULTS, **params}
        # 扫描用次日开盘成交重放，和回测默认 open_t+1 同一本账。
        # 现金给得足够大，避免扫描阶段被账户资金截断；涨停和停牌仍拒绝成交。
        book = replay_grid(
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
            entry_signal_ids=("grid_buy",),
            exit_signal_ids=("grid_sell",),
        )


MATRIX_STRATEGY = GridTradingMatrixStrategy()
