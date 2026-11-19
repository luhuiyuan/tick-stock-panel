"""迁移数值因子：独立数值、严格预热、空值与尺度验证。"""

import polars as pl
import pytest
from premarket_fixtures import (
    change_at,
    change_last,
    eighth_eval,
    eighth_gap_frame,
    eleventh_eval,
    fifth_eval,
    fourth_eval,
    frame,
    ninth_eval,
    ninth_vcp_frame,
    segmented_frame,
    seventh_eval,
    sixth_eval,
    tenth_cup_frame,
    tenth_eval,
    tenth_flag_frame,
    tenth_pullback_frame,
    twelfth_eval,
    twelfth_peak_frame,
    val,
)
from premarket_fixtures import (
    factors as factors,
)

from app.factors.premarket import FACTORS
from app.strategy.premarket_signal_definitions import (
    FIFTH_BATCH_SIGNALS,
)


@pytest.mark.parametrize(('factor', 'field'), [
    ('uf_yang_count_5', 'close'), ('uf_yang_count_5', 'open'),
    ('uf_priceup_amountdown_count_3', 'close'), ('uf_priceup_amountdown_count_3', 'amount'),
])
def test_fourth_count_factor_preserves_null_even_when_other_condition_false(factor, field):
    df, days = frame()
    # 所有价升/额缩条件原本为false，null也不能被False AND null压成0。
    for offset in [0, 1, 2]:
        missing = change_at(df, df.height-1-offset, **{field: None})
        out = fourth_eval(missing, days)
        assert out[factor][-1] is None
    out = fourth_eval(df, days)
    assert out[factor][-1] == 0
    assert out[factor][0] is None


def test_fifth_numeric_factors_against_independent_slices():
    import numpy as np
    df, days = frame()
    rng = np.random.default_rng(85)
    lows = rng.uniform(50, 80, df.height)
    highs = rng.uniform(110, 150, df.height)
    amounts = rng.uniform(1, 200, df.height)
    df = df.with_columns(pl.Series('high', highs), pl.Series('low', lows), pl.Series('amount', amounts))
    out = fifth_eval(df, days)
    for i in range(20, df.height):
        h = [max(highs[i-6-k:i+1-k]) for k in (0, 7, 14)]
        low = [min(lows[i-6-k:i+1-k]) for k in (0, 7, 14)]
        a = [np.mean(amounts[i-6-k:i+1-k]) for k in (0, 14)]
        h10 = [max(highs[i-9-k:i+1-k]) for k in (0, 10)]
        l10 = [min(lows[i-9-k:i+1-k]) for k in (0, 10)]
        expected = [h[0]/h[1]-1, low[0]/low[1]-1,
                    ((h[0]-low[0])-(h[2]-low[2]))/100,
                    max(h)/min(h)-1, a[0]/a[1], h10[0]/h10[1]-1,
                    l10[0]/l10[1]-1, ((h10[0]-l10[0])-(h10[1]-l10[1]))/100]
        for factor, value in zip(FACTORS[17:25], expected, strict=True):
            assert out[factor['id']][i] == pytest.approx(value), factor['id']
    # Exact factor warmup (signal contracts can intentionally require more inputs).
    for factor, n in zip(FACTORS[17:25], [14, 14, 21, 21, 21, 20, 20, 20], strict=True):
        values = out[factor['id']]
        assert values[:n-1].null_count() == n-1
        assert values[n-1] is not None


@pytest.mark.parametrize('factor,field', [
    ('uf_high_7_prev7_bias', 'high'), ('uf_low_7_prev7_bias', 'low'),
    ('uf_width_7_far7_delta', 'high'), ('uf_high_3seg7_spread', 'high'),
    ('uf_amount_7_far7_ratio', 'amount'), ('uf_high_10_prev10_bias', 'high'),
    ('uf_low_10_prev10_bias', 'low'), ('uf_width_10_prev10_delta', 'low'),
])
def test_fifth_raw_factor_null_is_not_zero(factor, field):
    df, days = frame()
    assert fifth_eval(change_last(df, **{field: None}), days)[factor][-1] is None


@pytest.mark.parametrize('offset', [0, 7, 14, 20])
def test_fifth_spread_factor_needs_all_three_complete_segments(offset):
    df, days = frame()
    out = fifth_eval(change_at(df, df.height-1-offset, high=None), days)
    assert out['uf_high_3seg7_spread'][-1] is None


