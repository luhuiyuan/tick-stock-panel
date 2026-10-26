"""日线三重底背离 — 移植 Allanli1011/cn_stock_scan 的 MACD 五条规则。

只做日线选股。默认 5/5 严格命中，打开「包含宽松」后 4/5 也入选。
不是分钟回转，也不使用底仓做 T。
"""

import numpy as np

from app.backtest.macd_triple_divergence import SCREEN_WARMUP_BARS, scan_frame
from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix

META = {
    "id": "macd_triple_bottom",
    "name": "日线三重底背离",
    "description": "日线 MACD 三段创新低、死叉 DIF 抬升、回调不破零、柱面积衰减且第三底仍新鲜。默认 5/5，可含 4/5。出场看三重顶背离、20 日或 -6% 止损。不做 T。",
    "tags": ["MACD", "底背离", "日线", "选股"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "allow_loose", "label": "包含宽松 4/5", "type": "bool", "default": False},
        {"id": "min_price_increase_pct", "label": "每段创新低最小幅度", "type": "float", "default": 0.001, "min": 0.0, "max": 0.05, "step": 0.001},
        {"id": "min_area_reduction", "label": "柱面积最小衰减", "type": "float", "default": 0.10, "min": 0.0, "max": 0.8, "step": 0.01},
        {"id": "dif_approach_zero_ratio", "label": "DIF 逼近零的比例", "type": "float", "default": 0.50, "min": 0.05, "max": 1.0, "step": 0.05},
        {"id": "dif_zero_tolerance", "label": "允许穿过零轴的容差", "type": "float", "default": 0.0, "min": 0.0, "max": 0.2, "step": 0.01},
        {"id": "recency_bars", "label": "第三底最大间隔", "type": "int", "default": 30, "min": 1, "max": 120, "step": 1},
    ],
    "scoring": {},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_macd_triple_bottom"]
EXIT_SIGNALS = ["signal_macd_triple_top"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 20


class MacdTripleBottomMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "high", "low"})

    screen_last_bar_only = True

    def required_warmup_bars(self, params: dict) -> int:
        return SCREEN_WARMUP_BARS

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        bottom, top, bottom_score, _top_score = scan_frame(
            market.close, market.high, market.low, params,
        )
        entry = bottom.astype(bool)
        exit_ = top.astype(bool)
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            score=bottom_score,
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_macd_triple_bottom",),
            exit_signal_ids=("signal_macd_triple_top",),
        )


MATRIX_STRATEGY = MacdTripleBottomMatrixStrategy()
