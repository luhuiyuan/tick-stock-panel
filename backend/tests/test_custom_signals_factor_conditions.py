"""自定义信号因子条件 — 字段白名单动态化 + 因子列物化链路。

因子接入规则 (P3): 条件可引用注册表因子 (虚拟/自定义/复合), 历史路径由
materialize_factor_columns 复用评分物化管线补算, 与检验/评分同一条计算逻辑。
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.strategy import custom_signals


def _frame(n_days: int = 30) -> pl.DataFrame:
    """两标的收盘价缓涨; ma20 手工预置为 0.9 倍滚动均值 (保证乖离恒为正)。"""
    rows = []
    end = date(2026, 8, 31)
    for symbol_id, base in (("A", 100.0), ("B", 50.0)):
        for i in range(n_days):
            rows.append({
                "symbol": symbol_id,
                "date": end - timedelta(days=n_days - 1 - i),
                "close": base * (1.0 + 0.001 * i),
            })
    df = pl.DataFrame(rows).sort(["symbol", "date"])
    return df.with_columns(
        (pl.col("close").rolling_mean(20).over("symbol") * 0.9).alias("ma20")
    )


def test_allowed_fields_union_registry() -> None:
    allowed = custom_signals.allowed_fields()
    assert "close" in allowed  # 物化白名单保留
    assert "ma20_bias" in allowed  # 虚拟因子
    assert "turnover_z_60d" in allowed  # 虚拟因子 (61 日预热)
    assert "momentum_20d" in allowed  # 既是白名单列也是基础因子
    assert "nope_col" not in allowed
    # 静态白名单不受污染 (供 monitor_rules 等仍按物化列口径使用)
    assert "ma20_bias" not in custom_signals.ALLOWED_FIELDS


def test_validate_accepts_and_rejects_factor_fields() -> None:
    ok = {
        "id": "t_bias_low", "name": "乖离超卖", "kind": "entry",
        "conditions": [{"left": "ma20_bias", "op": "<", "right": "-0.05", "leftDays": 0, "rightDays": 0}],
    }
    custom_signals.validate(ok)  # 不抛错即通过
    bad = {
        "id": "t_bad", "name": "x", "kind": "entry",
        "conditions": [{"left": "not_a_field", "op": "<", "right": "1"}],
    }
    with pytest.raises(ValueError):
        custom_signals.validate(bad)


def test_materialize_factor_columns_and_inject() -> None:
    sig = {
        "id": "bias_high", "name": "乖离偏高", "kind": "entry", "enabled": True,
        "conditions": [{"left": "ma20_bias", "op": ">", "right": "0", "leftDays": 0, "rightDays": 0}],
    }
    exprs = custom_signals.build_expressions([sig])
    col = custom_signals.column_name("bias_high")
    assert col in exprs

    df = _frame()
    assert "ma20_bias" not in df.columns
    df2 = custom_signals.materialize_factor_columns(df, exprs)
    assert "ma20_bias" in df2.columns  # 复用评分物化路径补算

    # ma20 = 0.9 x 滚动均值 → 窗口内乖离恒 > 0
    warm = df2.filter(pl.col("ma20_bias").is_not_null())
    assert warm.height > 0
    assert (warm["ma20_bias"] > 0).all()

    injected = custom_signals.inject(df2, exprs)
    assert col in injected.columns
    hit = injected.filter(pl.col("ma20_bias").is_not_null())
    assert hit[col].all()  # 条件在窗口内全部成立


def test_materialize_skips_unknown_columns() -> None:
    """非注册表缺失列: 物化不处理不报错, 由 inject 缺列告警跳过。"""
    sig = {
        "id": "t_unknown", "name": "x", "kind": "entry", "enabled": True,
        "conditions": [{"left": "not_a_field", "op": "<", "right": "1", "leftDays": 0, "rightDays": 0}],
    }
    exprs = custom_signals.build_expressions([sig])  # 编译不做白名单校验 (validate 负责)
    df = _frame()
    df2 = custom_signals.materialize_factor_columns(df, exprs)
    assert df2.columns == df.columns


def test_alpha158_signal_catalog_and_ai_fields():
    from app.api.signals import get_options
    from app.factors.alpha158 import ALPHA158_IDS
    from app.factors.registry import all_factors
    from app.strategy.custom_signals_ai import _format_fields

    allowed = custom_signals.allowed_fields()
    options = get_options()
    fields = {f['key'] for f in options['fields']}
    grouped = {f['key'] for g in options['groups'] for f in g['fields']}
    prompt = _format_fields()
    for fid in ALPHA158_IDS:
        assert fid in allowed
        assert fid in fields
        assert fid in grouped
        assert fid in prompt
    # 普通信号开放不改变默认挖掘目录。
    assert not any(s.id.startswith('a158_') for s in all_factors())
    assert '实验' in next(f['label'] for f in options['fields'] if f['key'] == 'a158_kmid')


def test_alpha158_signal_validation_and_right_field():
    sig = {'id': 'alpha_compare', 'name': 'Alpha比较', 'kind': 'entry',
           'conditions': [{'left': 'a158_kmid', 'op': '>', 'right': 'field:a158_kup'}]}
    custom_signals.validate(sig)
    custom_signals.validate_overlay_conditions(sig['conditions'])
    assert custom_signals.column_name(sig['id']) in custom_signals.build_expressions([sig])


def test_alpha158_materialization_is_on_demand_and_preserves_null():
    df = _frame(8).with_columns((pl.col('close') / 1.02).alias('open'))
    df = df.with_columns(pl.when(pl.col('date') == df['date'].max())
                         .then(None).otherwise(pl.col('open')).alias('open'))
    sig = {'id': 'alpha_positive', 'conditions': [
        {'left': 'a158_kmid', 'op': '>', 'right': '0.01'}]}
    exprs = custom_signals.build_expressions([sig])
    assert custom_signals.materialize_factor_columns(df, exprs, needed=set()).columns == df.columns
    out = custom_signals.materialize_factor_columns(df, exprs)
    assert set(out.columns) - set(df.columns) == {'a158_kmid'}
    assert out['a158_kmid'].drop_nulls().to_list() == pytest.approx([0.02] * 14)
    assert out['a158_kmid'].null_count() == 2
    result = custom_signals.inject(out, exprs)
    assert result[custom_signals.column_name('alpha_positive')].sum() == 14


def test_alpha158_rolling_signal_offset_and_warmup():
    from app.factors.registry import factor_dependencies, factor_warmup_bars

    fid = 'a158_ma_5'
    sig = {'id': 'alpha_history', 'conditions': [
        {'left': fid, 'op': '<', 'right': '1', 'leftDays': 1}]}
    exprs = custom_signals.build_expressions([sig])
    df = _frame(8)
    out = custom_signals.materialize_factor_columns(df, exprs)
    result = custom_signals.inject(out, exprs)
    for symbol in ('A', 'B'):
        rows = result.filter(pl.col('symbol') == symbol)
        assert rows[fid][:4].null_count() == 4
        assert rows[custom_signals.column_name('alpha_history')].to_list() == [None] * 5 + [True] * 3
    assert 'close' in factor_dependencies([fid])
    assert factor_warmup_bars([fid]) >= 5


def test_alpha158_missing_source_does_not_create_true_signal():
    sig = {'id': 'alpha_missing', 'conditions': [
        {'left': 'a158_kmid', 'op': '>', 'right': '0'}]}
    exprs = custom_signals.build_expressions([sig])
    out = custom_signals.materialize_factor_columns(_frame(8), exprs)
    result = custom_signals.inject(out, exprs)
    column = custom_signals.column_name('alpha_missing')
    assert column not in result.columns or not result[column].fill_null(False).any()
