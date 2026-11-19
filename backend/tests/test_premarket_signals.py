"""普通信号：七批公式、严格日历、三态及既有接口集成。"""
from datetime import timedelta

import polars as pl
import pytest
from premarket_fixtures import (
    change_at,
    change_last,
    eighth_eval,
    eighth_gap_frame,
    eighth_momentum_frame,
    eighth_morning_frame,
    eighth_raw_eval,
    eleventh_bridge_frame,
    eleventh_eval,
    eleventh_pit_frame,
    evaluate,
    fifth_eval,
    fourth_amount_window,
    fourth_eval,
    frame,
    ninth_compress_frame,
    ninth_eval,
    ninth_four_yang_frame,
    ninth_vcp_frame,
    segmented_frame,
    seventh_double_frame,
    seventh_eval,
    seventh_rising_frame,
    seventh_v_frame,
    sixth_bridge_frame,
    sixth_eval,
    sixth_narrow_frame,
    sixth_pivot_frame,
    tenth_cup_frame,
    tenth_eval,
    tenth_flag_frame,
    tenth_pullback_frame,
    twelfth_eval,
    twelfth_peak_frame,
    val,
    with_tail,
)
from premarket_fixtures import (
    factors as factors,
)

from app.factors.premarket import FACTORS
from app.indicators.pipeline import compute_indicators
from app.strategy import custom_signals
from app.strategy.premarket_signal_definitions import (
    EIGHTH_BATCH_SIGNALS,
    ELEVENTH_BATCH_SIGNALS,
    FIFTH_BATCH_SIGNALS,
    FOURTH_BATCH_SIGNALS,
    NINTH_BATCH_SIGNALS,
    SECOND_BATCH_SIGNALS,
    SEVENTH_BATCH_SIGNALS,
    SIGNALS,
    SIXTH_BATCH_SIGNALS,
    TENTH_BATCH_SIGNALS,
    THIRD_BATCH_SIGNALS,
    TWELFTH_BATCH_SIGNALS,
)
from app.strategy.strict_signal_data import MARKET_INDEX


@pytest.mark.parametrize(('sid', 'changes', 'expected'), [
    ('t_oversold', {'close': 85., 'low': 84.}, True),
    ('t_oversold', {'close': 85.0001, 'low': 84.}, False),
    ('t_platform', {'high': 104., 'low': 98.}, True),
    ('t_platform', {'high': 104.0001, 'low': 98.}, False),
    ('t_uppersh', {'open': 100., 'close': 101., 'high': 103.}, True),
    ('t_uppersh', {'open': 100., 'close': 101., 'high': 102.999}, False),
    ('t_lowersh', {'open': 100., 'close': 101., 'low': 98.}, True),
    ('t_lowersh', {'open': 100., 'close': 101., 'low': 98.001}, False),
    ('t_highpos', {'close': 95., 'low': 94.}, True),
    ('t_highpos', {'close': 94.999, 'low': 94.}, False),
    ('t_fake_yin', {'open': 102., 'close': 101.}, True),
    ('t_fake_yin', {'open': 102., 'close': 100.}, False),
    ('t_uppersh', {}, False),
    ('t_lowersh', {}, False),
])
def test_formula_boundaries(sid, changes, expected):
    df, days = frame()
    assert val(evaluate(change_last(df, **changes), days), sid) is expected


def test_bull_and_shrink():
    df, days = frame()
    rising = df.with_columns((pl.col('close') + pl.int_range(pl.len())).alias('close'))
    assert val(evaluate(rising, days), 't_mabull') is True
    assert val(evaluate(df, days), 't_mabull') is False
    for amount, expected in [(70., True), (70.001, False), (0., True)]:
        modified = df.with_columns(pl.when(pl.int_range(pl.len()) >= 60)
                                   .then(amount).otherwise(pl.col('amount')).alias('amount'))
        assert val(evaluate(modified, days), 't_shrink') is expected
    zeros = df.with_columns(pl.lit(0.).alias('amount'))
    assert val(evaluate(zeros, days), 't_shrink') is None


def test_gap_calendar_and_unknown_negation():
    df, days = frame()
    gap = df.filter(pl.col('date') != days[-3])
    out = evaluate(gap, days)
    for sid in ['t_oversold', 't_mabull', 't_platform', 't_shrink', 't_highpos']:
        assert val(out, sid) is None
        assert out.select((~pl.col(f'csg_{sid}')).last()).item() is None
    assert val(out, 't_fake_yin') is False  # 只依赖最近两日，不受更早缺口影响
    for calendar in (None, days[:-1]):
        out = evaluate(df, calendar)
        assert all(val(out, s['id']) is None for s in SIGNALS)
    out = evaluate(df, days[-10:])
    assert val(out, 't_highpos') is None
    assert val(out, 't_fake_yin') is False


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), 0., -1.])
def test_invalid_price_dominates_false_condition(bad):
    df, days = frame()
    out = evaluate(change_last(df, open=bad), days)
    for sid in ['t_fake_yin', 't_uppersh', 't_lowersh']:
        assert val(out, sid) is None


def test_ohlc_and_missing_column():
    df, days = frame()
    assert val(evaluate(change_last(df, high=99.), days), 't_uppersh') is None
    out = evaluate(df.drop('amount'), days)
    assert val(out, 't_shrink') is None
    assert val(out, 't_highpos') is True


def test_warmup_and_future_invariance():
    df, days = frame()
    full = evaluate(df, days)
    windows = {'t_oversold': 21, 't_mabull': 20, 't_platform': 6,
               't_shrink': 20, 't_uppersh': 1, 't_highpos': 60,
               't_lowersh': 1, 't_fake_yin': 2}
    for sid, n in windows.items():
        series = full[f'csg_{sid}']
        assert series[:n-1].null_count() == n-1
        assert series[n-1] is not None
    truncated = evaluate(df.head(61), days[:61])
    for sig in SIGNALS:
        col = f"csg_{sig['id']}"
        assert full[col][:61].equals(truncated[col])
    altered = evaluate(change_last(df, close=1000.), days)
    for sig in SIGNALS:
        col = f"csg_{sig['id']}"
        assert full[col][:-1].equals(altered[col][:-1])


def test_on_demand_and_realtime():
    df, days = frame()
    exprs = custom_signals.build_expressions(SIGNALS)
    out = custom_signals.materialize_factor_columns(
        compute_indicators(df), exprs, needed={'csg_t_fake_yin'}, trading_dates=days,
    )
    assert MARKET_INDEX in out.columns
    assert not any(f['id'] in out.columns for f in FACTORS)
    live = custom_signals.build_expressions_prev(SIGNALS, {'close'})
    for sig in SIGNALS:
        col = f"csg_{sig['id']}"
        assert df.tail(1).select(live[col]).item() is None


def test_pipeline_from_raw_and_calendar_cache(monkeypatch):
    from app.indicators import pipeline
    from app.services import trading_day
    df, days = frame()
    monkeypatch.setattr(pipeline, '_custom_signal_exprs', custom_signals.build_expressions(SIGNALS[:56]))
    monkeypatch.setattr(trading_day, 'cached_trading_calendar', lambda: set(days))
    out = pipeline.compute_signals(df, needed={f"csg_{s['id']}" for s in SIGNALS[:56]}, attach_extensions=False)
    reference = evaluate(df, days)
    for sig in SIGNALS[:56]:
        c = f"csg_{sig['id']}"
        assert out[c].equals(reference[c])
    assert MARKET_INDEX not in set().union(*pipeline.get_signal_dependencies().values())


def test_duplicate_unsorted_and_symbol_isolation():
    df, days = frame()
    duplicate = pl.concat([df, df.tail(1)]).sort(['symbol', 'date'])
    out = evaluate(duplicate, days)
    assert all(val(out, s['id']) is None for s in SIGNALS)
    from app.strategy.strict_signal_data import attach_calendar
    with pytest.raises(ValueError, match='排序'):
        attach_calendar(df.reverse(), days)
    other = change_last(df, close=85., low=84.).with_columns(pl.lit('B').alias('symbol'))
    combined = evaluate(pl.concat([df, other]), days)
    for symbol, source in [('A', df), ('B', other)]:
        expected = evaluate(source, days)
        for sig in SIGNALS:
            c = f"csg_{sig['id']}"
            assert combined.filter(pl.col('symbol') == symbol)[c].equals(expected[c])


def test_coverage_and_ordinary_signal_unchanged():
    from app.strategy.strict_signal_data import coverage
    df, days = frame()
    ordinary = {'id': 'old', 'conditions': [{'left': 'close', 'op': '>', 'right': '90'}]}
    exprs = custom_signals.build_expressions([ordinary])
    out = custom_signals.inject(df, exprs)
    assert out['csg_old'].all()
    assert MARKET_INDEX not in custom_signals.materialize_factor_columns(df, exprs).columns
    out = evaluate(df, days)
    counts = coverage(out, ['csg_t_highpos', 'missing'])
    assert counts['csg_t_highpos'] == {'true': 6, 'false': 0, 'unknown': 59}
    assert counts['missing']['unknown'] == df.height


@pytest.mark.parametrize('sig', SIGNALS, ids=lambda s: s['id'])
def test_api_roundtrip_preserves_contract_for_legacy_editor(tmp_path, sig):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.signals import router
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    client = TestClient(app)
    saved = client.post('/api/custom-signals', json=sig)
    assert saved.status_code == 200
    assert saved.json()['signal']['data_contract'] == sig['data_contract']
    edited = {k: v for k, v in sig.items() if k != 'data_contract'}
    edited['name'] = '改名'
    response = client.post('/api/custom-signals', json=edited)
    assert response.status_code == 200
    assert response.json()['signal']['data_contract'] == sig['data_contract']
    invalid = {**sig, 'data_contract': {'version': 999}}
    assert client.post('/api/custom-signals', json=invalid).status_code == 400
    listed = client.get('/api/custom-signals').json()['signals']
    assert listed[0]['data_contract'] == sig['data_contract']


def test_calendar_reader_never_fetches(monkeypatch):
    from app.services import trading_day
    _, days = frame()
    monkeypatch.setattr(trading_day, '_CAL', (0., set(days)))
    def forbidden():
        raise AssertionError('不得联网')
    monkeypatch.setattr(trading_day, '_fetch_fuyao_calendar', forbidden)
    actual = trading_day.cached_trading_calendar()
    actual.clear()
    assert trading_day.cached_trading_calendar() == set(days)


