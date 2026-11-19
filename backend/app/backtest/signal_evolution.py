"""Single-factor research evaluation. Neither orders nor production signals are produced."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

import polars as pl

HORIZONS = (1, 3, 5)
KEYS = ['symbol', 'date']
_TRAIN_MIN = (200, 30)
_HOLDOUT_MIN = (100, 15)


def _positive_close() -> pl.Expr:
    close = pl.col('close').cast(pl.Float64)
    return pl.when(close.is_finite() & (close > 0)).then(close).otherwise(None)


def prepare_panel(frame: pl.DataFrame, days: Sequence[date], symbols: Sequence[str] | None = None) -> pl.DataFrame:
    """Join on the actual market target day, not a security's next observed row."""
    if list(days) != sorted(set(days)) or len(days) < 10:
        raise ValueError('交易日历重复、乱序或样本日期不足')
    if frame.select(KEYS).null_count().row(0) != (0, 0) or frame.select(KEYS).is_duplicated().any():
        raise ValueError('证券日期键为空或重复')
    if frame.filter(~pl.col('date').is_in(days)).height:
        raise ValueError('存在不在研究交易日历内的行情')
    universe = sorted(set(symbols if symbols is not None else frame['symbol'].to_list()))
    if not universe or frame.is_empty():
        raise ValueError('研究范围没有可用日线缓存')
    if not set(frame['symbol'].to_list()) <= set(universe):
        raise ValueError('证券超出研究范围')
    factor = pl.col('factor').cast(pl.Float64)
    panel = pl.DataFrame({'symbol': universe}).join(pl.DataFrame({'date': days}), how='cross').join(
        frame.with_columns(_positive_close().alias('close'),
                           pl.when(factor.is_finite()).then(factor).otherwise(None).alias('factor')),
        on=KEYS, how='left', validate='1:1',
    )
    for h in HORIZONS:
        target = f'target_{h}d'
        panel = panel.join(pl.DataFrame({'date': days[:-h], target: days[h:]}), on='date', how='left', validate='m:1')
        prices = frame.select('symbol', pl.col('date').alias(target), _positive_close().alias('_future_close'))
        result = pl.col('_future_close') / pl.col('close') - 1
        panel = panel.join(prices, on=['symbol', target], how='left', validate='m:1').with_columns(
            pl.when(result.is_finite()).then(result).otherwise(None).alias(f'return_{h}d'),
        ).drop('_future_close')
    return panel.sort(KEYS)


def _metrics(segment: pl.DataFrame, minimum: tuple[int, int], *, boundary: date | None = None) -> dict:
    hit = pl.col('_hit')
    count = segment.select(hit.sum(), hit.not_().sum().alias('miss'), hit.null_count().alias('unknown')).row(0)
    metrics = {'rows': segment.height, 'hit': count[0], 'miss': count[1], 'unknown': count[2], 'horizons': {}}
    for h in HORIZONS:
        target = pl.col(f'target_{h}d')
        ret = pl.col(f'return_{h}d')
        allowed = target.is_not_null() if boundary is None else (target < boundary).fill_null(False)
        valid = ret.is_not_null() & allowed
        base = segment.filter(hit.is_not_null() & valid)
        matched = base.filter(hit)
        by_day = base.group_by('date').agg(ret.mean().alias('base'), ret.filter(hit).mean().alias('match')).sort('date')
        used_days = by_day.filter(pl.col('match').is_not_null())
        enough = matched.height >= minimum[0] and used_days.height >= minimum[1]
        summary = {'target_missing': segment.filter(ret.is_null() & allowed).height + segment.filter(target.is_null()).height,
                   'purged': segment.filter(target.is_not_null() & ~allowed).height,
                   'support': base.height, 'hits_with_return': matched.height, 'hit_days': used_days.height,
                   'status': 'evaluated' if enough else 'insufficient_samples',
                   'lift': None, 'mean': None, 'baseline': None, 'win_rate': None}
        if enough:
            mean, baseline = used_days.select(pl.col('match').mean(), pl.col('base').mean()).row(0)
            summary.update(lift=mean - baseline, mean=mean, baseline=baseline,
                           win_rate=matched.select((ret > 0).mean()).item())
        metrics['horizons'][str(h)] = summary
    metrics['lift_3d'] = metrics['horizons']['3']['lift']
    return metrics


def evaluate(
    frame: pl.DataFrame, days: Sequence[date], *, signal_condition_count: int,
    symbols: Sequence[str] | None = None, checkpoint: Callable[[], None] = lambda: None,
) -> dict:
    if not 1 <= signal_condition_count <= 7:
        raise ValueError('展开后的普通信号条件数不得超过 8')
    panel = prepare_panel(frame, days, symbols)
    cut = days[len(days) * 7 // 10]
    train = panel.filter(pl.col('date') < cut)
    # Use the same purged training dates for threshold fitting as for the primary target.
    fit = train.filter(pl.col('target_3d') < cut)['factor'].drop_nulls()
    thresholds: list[float] = []
    factor_status = 'ready'
    if fit.is_empty():
        factor_status = 'no_valid_training_values'
    elif fit.min() == fit.max():
        factor_status = 'constant_training_values'
    else:
        thresholds = sorted({fit.quantile(q, interpolation='linear') for q in (.2, .4, .6, .8)})
    signal = pl.col('signal')
    specs = [('signal', 'signal', None, None, signal, signal_condition_count)]
    for i, threshold in enumerate(thresholds):
        for op in ('>=', '<='):
            term = pl.col('factor') >= threshold if op == '>=' else pl.col('factor') <= threshold
            both = pl.when(signal.is_not_null() & term.is_not_null()).then(signal & term).otherwise(None)
            specs.extend([(f'factor:{i}:{op}', 'factor', op, threshold, term, 1),
                          (f'and:{i}:{op}', 'and', op, threshold, both, signal_condition_count + 1)])
    frozen = []
    masks = {}
    for sid, kind, op, threshold, mask, conditions in specs:
        checkpoint()
        masks[sid] = mask.alias('_hit')
        frozen.append({'id': sid, 'kind': kind, 'op': op, 'threshold': threshold, 'conditions': conditions,
                       'train': _metrics(train.with_columns(masks[sid]), _TRAIN_MIN, boundary=cut)})

    def rank(candidate):
        lift = candidate['train']['lift_3d']
        return (lift is None, -lift if lift is not None else 0,
                -candidate['train']['horizons']['3']['hit_days'], candidate['conditions'], candidate['id'])

    frozen.sort(key=rank)
    holdout = panel.filter(pl.col('date') >= cut)
    for candidate in frozen:
        checkpoint()
        candidate['holdout'] = _metrics(holdout.with_columns(masks[candidate['id']]), _HOLDOUT_MIN)
    return {'candidates': frozen, 'attempted': len(frozen), 'factor_status': factor_status,
            'threshold_fit_count': len(fit), 'split': {'holdout_start': cut.isoformat(), 'train_dates': days.index(cut),
            'holdout_dates': len(days) - days.index(cut), 'purged_rows_3d': train.filter(pl.col('target_3d') >= cut).height}}
