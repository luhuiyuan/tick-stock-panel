from __future__ import annotations

from datetime import datetime

import httpx
import polars as pl

from app.data_providers.free_public_provider import FreePublicProvider
from app.data_providers.registry import get_provider


def _kline_transport(request: httpx.Request) -> httpx.Response:
    if "stock/kline/get" in str(request.url):
        return httpx.Response(200, json={"data": {"klines": [
            "2026-10-16,10.0,10.2,10.3,9.9,1000,10200,2.0",
        ]}})
    return httpx.Response(200, json={"data": {"total": 0, "diff": []}})


def test_free_public_is_registered_without_global_backend():
    provider = get_provider("free_public")
    assert provider.name == "free_public"
    assert provider.capabilities.daily
    assert provider.capabilities.minute
    assert provider.capabilities.realtime
    assert provider.capabilities.depth5
    assert not provider.capabilities.adj_factor
    assert not provider.capabilities.financial


def test_data_sources_list_includes_free_public(monkeypatch):
    from app.api import settings as settings_api
    from app.data_providers import custom as custom_sources

    monkeypatch.setattr(custom_sources, "list_plugins", lambda: [])
    monkeypatch.setattr(custom_sources, "list_sources", lambda: [])
    monkeypatch.setattr(custom_sources, "errors", lambda: [])
    monkeypatch.setattr(custom_sources, "data_sources_dir", lambda: "/tmp/data-sources")

    result = settings_api.list_data_sources()
    free_public = next(item for item in result["builtin"] if item["name"] == "free_public")
    assert free_public == {
        "name": "free_public",
        "display_name": "免费公开源（新浪/东方财富/腾讯）",
        "datasets": ["daily", "minute", "realtime", "depth5", "instruments"],
    }


def test_daily_uses_normalized_raw_contract(monkeypatch):
    provider = FreePublicProvider()
    provider._client._http = httpx.Client(transport=httpx.MockTransport(_kline_transport))
    out = provider.get_daily(
        ["600000.SH"], datetime(2026, 10, 1), datetime(2026, 10, 16), "stock",
    )
    assert out.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"]
    assert out["close"].to_list() == [10.2]
    assert out["amount"].to_list() == [10200.0]


def test_adj_factor_is_explicitly_unsupported():
    assert FreePublicProvider().get_adj_factors([], None, None).is_empty()


def test_empty_minute_is_safe():
    provider = FreePublicProvider()
    provider._client._http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": {"klines": []}}),
    ))
    assert provider.get_minute(["600000.SH"], None, None).is_empty()
