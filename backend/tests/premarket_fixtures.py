"""迁移测试共用的隔离注册 fixture 与显式日历样本，不参与测试收集。"""
from datetime import date, timedelta
from itertools import pairwise

import polars as pl
import pytest

from app.factors import registry  # 初始化顺序与应用一致
from app.factors.premarket import FACTORS
from app.indicators.pipeline import compute_indicators
from app.strategy import custom_signals
from app.strategy.premarket_signal_definitions import (
    FIFTH_BATCH_SIGNALS,
    FOURTH_BATCH_SIGNALS,
    SEVENTH_BATCH_SIGNALS,
    SIGNALS,
    SIXTH_BATCH_SIGNALS,
)


def frame(n=65):
    # 显式提供测试日历，不用工作日推断生产日历。
    days = [date(2026, 1, 1) + timedelta(days=i * 2) for i in range(n)]
    df = pl.DataFrame({
        'symbol': ['A'] * n, 'date': days, 'open': [100.] * n,
        'high': [102.] * n, 'low': [98.] * n, 'close': [100.] * n,
        'amount': [100.] * n, 'volume': [10.] * n,
    })
    return df, days


@pytest.fixture(autouse=True)
def factors():
    from app.factors.store import register_definition
    with registry.isolated_registry():
        for factor in FACTORS:
            register_definition(factor)
        yield


def evaluate(df, calendar, signals=None):
    exprs = custom_signals.build_expressions(signals or SIGNALS)
    df = compute_indicators(df)
    df = custom_signals.materialize_factor_columns(df, exprs, trading_dates=calendar)
    return custom_signals.inject(df, exprs)


def val(df, sid):
    return df[f'csg_{sid}'][-1]


def change_last(df, **kwargs):
    return df.with_columns([
        pl.when(pl.int_range(pl.len()) == df.height - 1).then(pl.lit(v)).otherwise(pl.col(k)).alias(k)
        for k, v in kwargs.items()
    ])


def change_at(df, index, **kwargs):
    return df.with_columns([
        pl.when(pl.int_range(pl.len()) == index).then(pl.lit(v)).otherwise(pl.col(k)).alias(k)
        for k, v in kwargs.items()
    ])


def with_tail(df, **columns):
    for field, values in columns.items():
        for i, value in enumerate(values, start=df.height - len(values)):
            df = change_at(df, i, **{field: value})
    return df


def fourth_eval(df, days):
    return evaluate(df, days, FOURTH_BATCH_SIGNALS)


def fourth_amount_window(df, ratio):
    return df.with_columns(pl.when(pl.int_range(pl.len()) >= df.height-5)
                           .then(100. * ratio).otherwise(100.).alias('amount'))


def segmented_frame(segments, n=7):
    """Segments are oldest first: (high, low, amount), with valid midpoint OHLC."""
    df, days = frame(125)
    highs = [float(h) for h, _, _ in segments for _ in range(n)]
    lows = [float(low) for _, low, _ in segments for _ in range(n)]
    amounts = [float(a) for _, _, a in segments for _ in range(n)]
    mid = [(h + low) / 2 for h, low in zip(highs, lows, strict=True)]
    return with_tail(df, high=highs, low=lows, amount=amounts, open=mid, close=mid), days


def fifth_eval(df, days):
    return evaluate(df, days, FIFTH_BATCH_SIGNALS)


def sixth_eval(df, days):
    return evaluate(df, days, SIXTH_BATCH_SIGNALS)


def sixth_narrow_frame(near_amount=70., far_amount=100., high=108.):
    df, days = frame(125)
    # Materialized price columns, like daily Parquet; avoid Polars literal-column window bug.
    df = df.with_columns(pl.Series('open', [104.]*df.height), pl.Series('close', [104.]*df.height),
                         pl.Series('high', [float(high)]*df.height), pl.Series('low', [100.]*df.height))
    return with_tail(df, amount=[float(far_amount)]*60+[float(near_amount)]*10), days


