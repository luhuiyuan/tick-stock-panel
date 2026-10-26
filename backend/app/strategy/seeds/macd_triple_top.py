"""日线三重顶背离 — 与底背离同一套 cn_stock_scan 规则的镜像。

只作为日线风险名单。不买入、不做空、不做 T。回测入口会拒绝把它当成买入策略。
"""

import numpy as np

from app.backtest.macd_triple_divergence import SCREEN_WARMUP_BARS, scan_frame
from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix

META = {
    "id": "macd_triple_top",
    "name": "日线三重顶背离",
    "description": "日线 MACD 三段创新高、金叉 DIF 递减、回调不破零、柱面积衰减且第三顶仍新鲜。只用于选股排险，不作为买入或做空。",
    "tags": ["MACD", "顶背离", "日线", "选股"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "backtest_block_reason": "日线三重顶背离只用于选股排险，不作为买入回测。",
    "params": [
        {"id": "allow_loose", "label": "包含宽松 4/5", "type": "bool", "default": False},
        {"id": "min_price_increase_pct", "label": "每段创新高最小幅度", "type": "float", "default": 0.001, "min": 0.0, "max": 0.05, "step": 0.001},
        {"id": "min_area_reduction", "label": "柱面积最小衰减", "type": "float", "default": 0.10, "min": 0.0, "max": 0.8, "step": 0.01},
        {"id": "dif_approach_zero_ratio", "label": "DIF 逼近零的比例", "type": "float", "default": 0.50, "min": 0.05, "max": 1.0, "step": 0.05},
        {"id": "dif_zero_tolerance", "label": "允许穿过零轴的容差", "type": "float", "default": 0.0, "min": 0.0, "max": 0.2, "step": 0.01},
        {"id": "recency_bars", "label": "第三顶最大间隔", "type": "int", "default": 30, "min": 1, "max": 120, "step": 1},
    ],
    "scoring": {},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_macd_triple_top"]
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = None


class MacdTripleTopMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "high", "low"})

    screen_last_bar_only = True

    def required_warmup_bars(self, params: dict) -> int:
        return SCREEN_WARMUP_BARS

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        _bottom, top, _bottom_score, top_score = scan_frame(
            market.close, market.high, market.low, params,
        )
        entry = top.astype(bool)
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            score=top_score,
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_macd_triple_top",),
        )


MATRIX_STRATEGY = MacdTripleTopMatrixStrategy()
