from datetime import date, datetime
import polars as pl

from app.data_providers import custom as custom_sources
from app.plugins.akshare.provider import AkShareProvider, _normalize_raw_daily


def test_normalize_raw_daily_keeps_unadjusted_contract_columns():
    raw = pl.DataFrame({
        "日期": ["2026-10-15"],
        "开盘": [10.0], "最高": [11.0], "最低": [9.5], "收盘": [10.5],
        "成交量": [1000], "成交额": [10500],
    })
    out = _normalize_raw_daily(raw, "000001.SZ")
    assert out.select(["symbol", "date", "open", "close", "volume", "amount"]).to_dicts() == [{
        "symbol": "000001.SZ", "date": date(2026, 10, 15), "open": 10.0,
        "close": 10.5, "volume": 1000.0, "amount": 10500.0,
    }]


def test_get_daily_uses_empty_adjust_and_filters_window(monkeypatch):
    calls = []

    class FakeAk:
        def stock_zh_a_hist(self, **kwargs):
            calls.append(kwargs)
            return pl.DataFrame({
                "日期": ["2026-10-14", "2026-10-15", "2026-10-16"],
                "开盘": [9.0, 10.0, 11.0], "最高": [9.5, 10.5, 11.5],
                "最低": [8.5, 9.5, 10.5], "收盘": [9.2, 10.2, 11.2],
                "成交量": [1, 2, 3], "成交额": [9, 20, 33],
            })

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk())
    out = AkShareProvider().get_daily(
        ["000001.SZ"], date(2026, 10, 15), date(2026, 10, 16)
    )
    assert calls == [{
        "symbol": "000001", "period": "daily", "start_date": "20261015",
        "end_date": "20261016", "adjust": "",
    }]
    assert out["date"].to_list() == [date(2026, 10, 15), date(2026, 10, 16)]


def test_get_daily_empty_or_missing_columns_fails_closed(monkeypatch):
    class FakeAk:
        def stock_zh_a_hist(self, **kwargs):
            return pl.DataFrame({"日期": ["2026-10-15"], "收盘": [10.0]})

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk())
    assert AkShareProvider().get_daily(
        ["000001.SZ"], date(2026, 10, 15), date(2026, 10, 15)
    ).is_empty()


def test_plugin_manifest_declares_daily_and_minute_without_key():
    manifest = custom_sources.plugin_manifest("akshare")
    assert manifest["datasets"] == ["daily", "minute"]
    assert manifest.get("api_key_env", "") == ""


def test_provider_exposes_daily_and_minute_capability():
    provider = AkShareProvider()
    assert provider.capabilities.daily is True
    assert provider.capabilities.minute is True
    assert provider.config.datasets == {"daily": None, "minute": None}


def test_index_daily_uses_stable_endpoint_and_allows_missing_amount(monkeypatch):
    calls = []

    class FakeAk:
        def stock_zh_index_daily(self, **kwargs):
            calls.append(kwargs)
            return pl.DataFrame({
                "date": ["2026-10-14", "2026-10-15", "2026-10-16"],
                "open": [3700.0, 3710.0, 3720.0], "high": [3750.0, 3760.0, 3770.0],
                "low": [3680.0, 3690.0, 3700.0], "close": [3710.0, 3720.0, 3730.0],
                "volume": [100, 200, 300],
            })

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk())
    out = AkShareProvider().get_daily(
        ["000001.SH"], date(2026, 10, 15), date(2026, 10, 16), asset_type="index"
    )
    assert calls == [{"symbol": "sh000001"}]
    assert out["date"].to_list() == [date(2026, 10, 15), date(2026, 10, 16)]
    assert out["amount"].null_count() == 2


