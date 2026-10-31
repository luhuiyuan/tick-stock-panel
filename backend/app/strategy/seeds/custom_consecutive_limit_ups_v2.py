"""连板股·改进版 — 连板数2~4板追涨

在原连板股策略基础上增加连板上限(避免5板以上高位风险)。
退出条件: 跌破MA10次日确认退出(可通过参数关闭) + 止损/移动止损/回撤止盈/到期平仓。
移动止损/回撤止盈默认不启用, 由用户在回测参数中自行配置后保存。
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
    "id": "custom_consecutive_limit_ups_v2",
    "name": "连板股·改进版",
    "description": "连板数2~4板追涨, 跌破MA10次日确认退出(可关), 5%止损; 移动止损/回撤止盈默认空, 可自行配置",
    "tags": ["涨停", "连板"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 200,
        "market_cap_min": 10e8,
        "amount_min": 0.5e8,
        "exclude_st": True,
        "exclude_new_days": 30,
    },
    "params": [
        {
            "id": "min_boards",
            "label": "最少连板数",
            "type": "int",
            "default": 2,
            "min": 1,
            "max": 10,
            "step": 1,
        },
        {
            "id": "max_boards",
            "label": "最多连板数",
            "type": "int",
            "default": 4,
            "min": 1,
            "max": 20,
            "step": 1,
        },
        {
            "id": "use_ma10_exit",
            "label": "跌破MA10次日确认退出",
            "type": "bool",
            "default": True,
        },
    ],
    "scoring": {
        "consecutive_limit_ups": 0.5,
        "change_pct": 0.3,
        "amount": 0.2,
    },
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_limit_up"]
EXIT_SIGNALS = ["signal_ma10_breakdown"]
STOP_LOSS = -0.05
# 移动止损/回撤止盈: 默认 None(不启用), 用户可在回测参数中配置后保存
# 配置后引擎将从最高价回撤 trailing_stop 时止损, 或在收益达 activate 后回撤 drawdown 止盈
TRAILING_STOP = None
TRAILING_TAKE_PROFIT_ACTIVATE = None
TRAILING_TAKE_PROFIT_DRAWDOWN = None
MAX_HOLD_DAYS = 20
COOLDOWN_LOSS_STREAK = 2
COOLDOWN_DAYS = 3

RULES = """
1. 连板数在2~4之间，追涨强势连板股但避免5板以上高位风险
2. 退出(可配置): 跌破MA10且次日仍未收回时退出, 默认启用
3. 止损: 买入价下跌5%
4. 移动止损/回撤止盈: 默认不启用, 用户可在回测参数中自行配置
5. 最长持有20个交易日
"""


class ConsecutiveLimitUpsV2Strategy:
    """连板股·改进版矩阵策略。"""

    def required_fields(self) -> frozenset[str]:
        # ma10/close 用于退出信号; 即使关闭退出, 多声明字段只是多加载不影响正确性
        return frozenset({"consecutive_limit_ups", "close", "ma10"})

    def required_warmup_bars(self, params: dict) -> int:
        return 20

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        min_boards = int(params.get("min_boards", 2))
        max_boards = int(params.get("max_boards", 4))
        use_ma10_exit = bool(params.get("use_ma10_exit", True))

        # ── 入场: 连板数在 [min, max] 区间 ──
        boards = matrix_feature(market, "consecutive_limit_ups")
        entry = (boards >= min_boards) & (boards <= max_boards)

        # ── 退出: 跌破MA10 且 前一日也低于MA10 (次日确认) ──
        # 可通过 use_ma10_exit 参数关闭, 关闭后完全依靠止损/移动止损/回撤止盈/到期平仓
        if use_ma10_exit:
            ma10 = matrix_feature(market, "ma10")
            below_ma10 = market.close < ma10
            prev_below = shift(below_ma10.astype(np.float32), 1)
            exit_ = below_ma10 & (prev_below > 0.5)
            exit_signal_ids: tuple[str, ...] = ("signal_ma10_breakdown",)
        else:
            exit_ = np.zeros(market.shape, dtype=bool)
            exit_signal_ids = ()

        # ── 评分由引擎 MatrixStrategyPipeline 根据 META.scoring 自动计算 ──
        # 策略无需手动计算 score，返回 None 即可，引擎用 build_matrix_score 做归一化

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_limit_up",),
            exit_signal_ids=exit_signal_ids,
        )


MATRIX_STRATEGY = ConsecutiveLimitUpsV2Strategy()