def test_fifth_factors_are_scale_free_and_do_not_use_volume():
    df, days = segmented_frame([(120, 80, 100), (115, 85, 1), (110, 90, 201)])
    reference = fifth_eval(df, days)
    scaled = df.with_columns([pl.col(f)*10 for f in ('open', 'high', 'low', 'close', 'amount')])
    scaled = scaled.with_columns(pl.lit(0.).alias('volume'))
    out = fifth_eval(scaled, days)
    for factor in FACTORS[17:25]:
        assert out[factor['id']][-1] == pytest.approx(reference[factor['id']][-1])
    for sig in FIFTH_BATCH_SIGNALS:
        assert val(out, sig['id']) is val(reference, sig['id'])


def test_sixth_numeric_factors_against_independent_slices():
    import numpy as np
    df, days = frame(100)
    rng = np.random.default_rng(66)
    lows = rng.uniform(50,80,df.height)
    highs = rng.uniform(110,150,df.height)
    closes = rng.uniform(90,105,df.height)
    opens = rng.uniform(90,105,df.height)
    amounts = rng.uniform(1,200,df.height)
    df = df.with_columns(pl.Series('low',lows), pl.Series('high',highs), pl.Series('close',closes),
                         pl.Series('open',opens), pl.Series('amount',amounts))
    out = sixth_eval(df, days)
    for i in range(69, df.height):
        pivot = max(highs[i-39:i-9])
        segment_highs = [max(highs[i-4-k:i+1-k]) for k in (0,5,10,15)]
        expected = [closes[i]/closes[i-2]-1, 2*closes[i]/(opens[i-1]+closes[i-1])-1,
                    (max(highs[i-9:i+1])-min(lows[i-9:i+1]))/min(lows[i-9:i+1]),
                    np.mean(amounts[i-9:i+1])/np.mean(amounts[i-69:i-9]),
                    min(lows[i-9:i+1])/pivot-1, closes[i]/pivot-1,
                    min(lows[i-4:i+1])/min(lows[i-9:i-4])-1,
                    max(segment_highs)/min(segment_highs)-1]
        for factor, value in zip(FACTORS[25:33], expected, strict=True):
            assert out[factor['id']][i] == pytest.approx(value), factor['id']
    for factor, n in zip(FACTORS[25:33], [3,2,10,70,40,40,10,20], strict=True):
        values = out[factor['id']]
        assert values[:n-1].null_count() == n-1
        assert values[n-1] is not None
    scaled = df.with_columns([pl.col(f)*10 for f in ('open','high','low','close','amount')])
    scaled = scaled.with_columns(pl.lit(0.).alias('volume'))
    actual = sixth_eval(scaled, days)
    for factor in FACTORS[25:33]:
        assert actual[factor['id']][-1] == pytest.approx(out[factor['id']][-1])


@pytest.mark.parametrize(('factor', 'field', 'offset'), [
    ('uf_return_2d','close',0), ('uf_return_2d','close',2),
    ('uf_prev_body_midpoint_bias','open',1), ('uf_prev_body_midpoint_bias','close',0),
    ('uf_range_10d_low','high',9), ('uf_amount_10_prev60','amount',69),
    ('uf_low10_high30_lag10_bias','low',9), ('uf_low10_high30_lag10_bias','high',39),
    ('uf_close_high30_lag10_bias','high',10), ('uf_low_5_prev5_bias','low',9),
    *[('uf_high_4seg5_spread','high',offset) for offset in (0,5,10,15,19)],
])
def test_sixth_raw_factors_preserve_missing_inputs(factor, field, offset):
    df, days = frame(100)
    out = sixth_eval(change_at(df, df.height-1-offset, **{field: None}), days)
    assert out[factor][-1] is None


