"""因子自身契约及缓存身份，不依赖上层信号兜底。"""
from datetime import date, timedelta

import polars as pl
import pytest

from app.factors import materialize_cache, registry
from app.factors.store import register_definition
from app.strategy.scoring import materialize_scoring_columns
from app.strategy.strict_signal_data import attach_calendar


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    monkeypatch.setattr(materialize_cache, '_MIN_ROWS', 1)
    materialize_cache.invalidate()
    with registry.isolated_registry():
        yield
    materialize_cache.invalidate()


def definition(window=1):
    return {'id': 'uf_contract_cache_probe', 'kind': 'custom', 'label': 'contract probe',
            'formula': 'amount', 'status': 'active', 'version': 1,
            'data_contract': {'version': 2, 'inputs': {'amount': window}, 'ohlc_window': 0}}


def test_contract_change_invalidates_content_cache():
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(2)]
    df = attach_calendar(pl.DataFrame({'symbol':['X']*2, 'date':days, 'amount':[None, 10.]}), days)
    fid = definition()['id']
    register_definition(definition(1))
    assert materialize_scoring_columns(df, [fid])[fid][-1] == 10.
    assert materialize_scoring_columns(df, [fid])[fid][-1] == 10.
    assert materialize_cache.stats()['lru_hit'] >= 1
    registry.unregister_factor(fid)
    register_definition(definition(2))
    assert materialize_scoring_columns(df, [fid])[fid][-1] is None
    # 已附着的数值也不能跳过新契约。
    assert materialize_scoring_columns(df.with_columns(pl.lit(10.).alias(fid)), [fid])[fid][-1] is None


def test_contract_calendar_change_invalidates_content_cache():
    days = [date(2026, 1, 5), date(2026, 1, 7)]
    df = pl.DataFrame({'symbol':['X']*2, 'date':days, 'amount':[10., 10.]})
    fid = definition()['id']
    register_definition(definition(2))
    assert materialize_scoring_columns(attach_calendar(df, days), [fid])[fid][-1] == 10.
    full_calendar = [days[0], date(2026, 1, 6), days[1]]
    assert materialize_scoring_columns(attach_calendar(df, full_calendar), [fid])[fid][-1] is None