def test_normalize_raw_minute_converts_columns_filters_window_and_uses_wallclock():
    from datetime import datetime, timezone

    from app.plugins.akshare.provider import _normalize_raw_minute

    raw = pl.DataFrame({
        "时间": [
            "2026-10-16 09:29:00",
            "2026-10-16 09:30:00",
            "2026-10-16 09:31:00",
            "2026-10-16 15:01:00",
        ],
        "开盘": [10.0, 10.1, 10.2, 10.3],
        "最高": [10.1, 10.2, 10.3, 10.4],
        "最低": [9.9, 10.0, 10.1, 10.2],
        "收盘": [10.0, 10.15, 10.25, 10.35],
        "成交量": [1.0, 2.0, 3.0, 4.0],
        "成交额": [10.0, 20.0, 30.0, 40.0],
    })
    out = _normalize_raw_minute(
        raw,
        "000001.SZ",
        datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc),
    )

    assert out["symbol"].to_list() == ["000001.SZ", "000001.SZ"]
    assert out["datetime"].to_list() == [
        datetime(2026, 10, 16, 9, 30),
        datetime(2026, 10, 16, 9, 31),
    ]
    assert out.columns == ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]


def test_get_minute_uses_single_stock_unadjusted_endpoint(monkeypatch):
    calls = []

    class FakeAk:
        def stock_zh_a_hist_min_em(self, **kwargs):
            calls.append(kwargs)
            return pl.DataFrame({
                "时间": ["2026-10-16 09:30:00", "2026-10-16 09:31:00"],
                "开盘": [10.0, 10.1], "最高": [10.2, 10.2],
                "最低": [9.9, 10.0], "收盘": [10.1, 10.15],
                "成交量": [100, 200], "成交额": [1000, 2030],
            })

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk())
    out = AkShareProvider().get_minute(
        ["000001.SZ"], datetime(2026, 10, 16, 9, 25), datetime(2026, 10, 16, 15, 5)
    )

    assert calls == [{
        "symbol": "000001", "period": "1",
        "start_date": "2026-10-16 09:25:00",
        "end_date": "2026-10-16 15:05:00", "adjust": "",
    }]
    assert out.height == 2
    assert out["datetime"].to_list()[0] == datetime(2026, 10, 16, 9, 30)


def test_get_minute_falls_back_to_legacy_endpoint(monkeypatch):
    calls = []

    class FakeAk:
        def stock_zh_a_hist_min_em(self, **kwargs):
            calls.append(("primary", kwargs))
            raise ConnectionError("primary unavailable")

        def stock_zh_a_minute(self, **kwargs):
            calls.append(("legacy", kwargs))
            return pl.DataFrame({
                "day": ["2026-10-16 09:30"],
                "open": [10.0], "high": [10.2], "low": [9.9],
                "close": [10.1], "volume": [100.0], "amount": [1000.0],
            })

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk())
    out = AkShareProvider().get_minute(
        ["000001.SZ"], datetime(2026, 10, 16, 9, 25), datetime(2026, 10, 16, 15, 5)
    )

    assert [name for name, _ in calls] == ["primary", "legacy"]
    assert calls[1][1] == {"symbol": "sz000001", "period": "1", "adjust": ""}
    assert out.height == 1
    assert out["datetime"].to_list() == [datetime(2026, 10, 16, 9, 30)]


def test_get_minute_is_narrow_single_stock_one_minute_only(monkeypatch):
    class FakeAk:
        def stock_zh_a_hist_min_em(self, **kwargs):
            raise AssertionError("unsupported request must not call AkShare")

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk())
    provider = AkShareProvider()
    assert provider.get_minute(["000001.SZ", "600000.SH"], None, None).is_empty()
    assert provider.get_minute(["000001.SZ"], None, None, asset_type="index").is_empty()
    assert provider.get_minute(["000001.SZ"], None, None, freq="5m").is_empty()


def test_plugin_manifest_declares_daily_and_minute_without_full_minute():
    manifest = custom_sources.plugin_manifest("akshare")
    assert manifest["datasets"] == ["daily", "minute"]
    assert "full_minute" not in manifest["datasets"]


def test_provider_exposes_daily_and_minute_but_not_full_minute():
    provider = AkShareProvider()
    assert provider.capabilities.daily is True
    assert provider.capabilities.minute is True
    assert provider.config.datasets == {"daily": None, "minute": None}
