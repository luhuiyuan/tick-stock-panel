"""2连板特别版 — 基于"可买入样本"因子有效性分析优化评分权重

评分依据: 可买入2连板样本因子有效性分析 (analysis_buyable.py / analysis_buyable2.py / analysis_buyable3.py)
数据区间: 2025-09-09 ~ 2026-09-14, 246个交易日, 1788个可买入样本 (排除T+1涨停无法买入的样本)

核心思路转变:
  原版评分目标 = "选出最可能继续涨停的股票" → T+1涨停买不进 → 实际买到的是差股票
  新版评分目标 = "在可买入的2连板中, 选出持有收益最高的股票"

核心发现 (可买入样本 Spearman 相关性):
  - MA10偏离(ma10_bias): rho=-0.140***, 预测能力 #1 → 股价接近均线=回调买入机会
  - 日涨跌幅(change_pct): rho=-0.137***, 预测能力 #2 → 涨幅偏弱=未透支
  - MA20偏离(ma20_bias): rho=-0.128***, 预测能力 #3 → 中期均线附近更稳
  - 量比(vol_ratio_5d): rho=-0.035, 辅助因子 → 缩量封板更牢
  - 换手率(turnover_rate): rho=-0.066**, 辅助因子 → 低换手=筹码锁定
  - 开盘缺口(gap_return): rho=-0.082***, 方向翻转 → 低开反而更好 (原版high错误)

被移除的因子 (在可买入样本上无预测力):
  - 振幅(amplitude): rho=-0.035, 不显著 (原版权重0.25, 最大的错误)
  - 成交额(log_amount): rho=-0.030, 不显著
  - 流通市值(log_float_mv): rho=-0.017, 不显著

风控参数优化 (30组参数测试最优):
  - 止损: -4% → -3% (连板股跳空频发, 快割减少击穿损失)
  - 移动止损: -10% → -15% (放宽让盈利奔跑, 盈亏比从2.48→3.79)
  - 最大持有: 7天 → 10天 (给趋势更多展开空间)
"""

import numpy as np

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

META = {
    "id": "custom_two_board_special",
    "name": "2连板特别版",
    "description": (
        "当日涨停且连续涨停≥2天, 基于'可买入样本'因子有效性分析优化评分权重。"
        "核心逻辑: 在T+1可买入的2连板中, 优先选择股价接近均线(ma10/ma20偏离低)、"
        "涨幅偏弱、低换手、缩量、低开的股票。回避原版'选最可能涨停'的逻辑矛盾。"
        "数据依据: 246个交易日1788个可买入样本的因子Spearman分析。"
    ),
    "tags": ["涨停", "连板", "统计优化"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "require_limit_up", "label": "要求当日涨停", "type": "bool", "default": True},
        {"id": "use_boards_filter", "label": "启用连板数过滤", "type": "bool", "default": True},
        {
            "id": "min_boards",
            "label": "最少连板数",
            "type": "int",
            "default": 2,
            "min": 1,
            "max": 20,
            "step": 1,
        },
        {
            "id": "max_boards",
            "label": "最多连板数",
            "type": "int",
            "default": 2,
            "min": 1,
            "max": 20,
            "step": 1,
        },
    ],
    # ── 评分权重 (基于可买入样本 Spearman 相关性排名) ──
    # 所有因子在可买入样本上均为负相关, 方向统一设为 low (值越小得分越高)
    "scoring": {
        "ma10_bias": 0.25,      # MA10偏离: 预测能力 #1, rho=-0.140
        "change_pct": 0.20,     # 日涨跌幅: 预测能力 #2, rho=-0.137
        "ma20_bias": 0.15,      # MA20偏离: 预测能力 #3, rho=-0.128
        "vol_ratio_5d": 0.15,   # 量比: 辅助因子, rho=-0.035
        "turnover_rate": 0.15,  # 换手率: 辅助因子, rho=-0.066
        "gap_return": 0.10,     # 开盘缺口: 方向翻转, rho=-0.082
    },
    # ── 评分方向 ──
    # 全部负相关: "low" (值越小, 得分越高)
    "scoring_directions": {
        "ma10_bias": "low",
        "change_pct": "low",
        "ma20_bias": "low",
        "vol_ratio_5d": "low",
        "turnover_rate": "low",
        "gap_return": "low",
    },
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_limit_up"]
EXIT_SIGNALS = []
STOP_LOSS = -0.03
TRAILING_STOP = -0.15
MAX_HOLD_DAYS = 10


class TwoBoardSpecialMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        # 信号生成直接使用的字段; 评分依赖由 scoring_dependencies 自动解析
        return frozenset({"consecutive_limit_ups", "raw_close"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        # vol_ratio_5d 需要5日均量, ma10_bias/ma20_bias 需要20日均线
        # scoring_warmup_bars 会自动叠加评分因子的预热需求
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry = np.ones(market.shape, dtype=bool)
        if params.get("require_limit_up", True):
            entry &= market.limit_up_locked.astype(bool)
        if params.get("use_boards_filter", True):
            boards = matrix_feature(market, "consecutive_limit_ups")
            entry &= boards >= int(params.get("min_boards", 2))
            entry &= boards <= int(params.get("max_boards", 2))
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_limit_up",),
        )


MATRIX_STRATEGY = TwoBoardSpecialMatrixStrategy()