def sixth_pivot_frame(low=98., close=100.):
    df, days = frame(125)
    df = df.with_columns(pl.lit(99.).alias('open'), pl.lit(99.).alias('close'),
                         pl.lit(100.).alias('high'), pl.lit(98.).alias('low'))
    df = with_tail(df, low=[float(low)]*10, high=[104.]*10,
                   open=[float(close)]*10, close=[float(close)]*10)
    return df, days


def sixth_bridge_frame(far_high=105., last_close=99.75, lows=(80., 85., 90., 95.)):
    segments = [(far_high, lows[0], 100), (104, lows[1], 100),
                (103, lows[2], 100), (100, lows[3], 100)]
    df, days = segmented_frame(segments, 5)
    return change_last(df, close=last_close), days


def seventh_eval(df, days):
    return evaluate(df, days, SEVENTH_BATCH_SIGNALS)


def seventh_rising_frame(returns=(.01,.01,.01)):
    df, days = frame(125)
    closes = [100.]
    for ret in returns:
        closes.append(closes[-1]*(1+ret))
    df = with_tail(df,close=closes,open=[c+.1 for c in closes],
                   high=[100.5,104.,108.,112.],low=[99.]*4)
    df = change_at(df,df.height-10,high=120.)
    return df, days


def seventh_v_frame(drop=-.03, rebound=.04, old_high=115., last_close=101., last_open=102.):
    df,days=frame(125)
    df=with_tail(df,open=[105.]*15,close=[105.]*15,high=[float(old_high)]*10+[110.]*5,low=[100.]*15)
    df=change_at(df,df.height-6,close=110.)
    bottom=110*(1+drop)
    df=change_at(df,df.height-4,close=bottom)
    df=change_at(df,df.height-2,close=bottom*(1+rebound),high=120.)
    return change_last(df,close=last_close,open=last_open),days


def seventh_double_frame(right_low=102., neck=112., last_close=None):
    df,days=frame(125)
    df=with_tail(df,open=[105.]*28,close=[105.]*28,high=[108.]*28,low=[104.]*28)
    for offset in range(18,28):
        df=change_at(df,df.height-1-offset,low=100.)
    for offset in range(5,15):
        df=change_at(df,df.height-1-offset,low=float(right_low))
    for offset in range(12,20):
        df=change_at(df,df.height-1-offset,high=float(neck))
    close=float(neck if last_close is None else last_close)
    return change_last(df,close=close,open=105.,high=max(close,108.)),days



def eighth_eval(df, days):
    from app.strategy.premarket_signal_definitions import EIGHTH_BATCH_SIGNALS
    return evaluate(df, days, EIGHTH_BATCH_SIGNALS)


def eighth_morning_frame(first_close=96., middle_open=96., last_close=99.):
    df, days = frame(80)
    return with_tail(df, open=[100., middle_open, 100.],
                     close=[first_close, 96., last_close],
                     high=[105., 102., max(102., last_close)], low=[90., 90., 90.]), days


def eighth_momentum_frame(close=110., amount=300.):
    df, days = frame(80)
    return change_last(df, close=close, open=close, high=close+1, low=close-1,
                       amount=amount), days


def eighth_gap_frame(ratio=.8, amount=200.):
    df, days = frame(80)
    # M14=18.375/14=1.3125；ratio=.8时开盘=101，避免反推价格的舍入误差。
    closes = [98.625] + [100. if i % 2 == 0 else 99. for i in range(13)] + [105.]
    mean_move = sum(abs(b-a) for a, b in pairwise(closes)) / 14
    df = with_tail(df, close=closes)
    df = change_at(df, df.height-2, open=99., high=100., low=98.)
    return change_last(df, open=100. + ratio*mean_move*100./105., close=105.,
                       high=106., low=100.1, amount=amount), days


def eighth_raw_eval(df, days, signals):
    # 缺列走真实按需物化入口；全量指标计算要求完整行情字段。
    exprs = custom_signals.build_expressions(signals)
    df = custom_signals.materialize_factor_columns(df, exprs, trading_dates=days)
    return custom_signals.inject(df, exprs)


def ninth_eval(df, days):
    from app.strategy.premarket_signal_definitions import NINTH_BATCH_SIGNALS
    return evaluate(df, days, NINTH_BATCH_SIGNALS)


