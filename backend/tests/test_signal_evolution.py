from datetime import date, timedelta

import polars as pl
import pytest

from app.backtest.signal_evolution import evaluate, prepare_panel


def panel():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(100)]
    frame = pl.DataFrame([
        (f'S{i}', day, 100 + j + i, float(i + j), (i + j) % 3 != 0)
        for j, day in enumerate(days) for i in range(12)
    ], schema=['symbol', 'date', 'close', 'factor', 'signal'], orient='row')
    return frame, days


def test_frozen_candidates_do_not_depend_on_holdout():
    frame, days = panel()
    a = evaluate(frame, days, signal_condition_count=2)
    perturbed = frame.with_columns(
        pl.when(pl.col('date') >= days[70]).then(pl.col('close') * 2).otherwise(pl.col('close')).alias('close'),
        pl.when(pl.col('date') >= days[70]).then(pl.lit(None)).otherwise(pl.col('factor')).alias('factor'),
    )
    b = evaluate(perturbed, days, signal_condition_count=2)
    assert a['attempted'] == b['attempted'] == 17
    assert a['split']['holdout_start'] == days[70].isoformat()
    assert a['split']['purged_rows_3d'] == 36
    def frozen(result):
        return [(c['id'], c['threshold'], c['train']) for c in result['candidates']]

    assert frozen(a) == frozen(b)
    assert any(c['train']['lift_3d'] is not None for c in a['candidates'])
    assert [c['holdout'] for c in a['candidates']] != [c['holdout'] for c in b['candidates']]


def test_calendar_alignment_and_conservative_unknown():
    frame, days = panel()
    frame = frame.filter(~((pl.col('symbol') == 'S0') & (pl.col('date') == days[3])))
    panel_ = prepare_panel(frame, days)
    assert panel_.filter((pl.col('symbol') == 'S0') & (pl.col('date') == days[0]))['return_3d'].item() is None
    assert panel_.filter((pl.col('symbol') == 'S0') & (pl.col('date') == days[3]))['signal'].item() is None
    result = evaluate(frame.with_columns(pl.lit(float('inf')).alias('factor')), days, signal_condition_count=2)
    assert result['factor_status'] == 'no_valid_training_values'
    assert result['attempted'] == 1
    result = evaluate(frame.with_columns(pl.lit(1.0).alias('factor')), days, signal_condition_count=2)
    assert result['factor_status'] == 'constant_training_values'
    result = evaluate(frame.with_columns(pl.when(pl.col('symbol') == 'S0').then(None).otherwise(pl.col('factor')).alias('factor')), days, signal_condition_count=2)
    combos = [candidate for candidate in result['candidates'] if candidate['kind'] == 'and']
    assert combos and all(candidate['train']['unknown'] == 70 for candidate in combos)


def test_reject_duplicate_keys_and_condition_overflow():
    frame, days = panel()
    with pytest.raises(ValueError, match='重复'):
        evaluate(pl.concat([frame, frame.head(1)]), days, signal_condition_count=2)
    with pytest.raises(ValueError, match='8'):
        evaluate(frame, days, signal_condition_count=8)
