"""底仓做T（等量对冲）— 移植 er6y/stockquant 的双边挂单示例。

以当日第一根分钟线的开盘价为现价，在上方 sell_pct、下方 buy_pct 各挂一笔，
买卖股数相等。价差小于 0.8% 时不下单（源码 fee floor）。卖出只用昨仓。
14:55 或 14:57 把股数调回底仓。扫描假定底仓已经在手。
"""

from app.backtest.swing_rules import BAND_DEFAULTS, scan_intraday_base_t

META = {
    "id": "intraday_base_t_band",
    "name": "底仓做T·等量对冲",
    "description": "现价上下1.5%等量买卖，价差至少0.8%，尾盘把股数调回底仓",
    "tags": ["做T", "底仓", "分钟"],
    "asset_types": ["stock"],
    "timeframes": ["1m"],
    "scaled_execution": "base_t_band",
    "basic_filter": {
        "price_min": 2,
        "price_max": 300,
        "amount_min": 5e7,
        "exclude_st": True,
    },
    "params": [
        {"id": "base_shares", "label": "底仓股数", "type": "int", "default": 10000, "min": 100, "max": 1000000, "step": 100},
        {"id": "sell_pct", "label": "卖出上浮", "type": "float", "default": 0.015, "min": 0.001, "max": 0.1, "step": 0.001},
        {"id": "buy_pct", "label": "买入下浮", "type": "float", "default": 0.015, "min": 0.001, "max": 0.1, "step": 0.001},
        {"id": "fee_floor", "label": "最小价差", "type": "float", "default": 0.008, "min": 0.0, "max": 0.05, "step": 0.001},
        {"id": "target_yuan", "label": "单笔金额", "type": "float", "default": 10000, "min": 1000, "max": 1000000, "step": 1000},
        {"id": "max_sell_frac", "label": "最多卖出比例", "type": "float", "default": 0.5, "min": 0.1, "max": 1.0, "step": 0.1},
    ],
    "scoring": {"change_pct": 1.0},
    "order_by": "score",
    "descending": True,
    "limit": None,
}

EXECUTION_BACKEND = "minute_filter"
ENTRY_SIGNALS = ["base_t_buy"]
EXIT_SIGNALS = ["base_t_sell"]


def filter_minute_history(df, params):
    merged = {**BAND_DEFAULTS, **(params or {})}
    return scan_intraday_base_t(df, merged, kind="band")
