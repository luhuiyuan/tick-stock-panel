import polars as pl

from app.strategy.monitor import MonitorRuleEngine


def test_monitor_scope_filters_stock_boards():
    df = pl.DataFrame({"symbol": ["600000.SH", "000001.SZ", "300001.SZ", "688001.SH", "830001.BJ"]})
    scoped = MonitorRuleEngine._apply_scope(df, {
        "asset_type": "stock",
        "scope": "all",
        "basic_filter": {"boards": ["沪主板", "深主板"]},
    })
    assert scoped["symbol"].to_list() == ["600000.SH", "000001.SZ"]


def test_monitor_scope_empty_or_all_boards_is_unrestricted():
    df = pl.DataFrame({"symbol": ["600000.SH", "000001.SZ", "300001.SZ"]})
    for boards in ([], ["沪主板", "深主板", "创业板", "科创板", "北交所"]):
        scoped = MonitorRuleEngine._apply_scope(df, {
            "asset_type": "stock", "scope": "all", "basic_filter": {"boards": boards},
        })
        assert scoped.height == df.height
