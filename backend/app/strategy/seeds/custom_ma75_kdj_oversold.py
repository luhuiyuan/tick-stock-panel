"""MA75+KDJ超卖 — 中期趋势上方 KDJ 超卖低吸

收盘价站上 MA75 (中期多头) + KDJ 的 J 值 < 25 (短期超卖) + 当日涨跌幅在 [下限, 上限] 以内 (非涨停非暴跌) + 总市值 > 30 亿
+ 过去X个交易日内至少有一天满足: 单日涨幅 > 涨幅下限 + 放量(≥N倍前日量) + 上影线≤实体M%。
逻辑: 在中期上升趋势中, 利用 KDJ 超卖信号捕捉短线低吸机会, 且要求近期出现过放量大涨阳线(有资金关注)。
"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
)
from app.backtest.matrix import valid_shift as shift

META = {
    "id": "custom_ma75_kdj_oversold",
    "name": "MA75+KDJ超卖",
    "description": "收盘价站上MA75 + KDJ的J值<25 + 涨跌幅在[下限,上限]以内 + 总市值>30亿 + 近X日有过放量大涨阳线(涨幅>下限+量≥N倍+上影线小), 中期趋势上方超卖低吸",
    "tags": ["超卖", "KDJ", "均线", "低吸"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 200,
        "market_cap_min": 30e8,
        "amount_min": 0.5e8,
        "exclude_st": True,
        "exclude_new_days": 30,
    },
    "params": [
        {
            "id": "ma_period",
            "label": "均线周期",
            "type": "int",
            "default": 75,
            "min": 20,
            "max": 250,
            "step": 5,
        },
        {
            "id": "kdj_j_max",
            "label": "KDJ J值上限",
            "type": "float",
            "default": 25.0,
            "min": 0.0,
            "max": 50.0,
            "step": 1.0,
        },
        {
            "id": "change_pct_max",
            "label": "涨跌幅上限(%)",
            "type": "float",
            "default": 3.5,
            "min": 1.0,
            "max": 10.0,
            "step": 0.5,
        },
        {
            "id": "change_pct_min",
            "label": "涨跌幅下限(%)",
            "type": "float",
            "default": -3.5,
            "min": -10.0,
            "max": 0.0,
            "step": 0.5,
        },
        {
            "id": "surge_pct_min",
            "label": "近X日单日涨幅下限(%)",
            "type": "float",
            "default": 8.0,
            "min": 3.0,
            "max": 20.0,
            "step": 0.5,
        },
        {
            "id": "surge_lookback_days",
            "label": "涨幅回看天数",
            "type": "int",
            "default": 10,
            "min": 3,
            "max": 30,
            "step": 1,
        },
        {
            "id": "surge_vol_ratio_min",
            "label": "大涨日放量倍数下限",
            "type": "float",
            "default": 2.0,
            "min": 1.0,
            "max": 5.0,
            "step": 0.1,
        },
        {
            "id": "surge_shadow_ratio_max",
            "label": "大涨日上影线占实体比例上限",
            "type": "float",
            "default": 0.2,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
        },
    ],
    "scoring": {
        "kdj_j": 0.4,
        "change_pct": 0.3,
        "vol_ratio_5d": 0.3,
    },
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = []
EXIT_SIGNALS = ["signal_ma20_breakdown"]
STOP_LOSS = -0.05
TRAILING_STOP = -0.10
MAX_HOLD_DAYS = 15
COOLDOWN_LOSS_STREAK = 2
COOLDOWN_DAYS = 3

RULES = """
1. 收盘价站上MA75, 确认中期多头趋势
2. KDJ的J值小于25, 短期超卖低吸信号
3. 当日涨跌幅在 [下限, 上限] 以内 (默认-3.5%~3.5%), 排除涨停追高和暴跌接刀
4. 总市值大于30亿元, 排除小盘垃圾股
5. 过去X个交易日(默认10日)内至少有一天满足:
   a) 单日涨幅超过涨幅下限(默认8%)
   b) 当日成交量 ≥ 放量倍数下限(默认2倍) × 前一日成交量
   c) 上影线 ≤ 上影线比例上限(默认20%) × K线实体
   三者同时满足, 确保近期有放量大阳线(资金强势介入)
6. 止损: 买入价下跌5% 或 从最高价回撤10%
7. 最长持有15个交易日
"""


class Ma75KdjOversoldStrategy:
    """MA75+KDJ超卖矩阵策略。"""

    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "open", "high", "volume", "kdj_j"})

    def required_warmup_bars(self, params: dict) -> int:
        ma_period = int(params.get("ma_period", 75))
        lookback = int(params.get("surge_lookback_days", 10))
        return max(ma_period + 10, 80 + lookback)

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        ma_period = int(params.get("ma_period", 75))
        j_max = float(params.get("kdj_j_max", 25.0))
        change_max = float(params.get("change_pct_max", 3.5)) / 100.0
        change_min = float(params.get("change_pct_min", -3.5)) / 100.0
        surge_threshold = float(params.get("surge_pct_min", 8.0)) / 100.0
        lookback = int(params.get("surge_lookback_days", 10))
        vol_ratio_min = float(params.get("surge_vol_ratio_min", 2.0))
        shadow_ratio_max = float(params.get("surge_shadow_ratio_max", 0.2))

        # ── 入场条件 ──
        # 1. 收盘价 > MA75 (中期多头)
        ma = matrix_feature(market, f"ma{ma_period}")
        entry = market.close > ma

        # 2. KDJ J值 < j_max (短期超卖)
        kdj_j = matrix_feature(market, "kdj_j")
        entry &= kdj_j < j_max

        # 3. 涨跌幅在 [change_min, change_max] 以内
        change_pct = matrix_feature(market, "change_pct")
        entry &= (change_pct > change_min) & (change_pct < change_max)

        # 4. 过去 lookback 个交易日内至少有一天满足:
        #    a) 单日涨幅 > surge_threshold
        #    b) 当日成交量 >= vol_ratio_min × 前一日成交量 (放量)
        #    c) 上影线 <= shadow_ratio_max × K线实体 (小上影线)
        upper_shadow = market.high - np.maximum(market.open, market.close)
        body = np.abs(market.close - market.open)
        # body > 0 时检查上影线比例; body = 0 (十字星) 时不满足
        shadow_ok = (body > 0) & (upper_shadow <= shadow_ratio_max * body)

        vol_prev = shift(market.volume, 1)
        vol_ok = (vol_prev > 0) & (market.volume >= vol_ratio_min * vol_prev)

        surge_day = (change_pct > surge_threshold) & vol_ok & shadow_ok

        # valid_shift 强制转 float32, 需转回 bool 才能用 |= 累积
        surge_day_f = surge_day.astype(np.float32)
        has_surge = np.zeros(market.shape, dtype=bool)
        for d in range(1, lookback + 1):
            has_surge |= shift(surge_day_f, d) > 0.5
        entry &= has_surge

        # ── 退出条件: 跌破MA20 ──
        ma20 = matrix_feature(market, "ma20")
        exit_ = (market.close < ma20) & (shift(market.close, 1) >= shift(ma20, 1))

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            exit_signal_ids=("signal_ma20_breakdown",),
        )


MATRIX_STRATEGY = Ma75KdjOversoldStrategy()