def test_seventh_factors_numeric_windows_and_scale():
    import numpy as np
    df,days=frame(80)
    rng=np.random.default_rng(77)
    highs=rng.uniform(110,150,df.height)
    lows=rng.uniform(50,80,df.height)
    closes=rng.uniform(90,105,df.height)
    df=df.with_columns(pl.Series('high',highs),pl.Series('low',lows),pl.Series('close',closes))
    out=seventh_eval(df,days)
    for i in range(27,df.height):
        peak=max(highs[i-20:i])
        old_low=min(lows[i-14:i-4])
        left=min(lows[i-27:i-17])
        right=min(lows[i-14:i-4])
        neck=max(highs[i-19:i-11])
        expected=[min(closes[j]/closes[j-1]-1 for j in range(i-2,i+1)),
                  closes[i]/peak-1,highs[i-1]/peak-1,closes[i]/old_low-1,
                  abs(right/left-1),neck/left,closes[i]/neck-1]
        for factor,value in zip(FACTORS[33:40],expected,strict=True):
            assert out[factor['id']][i]==pytest.approx(value),factor['id']
    for factor,n in zip(FACTORS[33:40],[4,21,21,15,28,28,20],strict=True):
        values=out[factor['id']]
        assert values[:n-1].null_count()==n-1
        assert values[n-1] is not None
    scaled=df.with_columns([pl.col(f)*10 for f in ('open','high','low','close')])
    actual=seventh_eval(scaled,days)
    for factor in FACTORS[33:40]:
        assert actual[factor['id']][-1]==pytest.approx(out[factor['id']][-1])


@pytest.mark.parametrize(('factor','field','offset'), [
    ('uf_min_return_3d','close',3),('uf_close_prev20_high_bias','high',20),
    ('uf_prev_high_prev20_high_bias','high',1),('uf_close_low10_lag5_bias','low',14),
    ('uf_bottom10_lag5_lag18_abs_gap','low',27),('uf_bottom10_lag5_lag18_abs_gap','low',5),
    ('uf_high8_lag12_low10_lag18_ratio','high',19),('uf_high8_lag12_low10_lag18_ratio','low',18),
    ('uf_close_high8_lag12_bias','high',12),
])
def test_seventh_raw_factor_missing_input(factor,field,offset):
    df,days=frame()
    assert seventh_eval(change_at(df,df.height-1-offset,**{field:None}),days)[factor][-1] is None



def test_eighth_numeric_factors_independent_reference_and_warmup():
    import numpy as np
    df, days = frame(80)
    rng = np.random.default_rng(88)
    opens = rng.uniform(90., 110., df.height)
    closes = rng.uniform(90., 110., df.height)
    highs = np.maximum(opens, closes)+1.
    df = df.with_columns(pl.Series('open', opens), pl.Series('close', closes),
                         pl.Series('high', highs), pl.Series('low', np.minimum(opens, closes)-1.))
    out = eighth_eval(df, days)
    for i in range(14, df.height):
        mean_move = sum(abs(closes[j]-closes[j-1]) for j in range(i-13, i+1))/14
        expected = [abs(closes[i]-opens[i])/closes[i],
                    closes[i]/((opens[i-2]+closes[i-2])/2),
                    (opens[i]-highs[i-1])/highs[i-1]*closes[i]/mean_move,
                    opens[i]/(highs[i-1]*(1+.8*mean_move/closes[i]))]
        for f, value in zip(FACTORS[40:44], expected, strict=True):
            assert out[f['id']][i] == pytest.approx(value)
    for f, n in zip(FACTORS[40:44], [1, 3, 15, 15], strict=True):
        values = out[f['id']]
        assert values[:n-1].null_count() == n-1
        assert values[n-1] is not None
    for scale in [.01, .0333, 10.]:
        scaled = eighth_eval(df.with_columns([pl.col(f)*scale for f in ('open', 'high', 'low', 'close')]), days)
        for f in FACTORS[40:44]:
            assert scaled[f['id']][-1] == pytest.approx(out[f['id']][-1])


@pytest.mark.parametrize(('factor', 'field', 'offset'), [
    ('uf_abs_body_close', 'open', 0), ('uf_abs_body_close', 'close', 0),
    ('uf_close_body_midpoint_lag2_ratio', 'open', 2),
    ('uf_close_body_midpoint_lag2_ratio', 'close', 2),
    ('uf_close_body_midpoint_lag2_ratio', 'close', 0),
    ('uf_open_gap_close_move14_ratio', 'open', 0),
    ('uf_open_gap_close_move14_ratio', 'high', 1),
    *[('uf_open_gap_close_move14_ratio', 'close', k) for k in range(15)],
    ('uf_open_gap08_required_ratio', 'open', 0),
    ('uf_open_gap08_required_ratio', 'high', 1),
    *[('uf_open_gap08_required_ratio', 'close', k) for k in range(15)],
])
def test_eighth_raw_factors_preserve_missing(factor, field, offset):
    df, days = eighth_gap_frame()
    assert eighth_eval(change_at(df, df.height-1-offset, **{field: None}), days)[factor][-1] is None


