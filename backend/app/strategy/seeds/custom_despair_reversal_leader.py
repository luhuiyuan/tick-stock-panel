"""二板龙头·绝望弱转强（炸板回封型）

首板涨停后，次日盘中冲高回落甚至炸板（弱），
但尾盘重新封涨停（强），形成第二个涨停板。
配合 OAMV 市场活跃度过滤 + 连续阴线/下跌冷却期管理风险。

风控:
  - 止损: 下跌5% 或 最高价回撤10%
  - 止盈: 收益达30%后启用移动止盈(回撤5%卖出)
  - 最长持有: 10个交易日
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
    "id": "custom_despair_reversal_leader",
    "name": "二板龙头·绝望弱转强",
    "description": "炸板回封型二板龙头，OAMV环境过滤+阴线/下跌冷却风控",
    "tags": ["涨停", "二板", "弱转强", "炸板回封"],
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
            "id": "min_amplitude",
            "label": "最低日内振幅%",
            "type": "float",
            "default": 6.0,
            "min": 0,
            "max": 20,
            "step": 0.5,
        },
        {
            "id": "require_weak_open",
            "label": "要求低开(弱势开盘)",
            "type": "bool",
            "default": True,
        },
        {
            "id": "bearish_lookback",
            "label": "阴线回看天数",
            "type": "int",
            "default": 5,
            "min": 2,
            "max": 20,
            "step": 1,
        },
        {
            "id": "cooldown_days",
            "label": "连跌后冷却天数",
            "type": "int",
            "default": 3,
            "min": 0,
            "max": 10,
            "step": 1,
        },
        {
            "id": "oamv_ma_period",
            "label": "OAMV均线周期",
            "type": "int",
            "default": 5,
            "min": 2,
            "max": 30,
            "step": 1,
        },
    ],
    "scoring": {
        "consecutive_limit_ups": 0.3,
        "amount": 0.3,
        "momentum_5d": 0.2,
        "amplitude": 0.2,
    },
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_limit_up"]
EXIT_SIGNALS = ["signal_ma10_breakdown"]
STOP_LOSS = -0.05
TRAILING_STOP = -0.10
TRAILING_TAKE_PROFIT_ACTIVATE = 0.30
TRAILING_TAKE_PROFIT_DRAWDOWN = 0.05
MAX_HOLD_DAYS = 10

RULES = """
1. 首板涨停后次日盘中炸板或低开(弱势表现)，尾盘重新封涨停(强势确认)，形成二板
2. 入场前5天内无连续2根阴线，避免下跌趋势中的反弹
3. 市场活跃度(OAMV)需高于近N日均值，确保大盘环境配合
4. 连续2天股价下跌后触发退出，退出后冷却3天不重新入场
5. 跌破MA10且次日未收回时退出持仓
6. 止损: 买入价下跌5% 或 从最高价回撤10% 时强制止损
7. 止盈: 收益达30%后启用移动止盈，从最高价回撤5%时卖出
8. 最长持有10个交易日，超时强制平仓
"""


def _rolling_any_2d(mask_2d: np.ndarray, window: int) -> np.ndarray:
    """沿时间轴滚动 OR：过去 window 天内是否有 True。

    使用 cumsum 高效实现，避免逐行循环。
    """
    cumsum = np.cumsum(mask_2d.astype(np.float32), axis=0)
    result = np.zeros_like(mask_2d, dtype=bool)

    # 完整窗口部分；补一行前缀 0，确保窗口首日不被漏算。
    if window > 1 and window <= mask_2d.shape[0]:
        prefix = np.vstack([np.zeros((1, mask_2d.shape[1]), dtype=np.float32), cumsum])
        result[window - 1:] = prefix[window:] - prefix[:-window] > 0

    # 前 window-1 行逐行处理
    for i in range(min(window - 1, mask_2d.shape[0])):
        result[i] = mask_2d[: i + 1].any(axis=0)

    return result


def _moving_average_1d(arr: np.ndarray, window: int) -> np.ndarray:
    """一维数组移动平均，返回与输入等长的数组。"""
    n = len(arr)
    if n < window:
        return np.full_like(arr, np.nan, dtype=np.float64)
    cumsum = np.cumsum(np.insert(arr, 0, 0))
    ma = (cumsum[window:] - cumsum[:-window]) / window
    return np.concatenate([np.full(window - 1, np.nan), ma])


class DespairReversalLeaderStrategy:
    """二板龙头·绝望弱转强（炸板回封型）矩阵策略。"""

    def required_fields(self) -> frozenset[str]:
        return frozenset({
            "consecutive_limit_ups",
            "amount",
            "open",
            "close",
        })

    def required_warmup_bars(self, params: dict) -> int:
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        # ── 参数读取 ──
        min_boards = int(params.get("min_boards", 2))
        min_amp = float(params.get("min_amplitude", 6.0)) / 100.0
        require_weak_open = params.get("require_weak_open", True)
        bearish_lookback = int(params.get("bearish_lookback", 5))
        cooldown_days = int(params.get("cooldown_days", 3))
        oamv_period = int(params.get("oamv_ma_period", 5))

        # ── 1. OAMV 市场环境过滤 ──
        # 全市场日成交额 = 当前 universe 所有股票成交额之和
        # MarketDataMatrix 没有 amount 属性，需通过 matrix_feature 访问
        amount = matrix_feature(market, "amount")
        oamv = np.nansum(amount, axis=1)  # (T,)
        oamv_ma = _moving_average_1d(oamv, oamv_period)  # (T,)
        oamv_ok = np.full_like(oamv, False, dtype=bool)
        finite_mask = np.isfinite(oamv_ma)
        oamv_ok[finite_mask] = (oamv > oamv_ma)[finite_mask]
        entry = np.broadcast_to(oamv_ok[:, None], market.shape).copy()

        # ── 2. 二板龙头·炸板回封入场信号 ──
        boards = matrix_feature(market, "consecutive_limit_ups")
        sealed = market.limit_up_locked.astype(bool)
        amplitude = matrix_feature(market, "amplitude")
        prev_close = matrix_feature(market, "prev_close")

        is_second_board = boards >= min_boards
        large_range = amplitude >= min_amp
        weak_open = market.open < prev_close

        if require_weak_open:
            despair = is_second_board & sealed & (large_range | weak_open)
        else:
            despair = is_second_board & sealed & large_range
        entry &= despair

        # ── 3. 5天内无连续2根阴线 ──
        bearish = market.close < market.open
        bearish_f = bearish.astype(np.float32)
        prev_bearish = shift(bearish_f, 1)
        two_consec_bearish = bearish & (prev_bearish > 0.5)
        has_consec = _rolling_any_2d(two_consec_bearish, bearish_lookback)
        entry &= ~has_consec

        # ── 4. 连续2天下跌 → 退出 + 冷却期 ──
        down = market.close < prev_close
        down_f = down.astype(np.float32)
        prev_down = shift(down_f, 1)
        two_consec_down = down & (prev_down > 0.5)

        # 冷却期：连续2天下跌之后的 N 天内禁止入场
        cooldown = two_consec_down.copy()
        for d in range(1, cooldown_days):
            cooldown |= shift(two_consec_down.astype(np.float32), d) > 0.5
        entry &= ~cooldown

        # ── 5. 退出信号 ──
        exit_ = two_consec_down.copy()  # 连跌2天触发退出

        # 跌破MA10且次日未收回
        ma10 = matrix_feature(market, "ma10")
        below_ma10 = market.close < ma10
        below_ma10_f = below_ma10.astype(np.float32)
        prev_below = shift(below_ma10_f, 1)
        ma10_breakdown_confirmed = below_ma10 & (prev_below > 0.5)
        exit_ |= ma10_breakdown_confirmed

        # ── 6. 评分 ──
        score = np.zeros(market.shape, dtype=np.float32)
        score += np.nan_to_num(boards, nan=0.0) * 0.3
        score += np.nan_to_num(np.log1p(amount), nan=0.0) * 0.3
        score += np.nan_to_num(matrix_feature(market, "momentum_5d"), nan=0.0) * 0.2
        score += np.nan_to_num(amplitude, nan=0.0) * 0.2

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            score=score.astype(np.float32),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_limit_up",),
            exit_signal_ids=("signal_ma10_breakdown",),
        )


MATRIX_STRATEGY = DespairReversalLeaderStrategy()
