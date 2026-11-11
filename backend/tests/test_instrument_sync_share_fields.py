"""标的维表同步: provider 不提供股本时不覆盖已有值。

回归背景: 部分行情源的 get_instruments 只返回代码/名称/市场/类型, 没有
股本与交易规则字段。旧实现整表覆盖后 float_shares/total_shares 变 null,
enriched 的 turnover_rate 随之全空, 依赖换手率与流通市值的策略 (起爆/5连阳)
直接筛出 0 只 —— 同一个策略换数据源结果就不同。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.services import instrument_sync as isync


def _write_existing(data_dir: Path) -> None:
    out = data_dir / "instruments"
    out.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ"],
        "name": ["浦发银行", "平安银行"],
        "code": ["600000", "000001"],
        "exchange": ["SH", "SZ"],
        "region": ["CN", "CN"],
        "type": ["stock", "stock"],
        "listing_date": ["1999-11-10", "1991-04-03"],
        "total_shares": [2.9e10, 1.9e10],
        "float_shares": [2.9e10, 1.9e10],
        "tick_size": [0.01, 0.01],
        "limit_up": [11.0, 12.0],
        "limit_down": [9.0, 10.0],
    }).write_parquet(out / "instruments.parquet")


def _provider_rows(*symbols: str) -> list[dict]:
    """模拟只提供标识字段的行情源 (无股本/交易规则)。"""
    return [
        {
            "symbol": s, "name": s, "code": s.split(".")[0],
            "exchange": s.split(".")[1], "region": "CN", "type": "stock",
            "listing_date": None, "total_shares": None, "float_shares": None,
            "tick_size": None, "limit_up": None, "limit_down": None,
        }
        for s in symbols
    ]


def test_share_fields_restored_from_existing_table(tmp_path, monkeypatch) -> None:
    _write_existing(tmp_path)
    monkeypatch.setattr(
        isync, "_fetch_instruments_via_provider",
        lambda: _provider_rows("600000.SH", "000001.SZ"),
    )

    assert isync.sync_instruments(tmp_path) == 2

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    by_symbol = {r["symbol"]: r for r in df.to_dicts()}
    assert by_symbol["600000.SH"]["float_shares"] == pytest.approx(2.9e10)
    assert by_symbol["600000.SH"]["total_shares"] == pytest.approx(2.9e10)
    assert by_symbol["600000.SH"]["tick_size"] == pytest.approx(0.01)
    assert by_symbol["000001.SZ"]["float_shares"] == pytest.approx(1.9e10)


def test_new_symbol_keeps_null_shares_instead_of_faking(tmp_path, monkeypatch) -> None:
    """新上市标的没有历史值 → 保持 null, 不伪造股本。"""
    _write_existing(tmp_path)
    monkeypatch.setattr(
        isync, "_fetch_instruments_via_provider",
        lambda: _provider_rows("600000.SH", "301999.SZ"),
    )

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    new = df.filter(pl.col("symbol") == "301999.SZ")
    assert new.height == 1
    assert new["float_shares"][0] is None
    assert new["total_shares"][0] is None


def test_provider_fields_win_when_present(tmp_path, monkeypatch) -> None:
    """provider 自带股本时以 provider 为准 (不复用旧值)。"""
    _write_existing(tmp_path)
    rows = _provider_rows("600000.SH")
    rows[0]["float_shares"] = 1.0e9
    rows[0]["total_shares"] = 2.0e9
    monkeypatch.setattr(isync, "_fetch_instruments_via_provider", lambda: rows)

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["float_shares"][0] == pytest.approx(1.0e9)
    assert df["total_shares"][0] == pytest.approx(2.0e9)


def test_no_existing_table_is_tolerated(tmp_path, monkeypatch) -> None:
    """首次同步且无历史维表: 不报错, 字段保持 null。"""
    monkeypatch.setattr(
        isync, "_fetch_instruments_via_provider",
        lambda: _provider_rows("600000.SH"),
    )

    assert isync.sync_instruments(tmp_path) == 1

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["float_shares"][0] is None


def test_partial_provider_fields_filled_per_row(tmp_path, monkeypatch) -> None:
    """provider 只给部分标的股本时, 也要逐行补全 (不能整列跳过)。"""
    _write_existing(tmp_path)
    rows = _provider_rows("600000.SH", "000001.SZ")
    rows[0]["float_shares"] = 5.0e8  # 第一只有值, 第二只仍为空
    rows[0]["total_shares"] = 6.0e8
    monkeypatch.setattr(isync, "_fetch_instruments_via_provider", lambda: rows)

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    by_symbol = {r["symbol"]: r for r in df.to_dicts()}
    assert by_symbol["600000.SH"]["float_shares"] == pytest.approx(5.0e8)
    assert by_symbol["000001.SZ"]["float_shares"] == pytest.approx(1.9e10)


def test_daily_price_limits_are_not_inherited_from_old_metadata(tmp_path, monkeypatch):
    _write_existing(tmp_path)
    monkeypatch.setattr(isync, "_fetch_instruments_via_provider", lambda: _provider_rows("600000.SH"))

    isync.sync_instruments(tmp_path)
    row = pl.read_parquet(tmp_path / "instruments/instruments.parquet").row(0, named=True)
    assert row["float_shares"] == pytest.approx(2.9e10)
    assert row["limit_up"] is None
    assert row["limit_down"] is None


@pytest.mark.parametrize(("mode", "current_limits"), [("none", False), ("free", False), ("api_key", True)])
def test_only_paid_metadata_can_supply_todays_price_limits(tmp_path, monkeypatch, mode, current_limits):
    from types import SimpleNamespace

    from app.tickflow import client

    item = {"symbol": "002172.SZ", "name": "澳洋健康", "ext": {
        "limit_up": 6.50, "limit_down": 5.32, "float_shares": 765140730.0,
    }}
    monkeypatch.setattr(isync, "_fetch_instruments_via_provider", lambda: None)
    monkeypatch.setattr(isync, "get_client", lambda: SimpleNamespace(
        exchanges=SimpleNamespace(get_instruments=lambda *args, **kwargs: [item]),
    ))
    monkeypatch.setattr(client, "current_mode", lambda: mode)
    isync.sync_instruments(tmp_path)
    row = pl.read_parquet(tmp_path / "instruments/instruments.parquet").row(0, named=True)
    assert row["limit_up"] == (6.50 if current_limits else None)
    assert row["limit_down"] == (5.32 if current_limits else None)
    assert row["float_shares"] == 765140730.0


class _FakeProvider:
    """模拟带 get_financials 的行情源: shares=None 时抛异常(模拟 finance 不可用)。"""

    name = "fake"

    def __init__(self, shares: dict[str, tuple[float | None, float | None]] | None) -> None:
        self._shares = shares
        self.calls: list[tuple[str, list[str]]] = []

    def get_instruments(self, asset_type: str) -> list[dict]:
        return []

    def get_financials(self, table: str, symbols: list[str]):
        self.calls.append((table, symbols))
        if self._shares is None:
            raise RuntimeError("finance service down")
        rows = [
            {
                "symbol": s,
                "period_end": date(2026, 1, 1),
                "announce_date": date(2026, 1, 1),
                "total_shares": self._shares[s][0],
                "float_shares": self._shares[s][1],
            }
            for s in symbols if s in self._shares
        ]
        if not rows:
            return pl.DataFrame()
        return pl.DataFrame(rows)


def test_enrich_shares_fills_from_get_financials() -> None:
    """get_instruments 无股本, 但 get_financials 有 → 维表被权威补全。"""
    fake = _FakeProvider({"600000.SH": (2.9e10, 2.9e10), "000001.SZ": (1.9e10, 1.9e10)})
    rows = _provider_rows("600000.SH", "000001.SZ")
    out = isync._enrich_shares_via_provider(fake, rows)
    by = {r["symbol"]: r for r in out}
    assert by["600000.SH"]["total_shares"] == pytest.approx(2.9e10)
    assert by["600000.SH"]["float_shares"] == pytest.approx(2.9e10)
    assert by["000001.SZ"]["total_shares"] == pytest.approx(1.9e10)
    assert fake.calls and fake.calls[0][0] == "shares"


def test_enrich_shares_keeps_existing_value() -> None:
    """provider 已给股本时以 provider 为准, 不被 get_financials 覆盖。"""
    fake = _FakeProvider({"600000.SH": (2.9e10, 2.9e10)})
    rows = _provider_rows("600000.SH")
    rows[0]["total_shares"] = 1.0
    out = isync._enrich_shares_via_provider(fake, rows)
    assert out[0]["total_shares"] == pytest.approx(1.0)


def test_enrich_shares_skips_when_no_capability() -> None:
    """provider 没有 get_financials → 不改, 保持 null。"""

    class NoFin:
        name = "nofin"

    rows = _provider_rows("600000.SH")
    out = isync._enrich_shares_via_provider(NoFin(), rows)
    assert out[0]["total_shares"] is None


def test_enrich_shares_tolerates_finance_failure() -> None:
    """get_financials 抛异常 → 不伪造股本, 保持 null。"""
    fake = _FakeProvider(None)
    rows = _provider_rows("600000.SH")
    out = isync._enrich_shares_via_provider(fake, rows)
    assert out[0]["total_shares"] is None


def test_enrich_shares_falls_back_total_to_float() -> None:
    """total_shares 缺失时回退 float_shares(与 qatdx get_financials 行为一致)。"""
    fake = _FakeProvider({"600000.SH": (None, 2.9e10)})
    rows = _provider_rows("600000.SH")
    out = isync._enrich_shares_via_provider(fake, rows)
    assert out[0]["total_shares"] == pytest.approx(2.9e10)
    assert out[0]["float_shares"] == pytest.approx(2.9e10)