def test_eighth_gap_is_close_move_not_atr_or_absolute_gap_ratio():
    df, days = eighth_gap_frame(.8)
    out = eighth_eval(df, days)
    closes = df['close'].tail(15).to_list()
    move = sum(abs(a-b) for a, b in zip(closes[1:], closes[:-1], strict=True))/14
    expected = (df['open'][-1]-df['high'][-2])/df['high'][-2]*df['close'][-1]/move
    assert out['uf_open_gap_close_move14_ratio'][-1] == pytest.approx(expected)
    assert expected == pytest.approx(.8)
    absolute_gap = (df['open'][-1]-df['high'][-2])/move
    assert abs(expected-absolute_gap) > .01
    # 加大历史日内振幅，不改收盘/前高；真实波幅会变化，当前因子不应变化。
    changed = change_at(df, df.height-10, high=1000., low=1.)
    assert eighth_eval(changed, days)['uf_open_gap_close_move14_ratio'][-1] == out['uf_open_gap_close_move14_ratio'][-1]


def test_ninth_factors_against_independent_decimal_windows():
    from decimal import Decimal

    import numpy as np
    df, days = frame(80)
    rng = np.random.default_rng(99)
    df = df.with_columns(pl.Series('open', rng.uniform(90,100,df.height)),
                         pl.Series('close', rng.uniform(95,105,df.height)),
                         pl.Series('high', rng.uniform(110,150,df.height)),
                         pl.Series('low', rng.uniform(50,80,df.height)),
                         pl.Series('amount', rng.uniform(1,200,df.height)))
    rows = [{k:Decimal(str(r[k])) for k in ('high','low','close','amount')} for r in df.to_dicts()]
    out = ninth_eval(df, days)
    for i in range(19,df.height):
        amp = [(r['high']-r['low'])/r['close'] for r in rows[i-19:i+1]]
        highs=[r['high'] for r in rows[i-7:i+1]]
        lows=[r['low'] for r in rows[i-7:i+1]]
        expected = [(sum(amp[-5:])/5)/(sum(amp)/20),
                    rows[i]['amount']/(sum(r['amount'] for r in rows[i-4:i+1])/5),
                    (max(highs)-min(lows))/min(lows),
                    sum(r['amount'] for r in rows[i-7:i+1])/sum(r['amount'] for r in rows[i-15:i-7])]
        for f, value in zip(FACTORS[44:48],expected,strict=True):
            assert out[f['id']][i] == pytest.approx(float(value)), f['id']
    for f,n in zip(FACTORS[44:48],[20,5,8,16],strict=True):
        values=out[f['id']]
        assert values[:n-1].null_count()==n-1
        assert values[n-1] is not None
    scaled = ninth_eval(df.with_columns([pl.col(f)*.0333 for f in ('open','high','low','close','amount')]),days)
    for f in FACTORS[44:48]:
        assert scaled[f['id']][-1] == pytest.approx(out[f['id']][-1])


@pytest.mark.parametrize(('factor','field','offset'), [
    *[('uf_intraday_range_mean5_20_ratio',f,k) for f in ('high','low','close') for k in (0,4,5,19)],
    *[('uf_amount_current_mean5_ratio','amount',k) for k in range(5)],
    *[('uf_range_8d_low',f,k) for f in ('high','low') for k in (0,7)],
    *[('uf_amount_8_prev8_ratio','amount',k) for k in range(16)],
])
def test_ninth_raw_factor_missing_inputs(factor,field,offset):
    df,days=ninth_vcp_frame()
    assert ninth_eval(change_at(df,df.height-1-offset,**{field:None}),days)[factor][-1] is None


