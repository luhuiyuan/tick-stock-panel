"""收尾方案位置三档的优先验收：名义切点、尺度与严格窗口。

定义仅通过显式安装器落盘；原失败用例保留，不放宽断言掩盖分档错误。
"""
from datetime import date, timedelta
from decimal import Decimal

import polars as pl
import pytest

from app.factors import registry
from app.factors.premarket import FACTORS, FINAL_POSITION_FACTORS
from app.factors.store import register_definition
from app.strategy import custom_signals
from app.strategy.premarket_signal_definitions import FINAL_POSITION_SIGNALS, SIGNALS

SCALES = (.001, .007, .01, .0333, .07, .1, .2, .3, .3333,
          1., 2., 10., 123.45, 1000., 10000.)


@pytest.fixture(autouse=True)
def isolated_factors():
    with registry.isolated_registry():
        for factor in FINAL_POSITION_FACTORS:
            register_definition(factor)
        yield


def sample(drawdown, scale=1., n=60):
    days = []
    day = date(2025, 1, 2)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    # 先用十进制生成独立名义价格，再转换成实际管线使用的float64。
    close = float(Decimal('100') * (Decimal('1') - drawdown)) * scale
    df = pl.DataFrame({
        'symbol': ['000001.SZ'] * n, 'date': days,
        'open': [80. * scale] * n, 'high': [100. * scale] * n,
        'low': [70. * scale] * n, 'close': [close] * n,
    })
    return df, days


def evaluate(df, days):
    exprs = custom_signals.build_expressions(FINAL_POSITION_SIGNALS)
    assert len(exprs) == 3
    df = custom_signals.materialize_factor_columns(df, exprs, trading_dates=days)
    return custom_signals.inject(df, exprs)


@pytest.mark.parametrize('scale', SCALES)
@pytest.mark.parametrize('boundary,below,at', [('0.0598', 0, 1), ('0.1781', 1, 2)])
@pytest.mark.parametrize('delta', ['-0.00000001', '0', '0.00000001'])
def test_nominal_position_boundaries_across_price_scales(boundary, below, at, delta, scale):
    df, days = sample(Decimal(boundary) + Decimal(delta), scale)
    row = evaluate(df, days).row(-1, named=True)
    expected = below if Decimal(delta) < 0 else at
    flags = [row[f'csg_{sid}'] for sid in ('t_pos_high', 't_pos_mid', 't_pos_low')]
    assert flags.count(True) == 1, '合法样本必须恰好命中一档'
    assert row['uf_position_fixed_band'] == expected, (
        f'cut={boundary}, delta={delta}, scale={scale}, '
        f'actual_band={row["uf_position_fixed_band"]}, expected={expected}'
    )
    assert flags == [expected == i for i in range(3)]


@pytest.mark.parametrize('field', ['open', 'high', 'low', 'close'])
def test_missing_ohlc_is_unknown_in_all_three_bands(field):
    df, days = sample(Decimal('0.1'))
    df = df.with_columns(pl.when(pl.col('date') == days[13]).then(None)
                         .otherwise(pl.col(field)).alias(field))
    row = evaluate(df, days).row(-1, named=True)
    assert all(row[f'csg_{s["id"]}'] is None for s in FINAL_POSITION_SIGNALS)


def test_position_definitions_are_in_explicit_install_set():
    assert {f['id'] for f in FINAL_POSITION_FACTORS} <= {f['id'] for f in FACTORS}
    assert {s['id'] for s in FINAL_POSITION_SIGNALS} <= {s['id'] for s in SIGNALS}
    assert len(FACTORS) == 100 and len(SIGNALS) == 93


@pytest.mark.parametrize('cut,below,at', [(.0598, 0, 1), (.1781, 1, 2)])
@pytest.mark.parametrize('side', [-1, 0, 1])
def test_adjusted_classification_boundary_adjacent_floats(cut, below, at, side):
    import math

    from app.factors.dsl import compile_formula

    threshold = cut - 8 * 2.220446049250313e-16
    value = math.nextafter(threshold, -math.inf if side < 0 else math.inf) if side else threshold
    # 直接输入连续因子，隔离分类边界与价格除法的舍入误差。
    df = pl.DataFrame({'symbol': ['X'], 'uf_drawdown_high_60': [value]})
    # 这里只检验分类DSL的相邻浮点数；完整OHLC契约由上面的端到端测试验证。
    expr = compile_formula(FINAL_POSITION_FACTORS[1]['formula'])
    assert expr.ok
    out = df.with_columns(expr.frame_transform(df)['__dsl_factor__'].alias('uf_position_fixed_band'))
    assert out['uf_position_fixed_band'][0] == (below if side < 0 else at)
    assert out['uf_drawdown_high_60'][0] == value