def test_old_tag_parity_and_d_to_t_alignment():
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags
    # 旧入口按 T 取 T-1，普通信号直接归属 D。比较时只对齐一次。
    df, days = frame(125)
    rng = np.random.default_rng(7)
    close = 100. * np.exp(np.cumsum(rng.normal(0, .015, len(days))))
    opens = close * (1. + rng.normal(0, .006, len(days)))
    df = df.with_columns(pl.Series('close', close), pl.Series('open', opens),
                         pl.Series('high', np.maximum(close, opens) * 1.01),
                         pl.Series('low', np.minimum(close, opens) * .99),
                         pl.Series('amount', rng.uniform(50, 150, len(days))))
    tensor = np.zeros((1, len(days), 8), dtype=np.float64)
    for i, field in enumerate(['open', 'high', 'low', 'close', 'volume', 'amount']):
        tensor[0, :, i] = df[field].to_numpy()
    rows = []
    for day in days[100:]:
        rows.append(dict(t=str(day), symbol='A', bp=1, yizi_t=0, yizi_b=0,
                         broken_limit=0, phase='', mktcap=100, gap=0., seal_rate=0.,
                         limit_up=0, state=''))
    tags = build_tags(pd.DataFrame(rows), [str(d) for d in days], tensor, {'A': 0})
    out = evaluate(df, days)
    for sig, bit in zip(SIGNALS[:44], [0, 3, 4, 5, 6, 16, 20, 57, 1, 2, 14, 15, 17, 21, 22, 24, 7, 8, 18, 47, 48, 49, 50, 93, 23, 35, 36, 42, 54, 55, 65, 70, 79, 83, 84, 87, 64, 66, 67, 72, 68, 78, 82, 88], strict=True):
        actual = out[f"csg_{sig['id']}"][99:124].to_list()
        old = ((tags[:, bit // 53] >> (bit % 53)) & 1).astype(bool).tolist()
        assert actual == old, sig['id']


@pytest.mark.parametrize(('last_close', 'expected'), [(105., True), (95., True), (105.0001, False), (94.9999, False)])
def test_platform_return_boundaries(last_close, expected):
    df, days = frame()
    df = df.with_columns(pl.lit(100.).alias('high'), pl.lit(100.).alias('low'))
    df = change_last(df, close=last_close, high=max(100., last_close), low=min(100., last_close))
    assert val(evaluate(df, days), 't_platform') is expected


def test_numeric_factor_reuse_and_ohlc_semantics():
    df, days = frame()
    df = df.with_columns(pl.when(pl.int_range(pl.len()) >= 60).then(70.)
                         .otherwise(pl.col('amount')).alias('amount'), pl.lit(0.).alias('volume'))
    out = evaluate(df, days)
    assert out['uf_amount_5_prev15'][-1] == pytest.approx(.7)
    assert out['uf_range_5d_close'][-1] == pytest.approx(.04)
    assert val(out, 't_shrink') is True  # 不得误用 volume
    assert val(evaluate(change_last(df, high=1000.), days), 't_highpos') is True  # 最高收盘，不是最高价
    bearish = evaluate(change_last(df, open=101., close=100., high=103., low=98.), days)
    assert val(bearish, 't_uppersh') is True
    assert val(bearish, 't_lowersh') is True
    for amount in [-1., float('inf'), float('nan'), None]:
        assert val(evaluate(change_last(df, amount=amount), days), 't_shrink') is None
    # 振幅 8% 边界；成交额条件保持满足。
    assert val(evaluate(change_last(df, high=106.), days), 't_shrink') is True
    assert val(evaluate(change_last(df, high=106.0001), days), 't_shrink') is False


@pytest.mark.parametrize(('sid', 'close', 'expected'), [
    ('t_lowpos', 120., True), ('t_lowpos', 120.0001, False), ('t_lowpos', 119.9999, True),
    ('t_newhigh', 100., True), ('t_newhigh', 100.0001, True), ('t_newhigh', 99.9999, False),
    ('t_dd20', 85., True), ('t_dd20', 84.9999, True), ('t_dd20', 85.0001, False),
    ('t_lastdrop', 95., True), ('t_lastdrop', 94.9999, True), ('t_lastdrop', 95.0001, False),
])
def test_second_batch_price_boundaries(sid, close, expected):
    df, days = frame()
    if sid == 't_lowpos':
        df = df.with_columns(pl.lit(100.).alias('low'))
    df = change_last(df, close=close, high=max(102., close), low=min(100., close))
    assert val(evaluate(df, days), sid) is expected


@pytest.mark.parametrize(('delta', 'expected'), [(0., True), (-.0001, True), (.0001, False)])
def test_second_batch_ma24_boundary(delta, expected):
    df, days = frame()
    # x / ((23*100+x)/24) == .85；当日收盘必须进入 MA24。
    close = .85 * 2300 / (24 - .85) + delta
    assert val(evaluate(change_last(df, close=close), days), 't_bias24') is expected


@pytest.mark.parametrize(('delta', 'expected'), [(0., True), (-.0001, True), (.0001, False)])
def test_second_batch_ma_spread_boundary(delta, expected):
    df, days = frame()
    df = df.with_columns(pl.when(pl.int_range(pl.len()) < df.height - 5)
                         .then(100. + 8. / 3. + delta).otherwise(100.).alias('close'))
    out = evaluate(df, days)
    assert out['uf_ma_5_10_20_spread'][-1] == pytest.approx(.02 + .0075 * delta)
    assert val(out, 't_mafit') is expected


@pytest.mark.parametrize(('prev_open', 'prev_close', 'open_', 'close', 'expected'), [
    (102., 100., 100., 102., True),  # 吞没实体端点允许相等
    (102., 100., 99., 103., True),
    (102., 100., 100.001, 102., False),
    (102., 100., 100., 101.999, False),
    (100., 102., 99., 103., False),  # 前日必须收阴
    (100., 100., 99., 101., False),  # 前日十字星不是阴线
    (102., 100., 101., 101., False),
    (102., 100., 103., 99., False),
])
def test_second_batch_engulf(prev_open, prev_close, open_, close, expected):
    df, days = frame()
    df = change_at(df, df.height - 2, open=prev_open, close=prev_close)
    df = change_last(df, open=open_, close=close)
    assert val(evaluate(df, days), 't_engulf') is expected


@pytest.mark.parametrize(('close', 'amount', 'expected'), [
    (103., 80., True), (102.9999, 80., False), (103., 80.0001, False),
    (103.0001, 79.9999, True), (103., 0., True),
])
def test_second_batch_priceup_amount_boundary(close, amount, expected):
    df, days = frame()
    df = df.with_columns(pl.when(pl.int_range(pl.len()) >= df.height - 5)
                         .then(amount).otherwise(100.).alias('amount'), pl.lit(0.).alias('volume'))
    assert val(evaluate(change_last(df, close=close), days), 't_priceupvol') is expected
    zeros = df.with_columns(pl.lit(0.).alias('amount'))
    assert val(evaluate(change_last(zeros, close=close), days), 't_priceupvol') is None


@pytest.mark.parametrize('sig', SECOND_BATCH_SIGNALS + THIRD_BATCH_SIGNALS + FOURTH_BATCH_SIGNALS + FIFTH_BATCH_SIGNALS + SIXTH_BATCH_SIGNALS + SEVENTH_BATCH_SIGNALS, ids=lambda s: s['id'])
def test_second_batch_strict_windows_and_missing_inputs(sig):
    n = max(sig['data_contract']['inputs'].values())
    df, days = frame(max(65, n + 5))
    col = f"csg_{sig['id']}"
    out = evaluate(df, days, [sig])
    assert out[col][:n-1].null_count() == n-1
    assert out[col][n-1] is not None
    assert val(evaluate(df.tail(n), days, [sig]), sig['id']) is not None
    gap = df.filter(pl.col('date') != days[-2])
    unknown = evaluate(gap, days, [sig]) if n > 1 else evaluate(df, days[:-1], [sig])
    assert val(unknown, sig['id']) is None
    assert unknown.select((~pl.col(col)).last()).item() is None
    # 每个声明的必需字段均不能静默降级，包括只缺窗口内最早一根。
    for field, window in sig['data_contract']['inputs'].items():
        missing = change_at(df, df.height-window, **{field: None})
        assert val(evaluate(missing, days, [sig]), sig['id']) is None
        exprs = custom_signals.build_expressions([sig])
        missing_columns = custom_signals.materialize_factor_columns(
            df.drop(field), exprs, trading_dates=days,
        )
        assert val(custom_signals.inject(missing_columns, exprs), sig['id']) is None
        for bad in [float('nan'), float('inf'), -1.]:
            assert val(evaluate(change_last(df, **{field: bad}), days, [sig]), sig['id']) is None
        if field != 'amount':
            assert val(evaluate(change_last(df, **{field: 0.}), days, [sig]), sig['id']) is None


def test_second_batch_distinct_windows_and_numeric_reuse():
    df, days = frame()
    # 60日最低价98，最低收盘价100；当前119对应前者超20%，后者未超。
    assert val(evaluate(change_last(df, close=119.), days), 't_lowpos') is False
    # 当前创新高时，排除当前的突破因子 >0，含当前的回撤因子 =0。
    out = evaluate(change_last(df, close=110., high=1000.), days)
    assert out['uf_prev20_close_bias'][-1] == pytest.approx(.1)
    assert out['uf_high20_close_bias'][-1] == pytest.approx(0.)
    assert val(out, 't_newhigh') is True  # 不得误用最高价1000
    # D-20 的异常高收盘只影响此前20日，不属于含D的20日回撤窗口。
    df = change_at(df, df.height - 21, close=200.)
    out = evaluate(df, days)
    assert val(out, 't_newhigh') is False
    assert val(out, 't_dd20') is False
    assert out['uf_high20_close_bias'][-1] == pytest.approx(0.)
    assert val(evaluate(change_last(df, high=10000.), days), 't_dd20') is False


@pytest.mark.parametrize(('amount', 'close', 'expected'), [
    (180., 103., True), (179.9999, 103., False),
    (180., 102., False), (180., 102.0001, True),
])
def test_third_batch_breakout(amount, close, expected):
    df, days = frame()
    # 当前高点远高于收盘，但不能进入“此前5日”基准。
    df = change_last(df, amount=amount, close=close, high=1000.)
    out = evaluate(df, days)
    assert val(out, 't_volbreak') is expected
    assert out['amount_ratio_5d'][-1] == pytest.approx(amount / 100 - 1)
    assert out['uf_prev5_high_bias'][-1] == pytest.approx(close / 102 - 1)
    sig = next(s for s in THIRD_BATCH_SIGNALS if s['id'] == 't_volbreak')
    assert val(evaluate(df.tail(6), days, [sig]), 't_volbreak') is expected
    assert val(evaluate(df.tail(5), days, [sig]), 't_volbreak') is None


@pytest.mark.parametrize(('sid', 'close', 'amount', 'expected'), [
    ('t_longyin_shortzhu', 96., 70., True),
    ('t_longyin_shortzhu', 96.0001, 70., False),
    ('t_longyin_shortzhu', 96., 70.0001, False),
    ('t_longyin_shortzhu', 95.9999, 69.9999, True),
    ('t_changyang_aizhu', 105., 90., True),
    ('t_changyang_aizhu', 104.9999, 90., False),
    ('t_changyang_aizhu', 105., 90.0001, False),
    ('t_changyang_aizhu', 105.0001, 89.9999, True),
])
def test_third_batch_daily_return_not_candle(sid, close, amount, expected):
    df, days = frame()
    # 下跌日故意收阳，上涨日故意收阴；旧公式并不约束实体方向。
    open_ = close - 1 if close < 100 else close + 1
    df = change_last(df, close=close, open=open_, amount=amount, volume=100000.)
    assert val(evaluate(df, days), sid) is expected
    df = df.with_columns(pl.lit(0.).alias('amount'))
    assert val(evaluate(df, days), sid) is None


@pytest.mark.parametrize(('offset', 'expected'), [(-.02, True), (.02, True),
                                                           (-.020001, False), (.020001, False)])
def test_third_batch_pullback_bounds(offset, expected):
    df, days = frame()
    df = change_last(df, close=105., high=106., low=100.25 * (1 + offset))
    assert val(evaluate(df, days), 't_pullback') is expected
    # MA20必须严格上行，不以收盘站上替代；D-20与D同收盘令均线持平。
    flat = change_at(df, df.height-21, close=105.)
    assert val(evaluate(flat, days), 't_pullback') is False
    equal = change_last(frame()[0], low=100.)
    assert val(evaluate(equal, days), 't_pullback') is False


@pytest.mark.parametrize(('amounts', 'expected'), [
    ([100., 150., 105., 157.5], True),
    ([100., 149.999, 104., 156.], False),
    ([100., 150., 105.001, 158.], False),
    ([100., 150., 105., 157.499], False),
    ([100., 160., 100., 160.], True),
    ([157.5, 105., 150., 100.], False),
    ([0., 150., 105., 157.5], None),
    ([100., 0., 105., 157.5], None),
    ([100., 150., 0., 157.5], None),
])
def test_third_batch_amount_sequence(amounts, expected):
    df, days = frame()
    assert val(evaluate(with_tail(df, amount=amounts), days), 't_beiliang_shensuo') is expected


@pytest.mark.parametrize(('amounts', 'close', 'expected'), [
    ([100., 80., 60.], 99., True), ([100., 80., 80.], 99., False),
    ([100., 100., 60.], 99., False), ([100., 80., 60.], 100., False),
])
def test_third_batch_shrinking_amount_not_daily_price_decrease(amounts, close, expected):
    df, days = frame()
    df = with_tail(df, close=[100., 95., 98., close], amount=amounts)
    assert val(evaluate(df, days), 't_shrink_pullback') is expected


@pytest.mark.parametrize(('ret', 'expected'), [(-.03, True), (.03, True),
                                                        (-.030001, False), (.030001, False)])
def test_third_batch_three_yang_return_boundaries(ret, expected):
    df, days = frame()
    prices = [100. * (1 + ret) ** k for k in range(4)]
    df = with_tail(df, close=prices, open=[p - 1 for p in prices[1:]], high=[120., 121., 122.])
    assert val(evaluate(df, days), 't_3yang') is expected
    assert val(evaluate(change_last(df, high=121.), days), 't_3yang') is False
    assert val(evaluate(change_last(df, open=prices[-1]), days), 't_3yang') is False


@pytest.mark.parametrize(('body', 'expected'), [(0., False), (.0001, True), (.05, True), (.050001, False)])
def test_third_batch_small_yang_body_not_daily_return(body, expected):
    df, days = frame()
    df = with_tail(df, open=[100., 110., 120.],
                   close=[100. * (1 + body), 110. * (1 + body), 120. * (1 + body)])
    assert val(evaluate(df, days), 't_small_yang') is expected
    assert val(evaluate(df, days), 't_3yang') is False
    # 任意一根为十字星便不满足三连小阳，不能仅检查最后一根。
    for k in range(3):
        changed = change_at(df, df.height-3+k, close=[100., 110., 120.][k])
        assert val(evaluate(changed, days), 't_small_yang') is False


def test_three_yang_absolute_return_factor_and_condition_limit():
    sig = next(s for s in THIRD_BATCH_SIGNALS if s['id'] == 't_3yang')
    assert len(sig['conditions']) == 8
    custom_signals.validate(sig)
    df, days = frame()
    for close in [97., 103.]:
        out = evaluate(change_last(df, close=close), days)
        assert out['uf_abs_return_1d'][-1] == pytest.approx(.03)
    # 不修改公共上限来让本批通过：9条仍须拒绝。
    with pytest.raises(ValueError, match='最多 8'):
        custom_signals.validate({**sig, 'conditions': sig['conditions'] + [sig['conditions'][0]]})


@pytest.mark.parametrize(('sid', 'ratio', 'expected'), [
    ('t_full_uppersh', 1.5, True), ('t_full_uppersh', 1.49999, False),
    ('t_full_uppersh', 1.50001, True),
    ('t_hammer', .8, True), ('t_hammer', .80001, False), ('t_hammer', .79999, True),
])
def test_fourth_shadow_amount_boundaries(sid, ratio, expected):
    df, days = frame()
    df = change_last(df, open=100., close=101., high=103., low=99.8)
    df = (change_last(df, amount=ratio * 100) if sid == 't_full_uppersh'
          else fourth_amount_window(df, ratio))
    assert val(fourth_eval(df, days), sid) is expected
    if expected:
        assert val(fourth_eval(change_last(df, high=102.9999), days), sid) is False
        assert val(fourth_eval(change_last(df, low=99.7999), days), sid) is False
        # 十字星是有效但不满足，不因实体为0产生除零未知。
        assert val(fourth_eval(change_last(df, close=100.), days), sid) is False
        # 收阴也可满足上影条件，不能偷偷增加收阳条件。
        assert val(fourth_eval(change_last(df, open=101., close=100.), days), sid) is True
    assert val(fourth_eval(df.with_columns(pl.lit(0.).alias('amount')), days), sid) is None


@pytest.mark.parametrize(('high', 'low', 'expected'), [
    (110., 100., True), (109.9999, 100., False), (110.0001, 100., True),
    (110., 100.0001, False),
])
def test_fourth_longleg_range_denominator(high, low, expected):
    df, days = frame()
    df = change_last(df, open=102., close=103., high=high, low=low)
    out = fourth_eval(df, days)
    assert out['uf_day_range_low'][-1] == pytest.approx((high-low)/low)
    assert val(out, 't_full_longleg') is expected
    if expected:
        assert val(fourth_eval(change_last(df, open=103., close=102.), days), 't_full_longleg') is False
        assert val(fourth_eval(change_last(df, close=102.), days), 't_full_longleg') is False


@pytest.mark.parametrize(('previous_close', 'yang_count', 'expected'), [
    (100., 4, True), (99.999, 4, False), (100.001, 4, True),
    (100., 3, False), (100., 5, True),
])
def test_fourth_oversold_count_and_ten_percent(previous_close, yang_count, expected):
    df, days = frame()
    df = with_tail(df, close=[90.] * 6, open=[89.] * yang_count + [90.] * (5-yang_count))
    df = change_at(df, df.height-21, close=previous_close)
    out = fourth_eval(df, days)
    assert out['uf_yang_count_5'][-1] == yang_count
    assert val(out, 't_smallyang') is expected
    # 4阳不要求连续；任一位置十字星都不计作阳。
    if expected and yang_count == 4:
        for k in range(5):
            variant = with_tail(df, open=[90. if i == k else 89. for i in range(5)])
            assert val(fourth_eval(variant, days), 't_smallyang') is True


@pytest.mark.parametrize(('ret', 'expected'), [(.03, True), (-.03, True), (.030001, False), (-.030001, False)])
def test_fourth_oversold_every_return_bound(ret, expected):
    df, days = frame()
    prices = [90. * (1+ret)**k for k in range(6)]
    df = with_tail(df, close=prices, open=[v-1 for v in prices[1:]])
    df = change_at(df, df.height-21, close=prices[-1]/.8)
    assert val(fourth_eval(df, days), 't_smallyang') is expected


@pytest.mark.parametrize(('ret', 'ratio', 'high', 'expected'), [
    (.02, .7, 106., True), (.10, .7, 106., True),
    (.019999, .7, 106., False), (.100001, .7, 106., False),
    (.05, .700001, 106., False), (.05, .7, 106.0001, False),
    (.05, .699999, 105.9999, True),
])
def test_fourth_segmented_consolidation(ret, ratio, high, expected):
    df, days = frame()
    df = with_tail(df, open=[102.] * 5, close=[103.] * 5, high=[high] * 5, low=[100.] * 5)
    df = change_at(df, df.height-6, close=100.*(1+ret))
    df = fourth_amount_window(df, ratio)
    out = fourth_eval(df, days)
    assert out['uf_range_5d_low'][-1] == pytest.approx((high-100.)/100.)
    assert val(out, 't_absorb_break') is expected
    # 不要求D日突破前高；基准不是close，106/103的窄幅不能代替106/100。
    if expected:
        assert df['close'][-1] < df['high'][-1]


def test_fourth_amount_previous_twenty_not_fifteen_and_zero_denominator():
    df, days = frame()
    # 前20日最早5日放额，前15日均额仍100：两个比率不相等。
    df = df.with_columns(pl.when((pl.int_range(pl.len()) >= df.height-25)
                                & (pl.int_range(pl.len()) < df.height-20))
                         .then(300.).otherwise(100.).alias('amount'))
    df = change_last(df, open=100., close=101., high=103., low=99.8)
    out = fourth_eval(df, days)
    assert out['uf_amount_5_prev20'][-1] == pytest.approx(2/3)
    assert val(out, 't_hammer') is True
    zeros = df.with_columns(pl.when(pl.int_range(pl.len()) < df.height-5)
                            .then(0.).otherwise(100.).alias('amount'))
    assert val(fourth_eval(zeros, days), 't_hammer') is None


@pytest.mark.parametrize(('closes', 'amounts', 'count'), [
    ([100., 101., 102., 103.], [200., 150., 100., 50.], 3),
    ([100., 101., 100., 102.], [200., 150., 100., 50.], 2),
    ([100., 100., 100., 102.], [200., 150., 100., 50.], 1),
    ([100., 101., 102., 103.], [200., 200., 100., 50.], 2),
    ([100., 101., 102., 103.], [200., 210., 220., 230.], 0),
])
def test_fourth_joint_count_golden_pillar_and_sanyuan(closes, amounts, count):
    df, days = frame()
    df = with_tail(df, close=closes, amount=amounts, open=[99., 99., 99., 99.])
    out = fourth_eval(df, days)
    assert out['uf_priceup_amountdown_count_3'][-1] == count
    assert val(out, 't_golden_pillar') is (count >= 2)
    assert val(out, 't_sanyuan') is (count == 3)
    # 放额发生在D-3；其此前5日基准改为101便不再达到2倍。
    changed = df
    for k in range(4, 9):
        changed = change_at(changed, df.height-1-k, amount=101.)
    assert val(fourth_eval(changed, days), 't_golden_pillar') is False
    assert val(fourth_eval(change_at(df, df.height-4, open=100.), days), 't_golden_pillar') is False


@pytest.mark.parametrize(('sid', 'field', 'offset'), [
    ('t_smallyang', 'open', 1), ('t_golden_pillar', 'amount', 1), ('t_sanyuan', 'close', 1),
])
def test_fourth_count_missing_input_never_becomes_false(sid, field, offset):
    df, days = frame()
    # 原条件为false时，缺输入仍需盖过false输出unknown。
    df = change_at(df, df.height-1-offset, **{field: None})
    out = fourth_eval(df, days)
    assert val(out, sid) is None
    assert out.select((~pl.col(f'csg_{sid}')).last()).item() is None


@pytest.mark.parametrize(('closes', 'expected'), [
    ([100., 105., 105., 105.], True),
    ([100., 104.9999, 105., 105.], False),
    ([100., 105., 107.1, 107.1], True),
    ([100., 105., 107.1001, 107.1], False),
    ([100., 105., 107.1, 104.958], False),  # -2%但跌破前置收盘
    ([100., 105., 105., 104.9999], False),
    ([100., 105., 106., 105.], True),
])
def test_fourth_dayang_close_returns_not_bodies(closes, expected):
    df, days = frame()
    df = with_tail(df, close=closes, open=[150.] * 4)
    assert val(fourth_eval(df, days), 't_dayang_shuangxiu') is expected


def test_fourth_invalid_ohlc_and_condition_budget():
    df, days = frame()
    for sid in ['t_full_uppersh', 't_full_longleg', 't_hammer', 't_absorb_break']:
        assert val(fourth_eval(change_last(df, high=99.), days), sid) is None
    for sig in FOURTH_BATCH_SIGNALS:
        assert len(sig['conditions']) <= 8
        custom_signals.validate(sig)


@pytest.mark.parametrize(('sid', 'segments', 'n', 'expected'), [
    ('t_sym_triangle', [(120, 80, 100), (115, 85, 100), (110, 90, 100)], 7, True),
    ('t_sym_triangle', [(120, 80, 100), (120, 85, 100), (110, 90, 100)], 7, False),
    ('t_sym_triangle', [(120, 80, 100), (115, 85, 100), (115, 90, 100)], 7, False),
    ('t_sym_triangle', [(120, 80, 100), (115, 80, 100), (110, 90, 100)], 7, False),
    ('t_sym_triangle', [(120, 80, 100), (115, 85, 100), (110, 85, 100)], 7, False),
    ('t_sym_triangle', [(110, 90, 100), (115, 85, 100), (120, 80, 100)], 7, False),
    ('t_falling_wedge', [(120, 80, 100), (110, 75, 100)], 10, True),
    ('t_falling_wedge', [(120, 80, 100), (110, 70, 100)], 10, False),
    ('t_falling_wedge', [(120, 80, 100), (110, 69, 100)], 10, False),
    ('t_falling_wedge', [(120, 80, 100), (110, 80, 100)], 10, False),
    ('t_falling_wedge', [(120, 80, 100), (120, 75, 100)], 10, False),
    ('t_higher_lows_vol', [(120, 80, 100), (115, 85, 1), (110, 90, 201)], 7, True),
    ('t_higher_lows_vol', [(120, 80, 100), (115, 85, 1000), (110, 90, 200)], 7, False),
    ('t_higher_lows_vol', [(120, 80, 100), (115, 85, 1000), (110, 90, 200.001)], 7, True),
    ('t_higher_lows_vol', [(120, 80, 100), (115, 85, 0), (110, 90, 199.999)], 7, False),
    ('t_higher_lows_vol', [(120, 80, 100), (115, 80, 1), (110, 90, 201)], 7, False),
    ('t_higher_lows_vol', [(120, 80, 100), (115, 85, 1), (110, 85, 201)], 7, False),
    ('t_asc_triangle', [(106, 70, 100), (103, 80, 100), (100, 90, 100)], 7, True),
    ('t_asc_triangle', [(106.0001, 70, 100), (103, 80, 100), (100, 90, 100)], 7, False),
    ('t_asc_triangle', [(105.9999, 70, 100), (103, 80, 100), (100, 90, 100)], 7, True),
    ('t_asc_triangle', [(100, 80, 100), (103, 82, 100), (105, 85, 100)], 7, False),
    ('t_asc_triangle', [(100, 80, 100), (103, 82, 100), (106, 85, 100)], 7, False),
    ('t_asc_triangle', [(106, 70, 100), (103, 70, 100), (100, 90, 100)], 7, False),
    ('t_asc_triangle', [(106, 70, 100), (103, 80, 100), (100, 80, 100)], 7, False),
])
def test_fifth_segment_geometry_boundaries(sid, segments, n, expected):
    df, days = segmented_frame(segments, n)
    assert val(fifth_eval(df, days), sid) is expected


@pytest.mark.parametrize('near_amount', [0., 201.])
def test_fifth_zero_amount_denominator_is_unknown(near_amount):
    df, days = segmented_frame([(120, 80, 0), (115, 85, 100), (110, 90, near_amount)])
    out = fifth_eval(df, days)
    assert out['uf_amount_7_far7_ratio'][-1] is None
    assert val(out, 't_higher_lows_vol') is None
    assert out.select((~pl.col('csg_t_higher_lows_vol')).last()).item() is None
    # A separate false price condition must not swallow undefined amount ratio.
    out = fifth_eval(change_last(df, low=80.), days)
    assert val(out, 't_higher_lows_vol') is None


def test_fifth_zero_width_is_defined_and_not_divided():
    df, days = frame()
    df = df.with_columns(pl.lit(100.).alias('high'), pl.lit(100.).alias('low'))
    out = fifth_eval(df, days)
    assert out['uf_width_7_far7_delta'][-1] == 0.
    assert out['uf_width_10_prev10_delta'][-1] == 0.
    assert all(val(out, sig['id']) is False for sig in FIFTH_BATCH_SIGNALS)


def test_fifth_different_close_denominators_and_exact_segment_edges():
    df, days = segmented_frame([(120, 80, 100), (115, 85, 100), (110, 90, 201)])
    # Oldest close 119, middle 86, newest 109: normalized high ratios reverse direction.
    df = with_tail(df, close=[119.]*7 + [86.]*7 + [109.]*7)
    assert val(fifth_eval(df, days), 't_sym_triangle') is True
    # A point just outside the 21-day window must not enter any of its segments.
    outside = change_at(df, df.height-22, high=10000., low=1.)
    assert val(fifth_eval(outside, days), 't_sym_triangle') is True
    # The middle segment's oldest high belongs to the middle, not the far segment.
    inside = change_at(df, df.height-14, high=125.)
    assert val(fifth_eval(inside, days), 't_sym_triangle') is False
    # Low at the first day of the near segment invalidates the latest rise.
    inside = change_at(df, df.height-7, low=80.)
    assert val(fifth_eval(inside, days), 't_higher_lows_vol') is False


@pytest.mark.parametrize('sig', FIFTH_BATCH_SIGNALS, ids=lambda s: s['id'])
def test_fifth_ohlc_validation_and_condition_budget(sig):
    df, days = frame()
    assert len(sig['conditions']) <= 8
    custom_signals.validate(sig)
    # Finite positive fields, but high below open/close at earliest required bar.
    bad = change_at(df, df.height-sig['data_contract']['ohlc_window'], high=99.)
    assert val(evaluate(bad, days, [sig]), sig['id']) is None


@pytest.mark.parametrize(('segments', 'n', 'boundary_difference'), [
    ([(120, 80, 100), (115, 85, 1), (110, 90, 201)], 7, False),
    ([(105.9, 70, 100), (103, 80, 100), (100, 90, 201)], 7, False),
    ([(120, 80, 100), (110, 75, 100)], 10, False),
    ([(106, 70, 100), (103, 80, 100), (100, 90, 201)], 7, True),
])
def test_fifth_old_formula_positive_parity(segments, n, boundary_difference):
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags
    df, days = segmented_frame(segments, n)
    tensor = np.zeros((1, len(days)+1, 8), dtype=np.float64)
    for i, field in enumerate(['open', 'high', 'low', 'close', 'volume', 'amount']):
        tensor[0, :-1, i] = df[field].to_numpy()
        tensor[0, -1, i] = df[field][-1]
    t = str(days[-1] + timedelta(days=2))
    event = dict(t=t, symbol='A', bp=1, yizi_t=0, yizi_b=0, broken_limit=0,
                 phase='', mktcap=100, gap=0., seal_rate=0., limit_up=0, state='')
    tags = build_tags(pd.DataFrame([event]), [str(d) for d in days]+[t], tensor, {'A': 0})
    out = fifth_eval(df, days)
    for sig, bit in zip(FIFTH_BATCH_SIGNALS, [79, 83, 84, 87], strict=True):
        old = bool((tags[0, bit//53] >> (bit%53)) & 1)
        if boundary_difference and bit == 87:
            # 106/100-1 is 0.06000000000000005; strict <= includes the approved 6% boundary.
            assert old is False
            assert val(out, sig['id']) is True
        else:
            assert val(out, sig['id']) == old, sig['id']


def test_fifth_middle_amount_gap_and_zero_numerator():
    df, days = segmented_frame([(120, 80, 100), (115, 85, 0), (110, 90, 0)])
    out = fifth_eval(df, days)
    assert out['uf_amount_7_far7_ratio'][-1] == 0.
    assert val(out, 't_higher_lows_vol') is False
    # Ratio doesn't use the middle segment, but the approved 21-day contract does.
    out = fifth_eval(change_at(df, df.height-10, amount=None), days)
    assert out['uf_amount_7_far7_ratio'][-1] == 0.
    assert val(out, 't_higher_lows_vol') is None


@pytest.mark.parametrize(('prev2', 'prev1', 'prev_open', 'close', 'open_', 'expected'), [
    (100., 95., 103., 99., 98., True),  # -5%, midpoint equality, one flat day
    (95., 95., 103., 99., 98., True),  # other previous day can be flat too
    (101., 95., 103., 99., 98., False),
    (94., 95., 103., 99., 98., False),
    (100., 95.001, 103., 99.1, 98., False),
    (100., 94.999, 103., 99., 98., True),
    (100., 95., 103., 98.999, 98., False),
    (100., 95., 103., 99., 99., False),
    (100., 95., 103., 99., 100., False),
    (100., 95., 94., 96., 95., True),  # previous candle bullish, close return negative
])
def test_sixth_recovery_returns_and_midpoint(prev2, prev1, prev_open, close, open_, expected):
    df, days = frame(125)
    df = with_tail(df, close=[100., prev2, prev1, close], open=[99., 99., prev_open, open_])
    assert val(sixth_eval(df, days), 't_jiyin_ciyang') is expected


@pytest.mark.parametrize(('near', 'far', 'high', 'expected'), [
    (70., 100., 108., True), (70.0001, 100., 108., False),
    (69.9999, 100., 108., True), (70., 100., 108.0001, False),
    (70., 100., 107.9999, True), (0., 100., 108., True),
    (70., 0., 108., None), (0., 0., 108., None),
    (70., 0., 110., None),  # false range cannot swallow unknown denominator
])
def test_sixth_narrow_amount_and_range_bounds(near, far, high, expected):
    df, days = sixth_narrow_frame(near, far, high)
    out = sixth_eval(df, days)
    assert val(out, 't_xianchang') is expected
    if far == 0:
        assert out['uf_amount_10_prev60'][-1] is None
        assert out.select((~pl.col('csg_t_xianchang')).last()).item() is None


def test_sixth_amount_seventy_day_window_and_nonoverlap():
    df, days = sixth_narrow_frame()
    assert val(sixth_eval(df.tail(70), days), 't_xianchang') is True
    assert val(sixth_eval(df.tail(69), days), 't_xianchang') is None
    assert val(sixth_eval(change_at(df, df.height-71, amount=None), days), 't_xianchang') is True
    assert val(sixth_eval(change_at(df, df.height-70, amount=None), days), 't_xianchang') is None
    # Oldest of the previous 60 belongs to the denominator, not an inclusive last-60 mean.
    out = sixth_eval(change_at(df, df.height-70, amount=0.), days)
    assert out['uf_amount_10_prev60'][-1] == pytest.approx(70/(5900/60))
    assert val(out, 't_xianchang') is False
    # D-10 is the last denominator day; D-9 is the first numerator day.
    before = sixth_eval(change_at(df, df.height-11, amount=200.), days)
    after = sixth_eval(change_at(df, df.height-10, amount=200.), days)
    assert before['uf_amount_10_prev60'][-1] == pytest.approx(70/(6100/60))
    assert after['uf_amount_10_prev60'][-1] == pytest.approx(83/100)


@pytest.mark.parametrize(('low', 'close', 'expected'), [
    (98., 100., True), (97.9999, 100., False), (98.0001, 100., True),
    (102., 102., True), (102.0001, 103., False), (101.9999, 102., True),
    (98., 99.9999, False), (98., 100.0001, True),
])
def test_sixth_pivot_distance_and_close_bounds(low, close, expected):
    df, days = sixth_pivot_frame(low, close)
    assert val(sixth_eval(df, days), 't_huangjin_x') is expected


def test_sixth_pivot_excludes_recent_ten_and_window_edges():
    df, days = sixth_pivot_frame()
    for index in (df.height-41, df.height-10, df.height-1):
        out = sixth_eval(change_at(df, index, high=200.), days)
        assert val(out, 't_huangjin_x') is True
        assert out['uf_close_high30_lag10_bias'][-1] == 0.
    for index in (df.height-40, df.height-11):
        assert val(sixth_eval(change_at(df, index, high=200.), days), 't_huangjin_x') is False
    # Low from the first recent day counts even though today's low touches the pivot.
    assert val(sixth_eval(change_at(df, df.height-10, low=97.), days), 't_huangjin_x') is False


@pytest.mark.parametrize(('high', 'close', 'expected'), [
    (105., 99.75, True), (105.0001, 99.9, False), (104.9999, 99.75, True),
    (105., 99.7499, False), (105., 99.7501, True),
])
def test_sixth_bridge_five_percent_and_ninety_five_percent(high, close, expected):
    df, days = sixth_bridge_frame(high, close)
    out = sixth_eval(df, days)
    assert val(out, 't_caihong_qiao') is expected
    assert out['a158_max_20'][-1] == pytest.approx(max(high,104)/close)


@pytest.mark.parametrize('lows', [(80,80,90,95), (80,85,85,95), (80,85,90,90), (95,90,85,80)])
def test_sixth_bridge_all_three_low_steps_are_strict(lows):
    df, days = sixth_bridge_frame(lows=lows)
    assert val(sixth_eval(df, days), 't_caihong_qiao') is False


def test_sixth_bridge_high_not_close_and_exact_segment_edges():
    df, days = sixth_bridge_frame()
    # Closing far below the high in the old segments does not redefine resistance.
    df = with_tail(df, close=[81.]*5+[86.]*5+[91.]*5+[99.75]*5)
    assert val(sixth_eval(df, days), 't_caihong_qiao') is True
    assert val(sixth_eval(change_last(df, close=99.), days), 't_caihong_qiao') is False
    assert val(sixth_eval(change_at(df, df.height-21, high=200.), days), 't_caihong_qiao') is True
    for offset, low in [(15, 80.), (10, 85.), (5, 90.)]:
        assert val(sixth_eval(change_at(df, df.height-offset, low=low), days), 't_caihong_qiao') is False
    assert val(sixth_eval(change_at(df, df.height-20, high=200.), days), 't_caihong_qiao') is False


@pytest.mark.parametrize('sig', SIXTH_BATCH_SIGNALS, ids=lambda s: s['id'])
def test_sixth_ohlc_contract_condition_budget_and_positive_prefix(sig):
    custom_signals.validate(sig)
    assert len(sig['conditions']) <= 8
    if sig['id'] == 't_jiyin_ciyang':
        df, days = frame(125)
        df = with_tail(df, close=[100.,100.,95.,99.],open=[99.,99.,103.,98.])
    elif sig['id'] == 't_xianchang':
        df, days = sixth_narrow_frame()
    elif sig['id'] == 't_huangjin_x':
        df, days = sixth_pivot_frame()
    else:
        df, days = sixth_bridge_frame()
    out = evaluate(df, days, [sig])
    assert val(out, sig['id']) is True
    future_day = days[-1]+timedelta(days=2)
    future = df.tail(1).with_columns(pl.lit(future_day).alias('date'),pl.lit(10000.).alias('high'))
    actual = evaluate(pl.concat([df,future]),[*days, future_day],[sig])
    assert actual[f"csg_{sig['id']}"][:-1].equals(out[f"csg_{sig['id']}"])
    if sig['data_contract']['ohlc_window']:
        bad = change_at(df,df.height-sig['data_contract']['ohlc_window'],high=1.)
        assert val(evaluate(bad,days,[sig]),sig['id']) is None


@pytest.mark.parametrize('case', ['recovery','narrow','pivot','bridge'])
def test_sixth_old_positive_formula_parity(case):
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags
    if case == 'recovery':
        df, days = frame(125)
        df = with_tail(df,close=[100.,99.,94.,99.],open=[99.,99.,102.,98.],high=[110.]*4,low=[90.]*4)
    elif case == 'narrow':
        df, days = sixth_narrow_frame(69.)
    elif case == 'pivot':
        df, days = sixth_pivot_frame(99.)
    else:
        df, days = sixth_bridge_frame(104.9,99.9)
    tensor = np.zeros((1,len(days)+1,8),dtype=np.float64)
    for i, field in enumerate(['open','high','low','close','volume','amount']):
        tensor[0,:-1,i] = df[field].to_numpy()
        tensor[0,-1,i] = df[field][-1]
    t = str(days[-1]+timedelta(days=2))
    event = dict(t=t,symbol='A',bp=1,yizi_t=0,yizi_b=0,broken_limit=0,
                 phase='',mktcap=100,gap=0.,seal_rate=0.,limit_up=0,state='')
    tags = build_tags(pd.DataFrame([event]),[str(d) for d in days]+[t],tensor,{'A':0})
    out = sixth_eval(df,days)
    for sig,bit in zip(SIXTH_BATCH_SIGNALS,[64,66,67,72],strict=True):
        assert val(out,sig['id']) == bool((tags[0,bit//53] >> (bit%53)) & 1), sig['id']


@pytest.mark.parametrize(('ret', 'expected'), [(0.,False),(.03,True),(.030001,False),(-.001,False),(.000001,True)])
@pytest.mark.parametrize('index', [0,1,2])
def test_seventh_rising_each_return(ret, expected, index):
    returns=[.01]*3
    returns[index]=ret
    df, days=seventh_rising_frame(returns)
    assert val(seventh_eval(df,days),'t_binglin') is expected


def test_seventh_rising_peak_edges_and_daily_high_not_close():
    df,days=seventh_rising_frame()
    # All four candles bearish, yet the three close returns are positive.
    assert val(seventh_eval(df,days),'t_binglin') is True
    assert val(seventh_eval(change_last(df,high=121.),days),'t_binglin') is True
    assert val(seventh_eval(change_at(df,df.height-2,high=120.),days),'t_binglin') is False
    for idx,high in [(df.height-1,108.),(df.height-2,104.)]:
        assert val(seventh_eval(change_at(df,idx,high=high),days),'t_binglin') is False
    # Choose 3% growth so final close is exactly the independent historical peak.
    df,days=seventh_rising_frame((.03,.03,.03))
    peak=df['close'][-1]
    df=change_at(df,df.height-10,high=peak)
    assert val(seventh_eval(df,days),'t_binglin') is True
    assert val(seventh_eval(change_last(df,close=peak+.0001),days),'t_binglin') is False
    # Only D-20 can supply the left peak; D-21 is outside the peak window.
    df,days=seventh_rising_frame()
    df=change_at(df,df.height-10,high=102.)
    assert val(seventh_eval(change_at(df,df.height-21,high=120.),days),'t_binglin') is True
    assert val(seventh_eval(change_at(df,df.height-22,high=120.),days),'t_binglin') is False


@pytest.mark.parametrize(('drop','rebound','high','close','open_','expected'), [
    (-.03,.04,115.,101.,102.,True),(-.029999,.04,115.,101.,102.,False),
    (-.030001,.04,115.,101.,102.,True),(-.03,.039999,115.,101.,102.,False),
    (-.03,.040001,115.,101.,102.,True),(-.03,.04,115.0001,101.,102.,False),
    (-.03,.04,114.9999,101.,102.,True),(-.03,.04,115.,100.,102.,False),
    (-.03,.04,115.,101.,101.,False),(-.03,.04,115.,102.,101.,False),
])
def test_seventh_v_boundaries(drop,rebound,high,close,open_,expected):
    df,days=seventh_v_frame(drop,rebound,high,close,open_)
    assert val(seventh_eval(df,days),'t_v_fanzhuan_yiyin') is expected


def test_seventh_v_fifteen_day_contract_and_segment_offsets():
    df,days=seventh_v_frame()
    assert val(seventh_eval(df.tail(15),days),'t_v_fanzhuan_yiyin') is True
    assert val(seventh_eval(df.tail(14),days),'t_v_fanzhuan_yiyin') is None
    assert val(seventh_eval(change_at(df,df.height-16,close=None,high=1000.),days),'t_v_fanzhuan_yiyin') is True
    for idx in (df.height-15,df.height-6):
        assert val(seventh_eval(change_at(df,idx,high=120.),days),'t_v_fanzhuan_yiyin') is False
    assert val(seventh_eval(change_at(df,df.height-5,high=1000.),days),'t_v_fanzhuan_yiyin') is True
    # Move the rebound to D, leaving D-1 weak; it must not substitute for the approved interval.
    wrong=change_at(df,df.height-2,close=106.)
    wrong=change_last(wrong,close=111.,open=112.,high=113.)
    assert val(seventh_eval(wrong,days),'t_v_fanzhuan_yiyin') is False


@pytest.mark.parametrize(('right','neck','close','expected'), [
    (103.,112.,112.,True),(103.0001,112.,112.,False),
    (97.,112.,112.,True),(96.9999,112.,112.,False),
    (102.,110.,110.,False),(102.,110.0001,110.0001,True),
    (102.,109.9999,109.9999,False),(102.,112.,111.9999,False),
    (102.,112.,112.0001,True),
])
def test_seventh_double_bottom_bounds(right,neck,close,expected):
    df,days=seventh_double_frame(right,neck,close)
    assert val(seventh_eval(df,days),'t_double_bottom') is expected


def test_seventh_double_bottom_slice_edges_and_overlap():
    df,days=seventh_double_frame()
    assert val(seventh_eval(df,days),'t_double_bottom') is True
    for offset in (27,18):
        assert val(seventh_eval(change_at(df,df.height-1-offset,low=90.),days),'t_double_bottom') is False
    for offset in (14,5):
        assert val(seventh_eval(change_at(df,df.height-1-offset,low=90.),days),'t_double_bottom') is False
    for offset in (28,17,4):
        assert val(seventh_eval(change_at(df,df.height-1-offset,low=90.),days),'t_double_bottom') is True
    for offset in (19,12):
        # Neck overlaps the left/right low windows: those high values really do participate.
        assert val(seventh_eval(change_at(df,df.height-1-offset,high=120.),days),'t_double_bottom') is False
    for offset in (20,11):
        assert val(seventh_eval(change_at(df,df.height-1-offset,high=120.),days),'t_double_bottom') is True


@pytest.mark.parametrize(('open_','close','high','low','expected'), [
    (100.,101.,102.,98.,True),(100.,101.,102.,98.0001,False),
    (100.,101.,102.,97.9999,True),(101.,100.,102.,98.,False),
    (100.,100.,102.,98.,False),(100.,101.,104.,100.,False),
])
def test_seventh_lower_shadow_bullish_not_doji(open_,close,high,low,expected):
    df,days=frame()
    df=change_last(df,open=open_,close=close,high=high,low=low)
    out=evaluate(df,days)
    assert val(out,'t_long_lower_shadow') is expected
    if open_==101. and close==100.:
        assert val(out,'t_lowersh') is True
    if expected:
        assert val(out,'t_full_longleg') is False  # no 10% amplitude in this candle


@pytest.mark.parametrize('sig',SEVENTH_BATCH_SIGNALS,ids=lambda s:s['id'])
def test_seventh_positive_future_invariance_and_ohlc(sig):
    custom_signals.validate(sig)
    assert len(sig['conditions'])<=8
    if sig['id']=='t_binglin':
        df,days=seventh_rising_frame()
        assert len(sig['conditions'])==8
    elif sig['id']=='t_v_fanzhuan_yiyin':
        df,days=seventh_v_frame()
    elif sig['id']=='t_double_bottom':
        df,days=seventh_double_frame()
    else:
        df,days=frame()
        df=change_last(df,open=100.,close=101.,high=102.,low=98.)
    out=evaluate(df,days,[sig])
    assert val(out,sig['id']) is True
    day=days[-1]+timedelta(days=2)
    future=df.tail(1).with_columns(pl.lit(day).alias('date'),pl.lit(10000.).alias('high'))
    actual=evaluate(pl.concat([df,future]),[*days,day],[sig])
    assert actual[f"csg_{sig['id']}"][:-1].equals(out[f"csg_{sig['id']}"])
    bad=change_at(df,df.height-sig['data_contract']['ohlc_window'],high=1.)
    assert val(evaluate(bad,days,[sig]),sig['id']) is None


@pytest.mark.parametrize('case',['rising','v','double','shadow'])
def test_seventh_old_positive_parity(case):
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags
    if case=='rising':
        df,days=seventh_rising_frame()
    elif case=='v':
        df,days=seventh_v_frame(-.031,.041,114.)
    elif case=='double':
        df,days=seventh_double_frame()
    else:
        df,days=frame(125)
        df=change_last(df,open=100.,close=101.,high=102.,low=98.)
    tensor=np.zeros((1,len(days)+1,8),dtype=np.float64)
    for i,field in enumerate(['open','high','low','close','volume','amount']):
        tensor[0,:-1,i]=df[field].to_numpy()
        tensor[0,-1,i]=df[field][-1]
    t=str(days[-1]+timedelta(days=2))
    event=dict(t=t,symbol='A',bp=1,yizi_t=0,yizi_b=0,broken_limit=0,phase='',mktcap=100,gap=0.,seal_rate=0.,limit_up=0,state='')
    tags=build_tags(pd.DataFrame([event]),[str(d) for d in days]+[t],tensor,{'A':0})
    out=seventh_eval(df,days)
    for sig,bit in zip(SEVENTH_BATCH_SIGNALS,[68,78,82,88],strict=True):
        assert val(out,sig['id'])==bool((tags[0,bit//53] >> (bit%53)) & 1),sig['id']


@pytest.mark.parametrize('scale', [1., 10., .1, .01, .0333])
@pytest.mark.parametrize(('neck','expected'), [(110.,False),(110.0001,True),(109.9999,False)])
def test_seventh_strict_neck_boundary_across_price_scales(scale,neck,expected):
    df,days=seventh_double_frame(neck=neck)
    df=df.with_columns([pl.col(f)*scale for f in ('open','high','low','close')])
    assert val(seventh_eval(df,days),'t_double_bottom') is expected


def test_seventh_ratio_contract_replaces_unreleased_bias():
    ids = [factor['id'] for factor in FACTORS[:40]]
    assert len(ids) == len(set(ids)) == 40
    assert 'uf_high8_lag12_low10_lag18_bias' not in ids
    assert 'uf_high8_lag12_low10_lag18_ratio' in ids
    sig = next(s for s in SEVENTH_BATCH_SIGNALS if s['id'] == 't_double_bottom')
    condition = next(c for c in sig['conditions'] if c['left'] == 'uf_high8_lag12_low10_lag18_ratio')
    assert (condition['op'], condition['right']) == ('>', '1.10')
    df, days = seventh_double_frame(neck=110.)
    out = seventh_eval(df, days)
    assert out['uf_high8_lag12_low10_lag18_ratio'][-1] == 1.10
    assert val(out, 't_double_bottom') is False
    missing = seventh_eval(change_at(df, df.height-28, low=0.), days)
    assert missing['uf_high8_lag12_low10_lag18_ratio'][-1] is None
    assert val(missing, 't_double_bottom') is None



@pytest.mark.parametrize(('first_close', 'expected'), [
    (97., True), (97.0001, False), (96.9999, True), (100., False),
])
def test_eighth_morning_daily_decline(first_close, expected):
    df, days = eighth_morning_frame(first_close=first_close)
    assert val(eighth_eval(df, days), 't_morning_star') is expected


@pytest.mark.parametrize(('middle_open', 'expected'), [
    (96., True), (96.96, True), (96.9601, False), (95.04, True), (95.0399, False),
])
def test_eighth_morning_body_close_denominator(middle_open, expected):
    df, days = eighth_morning_frame(middle_open=middle_open)
    assert val(eighth_eval(df, days), 't_morning_star') is expected
    # 96.9601/96 is outside 1%, though dividing the body by open would pass.
    if middle_open == 96.9601:
        assert abs(96.-middle_open)/middle_open < .01


@pytest.mark.parametrize('scale', [1., 10., .1, .01, .0333])
@pytest.mark.parametrize(('offset', 'expected'), [(0., False), (.0001, True), (-.0001, False)])
def test_eighth_morning_strict_midpoint_scales(scale, offset, expected):
    df, days = eighth_morning_frame()
    df = df.with_columns([pl.col(f)*scale for f in ('open', 'high', 'low', 'close')])
    midpoint = (df['open'][-3]+df['close'][-3])/2
    df = change_last(df, close=midpoint+offset*scale)
    assert val(eighth_eval(df, days), 't_morning_star') is expected


def test_eighth_morning_corrects_jump_up_and_does_not_require_last_bull():
    df, days = eighth_morning_frame()
    assert df['close'][-1] < df['open'][-1]  # 收阴但超过旧中点仍可命中。
    assert val(eighth_eval(df, days), 't_morning_star') is True
    df = change_at(df, df.height-4, close=90.)
    assert abs(df['close'][-3]/df['close'][-4]-1) > .03
    assert df['close'][-3] < df['open'][-3]
    assert val(eighth_eval(df, days), 't_morning_star') is False


@pytest.mark.parametrize(('close', 'amount', 'expected'), [
    (110., 300., True), (109.9999, 300., False), (110.0001, 300., True),
    (110., 299.9999, False), (110., 300.0001, True), (110., 0., False),
])
def test_eighth_momentum_bounds(close, amount, expected):
    df, days = eighth_momentum_frame(close, amount)
    assert val(eighth_eval(df, days), 't_momentum_accel') is expected


@pytest.mark.parametrize(('near', 'far', 'expected', 'old'), [
    (100., 1000., True, False), (200., 1., False, True),
])
def test_eighth_momentum_uses_adjacent_previous_five(near, far, expected, old):
    df, days = eighth_momentum_frame()
    # D-5在两个窗口中重叠，其余4日必须明确区分。
    df = with_tail(df, amount=[far]*4 + [near]*5 + [300.])
    assert (300./((near+4*far)/5) >= 3) is old
    assert val(eighth_eval(df, days), 't_momentum_accel') is expected
    assert val(eighth_eval(df.tail(6), days), 't_momentum_accel') is expected
    # 不需要OHLC：删除这些列不会改变只依赖收盘/成交额的信号。
    sig = [s for s in EIGHTH_BATCH_SIGNALS if s['id'] == 't_momentum_accel']
    assert val(eighth_raw_eval(df.drop('open', 'high', 'low'), days, sig), 't_momentum_accel') is expected


@pytest.mark.parametrize(('ratio', 'amount', 'expected'), [
    (.8, 200., True), (.7999, 200., False), (.8001, 200., True),
    (.8, 199.9999, False), (.8, 200.0001, True), (.8, 0., False),
])
def test_eighth_gap_ratio_and_amount(ratio, amount, expected):
    df, days = eighth_gap_frame(ratio, amount)
    assert val(eighth_eval(df, days), 't_breakaway_gap') is expected


@pytest.mark.parametrize(('field', 'value', 'expected'), [
    ('open', 100., False), ('open', 99.9, False),
    ('low', 100., False), ('low', 99.9, False), ('low', 100.0001, True),
])
def test_eighth_gap_strict_unfilled_boundary(field, value, expected):
    df, days = eighth_gap_frame()
    if field == 'open':  # 保持有效OHLC，避免把false误测成unknown。
        df = change_last(df, low=99.)
    assert val(eighth_eval(change_last(df, **{field: value}), days), 't_breakaway_gap') is expected


def test_eighth_gap_zero_move_and_zero_amount_denominators():
    df, days = eighth_gap_frame()
    flat = df.with_columns(pl.Series('close', [104.]*df.height))
    flat = with_tail(flat, open=[104., 104.], high=[106., 106.], low=[103., 103.])
    assert val(eighth_eval(flat, days), 't_breakaway_gap') is None
    assert val(eighth_eval(change_at(df, df.height-2, amount=0.), days), 't_breakaway_gap') is None
    df, days = eighth_momentum_frame()
    df = with_tail(df, amount=[0.]*5+[300.])
    assert val(eighth_eval(df, days), 't_momentum_accel') is None


@pytest.mark.parametrize('case', ['morning', 'momentum', 'gap'])
def test_eighth_contract_missing_calendar_and_future_invariance(case):
    builder, index = {'morning': (eighth_morning_frame, 0),
                      'momentum': (eighth_momentum_frame, 1),
                      'gap': (eighth_gap_frame, 2)}[case]
    df, days = builder()
    sig = EIGHTH_BATCH_SIGNALS[index]
    sid, contract = sig['id'], sig['data_contract']
    n = max(contract['inputs'].values())
    assert val(evaluate(df, days, [sig]), sid) is True
    assert val(evaluate(df.tail(n), days, [sig]), sid) is True
    assert val(evaluate(df.tail(n-1), days, [sig]), sid) is None
    for field, window in contract['inputs'].items():
        for offset in {0, window-1}:
            broken = change_at(df, df.height-1-offset, **{field: None})
            out = evaluate(broken, days, [sig])
            assert val(out, sid) is None, (field, offset)
            assert out.select((~pl.col(f'csg_{sid}')).last()).item() is None
        assert val(eighth_raw_eval(df.drop(field), days, [sig]), sid) is None
    hole = df.filter(pl.col('date') != days[-2])
    assert val(evaluate(hole, days, [sig]), sid) is None
    assert val(evaluate(df, days[:-1], [sig]), sid) is None
    duplicate = pl.concat([df, df.tail(1)])
    assert val(evaluate(duplicate, days, [sig]), sid) is None
    with pytest.raises(ValueError, match='排序'):
        eighth_raw_eval(df.reverse(), days, [sig])
    # 同一股票追加未来一根极端日线；历史所有信号输出不变。
    from datetime import timedelta
    future_day = days[-1]+timedelta(days=2)
    future = change_last(df.tail(1), open=1., high=1000., low=.1, close=999., amount=0.)
    future = future.with_columns(pl.lit(future_day).alias('date'))
    original = evaluate(df, days, [sig])
    extended = evaluate(pl.concat([df, future]), [*days, future_day], [sig])
    assert extended[f'csg_{sid}'][:-1].equals(original[f'csg_{sid}'])
    other = df.with_columns(pl.lit('B').alias('symbol'), pl.lit(None).cast(pl.Float64).alias('close'))
    mixed = evaluate(pl.concat([df, other]), days, [sig])
    assert mixed.filter(pl.col('symbol') == 'A')[f'csg_{sid}'].equals(original[f'csg_{sid}'])
    assert mixed.filter(pl.col('symbol') == 'B')[f'csg_{sid}'].null_count() == df.height


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), 0., -1.])
@pytest.mark.parametrize('case', ['morning', 'momentum', 'gap'])
def test_eighth_invalid_required_price_overrides_false(case, bad):
    builder, index = {'morning': (eighth_morning_frame, 0),
                      'momentum': (eighth_momentum_frame, 1),
                      'gap': (eighth_gap_frame, 2)}[case]
    df, days = builder()
    sig = EIGHTH_BATCH_SIGNALS[index]
    df = change_last(df, close=bad, amount=0.)
    assert val(evaluate(df, days, [sig]), sig['id']) is None


@pytest.mark.parametrize('index', [0, 2])
def test_eighth_illegal_ohlc(index):
    df, days = (eighth_morning_frame if index == 0 else eighth_gap_frame)()
    sig = EIGHTH_BATCH_SIGNALS[index]
    assert val(evaluate(change_last(df, high=1.), days, [sig]), sig['id']) is None


@pytest.mark.parametrize('index', [1, 2])
@pytest.mark.parametrize('bad', [-1., float('nan'), float('inf')])
def test_eighth_bad_amount(index, bad):
    df, days = (eighth_momentum_frame if index == 1 else eighth_gap_frame)()
    sig = EIGHTH_BATCH_SIGNALS[index]
    assert val(evaluate(change_last(df, amount=bad), days, [sig]), sig['id']) is None


@pytest.mark.parametrize('scale', [1., 10., .1, .01, .0333])
@pytest.mark.parametrize(('ratio', 'expected'), [(.8, True), (.7999, False), (.8001, True)])
def test_eighth_gap_threshold_across_price_scales(scale, ratio, expected):
    df, days = eighth_gap_frame(ratio)
    df = df.with_columns([pl.col(f)*scale for f in ('open', 'high', 'low', 'close')])
    out = eighth_eval(df, days)
    assert val(out, 't_breakaway_gap') is expected, out['uf_open_gap_close_move14_ratio'][-1]


def test_eighth_zero_move_stays_unknown_even_with_finite_new_ratio():
    df, days = eighth_gap_frame()
    df = df.with_columns(pl.Series('close', [104.]*df.height),
                         pl.Series('open', [104.]*df.height),
                         pl.Series('high', [106.]*df.height),
                         pl.Series('low', [103.]*df.height))
    out = eighth_eval(df, days)
    assert out['uf_open_gap08_required_ratio'][-1] == pytest.approx(104/106)
    assert out['uf_open_gap_close_move14_ratio'][-1] is None
    assert val(out, 't_breakaway_gap') is None


@pytest.mark.parametrize('case', ['morning', 'morning_jump', 'momentum_new', 'momentum_old', 'gap'])
def test_eighth_old_formula_parity_and_intentional_differences(case):
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags

    if case.startswith('morning'):
        df, days = eighth_morning_frame()
        bit, sid, old_expected, expected = 41, 't_morning_star', True, True
        if case == 'morning_jump':
            df = change_at(df, df.height-4, close=90.)
            expected = False
    elif case.startswith('momentum'):
        df, days = eighth_momentum_frame()
        bit, sid = 89, 't_momentum_accel'
        near, far = (100., 1000.) if case == 'momentum_new' else (200., 1.)
        df = with_tail(df, amount=[far]*4+[near]*5+[300.])
        expected, old_expected = case == 'momentum_new', case == 'momentum_old'
    else:
        df, days = eighth_gap_frame(.81)
        bit, sid, old_expected, expected = 91, 't_breakaway_gap', True, True
    # 旧标签读取T的前一日；在D后加一根占位行情，只对齐一次。
    future = days[-1]+timedelta(days=2)
    extended = pl.concat([df, df.tail(1).with_columns(pl.lit(future).alias('date'))])
    tensor = np.zeros((1, extended.height, 8), dtype=np.float64)
    for i, field in enumerate(['open', 'high', 'low', 'close', 'volume', 'amount']):
        tensor[0, :, i] = extended[field].to_numpy()
    row = dict(t=str(future), symbol='A', bp=1, yizi_t=0, yizi_b=0,
               broken_limit=0, phase='', mktcap=100, gap=0., seal_rate=0., limit_up=0, state='')
    tags = build_tags(pd.DataFrame([row]), [str(d) for d in [*days, future]], tensor, {'A': 0})
    actual_old = bool((tags[0, bit//53] >> (bit % 53)) & 1)
    assert actual_old is old_expected
    assert val(eighth_eval(df, days), sid) is expected


@pytest.mark.parametrize('scale', [1., 10., .1, .01, .0333])
@pytest.mark.parametrize(('width', 'expected'), [(9., True), (8.9999, True), (9.0001, False)])
def test_ninth_compression_boundary_and_scales(scale, width, expected):
    df, days = ninth_compress_frame(near_width=width)
    df = df.with_columns([pl.col(f)*scale for f in ('open', 'high', 'low', 'close')])
    out = ninth_eval(df, days)
    assert val(out, 't_amp_mean_compress') is expected, out['uf_intraday_range_mean5_20_ratio'][-1]


def test_ninth_compression_is_mean_daily_range_not_range_of_window():
    df, days = ninth_compress_frame()
    out = ninth_eval(df, days)
    assert out['uf_intraday_range_mean5_20_ratio'][-1] == pytest.approx(.6)
    assert (df['high'].tail(5).max()-df['low'].tail(5).min())/(df['high'].tail(20).max()-df['low'].tail(20).min()) == pytest.approx(9/17)
    # 只改变早段收盘，区间极值不变；日内振幅均值应随每日分母变化。
    changed = change_at(df, df.height-20, close=110.)
    actual = ninth_eval(changed, days)
    assert actual['uf_intraday_range_mean5_20_ratio'][-1] > .6
    assert val(actual, 't_amp_mean_compress') is False
    # 窗口外的极端高点不应进入20日分母。
    outside = change_at(df, df.height-21, high=10000.)
    assert ninth_eval(outside, days)['uf_intraday_range_mean5_20_ratio'][-1] == out['uf_intraday_range_mean5_20_ratio'][-1]


@pytest.mark.parametrize(('near', 'far', 'expected'), [(0., 17., True), (0., 0., None)])
def test_ninth_compression_zero_width(near, far, expected):
    df, days = ninth_compress_frame(near, far)
    assert val(ninth_eval(df, days), 't_amp_mean_compress') is expected


@pytest.mark.parametrize('yin_position', range(5))
def test_ninth_four_yang_requires_last_yin(yin_position):
    df, days = ninth_four_yang_frame()
    opens = [99.]*5
    amounts = [100.]*5
    opens[yin_position], amounts[yin_position] = 101., 50.
    df = with_tail(df, open=opens, amount=amounts)
    assert val(ninth_eval(df, days), 't_4yang1yin') is (yin_position == 4)


@pytest.mark.parametrize('position', range(5))
def test_ninth_four_yang_excludes_doji(position):
    df, days = ninth_four_yang_frame()
    df = change_at(df, df.height-5+position, open=100.)
    assert val(ninth_eval(df, days), 't_4yang1yin') is False


@pytest.mark.parametrize('scale', [1., 10., .1, .0333])
@pytest.mark.parametrize(('amount', 'expected'), [(99.9999, True), (100., False), (100.0001, False), (0., True)])
def test_ninth_four_yang_amount_strict_boundary(scale, amount, expected):
    df, days = ninth_four_yang_frame(amount)
    df = df.with_columns(pl.col('amount')*scale)
    assert val(ninth_eval(df, days), 't_4yang1yin') is expected


def test_ninth_four_yang_includes_today_in_amount_mean():
    df, days = ninth_four_yang_frame(90.)
    df = change_at(df, df.height-6, amount=0.)
    out = ninth_eval(df, days)
    assert out['uf_amount_current_mean5_ratio'][-1] == pytest.approx(90/98)
    assert 90/((0+4*100)/5) > 1  # 用不含当日的此前5日会错误拒绝。
    assert val(out, 't_4yang1yin') is True
    assert val(ninth_eval(df.tail(5), days), 't_4yang1yin') is True
    assert val(ninth_eval(with_tail(df, amount=[0.]*5), days), 't_4yang1yin') is None


@pytest.mark.parametrize(('highs', 'amounts', 'expected'), [
    ((130.,120.,110.), (300.,200.,100.), True),
    ((130.,120.,120.), (300.,200.,100.), False),
    ((130.,130.,110.), (300.,200.,100.), False),
    ((110.,120.,130.), (300.,200.,100.), False),
    ((130.,120.,110.), (300.,200.,200.), False),
    ((130.,120.,110.), (300.,300.,100.), False),
    ((130.,120.,110.), (100.,200.,300.), False),
    ((130.,120.,100.), (300.,200.,0.), True),
    ((130.,100.,100.), (300.,200.,100.), False),
    ((130.,120.,110.), (300.,0.,0.), None),
    ((130.,120.,110.), (0.,200.,100.), None),
    ((130.,120.,110.), (0.,0.,0.), None),
])
def test_ninth_vcp_strict_order_and_zero_segments(highs, amounts, expected):
    df, days = ninth_vcp_frame(highs, amounts)
    assert val(ninth_eval(df, days), 't_vcp_3seg8') is expected


@pytest.mark.parametrize('scale', [1., 10., .1, .01, .0333])
@pytest.mark.parametrize(('highs', 'expected'), [((130.,120.,110.), True), ((130.,120.,120.), False)])
def test_ninth_vcp_geometry_scale_and_equal_width(scale, highs, expected):
    df, days = ninth_vcp_frame(highs)
    df = df.with_columns([pl.col(f)*scale for f in ('open','high','low','close','amount')])
    assert val(ninth_eval(df, days), 't_vcp_3seg8') is expected


@pytest.mark.parametrize('offset', [0, 7, 8, 15, 16, 23])
def test_ninth_vcp_exact_segment_edges(offset):
    df, days = ninth_vcp_frame()
    df = change_at(df, df.height-1-offset, high=150., amount=1000.)
    out = ninth_eval(df, days)
    for lag in (0,8,16):
        i = df.height-1-lag
        h = df['high'][i-7:i+1].max()
        low = df['low'][i-7:i+1].min()
        assert out['uf_range_8d_low'][i] == pytest.approx((h-low)/low)
    for lag in (0,8):
        i = df.height-1-lag
        assert out['uf_amount_8_prev8_ratio'][i] == pytest.approx(df['amount'][i-7:i+1].mean()/df['amount'][i-15:i-7].mean())
    baseline, _ = ninth_vcp_frame()
    outside = change_at(baseline, baseline.height-25, high=10000., amount=100000.)
    assert val(ninth_eval(outside, days), 't_vcp_3seg8') is True


@pytest.mark.parametrize('case', ['compress', 'fouryang', 'vcp'])
def test_ninth_volume_is_not_amount(case):
    builder, sid = {'compress': (ninth_compress_frame, 't_amp_mean_compress'),
                    'fouryang': (ninth_four_yang_frame, 't_4yang1yin'),
                    'vcp': (ninth_vcp_frame, 't_vcp_3seg8')}[case]
    df, days = builder()
    out = ninth_eval(df, days)
    for volume in [0., -100., None]:
        actual = ninth_eval(df.with_columns(pl.lit(volume).cast(pl.Float64).alias('volume')), days)
        assert actual[f'csg_{sid}'].equals(out[f'csg_{sid}'])


@pytest.mark.parametrize('case', ['compress', 'fouryang', 'vcp'])
def test_ninth_contract_missing_calendar_and_future_invariance(case):
    builder, index = {'compress': (ninth_compress_frame, 0),
                      'fouryang': (ninth_four_yang_frame, 1),
                      'vcp': (ninth_vcp_frame, 2)}[case]
    df, days = builder()
    sig = NINTH_BATCH_SIGNALS[index]
    sid, contract = sig['id'], sig['data_contract']
    n = max(contract['inputs'].values())
    assert val(evaluate(df, days, [sig]), sid) is True
    assert val(evaluate(df.tail(n), days, [sig]), sid) is True
    assert val(evaluate(df.tail(n-1), days, [sig]), sid) is None
    for field, window in contract['inputs'].items():
        for offset in {0, window-1}:
            broken = change_at(df, df.height-1-offset, **{field: None})
            out = evaluate(broken, days, [sig])
            assert val(out, sid) is None, (field, offset)
            assert out.select((~pl.col(f'csg_{sid}')).last()).item() is None
        assert val(eighth_raw_eval(df.drop(field), days, [sig]), sid) is None
    hole = df.filter(pl.col('date') != days[-2])
    assert val(evaluate(hole, days, [sig]), sid) is None
    assert val(evaluate(df, days[:-1], [sig]), sid) is None
    duplicate = pl.concat([df, df.tail(1)])
    assert val(evaluate(duplicate, days, [sig]), sid) is None
    with pytest.raises(ValueError, match='排序'):
        eighth_raw_eval(df.reverse(), days, [sig])
    # 同一股票追加未来一根极端日线；历史所有信号输出不变。
    from datetime import timedelta
    future_day = days[-1]+timedelta(days=2)
    future = change_last(df.tail(1), open=1., high=1000., low=.1, close=999., amount=0.)
    future = future.with_columns(pl.lit(future_day).alias('date'))
    original = evaluate(df, days, [sig])
    extended = evaluate(pl.concat([df, future]), [*days, future_day], [sig])
    assert extended[f'csg_{sid}'][:-1].equals(original[f'csg_{sid}'])
    other = df.with_columns(pl.lit('B').alias('symbol'), pl.lit(None).cast(pl.Float64).alias('close'))
    mixed = evaluate(pl.concat([df, other]), days, [sig])
    assert mixed.filter(pl.col('symbol') == 'A')[f'csg_{sid}'].equals(original[f'csg_{sid}'])
    assert mixed.filter(pl.col('symbol') == 'B')[f'csg_{sid}'].null_count() == df.height



@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), 0., -1.])
@pytest.mark.parametrize('case', ['compress', 'fouryang', 'vcp'])
def test_ninth_invalid_required_price_overrides_false(case, bad):
    builder, index = {'compress': (ninth_compress_frame, 0),
                      'fouryang': (ninth_four_yang_frame, 1),
                      'vcp': (ninth_vcp_frame, 2)}[case]
    df, days = builder()
    sig = NINTH_BATCH_SIGNALS[index]
    df = change_last(df, close=bad, amount=0.)
    assert val(evaluate(df, days, [sig]), sig['id']) is None



@pytest.mark.parametrize('case', [
    'compress_true', 'compress_false', 'compress_missing', 'compress_zero',
    'fouryang_true', 'fouryang_wrong_order', 'fouryang_doji',
    'vcp_true', 'vcp_equal', 'vcp_missing_amount', 'vcp_zero_amount',
])
def test_ninth_old_tag_parity_and_approved_differences(case):
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags

    if case.startswith('compress'):
        df, days = ninth_compress_frame(near_width=8.)
        bit, sid, old_expected, expected = 19, 't_amp_mean_compress', True, True
        if case == 'compress_false':
            df, days = ninth_compress_frame(near_width=17.)
            old_expected = expected = False
        elif case == 'compress_missing':
            df = change_at(df, df.height-1, high=None)
            expected = None
        elif case == 'compress_zero':
            df, days = ninth_compress_frame(0.,0.)
            old_expected, expected = False, None
    elif case.startswith('fouryang'):
        df, days = ninth_four_yang_frame()
        bit, sid, old_expected, expected = 25, 't_4yang1yin', True, True
        if case == 'fouryang_wrong_order':
            df = with_tail(df, open=[101.,99.,99.,99.,99.], amount=[50.,100.,100.,100.,100.])
            expected = False
        elif case == 'fouryang_doji':
            df = change_last(df, open=100.)
            expected = False
    else:
        df, days = ninth_vcp_frame()
        bit, sid, old_expected, expected = 81, 't_vcp_3seg8', True, True
        if case == 'vcp_equal':
            df, days = ninth_vcp_frame(highs=(130.,120.,120.))
            old_expected = expected = False
        elif case == 'vcp_missing_amount':
            df = change_at(df,df.height-10,amount=None)
            expected = None
        elif case == 'vcp_zero_amount':
            df, days = ninth_vcp_frame(amounts=(300.,0.,0.))
            old_expected, expected = False, None
    future = days[-1]+timedelta(days=2)
    extended = pl.concat([df,df.tail(1).with_columns(pl.lit(future).alias('date'))])
    tensor = np.zeros((1,extended.height,8),dtype=np.float64)
    for i,field in enumerate(['open','high','low','close','volume','amount']):
        tensor[0,:,i] = extended[field].to_numpy()
    row = dict(t=str(future),symbol='A',bp=1,yizi_t=0,yizi_b=0,broken_limit=0,phase='',
               mktcap=100,gap=0.,seal_rate=0.,limit_up=0,state='')
    tags = build_tags(pd.DataFrame([row]),[str(d) for d in [*days,future]],tensor,{'A':0})
    assert bool((tags[0,bit//53] >> (bit % 53)) & 1) is old_expected
    assert val(ninth_eval(df,days),sid) is expected


@pytest.mark.parametrize('index', [0,1,2])
def test_ninth_ohlc_consistency(index):
    builder = [ninth_compress_frame,ninth_four_yang_frame,ninth_vcp_frame][index]
    df,days = builder()
    sig = NINTH_BATCH_SIGNALS[index]
    assert val(evaluate(change_last(df,high=1.),days,[sig]),sig['id']) is None


@pytest.mark.parametrize('index', [1,2])
@pytest.mark.parametrize('bad', [-1.,float('nan'),float('inf')])
def test_ninth_invalid_amount(index,bad):
    df,days = (ninth_four_yang_frame if index==1 else ninth_vcp_frame)()
    sig = NINTH_BATCH_SIGNALS[index]
    assert val(evaluate(change_last(df,amount=bad),days,[sig]),sig['id']) is None


@pytest.mark.parametrize('close,pole,amount,expected', [
    (110.,120.,80.,True), (109.999,120.,80.,False), (110.001,120.,80.,True),
    (112.,115.,80.,True), (112.,114.999,80.,False),
    (112.,120.,80.001,False), (112.,120.,0.,True),
])
def test_tenth_flag_midpoint_pole_and_amount(close,pole,amount,expected):
    df, days = tenth_flag_frame(close,pole,amount)
    assert val(tenth_eval(df,days),'t_qixing_zhengli') is expected


@pytest.mark.parametrize('field,value', [('high',122.),('high',123.),('low',108.),('low',109.)])
def test_tenth_flag_strict_downward_geometry(field,value):
    df, days = tenth_flag_frame()
    df = with_tail(df, **{field:[value]*5})
    assert val(tenth_eval(df,days),'t_qixing_zhengli') is False


@pytest.mark.parametrize('offset,expected', [(0,False),(9,False),(10,True),(19,True),(20,True)])
def test_tenth_flag_amount_segment_edges(offset,expected):
    df, days = tenth_flag_frame(near_amount=79.)
    df = change_at(df,df.height-1-offset,amount=1000.)
    assert val(tenth_eval(df,days),'t_qixing_zhengli') is expected


def test_tenth_flag_zero_amount_and_close_only_midpoint():
    df, days = tenth_flag_frame()
    assert val(tenth_eval(with_tail(df,amount=[0.]*20),days),'t_qixing_zhengli') is None
    # 中途低点可以跌破中点；当前收盘守住即可。
    df = change_at(df,df.height-3,low=50.)
    assert val(tenth_eval(df,days),'t_qixing_zhengli') is True
    # 旗杆起点之外的输入不参与。
    df = change_at(df,df.height-22,close=None,amount=None)
    assert val(tenth_eval(df,days),'t_qixing_zhengli') is True


@pytest.mark.parametrize('kwargs,expected', [
    ({},True), ({'bottom':90.},False), ({'bottom':89.999},True),
    ({'right':101.,'bottom':90.},False), ({'left':101.,'bottom':90.},False),
    ({'right':106.},False), # 柄不足右沿88%；其他条件通过。
    ({'right':106.,'handle':94.,'close':101.},True),
    ({'right':106.001,'handle':94.,'close':101.},False),
    ({'left':106.,'right':100.},True), # 左沿作分母，与右沿作分母不同。
    ({'handle':79.},False), ({'bottom':88.,'handle':88.},True),
    ({'handle':100.,'close':100.},False),
    ({'handle':88.},True), ({'handle':87.999},False),
    ({'close':95.},True), ({'close':94.999},False), ({'close':101.},True),
])
def test_tenth_cup_seven_conditions(kwargs,expected):
    df, days = tenth_cup_frame(**kwargs)
    assert val(tenth_eval(df,days),'t_cup_handle') is expected


@pytest.mark.parametrize('offset', [0,2,3,7,8,19,20,29,30])
def test_tenth_cup_segment_boundaries(offset):
    df, days = tenth_cup_frame()
    # 每段极值变化具有不同结果，验证切分而非仅契约长度。
    df = change_at(df,df.height-1-offset,high=200.,low=1.)
    expected = offset in (8,19,30)  # 更深杯底仍符合；左/右沿或柄改变则失败。
    assert val(tenth_eval(df,days),'t_cup_handle') is expected


@pytest.mark.parametrize('low,amount,expected', [
    (103.525,130.,True), (103.5251,130.,False), (103.,120.,False),
    (103.,120.001,True), (1.,130.,True), (103.,0.,False),
])
def test_tenth_pullback_touch_and_amount(low,amount,expected):
    df, days = tenth_pullback_frame(low,amount)
    assert val(tenth_eval(df,days),'t_pullback_continue') is expected


@pytest.mark.parametrize('open_,expected', [(110.,False),(111.,False),(109.,True)])
def test_tenth_pullback_strict_yang(open_,expected):
    df, days = tenth_pullback_frame()
    assert val(tenth_eval(change_last(df,open=open_),days),'t_pullback_continue') is expected


def test_tenth_pullback_five_day_slope_not_previous_day_or_engulf():
    df, days = tenth_pullback_frame()
    # 昨收也是110，当前只收110，非反包；仍满足。
    assert val(tenth_eval(df,days),'t_pullback_continue') is True
    # D-20抬至120使当日MA20低于昨日，但仍高于5日前。
    df = change_at(df,df.height-21,open=120.,close=120.,high=121.,low=119.)
    out = tenth_eval(df,days)
    assert out['ma20'][-1] < out['ma20'][-2]
    assert out['ma20'][-1] > out['ma20'][-6]
    assert val(out,'t_pullback_continue') is True
    assert val(tenth_eval(change_at(df,df.height-2,amount=0.),days),'t_pullback_continue') is None
    # 高于当前均线但5日前均线更高，趋势条件必须失败。
    df, days = tenth_pullback_frame()
    for offset in range(20,25):
        df=change_at(df,df.height-1-offset,open=150.,close=150.,high=151.,low=149.)
    assert val(tenth_eval(df,days),'t_pullback_continue') is False


@pytest.mark.parametrize('index,builder', [(0,tenth_flag_frame),(1,tenth_cup_frame),(2,tenth_pullback_frame)])
def test_tenth_strict_contract_all_positions_and_isolation(index,builder):
    df, days = builder()
    sig = TENTH_BATCH_SIGNALS[index]
    sid = sig['id']
    n=max(sig['data_contract']['inputs'].values())
    assert val(evaluate(df.tail(n),days,[sig]),sid) is True
    assert val(evaluate(df.tail(n-1),days,[sig]),sid) is None
    # 每个所需字段在每个窗口位置缺失均未知，而非只抽首尾。
    for field, window in sig['data_contract']['inputs'].items():
        for offset in range(window):
            broken=change_at(df,df.height-1-offset,**{field:None})
            out=eighth_raw_eval(broken,days,[sig])
            assert val(out,sid) is None, (field,offset)
            assert out.select((~pl.col(f'csg_{sid}')).last()).item() is None
        assert val(eighth_raw_eval(df.drop(field),days,[sig]),sid) is None
    assert val(evaluate(df.filter(pl.col('date')!=days[-2]),days,[sig]),sid) is None
    assert val(evaluate(df,days[:-1],[sig]),sid) is None
    assert val(evaluate(pl.concat([df,df.tail(1)]),days,[sig]),sid) is None
    with pytest.raises(ValueError,match='排序'):
        eighth_raw_eval(df.reverse(),days,[sig])
    future_day=days[-1]+timedelta(days=2)
    future=change_last(df.tail(1),open=1.,high=1000.,low=.1,close=999.,amount=0.)
    future=future.with_columns(pl.lit(future_day).alias('date'))
    original=evaluate(df,days,[sig])
    extended=evaluate(pl.concat([df,future]),[*days,future_day],[sig])
    assert extended[f'csg_{sid}'][:-1].equals(original[f'csg_{sid}'])
    other=df.with_columns(pl.lit('B').alias('symbol'),pl.lit(None).cast(pl.Float64).alias('close'))
    mixed=evaluate(pl.concat([df,other]),days,[sig])
    assert mixed.filter(pl.col('symbol')=='A')[f'csg_{sid}'].equals(original[f'csg_{sid}'])
    assert mixed.filter(pl.col('symbol')=='B')[f'csg_{sid}'].null_count()==df.height


@pytest.mark.parametrize('index,builder', [(0,tenth_flag_frame),(1,tenth_cup_frame),(2,tenth_pullback_frame)])
@pytest.mark.parametrize('bad',[None,float('nan'),float('inf'),0.,-1.])
def test_tenth_invalid_price_dominates_false(index,builder,bad):
    df, days=builder()
    sig=TENTH_BATCH_SIGNALS[index]
    from copy import deepcopy
    sig=deepcopy(sig)
    sig['conditions'].append({'left':'close','op':'<','right':'0'})
    assert val(eighth_raw_eval(change_last(df,low=bad),days,[sig]),sig['id']) is None


@pytest.mark.parametrize('index,builder', [(0,tenth_flag_frame),(1,tenth_cup_frame),(2,tenth_pullback_frame)])
def test_tenth_ohlc_and_irrelevant_fields(index,builder):
    df, days=builder()
    sid=TENTH_BATCH_SIGNALS[index]['id']
    assert val(tenth_eval(change_last(df,high=1.),days),sid) is None
    assert val(tenth_eval(df.with_columns(pl.lit(None).cast(pl.Float64).alias('volume')),days),sid) is True
    if index==1:
        assert val(tenth_eval(df.with_columns(pl.lit(None).cast(pl.Float64).alias('amount')),days),sid) is True
    else:
        for bad in [-1.,float('inf'),float('nan')]:
            assert val(tenth_eval(change_last(df,amount=bad),days),sid) is None


@pytest.mark.parametrize('case', ['flag_true','flag_midpoint','flag_false','flag_missing',
                                  'cup_true','cup_false','pullback_true','pullback_false','pullback_deep'])
def test_tenth_old_tag_parity_and_approved_changes(case):
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags
    if case.startswith('flag'):
        df, days=tenth_flag_frame()
        bit,sid,old_expected,expected=74,'t_qixing_zhengli',True,True
        if case=='flag_midpoint':
            df=change_last(df,close=109.)
            expected=False
        elif case=='flag_false':
            df=change_last(df,amount=1000.)
            old_expected=expected=False
        elif case=='flag_missing':
            df=change_last(df,amount=None)
            expected=None
    elif case.startswith('cup'):
        df,days=tenth_cup_frame(close=94. if case=='cup_false' else 96.)
        bit,sid=80,'t_cup_handle'
        old_expected=expected=case=='cup_true'
    else:
        df,days=tenth_pullback_frame(low=1. if case=='pullback_deep' else 103.,
                                    amount=119. if case=='pullback_false' else 130.)
        bit,sid=86,'t_pullback_continue'
        old_expected=expected=case!='pullback_false'
    future=days[-1]+timedelta(days=2)
    extended=pl.concat([df,df.tail(1).with_columns(pl.lit(future).alias('date'))])
    tensor=np.zeros((1,extended.height,8),dtype=np.float64)
    for i,field in enumerate(['open','high','low','close','volume','amount']):
        tensor[0,:,i]=extended[field].to_numpy()
    row=dict(t=str(future),symbol='A',bp=1,yizi_t=0,yizi_b=0,broken_limit=0,phase='',
             mktcap=100,gap=0.,seal_rate=0.,limit_up=0,state='')
    tags=build_tags(pd.DataFrame([row]),[str(d) for d in [*days,future]],tensor,{'A':0})
    assert bool((tags[0,bit//53] >> (bit%53)) & 1) is old_expected
    assert val(tenth_eval(df,days),sid) is expected


@pytest.mark.parametrize('scale', [.0333, .1, .3, 1., 10., 10000.])
def test_tenth_cup_exact_ten_percent_remains_excluded_across_scales(scale):
    # 已批准严格下探超过10%；等于10%不能因价格单位变化变成命中。
    df, days = tenth_cup_frame(bottom=90.)
    df = df.with_columns([pl.col(f)*scale for f in ('open','high','low','close')])
    assert val(tenth_eval(df,days),'t_cup_handle') is False


@pytest.mark.parametrize('scale', [.001, .01, .0333, .1, .3, 1., 2., 10., 1000., 10000.])
@pytest.mark.parametrize('left,right', [(100.,100.), (100.,101.), (101.,100.)])
@pytest.mark.parametrize('bottom,expected', [(89.999,True), (90.,False), (90.001,False)])
def test_tenth_cup_each_rim_depth_boundary_and_scale(scale,left,right,bottom,expected):
    df, days = tenth_cup_frame(left=left,right=right,bottom=bottom,handle=91.,close=97.)
    df = df.with_columns([pl.col(f)*scale for f in ('open','high','low','close')])
    assert val(tenth_eval(df,days),'t_cup_handle') is expected



@pytest.mark.parametrize('scale', [.001,.01,.0333,.1,.3,1.,2.,10.,1000.,10000.])
@pytest.mark.parametrize('index,builder', [(0,eleventh_pit_frame),(1,eleventh_bridge_frame)])
@pytest.mark.parametrize('pit_low,expected', [(98.999,True),(99.,False),(99.001,False)])
def test_eleventh_strict_one_percent_scales(scale,index,builder,pit_low,expected):
    df,days=builder(pit_low=pit_low)
    df=df.with_columns([pl.col(f)*scale for f in ('open','high','low','close')])
    assert val(eleventh_eval(df,days),ELEVENTH_BATCH_SIGNALS[index]['id']) is expected


@pytest.mark.parametrize('kwargs,expected',[
    ({},True),({'high':112.001},False),({'high':111.999},True),
    ({'near_amount':80.001},False),({'near_amount':0.},True),
    ({'close':100.},False),({'close':99.999},False),({'close':100.001},True),
])
def test_eleventh_pit_condition_boundaries(kwargs,expected):
    df,days=eleventh_pit_frame(**kwargs)
    assert val(eleventh_eval(df,days),'t_sanhu_keng') is expected


@pytest.mark.parametrize('kwargs,expected',[
    ({},True),({'bridge_high':108.001},False),({'bridge_high':107.999},True),
    ({'near_low':90.},False),({'near_low':89.999},False),({'near_low':90.001},True),
    ({'near_amount':80.001},False),({'near_amount':0.},True),
])
def test_eleventh_bridge_condition_boundaries(kwargs,expected):
    df,days=eleventh_bridge_frame(**kwargs)
    assert val(eleventh_eval(df,days),'t_keng_qiao_die') is expected


@pytest.mark.parametrize('index,builder,far,near',[
    (0,eleventh_pit_frame,range(9,21),range(1,9)),
    (1,eleventh_bridge_frame,range(22,39),range(10,20)),
])
def test_eleventh_amount_zero_and_independent_scales(index,builder,far,near):
    df,days=builder()
    sid=ELEVENTH_BATCH_SIGNALS[index]['id']
    for offsets in [far,list(far)+list(near)]:
        broken=df
        for k in offsets:
            broken=change_at(broken,broken.height-1-k,amount=0.)
        assert val(eleventh_eval(broken,days),sid) is None
    for scale in (.0333,10.,10000.):
        changed=df.with_columns(*[pl.col(f)*scale for f in ('open','high','low','close')],pl.col('amount')*17.)
        assert val(eleventh_eval(changed,days),sid) is True


@pytest.mark.parametrize('offset,expected',[(0,True),(1,True),(8,True),(9,False),(20,False),(21,True)])
def test_eleventh_pit_low_window_edges(offset,expected):
    df,days=eleventh_pit_frame()
    changed=change_at(df,df.height-1-offset,low=1.)
    assert val(eleventh_eval(changed,days),'t_sanhu_keng') is expected


@pytest.mark.parametrize('offset,expected',[(0,False),(4,False),(5,True),(9,True),(10,True),
                                         (19,True),(20,True),(21,True),(22,False),(38,False),(39,True)])
def test_eleventh_bridge_low_window_edges(offset,expected):
    df,days=eleventh_bridge_frame()
    changed=change_at(df,df.height-1-offset,low=1.)
    assert val(eleventh_eval(changed,days),'t_keng_qiao_die') is expected


@pytest.mark.parametrize('index,builder,noncomputing',[
    (0,eleventh_pit_frame,[0]),(1,eleventh_bridge_frame,[*range(10),20,21]),
])
def test_eleventh_noncomputing_amount_is_still_required(index,builder,noncomputing):
    df,days=builder()
    sig=ELEVENTH_BATCH_SIGNALS[index]
    for offset in noncomputing:
        for good in (0.,100000.):
            changed=change_at(df,df.height-1-offset,amount=good)
            assert val(eleventh_eval(changed,days),sig['id']) is True
        for bad in (None,-1.,float('inf'),float('nan')):
            changed=change_at(df,df.height-1-offset,amount=bad)
            assert val(eighth_raw_eval(changed,days,[sig]),sig['id']) is None


def test_eleventh_bridge_gap_and_high_high_not_high_low():
    df,days=eleventh_bridge_frame()
    sid='t_keng_qiao_die'
    out=eleventh_eval(df,days)
    assert val(out,sid) is True
    assert max(df['high'][-10:])/min(df['low'][-10:])>1.08
    assert out['uf_high_spread_10_ratio'][-1]==pytest.approx(1.08)
    # 不要求收复早段低点，不要求早段窄幅，不要求四段彩虹桥。
    assert df['close'][-1]<100.
    assert val(eleventh_eval(change_at(df,df.height-30,high=1000.),days),sid) is True
    for offset in (20,21):
        changed=change_at(df,df.height-1-offset,open=500.,high=1000.,low=1.,close=700.,amount=0.)
        assert val(eleventh_eval(changed,days),sid) is True
        for f in ('open','high','low','close'):
            assert val(eleventh_eval(change_at(changed,df.height-1-offset,**{f:None}),days),sid) is None


@pytest.mark.parametrize('index,builder',[(0,eleventh_pit_frame),(1,eleventh_bridge_frame)])
def test_eleventh_strict_contract_every_position_and_isolation(index,builder):
    df,days=builder()
    sig=ELEVENTH_BATCH_SIGNALS[index]
    sid=sig['id']
    n=sig['data_contract']['ohlc_window']
    assert val(evaluate(df.tail(n),days,[sig]),sid) is True
    assert val(evaluate(df.tail(n-1),days,[sig]),sid) is None
    for field,window in sig['data_contract']['inputs'].items():
        for offset in range(window):
            changed=change_at(df,df.height-1-offset,**{field:None})
            out=eighth_raw_eval(changed,days,[sig])
            assert val(out,sid) is None,(field,offset)
            assert out.select((~pl.col(f'csg_{sid}')).last()).item() is None
        assert val(eighth_raw_eval(df.drop(field),days,[sig]),sid) is None
    assert val(evaluate(df.filter(pl.col('date')!=days[-2]),days,[sig]),sid) is None
    assert val(evaluate(df,days[:-1],[sig]),sid) is None
    assert val(evaluate(pl.concat([df,df.tail(1)]),days,[sig]),sid) is None
    with pytest.raises(ValueError,match='排序'):
        eighth_raw_eval(df.reverse(),days,[sig])
    future_day=days[-1]+timedelta(days=2)
    future=change_last(df.tail(1),open=1.,high=1000.,low=.1,close=999.,amount=0.)
    future=future.with_columns(pl.lit(future_day).alias('date'))
    original=evaluate(df,days,[sig])
    extended=evaluate(pl.concat([df,future]),[*days,future_day],[sig])
    assert extended[f'csg_{sid}'][:-1].equals(original[f'csg_{sid}'])
    other=df.with_columns(pl.lit('B').alias('symbol'),pl.lit(None).cast(pl.Float64).alias('close'))
    mixed=evaluate(pl.concat([df,other]),days,[sig])
    assert mixed.filter(pl.col('symbol')=='A')[f'csg_{sid}'].equals(original[f'csg_{sid}'])
    assert mixed.filter(pl.col('symbol')=='B')[f'csg_{sid}'].null_count()==df.height


@pytest.mark.parametrize('index,builder',[(0,eleventh_pit_frame),(1,eleventh_bridge_frame)])
@pytest.mark.parametrize('bad',[None,float('nan'),float('inf'),0.,-1.])
def test_eleventh_invalid_price_dominates_false(index,builder,bad):
    from copy import deepcopy
    df,days=builder()
    sig=deepcopy(ELEVENTH_BATCH_SIGNALS[index])
    sig['conditions'].append({'left':'close','op':'<','right':'0'})
    assert val(eighth_raw_eval(change_last(df,low=bad),days,[sig]),sig['id']) is None


@pytest.mark.parametrize('index,builder',[(0,eleventh_pit_frame),(1,eleventh_bridge_frame)])
def test_eleventh_bad_ohlc_and_irrelevant_volume(index,builder):
    df,days=builder()
    sid=ELEVENTH_BATCH_SIGNALS[index]['id']
    assert val(eleventh_eval(change_last(df,high=1.),days),sid) is None
    changed=df.with_columns(pl.lit(None).cast(pl.Float64).alias('volume'))
    assert val(eleventh_eval(changed,days),sid) is True
    if index==0:
        # 默认D为阴线，收复下沿即可命中；D的最低价不进入坑段。
        assert df['close'][-1]<df['open'][-1]
        assert val(eleventh_eval(change_last(df,low=1.,amount=10000.),days),sid) is True


@pytest.mark.parametrize('index,builder,offset,expected',[
    (0,eleventh_pit_frame,0,True),(0,eleventh_pit_frame,1,False),
    (0,eleventh_pit_frame,8,False),(0,eleventh_pit_frame,9,True),(0,eleventh_pit_frame,20,True),
    (1,eleventh_bridge_frame,9,True),(1,eleventh_bridge_frame,10,False),
    (1,eleventh_bridge_frame,19,False),(1,eleventh_bridge_frame,20,True),
    (1,eleventh_bridge_frame,21,True),(1,eleventh_bridge_frame,22,True),(1,eleventh_bridge_frame,38,True),
])
def test_eleventh_amount_window_edges(index,builder,offset,expected):
    df,days=builder(near_amount=79.)
    df=change_at(df,df.height-1-offset,amount=1000.)
    assert val(eleventh_eval(df,days),ELEVENTH_BATCH_SIGNALS[index]['id']) is expected


@pytest.mark.parametrize('scale',[.0333,.1,1.,10.,10000.])
@pytest.mark.parametrize('index,builder',[(0,eleventh_pit_frame),(1,eleventh_bridge_frame)])
def test_eleventh_inclusive_geometry_and_amount_boundary_scales(scale,index,builder):
    # 默认同时处于12%/8%和80%含等于边界。
    df,days=builder()
    df=df.with_columns([pl.col(f)*scale for f in ('open','high','low','close','amount')])
    assert val(eleventh_eval(df,days),ELEVENTH_BATCH_SIGNALS[index]['id']) is True


@pytest.mark.parametrize('case',[
    'pit_true','pit_false','pit_partial_amount','pit_unused_amount','pit_current_deep',
    'bridge_true','bridge_false','bridge_partial_amount','bridge_unused_amount','bridge_gap',
])
def test_eleventh_old_tag_parity_and_contract_differences(case):
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags
    if case.startswith('pit'):
        df,days=eleventh_pit_frame(high=111.)
        bit,sid=71,'t_sanhu_keng'
        old_expected=expected=True
        if case=='pit_false':
            df=change_last(df,close=100.)
            old_expected=expected=False
        elif case=='pit_partial_amount':
            df=change_at(df,df.height-5,amount=None)
            expected=None
        elif case=='pit_unused_amount':
            df=change_last(df,amount=None)
            expected=None
        elif case=='pit_current_deep':
            df=change_last(df,low=1.)
    else:
        df,days=eleventh_bridge_frame(bridge_high=107.)
        bit,sid=73,'t_keng_qiao_die'
        old_expected=expected=True
        if case=='bridge_false':
            df=change_last(df,low=89.)
            old_expected=expected=False
        elif case=='bridge_partial_amount':
            df=change_at(df,df.height-15,amount=None)
            expected=None
        elif case=='bridge_unused_amount':
            df=change_last(df,amount=None)
            expected=None
        elif case=='bridge_gap':
            df=change_at(df,df.height-21,low=None,amount=None)
            expected=None
    future=days[-1]+timedelta(days=2)
    extended=pl.concat([df,df.tail(1).with_columns(pl.lit(future).alias('date'))])
    tensor=np.zeros((1,extended.height,8),dtype=np.float64)
    for i,field in enumerate(['open','high','low','close','volume','amount']):
        tensor[0,:,i]=extended[field].to_numpy()
    row=dict(t=str(future),symbol='A',bp=1,yizi_t=0,yizi_b=0,broken_limit=0,phase='',
             mktcap=100,gap=0.,seal_rate=0.,limit_up=0,state='')
    tags=build_tags(pd.DataFrame([row]),[str(d) for d in [*days,future]],tensor,{'A':0})
    assert bool((tags[0,bit//53] >> (bit%53)) & 1) is old_expected
    assert val(eleventh_eval(df,days),sid) is expected



@pytest.mark.parametrize('scale', [.001,.007,.01,.0333,.07,.1,.2,.3,.3333,1.,2.,10.,123.45,1000.,10000.])
@pytest.mark.parametrize('close,expected', [(100.999,False),(101.,False),(101.001,True)])
def test_twelfth_strict_breakout_boundary_scales(scale,close,expected):
    df,days=twelfth_peak_frame(close=close)
    df=df.with_columns([pl.col(f)*scale for f in ('open','high','low','close')])
    assert val(twelfth_eval(df,days),'t_guozuofeng') is expected


@pytest.mark.parametrize('position', ['original','original_next','below','equal','above'])
def test_twelfth_local_tolerance_boundary(position):
    import math
    threshold=1.01+8*math.ulp(1.)*1.01
    close={'original':1.01,'original_next':math.nextafter(1.01,math.inf),
           'below':math.nextafter(threshold,-math.inf),'equal':threshold,
           'above':math.nextafter(threshold,math.inf)}[position]
    df,days=twelfth_peak_frame()
    df=df.with_columns([pl.col(f)*.01 for f in ('open','high','low','close')])
    out=twelfth_eval(change_last(df,close=close),days)
    assert out['uf_close_prev60_high_ratio'][-1]==close
    assert val(out,'t_guozuofeng') is (position=='above')


def test_twelfth_tolerance_is_local_not_shared_comparison():
    import math
    from copy import deepcopy
    df,days=twelfth_peak_frame()
    df=df.with_columns([pl.col(f)*.01 for f in ('open','high','low','close')])
    df=change_last(df,close=math.nextafter(1.01,math.inf))
    original=deepcopy(TWELFTH_BATCH_SIGNALS[0])
    original['id']='unbuffered_control'
    original['conditions'][0]['right']='1.01'
    out=evaluate(df,days,[*TWELFTH_BATCH_SIGNALS,original])
    assert val(out,'t_guozuofeng') is False
    assert val(out,'unbuffered_control') is True


@pytest.mark.parametrize('peaks,count',[((),0),((1,),0),((2,),1),((30,),1),((59,),1),((60,),0),
                                       ((29,30),0),((59,60),0),((2,30,59),3)])
def test_twelfth_peak_positions_and_plateaus(peaks,count):
    df,days=twelfth_peak_frame(peaks=peaks)
    out=twelfth_eval(df,days)
    assert out['uf_confirmed_high_peak_count59'][-1]==count
    assert val(out,'t_guozuofeng') is (count>0)


@pytest.mark.parametrize('offset',[1,30,60])
def test_twelfth_highest_point_need_not_be_local_peak(offset):
    df,days=twelfth_peak_frame(close=122.)
    df=change_at(df,df.height-1-offset,high=120.)
    out=twelfth_eval(df,days)
    assert out['uf_close_prev60_high_ratio'][-1]==pytest.approx(122/120)
    assert val(out,'t_guozuofeng') is True


def test_twelfth_full_contract_every_cell_and_isolation():
    df,days=twelfth_peak_frame()
    sig=TWELFTH_BATCH_SIGNALS[0]
    sid=sig['id']
    assert val(evaluate(df.tail(61),days,[sig]),sid) is True
    assert val(evaluate(df.tail(60),days,[sig]),sid) is None
    for field in ('open','high','low','close'):
        for offset in range(61):
            broken=change_at(df,df.height-1-offset,**{field:None})
            out=eighth_raw_eval(broken,days,[sig])
            assert val(out,sid) is None,(field,offset)
            assert out.select((~pl.col(f'csg_{sid}')).last()).item() is None
        assert val(eighth_raw_eval(df.drop(field),days,[sig]),sid) is None
    assert val(evaluate(df.filter(pl.col('date')!=days[-2]),days,[sig]),sid) is None
    assert val(evaluate(df,days[:-1],[sig]),sid) is None
    assert val(evaluate(pl.concat([df,df.tail(1)]),days,[sig]),sid) is None
    with pytest.raises(ValueError,match='排序'):
        eighth_raw_eval(df.reverse(),days,[sig])
    future_day=days[-1]+timedelta(days=2)
    future=change_last(df.tail(1),open=1.,high=1000.,low=.1,close=999.,amount=0.)
    future=future.with_columns(pl.lit(future_day).alias('date'))
    original=evaluate(df,days,[sig])
    extended=evaluate(pl.concat([df,future]),[*days,future_day],[sig])
    for col in ['uf_close_prev60_high_ratio','uf_confirmed_high_peak_count59',f'csg_{sid}']:
        assert extended[col][:-1].equals(original[col])
    other=df.with_columns(pl.lit('B').alias('symbol'),pl.lit(None).cast(pl.Float64).alias('high'))
    mixed=evaluate(pl.concat([df,other]),days,[sig])
    assert mixed.filter(pl.col('symbol')=='A')[f'csg_{sid}'].equals(original[f'csg_{sid}'])
    assert mixed.filter(pl.col('symbol')=='B')[f'csg_{sid}'].null_count()==df.height


@pytest.mark.parametrize('bad',[None,float('nan'),float('inf'),0.,-1.])
@pytest.mark.parametrize('field',['open','high','low','close'])
def test_twelfth_bad_price_dominates_false(field,bad):
    df,days=twelfth_peak_frame(close=99.)
    assert val(twelfth_eval(change_last(df,**{field:bad}),days),'t_guozuofeng') is None


def test_twelfth_ohlc_and_unused_fields_and_outside_window():
    df,days=twelfth_peak_frame()
    sid='t_guozuofeng'
    assert df['close'][-1]<df['open'][-1] # 不要求收阳。
    assert val(twelfth_eval(change_last(df,low=200.),days),sid) is None
    assert val(twelfth_eval(change_last(df,high=1.),days),sid) is None
    good=change_last(df,open=100.,low=1.)
    assert val(twelfth_eval(good,days),sid) is True
    for value in [None,0.,float('nan')]:
        changed=df.with_columns(pl.lit(value).cast(pl.Float64).alias('amount'),
                                pl.lit(value).cast(pl.Float64).alias('volume'))
        assert val(twelfth_eval(changed,days),sid) is True
    assert val(eighth_raw_eval(df.drop('amount','volume'),days,TWELFTH_BATCH_SIGNALS),sid) is True
    for value in [None,10000.]:
        outside=change_at(df,df.height-62,high=value)
        out=twelfth_eval(outside,days)
        assert val(out,sid) is True
        assert out['uf_confirmed_high_peak_count59'][-1]==1


@pytest.mark.parametrize('case',['true','false_breakout','no_peak','plateau','endpoint_peak',
                                 'different_peak','partial_high','unused_open','local_tolerance'])
def test_twelfth_old_tag_parity_and_approved_differences(case):
    import numpy as np
    import pandas as pd

    from app.services.daban_compute import build_tags
    df,days=twelfth_peak_frame()
    old_expected=expected=True
    if case=='false_breakout':
        df=change_last(df,close=99.)
        old_expected=expected=False
    elif case in ('no_peak','plateau','endpoint_peak'):
        peaks={'no_peak':(),'plateau':(29,30),'endpoint_peak':(60,)}[case]
        df,days=twelfth_peak_frame(peaks=peaks)
        old_expected=expected=False
    elif case=='different_peak':
        df,days=twelfth_peak_frame(close=122.)
        df=change_at(df,df.height-61,high=120.)
    elif case=='partial_high':
        df=change_at(df,df.height-11,high=None)
        expected=None
    elif case=='unused_open':
        df=change_at(df,df.height-11,open=None)
        expected=None
    elif case=='local_tolerance':
        df=change_last(df,close=101.00000000000003)
        expected=False
    future=days[-1]+timedelta(days=2)
    extended=pl.concat([df,df.tail(1).with_columns(pl.lit(future).alias('date'))])
    tensor=np.zeros((1,extended.height,8),dtype=np.float64)
    for i,field in enumerate(['open','high','low','close','volume','amount']):
        tensor[0,:,i]=extended[field].to_numpy()
    row=dict(t=str(future),symbol='A',bp=1,yizi_t=0,yizi_b=0,broken_limit=0,phase='',
             mktcap=100,gap=0.,seal_rate=0.,limit_up=0,state='')
    tags=build_tags(pd.DataFrame([row]),[str(d) for d in [*days,future]],tensor,{'A':0})
    assert bool((tags[0,63//53] >> (63%53)) & 1) is old_expected
    assert val(twelfth_eval(df,days),'t_guozuofeng') is expected
