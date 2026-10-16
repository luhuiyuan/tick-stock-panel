"""P2A-002: fixed-source strategy exercised through real matrix contracts."""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import (
    MatrixPipelineConfig, MatrixStrategyPipeline, build_market_data_matrix,
)
from app.strategy.ai_generator import AIStrategyGenerator
from app.strategy.engine import StrategyEngine

PATH = Path(__file__).resolve().parents[2] / 'data/strategies/custom/custom_consecutive_limit_ups_v2.py'


@pytest.fixture
def strategy():
    return StrategyEngine._load_file(PATH)


def market(boards=(0, 1, 2, 3, 4, 5, 2, 2), missing=False):
    rows = []
    for i, count in enumerate(boards):
        close = 9.0 if i >= 6 else 11.0
        rows.append(dict(symbol='000001.SZ', date=date(2024, 1, 1) + timedelta(days=i),
                         open=close, high=close + 0.2, low=close - 0.2, close=close,
                         volume=1000.0, amount=10000.0, ma10=10.0,
                         consecutive_limit_ups=float(count)))
    panel = pl.DataFrame(rows)
    fields = {'amount', 'ma10', 'consecutive_limit_ups'}
    if missing:
        panel = panel.drop('consecutive_limit_ups')
        fields.remove('consecutive_limit_ups')
    return build_market_data_matrix(panel, field_columns=fields)


def run(strategy, data, params=None):
    return MatrixStrategyPipeline().run(
        strategy.matrix_strategy, data, params or {},
        MatrixPipelineConfig(basic_filter={'enabled': False}, scoring=strategy.meta['scoring'],
                             order_by='score', descending=True),
    )


def test_default_bounds_and_confirmed_exit(strategy):
    result = run(strategy, market())
    np.testing.assert_array_equal(result.entry[:, 0], [0, 0, 1, 1, 1, 0, 1, 1])
    np.testing.assert_array_equal(result.exit[:, 0], [0, 0, 0, 0, 0, 0, 0, 1])
    assert np.isfinite(result.score).all()
    assert strategy.stop_loss == -0.05
    assert strategy.max_hold_days == 20
    assert strategy.trailing_stop is None


def test_custom_bounds_and_exit_disabled(strategy):
    result = run(strategy, market(), {'min_boards': 3, 'max_boards': 3, 'use_ma10_exit': False})
    np.testing.assert_array_equal(result.entry[:, 0], [0, 0, 0, 1, 0, 0, 0, 0])
    assert not result.exit.any()
    assert result.exit_signal_ids == ()


def test_missing_boards_rejected(strategy):
    with pytest.raises(ValueError, match='unsupported matrix feature: consecutive_limit_ups'):
        run(strategy, market(missing=True))


def test_nan_boards_do_not_enter(strategy):
    result = run(strategy, market((np.nan, np.nan, 2, 4)))
    np.testing.assert_array_equal(result.entry[:, 0], [0, 0, 1, 1])


def test_missing_ma10_does_not_trigger_exit(strategy):
    data = market()
    data = replace(data, fields={**data.fields, 'ma10': np.full(data.shape, np.nan)})
    assert not run(strategy, data).exit.any()


def test_prefix_signals_unchanged_by_future_bars(strategy):
    full = run(strategy, market())
    short = run(strategy, market((0, 1, 2, 3, 4)))
    np.testing.assert_array_equal(full.entry[:5], short.entry)
    np.testing.assert_array_equal(full.exit[:5], short.exit)


def test_safety_and_field_plan(strategy):
    AIStrategyGenerator._validate_safety(PATH.read_text())
    assert strategy.meta['id'] == 'custom_consecutive_limit_ups_v2'
    fields = StrategyEngine._matrix_field_columns(strategy, None, {})
    assert {'consecutive_limit_ups', 'close', 'ma10', 'amount'} <= fields
