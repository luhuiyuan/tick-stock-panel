"""尾盘持股 — 收盘买入，次日 9:30–10:00 择机卖出。

规则按公开的八步法里日线能够计算的部分重写，不复制外部仓库源码。
未实现「强于大盘」和「尾盘最后 30 分钟强势」：本地矩阵没有指数序列，
三个参考实现里也没有可运行的分时尾盘条件。量比用收盘后的
当日成交量 / 前 5 日均量，不等于 14:30 当时的分时量比。

卖出不在这张日线信号里。回测和模拟仓都走次日早盘规则：高开 2% 且未涨停就卖，
高开不足 2% 时上冲 1% 卖，低开后跌破分时均价且低于昨收卖，10:00 仍未卖出则卖。
10:00 仍封涨停则留到开板后的下一笔，当天不开板就收盘卖。跌停不成交。
没有对应分钟线的回测信号会剔除，不用开盘价或收盘价代替。

单位：
- change_pct 是小数，参数里的涨幅是百分数
- turnover_rate 在矩阵里是百分数，5 表示 5%
- volume 是手
- 流通市值(元) = raw_close * 当前流通股本。raw_close 是不复权收盘价，
  流通股本来自标的表的最新 float_shares，不随历史股本回放，也不用前复权价
"""

from __future__ import annotations

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    valid_shift,
)

META = {
    "id": "overnight_tail_hold",
    "name": "尾盘持股",
    "description": (
        "收盘买入，次日 9:30–10:00 择机卖。涨幅 3%~5%，全天量比>1，换手率 5%~10%，"
        "流通市值 50~200 亿（不复权收盘价×当前流通股本），成交额不低于 5000 万，近 5 日量递增，"
        "MA5>MA10>MA20 且三线向上、收盘在 MA20 上。排除 ST、退市和涨跌停。"
        "高开≥2%且未涨停卖出；否则上冲≥1%卖出；低开后跌破分时均价且低于昨收卖出；"
        "10:00 未卖出则卖，仍封涨停留到开板或收盘。跌停不成交。不设日线止损。"
        "模拟仓请用收盘买单，卖出由早盘计划执行，不要再用出场信号卖出。"
    ),
    "tags": ["尾盘", "隔夜", "超短"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "change_min",
            "label": "最低涨幅%",
            "type": "float",
            "default": 3.0,
            "min": 0.0,
            "max": 10.0,
            "step": 0.5,
        },
        {
            "id": "change_max",
            "label": "最高涨幅%",
            "type": "float",
            "default": 5.0,
            "min": 0.0,
            "max": 15.0,
            "step": 0.5,
        },
        {
            "id": "volume_ratio_min",
            "label": "最低量比",
            "type": "float",
            "default": 1.0,
            "min": 0.5,
            "max": 5.0,
            "step": 0.1,
        },
        {
            "id": "turnover_min",
            "label": "最低换手率%",
            "type": "float",
            "default": 5.0,
            "min": 0.0,
            "max": 30.0,
            "step": 0.5,
        },
        {
            "id": "turnover_max",
            "label": "最高换手率%",
            "type": "float",
            "default": 10.0,
            "min": 0.0,
            "max": 40.0,
            "step": 0.5,
        },
        {
            "id": "float_mv_min",
            "label": "流通市值下限(亿)",
            "type": "float",
            "default": 50.0,
            "min": 0.0,
            "max": 500.0,
            "step": 10.0,
        },
        {
            "id": "float_mv_max",
            "label": "流通市值上限(亿)",
            "type": "float",
            "default": 200.0,
            "min": 10.0,
            "max": 2000.0,
            "step": 10.0,
        },
        {
            "id": "amount_min",
            "label": "最低成交额(万元)",
            "type": "float",
            "default": 5000.0,
            "min": 0.0,
            "max": 100000.0,
            "step": 500.0,
        },
    ],
    "scoring": {"vol_ratio_5d": 0.4, "turnover_rate": 0.3, "change_pct": 0.3},
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_overnight_tail_hold"]
EXIT_SIGNALS: list[str] = []
# 日线引擎不负责卖出。回测服务会用分钟线写入卖出价；这里不再设 1 日到期，
# 否则会在分钟价写上之前按日线收盘价卖掉。
STOP_LOSS = None
MAX_HOLD_DAYS = None
ENTRY_FILL = "close_t"
EXIT_FILL = "morning_9:30_10:00"


def _blocked_names(names: tuple[str, ...]) -> np.ndarray:
    blocked = np.zeros(len(names), dtype=bool)
    for index, name in enumerate(names):
        if "ST" in name.upper() or "退" in name:
            blocked[index] = True
    return blocked


class OvernightTailHoldMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "volume", "turnover_rate", "amount", "raw_close", "float_shares"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 21

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        change = matrix_feature(market, "change_pct")
        turnover = matrix_feature(market, "turnover_rate")
        amount = matrix_feature(market, "amount")
        volume_ratio = matrix_feature(market, "vol_ratio_5d")
        ma5 = matrix_feature(market, "ma5")
        ma10 = matrix_feature(market, "ma10")
        ma20 = matrix_feature(market, "ma20")
        volume = market.volume

        change_min = float(params.get("change_min", 3.0))
        change_max = float(params.get("change_max", 5.0))
        turnover_min = float(params.get("turnover_min", 5.0))
        turnover_max = float(params.get("turnover_max", 10.0))
        float_mv_min = float(params.get("float_mv_min", 50.0)) * 1e8
        float_mv_max = float(params.get("float_mv_max", 200.0)) * 1e8
        amount_min = float(params.get("amount_min", 5000.0)) * 1e4

        raw_close = market.field("raw_close")
        float_shares = market.field("float_shares")
        valid_shares = np.isfinite(float_shares) & (float_shares > 0) & np.isfinite(raw_close)
        float_mv = np.full(market.shape, np.nan, dtype=np.float32)
        np.multiply(raw_close, float_shares, out=float_mv, where=valid_shares)
        volume_1 = valid_shift(volume, 1)
        volume_2 = valid_shift(volume, 2)
        volume_3 = valid_shift(volume, 3)
        volume_4 = valid_shift(volume, 4)
        volume_ladder = (
            (volume >= volume_1)
            & (volume_1 >= volume_2)
            & (volume_2 >= volume_3)
            & (volume_3 >= volume_4)
        )
        ma_aligned = (ma5 > ma10) & (ma10 > ma20) & (market.close > ma20)
        ma_rising = (
            (ma5 > valid_shift(ma5, 1))
            & (ma10 > valid_shift(ma10, 1))
            & (ma20 > valid_shift(ma20, 1))
        )
        entry = (
            market.tradable.astype(bool)
            & ~market.limit_up_locked.astype(bool)
            & ~market.limit_down_locked.astype(bool)
            & (np.round(change * np.float32(100.0), 2) >= change_min)
            & (np.round(change * np.float32(100.0), 2) <= change_max)
            & (volume_ratio > float(params.get("volume_ratio_min", 1.0)))
            & (turnover >= turnover_min)
            & (turnover <= turnover_max)
            & (float_mv >= float_mv_min)
            & (float_mv <= float_mv_max)
            & (amount >= amount_min)
            & volume_ladder
            & ma_aligned
            & ma_rising
        )
        blocked = _blocked_names(market.names)
        if blocked.any():
            entry = entry.copy()
            entry[:, blocked] = False
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_overnight_tail_hold",),
        )


MATRIX_STRATEGY = OvernightTailHoldMatrixStrategy()