def ninth_compress_frame(near_width=9., far_width=17.):
    df, days = frame(125)
    return with_tail(df, open=[100.]*20, close=[100.]*20, low=[100.]*20,
                     high=[100.+far_width]*15+[100.+near_width]*5), days


def ninth_four_yang_frame(last_amount=50.):
    df, days = frame(125)
    return with_tail(df, open=[99.]*4+[101.], close=[100.]*5,
                     high=[102.]*5, low=[98.]*5, amount=[100.]*4+[last_amount]), days


def ninth_vcp_frame(highs=(130., 120., 110.), amounts=(300., 200., 100.)):
    return segmented_frame([(h, 100., a) for h, a in zip(highs, amounts, strict=True)], 8)


def tenth_eval(df, days):
    from app.strategy.premarket_signal_definitions import TENTH_BATCH_SIGNALS
    return evaluate(df, days, TENTH_BATCH_SIGNALS)


def tenth_flag_frame(last_close=112., pole_end=120., near_amount=80.):
    df, days = frame(125)
    df = with_tail(df, open=[100.]*11+[118.]*5+[111.]*5,
                   close=[100.]*10+[pole_end]+[118.]*5+[112.]*5,
                   high=[125.]*11+[122.]*5+[119.]*5,
                   low=[99.]*11+[108.]*5+[105.]*5,
                   amount=[100.]*11+[near_amount]*10)
    return change_last(df, close=last_close), days


def tenth_cup_frame(left=100., right=100., bottom=80., handle=90., close=96.):
    df, days = frame(125)
    highs = [left]*10+[95.]*12+[right]*5+[max(right, close)]*3
    lows = [94.]*10+[bottom]*12+[94.]*5+[handle]*3
    mids = [(h+lo)/2 for h, lo in zip(highs, lows, strict=True)]
    df = with_tail(df, high=highs, low=lows, open=mids, close=mids)
    return change_last(df, close=close), days


def tenth_pullback_frame(low=103., amount=130.):
    df, days = frame(125)
    df = with_tail(df, open=[90.]*5+[100.]*15+[109.]*5,
                   close=[90.]*5+[100.]*15+[110.]*5,
                   high=[92.]*5+[102.]*15+[115.]*5,
                   low=[88.]*5+[98.]*15+[low]*5)
    return change_last(df, amount=amount), days



def eleventh_eval(df, days):
    from app.strategy.premarket_signal_definitions import ELEVENTH_BATCH_SIGNALS
    return evaluate(df, days, ELEVENTH_BATCH_SIGNALS)


def eleventh_pit_frame(pit_low=98., high=112., near_amount=80., close=101.):
    df, days = frame(125)
    df = with_tail(df, open=[106.]*12+[103.]*8+[102.],
                   close=[106.]*12+[103.]*8+[close],
                   high=[high]*12+[108.]*8+[110.],
                   low=[100.]*12+[pit_low]*8+[99.],
                   amount=[100.]*12+[near_amount]*8+[100.])
    return df, days


def eleventh_bridge_frame(pit_low=98., near_amount=80., bridge_high=108., near_low=95.):
    df, days = frame(125)
    highs=[110.]*17+[115.]*2+[110.]*10+[bridge_high]*5+[100.]*5
    lows=[100.]*17+[95.]*2+[pit_low]*10+[90.]*5+[near_low]*5
    mids=[(h+low)/2 for h,low in zip(highs,lows,strict=True)]
    return with_tail(df,high=highs,low=lows,open=mids,close=mids,
                     amount=[100.]*19+[near_amount]*10+[100.]*10),days



def twelfth_eval(df, days):
    from app.strategy.premarket_signal_definitions import TWELFTH_BATCH_SIGNALS
    return evaluate(df, days, TWELFTH_BATCH_SIGNALS)


def twelfth_peak_frame(close=101.5, peaks=(30,)):
    df, days = frame(125)
    df = with_tail(df, open=[85.]*61, close=[85.]*61, high=[90.]*61, low=[80.]*61)
    for offset in peaks:
        df = change_at(df, df.height-1-offset, high=100.)
    return change_last(df, open=102., high=max(102., close), close=close), days
