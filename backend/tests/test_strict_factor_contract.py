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
    """日历变化(某日被移出权威日历)使个股该行 index=null, 缓存身份与结果同步失效。"""
    days = [date(2026, 1, 5), date(2026, 1, 7)]
    df = pl.DataFrame({'symbol':['X']*2, 'date':days, 'amount':[10., 10.]})
    fid = definition()['id']
    register_definition(definition(2))
    assert materialize_scoring_columns(attach_calendar(df, days), [fid])[fid][-1] == 10.
    # A方案: 日历补中间停牌日(01-06)不再使因子失效; 改为把 01-07 移出日历,
    # 该行 market_index=null -> 窗口不完整 -> None, 仍能验证缓存身份随日历失效。
    shrunk_calendar = [days[0]]
    assert materialize_scoring_columns(attach_calendar(df, shrunk_calendar), [fid])[fid][-1] is None


def test_suspension_gap_long_window_still_valid():
    """A方案: 停牌致 market_index 跳变(0->2), 但个股连续 n 行 K 线有效 -> 因子仍有值。"""
    days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
    # 01-06 是交易日(日历里有), 个股停牌无 K 线; 个股仅 01-05、01-07 两行。
    df = pl.DataFrame({'symbol': ['X'] * 2, 'date': [days[0], days[2]], 'amount': [10., 12.]})
    fid = definition()['id']
    register_definition(definition(2))  # window=2, 会触发旧的 index.shift(1) 连续性检查
    out = materialize_scoring_columns(attach_calendar(df, days), [fid])[fid]
    # 旧逻辑: index=[0,2], 2-0!=1 -> None; A方案: 个股连续两行有效 -> 12.
    assert out[-1] == 12.


def test_duplicate_calendar_key_still_invalid():
    """A方案只放行停牌跳变; 同一交易日重复记录(index=null)仍须判窗口无效。"""
    days = [date(2026, 1, 5), date(2026, 1, 6)]
    df = pl.DataFrame(
        {'symbol': ['X'] * 3, 'date': [days[0], days[0], days[1]], 'amount': [10., 10., 12.]}
    )
    fid = definition()['id']
    register_definition(definition(2))
    # attach_calendar 把重复 (symbol,date) 的 index 置 null -> 连续 2 行不全有效 -> None。
    out = materialize_scoring_columns(attach_calendar(df, days), [fid])[fid]
    assert out[-1] is None
