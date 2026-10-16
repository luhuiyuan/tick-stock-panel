"""Fixed fork cooldown semantics on the production portfolio matcher."""
from datetime import date, timedelta

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.api.strategy import _strategy_detail
from app.strategy.engine import StrategyEngine
from pathlib import Path


def run(prices=(9., 9.), threshold=2, days=3, fees=0.):
    rows=[]
    # Consecutive trades close on day 1 and day 3. C is offered every
    # subsequent day, so its entry date exposes both cooldown boundaries.
    for symbol in ('A', 'B', 'C'):
        for i in range(9):
            price = prices[0] if symbol == 'A' and i >= 1 else prices[1] if symbol == 'B' and i >= 3 else 10.
            rows.append(dict(symbol=symbol, name=symbol, date=date(2024,1,1)+timedelta(days=i),
                             open=price, high=price, low=price, close=price, volume=100000,
                             score=1., signal_limit_up=False, signal_limit_down=False))
    panel=pl.DataFrame(rows)
    entries=pl.Series([r['symbol']=='A' and r['date'].day==1 or r['symbol']=='B' and r['date'].day==3 or r['symbol']=='C' and r['date'].day>=4 for r in rows])
    exits=pl.Series([r['symbol']=='A' and r['date'].day==2 or r['symbol']=='B' and r['date'].day==4 for r in rows])
    return BacktestEngine(repo=None).simulate_portfolio(panel,entries,exits,MatcherConfig(
        matching='close_t', fees_pct=fees, slippage_bps=0, max_positions=1,
        initial_capital=100000, cooldown_loss_streak=threshold,cooldown_days=days))


def test_blocks_trigger_day_and_resumes_at_boundary():
    result=run()
    assert next(t for t in result.trades if t.symbol=='C').entry_date=='2024-01-07'
    assert result.stats['execution']['buy_cooldown']==3
    assert [t.pnl_amount < 0 for t in result.trades[:2]] == [True, True]


@pytest.mark.parametrize('threshold,days',[(None,3),(0,3),(2,None),(2,0)])
def test_disabled(threshold, days):
    result=run(threshold=threshold,days=days)
    assert next(t for t in result.trades if t.symbol=='C').entry_date=='2024-01-04'
    assert result.stats['execution']['buy_cooldown']==0


@pytest.mark.parametrize('prices',[(11.,9.),(10.,9.),(9.,11.),(9.,10.)])
def test_non_loss_breaks_streak(prices):
    result=run(prices=prices)
    assert next(t for t in result.trades if t.symbol=='C').entry_date=='2024-01-04'


def test_costs_count_as_realized_loss():
    result=run(prices=(10.,10.),fees=.001)
    assert next(t for t in result.trades if t.symbol=='C').entry_date=='2024-01-07'


def test_defaults_and_explicit_null_override():
    path=Path(__file__).resolve().parents[3]/'data/strategies/custom/custom_consecutive_limit_ups_v2.py'
    strategy=StrategyEngine._load_file(path)
    detail=_strategy_detail(strategy,overrides=None)
    assert (detail['cooldown_loss_streak'],detail['cooldown_days'])==(2,3)
    detail=_strategy_detail(strategy,overrides={'cooldown_loss_streak':None,'cooldown_days':5})
    assert detail['cooldown_loss_streak'] is None
    assert detail['cooldown_days']==5