def test_tenth_factors_independent_decimal_and_warmup():
    from decimal import Decimal

    import numpy as np
    df, days = frame(65)
    rng = np.random.default_rng(1010)
    df = df.with_columns(pl.Series('high', rng.uniform(110,150,df.height)),
                         pl.Series('low', rng.uniform(50,80,df.height)),
                         pl.Series('close', rng.uniform(95,105,df.height)),
                         pl.Series('amount', rng.uniform(1,200,df.height)))
    rows = [{k: Decimal(str(v)) for k,v in row.items() if k in ('high','low','close','amount')}
            for row in df.to_dicts()]
    out = tenth_eval(df, days)
    for i in range(29, df.height):
        def window(field, lo, hi, i=i):
            return [rows[i-k][field] for k in range(lo, hi+1)]
        left, right = max(window('high',20,29)), max(window('high',3,7))
        bottom, handle = min(window('low',8,19)), min(window('low',0,2))
        c = rows[i]['close']
        expected = [max(window('high',0,4))/max(window('high',5,9)),
                    sum(window('amount',0,9))/sum(window('amount',10,19)),
                    2*c/(rows[i-10]['close']+rows[i-20]['close']),
                    left/bottom, right/bottom, abs(right/left-1), handle/bottom,
                    handle/right, c/right, min(window('low',0,4))/(sum(window('close',0,19))/20)]
        for factor, value in zip(FACTORS[48:58], expected, strict=True):
            assert out[factor['id']][i] == pytest.approx(float(value)), factor['id']
    for factor, n in zip(FACTORS[48:58], [10,20,21,30,20,30,20,8,8,20], strict=True):
        values = out[factor['id']]
        assert values[:n-1].null_count() == n-1, factor['id']
        assert values[n-1] is not None, factor['id']
    for scale in (.0333, 10., 10000.):
        scaled = tenth_eval(df.with_columns([pl.col(f)*scale for f in ('open','high','low','close','amount')]), days)
        for factor in FACTORS[48:58]:
            assert scaled[factor['id']][-1] == pytest.approx(out[factor['id']][-1])


@pytest.mark.parametrize('index,field,offset', [
    (0,'high',0), (0,'high',9), (1,'amount',0), (1,'amount',19),
    (2,'close',0), (2,'close',10), (2,'close',20),
    (3,'low',8), (3,'low',19), (3,'high',20), (3,'high',29),
    (4,'low',19), (4,'high',3), (4,'high',7),
    (5,'high',3), (5,'high',29), (6,'low',0), (6,'low',19),
    (7,'low',2), (7,'high',7), (8,'close',0), (8,'high',3),
    (9,'low',4), (9,'close',19),
])
def test_tenth_factor_missing_inputs(index, field, offset):
    df, days = frame()
    out = tenth_eval(change_at(df, df.height-1-offset, **{field: None}), days)
    assert out[FACTORS[48+index]['id']][-1] is None


@pytest.mark.parametrize('index,builder', [(0,tenth_flag_frame),(1,tenth_cup_frame),(2,tenth_pullback_frame)])
def test_tenth_factor_and_signal_scale_invariance(index, builder):
    from app.strategy.premarket_signal_definitions import TENTH_BATCH_SIGNALS
    df, days = builder()
    sid = TENTH_BATCH_SIGNALS[index]['id']
    assert val(tenth_eval(df,days),sid) is True
    for scale in (.0333, 10., 10000.):
        scaled = df.with_columns(*[pl.col(f)*scale for f in ('open','high','low','close')],
                                 pl.col('amount')*17., pl.lit(0.).alias('volume'))
        assert val(tenth_eval(scaled,days),sid) is True


def test_eleventh_factors_decimal_windows_and_warmup():
    from decimal import Decimal

    import numpy as np
    df,days=frame(80)
    rng=np.random.default_rng(1111)
    df=df.with_columns(pl.Series('high',rng.uniform(110,150,df.height)),
                       pl.Series('low',rng.uniform(50,80,df.height)),
                       pl.Series('close',rng.uniform(95,105,df.height)),
                       pl.Series('amount',rng.uniform(1,200,df.height)))
    rows=[{k:Decimal(str(v)) for k,v in row.items() if k in ('high','low','close','amount')}
          for row in df.to_dicts()]
    out=eleventh_eval(df,days)
    for i in range(38,df.height):
        def w(field,lo,hi,i=i):
            return [rows[i-k][field] for k in range(lo,hi+1)]
        expected=[max(w('high',0,11))/min(w('low',0,11)),
                  min(w('low',9,20))/min(w('low',1,8)),
                  (sum(w('amount',1,8))/8)/(sum(w('amount',9,20))/12),
                  rows[i]['close']/min(w('low',9,20)),
                  min(w('low',22,38))/min(w('low',10,19)),
                  (sum(w('amount',10,19))/10)/(sum(w('amount',22,38))/17),
                  max(w('high',0,9))/min(w('high',0,9))]
        for factor,value in zip(FACTORS[58:65],expected,strict=True):
            assert out[factor['id']][i]==pytest.approx(float(value)),factor['id']
    for factor,n in zip(FACTORS[58:65],[12,21,21,21,39,39,10],strict=True):
        values=out[factor['id']]
        assert values[:n-1].null_count()==n-1
        assert values[n-1] is not None
    for scale in (.0333,10.,10000.):
        scaled=eleventh_eval(df.with_columns([pl.col(f)*scale for f in ('open','high','low','close','amount')]),days)
        for factor in FACTORS[58:65]:
            assert scaled[factor['id']][-1]==pytest.approx(out[factor['id']][-1])


