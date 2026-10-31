"""尾盘持股的日线信号。只验证筛选规则，不跑全市场回测。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

from app.backtest.matrix import MarketDataMatrix

_SEED = Path(__file__).resolve().parents[1] / "app" / "strategy" / "seeds" / "overnight_tail_hold.py"


def _strategy():
    spec = importlib.util.spec_from_file_location("overnight_tail_hold_seed", _SEED)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _matrix(
    close: np.ndarray,
    volume: np.ndarray,
    *,
    names: tuple[str, ...],
    turnover: float | np.ndarray = 7.0,
    amount: float | np.ndarray = 80_000_000.0,
    limit_up: np.ndarray | None = None,
    limit_down: np.ndarray | None = None,
    raw_close: np.ndarray | None = None,
    float_shares: np.ndarray | float | None = None,
) -> MarketDataMatrix:
    close = np.asarray(close, dtype=np.float32)
    volume = np.asarray(volume, dtype=np.float32)
    if close.ndim == 1:
        close = close[:, None]
        volume = volume[:, None]
    times, assets = close.shape
    turnover_values = np.asarray(turnover, dtype=np.float32)
    amount_values = np.asarray(amount, dtype=np.float32)
    if turnover_values.ndim == 0:
        turnover_values = np.full((times, assets), float(turnover_values), dtype=np.float32)
    elif turnover_values.shape != (times, assets):
        turnover_values = np.broadcast_to(turnover_values, (times, assets)).astype(np.float32, copy=True)
    if amount_values.ndim == 0:
        amount_values = np.full((times, assets), float(amount_values), dtype=np.float32)
    if raw_close is None:
        raw_values = close.copy()
    else:
        raw_values = np.asarray(raw_close, dtype=np.float32)
        if raw_values.ndim == 1:
            raw_values = raw_values[:, None]
        if raw_values.shape != (times, assets):
            raw_values = np.broadcast_to(raw_values, (times, assets)).astype(np.float32, copy=True)
    if float_shares is None:
        share_values = (
            volume[-1].astype(np.float64)
            * 10_000.0
            / turnover_values[-1].astype(np.float64)
        ).astype(np.float32)
        share_values = np.broadcast_to(share_values, (times, assets)).astype(np.float32, copy=True)
    else:
        share_values = np.asarray(float_shares, dtype=np.float32)
        if share_values.ndim == 0:
            share_values = np.full((times, assets), float(share_values), dtype=np.float32)
        elif share_values.ndim == 1:
            share_values = np.broadcast_to(share_values, (times, assets)).astype(np.float32, copy=True)
    zeros = np.zeros((times, assets), dtype=np.uint8)
    return MarketDataMatrix(
        timestamps=np.arange(times, dtype=np.int64),
        timestamp_labels=tuple(f"2024-01-{(index % 28) + 1:02d}" for index in range(times)),
        session_ids=np.zeros(times, dtype=np.int32),
        symbols=tuple(f"{index:06d}.SZ" for index in range(assets)),
        names=names,
        open=close.copy(),
        high=close * np.float32(1.01),
        low=close * np.float32(0.99),
        close=close,
        volume=volume,
        tradable=np.ones((times, assets), dtype=np.uint8),
        limit_up_locked=zeros if limit_up is None else limit_up.astype(np.uint8),
        limit_down_locked=zeros if limit_down is None else limit_down.astype(np.uint8),
        fields={
            "turnover_rate": turnover_values,
            "amount": amount_values,
            "raw_close": raw_values,
            "float_shares": share_values,
        },
    )


def _uptrend(last_return: float = 0.04, bars: int = 30) -> np.ndarray:
    close = 10.0 * np.power(1.004, np.arange(bars, dtype=np.float64))
    close[-1] = close[-2] * (1.0 + last_return)
    return close.astype(np.float32)


def _rising_volume(bars: int = 30) -> np.ndarray:
    volume = np.full(bars, 100_000.0, dtype=np.float32)
    volume[-5:] = np.array([400_000, 450_000, 500_000, 550_000, 600_000], dtype=np.float32)
    return volume


def test_contract_buys_at_close_and_sells_in_the_morning():
    module = _strategy()
    assert module.META["id"] == "overnight_tail_hold"
    assert module.STOP_LOSS is None
    assert module.MAX_HOLD_DAYS is None
    assert module.ENTRY_FILL == "close_t"
    assert module.EXIT_FILL == "morning_9:30_10:00"
    assert module.EXIT_SIGNALS == []
    assert "9:30" in module.META["description"]
    assert "开盘卖出" not in module.META["description"]


def test_last_bar_passes_and_blockers_are_rejected():
    module = _strategy()
    bars = 30
    base_close = _uptrend()
    base_volume = _rising_volume()
    close = np.column_stack([
        base_close,
        base_close,
        base_close,
        _uptrend(0.08),
        base_close,
        base_close,
        base_close,
        20.0 - np.arange(bars, dtype=np.float32) * 0.15,
    ])
    volume = np.column_stack([base_volume] * 8)
    volume[:, 6] = base_volume.copy()
    volume[-5:, 6] = np.array([500_000, 600_000, 400_000, 550_000, 580_000], dtype=np.float32)
    turnover = np.full(close.shape, 7.0, dtype=np.float32)
    turnover[:, 4] = 1.0
    amount = np.full(close.shape, 80_000_000.0, dtype=np.float32)
    amount[:, 5] = 1_000_000.0
    limit_up = np.zeros(close.shape, dtype=np.uint8)
    limit_up[-1, 2] = 1
    names = ("平安", "ST平安", "涨停", "涨太多", "低换手", "无成交", "量不递增", "空头")
    market = _matrix(close, volume, names=names, turnover=turnover, amount=amount, limit_up=limit_up)

    signals = module.MATRIX_STRATEGY.compute_signals(market, {})

    assert signals.entry[-1].tolist() == [1, 0, 0, 0, 0, 0, 0, 0]
    assert signals.entry[:, 1].sum() == 0
    assert int(signals.exit.sum()) == 0


def test_change_turnover_amount_and_float_mv_bounds_are_inclusive():
    module = _strategy()
    close = _uptrend(0.03)
    volume = _rising_volume()
    # 未显式给股本时，按最后一根的成交量和换手率反推当前流通股本，
    # 使最后一根的 raw_close×股本与旧公式对齐。
    cases = {
        "change_low": (_uptrend(0.03), volume, 7.0, 80_000_000.0, True),
        "change_high": (_uptrend(0.05), volume, 7.0, 80_000_000.0, True),
        "change_below": (_uptrend(0.029), volume, 7.0, 80_000_000.0, False),
        "change_above": (_uptrend(0.051), volume, 7.0, 80_000_000.0, False),
        "turnover_low": (close, volume, 5.0, 80_000_000.0, True),
        "turnover_high": (close, volume, 10.0, 80_000_000.0, True),
        "turnover_below": (close, volume, 4.9, 80_000_000.0, False),
        "amount_exact": (close, volume, 7.0, 50_000_000.0, True),
        "amount_below": (close, volume, 7.0, 49_000_000.0, False),
    }
    for name, (case_close, case_volume, case_turnover, case_amount, expected) in cases.items():
        market = _matrix(
            case_close,
            case_volume,
            names=("标的",),
            turnover=case_turnover,
            amount=case_amount,
        )
        entry = module.MATRIX_STRATEGY.compute_signals(market, {}).entry[-1, 0]
        assert bool(entry) is expected, name


def _volume_for_float_mv(close: np.ndarray, turnover: float, target_yuan: float) -> np.ndarray:
    volume = _rising_volume()
    last_close = float(close[-1])
    last_volume = target_yuan * turnover / (last_close * 10_000.0)
    previous = last_volume * 0.9
    volume[-5:] = np.array(
        [previous * 0.8, previous * 0.85, previous * 0.9, previous * 0.95, last_volume],
        dtype=np.float32,
    )
    return volume


def test_float_market_cap_bounds_keep_turnover_inside_the_band():
    module = _strategy()
    close = _uptrend(0.04)
    cases = {
        "mv_low": (5_000_000_000.0, 10.0, True),
        "mv_high": (20_000_000_000.0, 5.0, True),
        "mv_below": (4_000_000_000.0, 10.0, False),
        "mv_above": (22_000_000_000.0, 5.0, False),
    }
    for name, (target, turnover, expected) in cases.items():
        market = _matrix(
            close,
            _volume_for_float_mv(close, turnover, target),
            names=("标的",),
            turnover=turnover,
        )
        entry = module.MATRIX_STRATEGY.compute_signals(market, {}).entry[-1, 0]
        assert bool(entry) is expected, name


def test_float_market_cap_uses_raw_close_times_current_shares():
    module = _strategy()
    close = _uptrend(0.04)
    volume = _rising_volume()
    turnover = 7.0
    raw_close = close / np.float32(10.0)
    implied_shares = float(volume[-1]) * 10_000.0 / turnover
    # 前复权价乘这批股本大约 100 亿，会通过；不复权价只有其 1/10，应被下限拦住。
    rejected = _matrix(
        close,
        volume,
        names=("标的",),
        turnover=turnover,
        raw_close=raw_close,
        float_shares=implied_shares,
    )
    assert module.MATRIX_STRATEGY.compute_signals(rejected, {}).entry[-1, 0] == 0

    target = 10_000_000_000.0
    shares_for_raw = target / float(raw_close[-1])
    # 成交量按前复权价会算出 200 亿以上，但市值只看不复权价×当前股本。
    heavy_volume = _rising_volume()
    heavy_volume[-5:] = np.array(
        [800_000, 900_000, 1_000_000, 1_100_000, 1_300_000],
        dtype=np.float32,
    )
    accepted = _matrix(
        close,
        heavy_volume,
        names=("标的",),
        turnover=turnover,
        raw_close=raw_close,
        float_shares=shares_for_raw,
    )
    assert module.MATRIX_STRATEGY.compute_signals(accepted, {}).entry[-1, 0] == 1

    missing = _matrix(
        close,
        volume,
        names=("标的",),
        turnover=turnover,
        raw_close=close,
        float_shares=np.nan,
    )
    assert module.MATRIX_STRATEGY.compute_signals(missing, {}).entry[-1, 0] == 0
