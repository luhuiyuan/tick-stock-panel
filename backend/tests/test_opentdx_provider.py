from __future__ import annotations

from datetime import datetime

import polars as pl

from app.data_providers.opentdx_provider import OpenTDXProvider
from app.data_providers.registry import get_provider


class _FakeClient:
    def q_client(self):
        return type("Q", (), {"connected": True})()

    def stock_list(self, market, count=0):
        return [{"code": "000001", "name": "平安银行"}] if getattr(market, "name", "") == "SZ" else []

    def stock_kline(self, market, code, period, count=0, adjust=None):
        return [{
            "datetime": datetime(2026, 10, 16, 15, 0), "open": 10.0,
            "high": 10.3, "low": 9.9, "close": 10.2,
            "vol": 1000, "amount": 10200,
        }]

    def stock_quotes(self, pairs):
        return [{"market": pairs[0][0].value, "code": pairs[0][1], "open": 10.0,
                 "high": 10.3, "low": 9.9, "close": 10.2, "pre_close": 10.0,
                 "vol": 1000, "amount": 10200}]

    def stock_quotes_detail(self, pairs):
        return [{"market": pairs[0][0].value, "code": pairs[0][1],
                 "handicap": {"bid": [{"price": 10.1, "vol": 200}],
                              "ask": [{"price": 10.2, "vol": 100}]}}]


def test_opentdx_is_registered_without_changing_default():
    provider = get_provider("opentdx")
    assert provider.name == "opentdx"
    assert provider.capabilities.daily
    assert provider.capabilities.minute
    assert provider.capabilities.realtime
    assert provider.capabilities.depth5
    assert not provider.capabilities.adj_factor
    assert not provider.capabilities.financial


def test_opentdx_normalizes_daily_minute_realtime_and_depth():
    provider = OpenTDXProvider(_FakeClient())
    daily = provider.get_daily(["000001.SZ"], datetime(2026, 10, 1), datetime(2026, 10, 16))
    assert daily.select("close").to_series().to_list() == [10.2]
    minute = provider.get_minute(["000001.SZ"], None, None, freq="1m")
    assert minute.schema["datetime"] == pl.Datetime("us")
    realtime = provider.get_realtime(symbols=["000001.SZ"])
    assert realtime["symbol"].to_list() == ["000001.SZ"]
    depth = provider.get_depth_batch(["000001.SZ"])
    assert depth["000001.SZ"]["bid_volumes"] == [200]
    assert depth["000001.SZ"]["ask_volumes"] == [100]


def test_opentdx_sources_are_listed_without_global_route_switch(monkeypatch):
    from app.api import settings as settings_api
    from app.data_providers import custom as custom_sources

    monkeypatch.setattr(custom_sources, "list_plugins", lambda: [])
    monkeypatch.setattr(custom_sources, "list_sources", lambda: [])
    monkeypatch.setattr(custom_sources, "errors", lambda: [])
    result = settings_api.list_data_sources()
    source = next(item for item in result["builtin"] if item["name"] == "opentdx")
    assert source["datasets"] == ["daily", "minute", "realtime", "depth5", "instruments"]
