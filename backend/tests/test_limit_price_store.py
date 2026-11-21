"""涨停价历史小表(纯规则推断价)的单元测试。

涨停价不再经由任何外部接口获取: 表只存 limit_up_inferred / limit_down_inferred,
由本地规则(除权日用 xdxr_ref 修正昨收)算出并落盘。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from app.services import limit_price_store as lps

SYM = "600825.SH"


# ---------------------------------------------------------------------------
# 存储服务
# ---------------------------------------------------------------------------


def test_merge_keeps_existing_value_when_new_is_null(tmp_path):
    d1 = pl.DataFrame({
        "symbol": [SYM],
        "trade_date": [date(2026, 9, 21)],
        "limit_up_inferred": [5.84],
        "limit_down_inferred": [4.78],
    })
    lps.merge(tmp_path, d1)
    # 同键重写, up 为 null 不应抹掉旧值; down 更新。
    d2 = pl.DataFrame({
        "symbol": [SYM],
        "trade_date": [date(2026, 9, 21)],
        "limit_up_inferred": [None],
        "limit_down_inferred": [4.80],
    })
    added, table = lps.merge(tmp_path, d2)
    row = table.row(0, named=True)
    assert added == 0
    assert row["limit_up_inferred"] == 5.84  # 旧值保留
    assert row["limit_down_inferred"] == 4.80  # 新非 null 覆盖


def test_merge_normalizes_missing_columns_and_dedups(tmp_path):
    d = pl.DataFrame({
        "symbol": [SYM, SYM, "000001.SZ"],
        "trade_date": [date(2026, 9, 21)] * 3,
        "limit_up_inferred": [5.0, 5.84, 10.0],  # 同键两条, keep last
    })
    added, table = lps.merge(tmp_path, d)
    assert added == 2
    assert table.height == 2
    row = table.filter(pl.col("symbol") == SYM).row(0, named=True)
    assert row["limit_up_inferred"] == 5.84
    assert row["limit_down_inferred"] is None
    assert set(table.columns) == set(lps.COLUMNS)


def test_load_missing_file_returns_empty_with_schema(tmp_path):
    empty = lps.load(tmp_path)
    assert empty.height == 0
    assert set(empty.columns) == set(lps.COLUMNS)
    assert "limit_up_api" not in lps.COLUMNS
    assert "limit_down_api" not in lps.COLUMNS


def test_load_drops_legacy_api_columns(tmp_path):
    """旧版含 api 列的 parquet 读入后自动丢弃 api 列(兼容历史数据)。"""
    legacy = pl.DataFrame({
        "symbol": [SYM],
        "trade_date": [date(2026, 9, 21)],
        "limit_up_api": [5.84],
        "limit_down_api": [4.78],
        "limit_up_inferred": [5.84],
        "limit_down_inferred": [4.78],
    })
    (tmp_path / "limit_prices").mkdir()
    legacy.write_parquet(tmp_path / "limit_prices" / "all.parquet")
    loaded = lps.load(tmp_path)
    assert set(loaded.columns) == set(lps.COLUMNS)
    assert "limit_up_api" not in loaded.columns