@pytest.mark.parametrize('index,field,offset',[
    (0,'high',0),(0,'high',11),(0,'low',0),(0,'low',11),
    (1,'low',1),(1,'low',8),(1,'low',9),(1,'low',20),
    (2,'amount',1),(2,'amount',8),(2,'amount',9),(2,'amount',20),
    (3,'close',0),(3,'low',9),(3,'low',20),
    (4,'low',10),(4,'low',19),(4,'low',22),(4,'low',38),
    (5,'amount',10),(5,'amount',19),(5,'amount',22),(5,'amount',38),
    (6,'high',0),(6,'high',9),
])
def test_eleventh_raw_factor_null_dependencies(index,field,offset):
    df,days=frame()
    out=eleventh_eval(change_at(df,df.height-1-offset,**{field:None}),days)
    assert out[FACTORS[58+index]['id']][-1] is None


def test_twelfth_factors_independent_decimal_and_warmup():
    from decimal import Decimal

    import numpy as np
    df,days=frame(100)
    rng=np.random.default_rng(1212)
    df=df.with_columns(pl.Series('high',rng.uniform(110,150,df.height)),
                       pl.Series('low',rng.uniform(50,80,df.height)),
                       pl.Series('close',rng.uniform(95,105,df.height)))
    highs=[Decimal(str(x)) for x in df['high']]
    closes=[Decimal(str(x)) for x in df['close']]
    out=twelfth_eval(df,days)
    for i in range(60,df.height):
        ratio=closes[i]/max(highs[i-60:i])
        count=sum(highs[j]>highs[j-1] and highs[j]>highs[j+1] for j in range(i-59,i))
        assert out['uf_close_prev60_high_ratio'][i]==pytest.approx(float(ratio))
        assert out['uf_confirmed_high_peak_count59'][i]==count
    for factor in FACTORS[65:67]:
        assert out[factor['id']][:60].null_count()==60
        assert out[factor['id']][60] is not None
    for scale in (.0333,.1,10.,10000.):
        scaled=twelfth_eval(df.with_columns([pl.col(f)*scale for f in ('open','high','low','close')]),days)
        assert scaled['uf_close_prev60_high_ratio'][-1]==pytest.approx(out['uf_close_prev60_high_ratio'][-1])
        assert scaled['uf_confirmed_high_peak_count59'].equals(out['uf_confirmed_high_peak_count59'])


@pytest.mark.parametrize('offset',range(61))
def test_twelfth_peak_factor_requires_every_high(offset):
    df,days=twelfth_peak_frame()
    out=twelfth_eval(change_at(df,df.height-1-offset,high=None),days)
    assert out['uf_confirmed_high_peak_count59'][-1] is None
    if offset:
        assert out['uf_close_prev60_high_ratio'][-1] is None
    else:
        assert out['uf_close_prev60_high_ratio'][-1]==pytest.approx(1.015)


def test_twelfth_high_count_extremes_current_confirmation_and_no_lookahead():
    df,days=twelfth_peak_frame(close=85.,peaks=tuple(range(1,60,2)))
    df=change_last(df,open=85.,high=90.)
    out=twelfth_eval(df,days)
    assert out['uf_confirmed_high_peak_count59'][-1]==30
    assert val(out,'t_guozuofeng') is False
    # 改D的high只取消D-1这个局部峰，不改变此前60日最高价。
    changed=twelfth_eval(change_last(df,high=200.),days)
    assert changed['uf_confirmed_high_peak_count59'][-1]==29
    assert changed['uf_close_prev60_high_ratio'][-1]==out['uf_close_prev60_high_ratio'][-1]
    assert changed['uf_confirmed_high_peak_count59'][:-1].equals(out['uf_confirmed_high_peak_count59'][:-1])
