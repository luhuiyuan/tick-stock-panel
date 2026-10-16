"""P2A-003 scoring defaults, matrix signals, and API compatibility."""
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.api.strategy import _strategy_detail
from app.backtest.matrix import MatrixPipelineConfig, MatrixStrategyPipeline, build_market_data_matrix
from app.backtest.strategy import StrategyBacktestService
from app.strategy.engine import StrategyEngine
from app.strategy.scoring import effective_scoring_directions

PATH = Path(__file__).resolve().parents[2] / 'data/strategies/custom/custom_two_board_special.py'


@pytest.fixture
def strategy():
    return StrategyEngine._load_file(PATH)


@pytest.mark.parametrize('override,expected', [
    (None, {'ma10_bias': 'low'}),
    ({'scoring_directions': {'ma10_bias': 'high'}}, {'ma10_bias': 'high'}),
    ({'scoring_directions': {}}, {}),
    ({'scoring_directions': {'ma10_bias': 'invalid'}}, {}),
])
def test_override_precedence(override, expected):
    assert effective_scoring_directions(override, {'scoring_directions': {'ma10_bias': 'low'}}) == expected


def test_legacy_defaults_unchanged():
    assert effective_scoring_directions(None) == {}
    assert effective_scoring_directions({}, {'scoring': {'close': 1}}) == {}


def test_api_reports_six_low_directions(strategy):
    detail = _strategy_detail(strategy)
    assert len(detail['scoring_directions']) == 6
    assert set(detail['scoring_directions'].values()) == {'low'}
    assert strategy.stop_loss == -0.03
    assert strategy.trailing_stop == -0.15
    assert strategy.max_hold_days == 10


def test_backtest_and_realtime_prefer_low_values():
    panel = pl.DataFrame({'symbol': ['A', 'B'], 'date': [date(2024, 1, 2)] * 2,
                          'close': [11., 12.], 'ma10': [10., 10.]})
    meta = {'scoring': {'ma10_bias': 1.}, 'scoring_directions': {'ma10_bias': 'low'},
            'order_by': 'score', 'descending': True}
    strategy = SimpleNamespace(meta=meta)
    a = StrategyBacktestService._apply_score(panel, strategy, None)
    b = StrategyEngine._apply_scoring(panel, meta['scoring'], effective_scoring_directions(None, meta))
    assert a['score'].to_list() == pytest.approx([100, 0])
    assert a['score'].to_list() == pytest.approx(b['score'].to_list())
    override = {'scoring_directions': {'ma10_bias': 'high'}}
    assert StrategyBacktestService._apply_score(panel, strategy, override)['score'].to_list() == pytest.approx([0, 100])


def _market(days=65):
    rows=[]
    for i in range(days):
        for symbol, value in [('A', 10.), ('B', 12.)]:
            rows.append(dict(symbol=symbol, date=date(2024, 1, 1)+timedelta(days=i),
                open=value, high=value+1, low=value-1, close=value, raw_close=value,
                volume=1000., amount=100000., turnover_rate=value,
                consecutive_limit_ups=float([1, 2, 3, 2][i % 4]),
                signal_limit_up=i % 4 != 3, ma10=10., ma20=10.,
                prev_close=10., change_pct=(value / 10.-1), vol_ratio_5d=value / 10.))
    return build_market_data_matrix(pl.DataFrame(rows), field_columns={
        'consecutive_limit_ups', 'raw_close', 'amount', 'turnover_rate',
        'ma10', 'ma20', 'prev_close', 'change_pct', 'vol_ratio_5d'})


def _run(strategy, data, params=None):
    return MatrixStrategyPipeline().run(strategy.matrix_strategy, data, params or {},
        MatrixPipelineConfig(basic_filter={'enabled': False}, scoring=strategy.meta['scoring'],
            scoring_directions=effective_scoring_directions(None, strategy.meta),
            order_by='score', descending=True))


def test_real_matrix_two_boards_and_low_score(strategy):
    result = _run(strategy, _market())
    assert result.entry[61].tolist() == [1, 1]
    assert result.entry[62].tolist() == [0, 0]  # three boards excluded
    assert result.entry[63].tolist() == [0, 0]  # not locked limit-up
    assert result.score[61, 0] > result.score[61, 1]
    assert not result.exit.any()


def test_filter_switches_and_prefix_invariance(strategy):
    full = _run(strategy, _market())
    prefix = _run(strategy, _market(62))
    np.testing.assert_array_equal(full.entry[:62], prefix.entry)
    np.testing.assert_allclose(full.score[:62], prefix.score)
    both_off = _run(strategy, _market(), {'require_limit_up': False, 'use_boards_filter': False})
    assert both_off.entry.all()
