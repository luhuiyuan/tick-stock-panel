"""底仓做T（MACD）— 移植 TruthHun/day-wing-transaction。

1 分钟收盘价取最近 35 根算 MACD(12,26) 线。MACD>0 买 100 股，MACD<0 卖 100 股。
当天累计买、累计卖都必须小于底仓。14:55 或 14:57 把股数调回开盘底仓。
卖出只使用昨天的可卖数量，今天买的不卖。扫描假定底仓已经在手。
"""

from app.backtest.swing_rules import MACD_DEFAULTS, scan_intraday_base_t

META = {
    "id": "intraday_base_t_macd",
    "name": "底仓做T·MACD",
    "description": "1分钟MACD线做底仓回转：正买100、负卖100，累计不超过底仓，14:55调回",
    "tags": ["做T", "底仓", "分钟"],
    "asset_types": ["stock"],
    "timeframes": ["1m"],
    "scaled_execution": "base_t_macd",
    "basic_filter": {
        "price_min": 2,
        "price_max": 300,
        "amount_min": 5e7,
        "exclude_st": True,
    },
    "params": [
        {"id": "base_shares", "label": "底仓股数", "type": "int", "default": 10000, "min": 100, "max": 1000000, "step": 100},
        {"id": "trade_n", "label": "每次股数", "type": "int", "default": 100, "min": 100, "max": 10000, "step": 100},
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
    merged = {**MACD_DEFAULTS, **(params or {})}
    return scan_intraday_base_t(df, merged, kind="macd")
