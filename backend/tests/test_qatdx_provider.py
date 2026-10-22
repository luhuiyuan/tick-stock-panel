"""QATdx provider 契约测试。全部打桩 client，不连行情主站。"""
from __future__ import annotations

from datetime import date, datetime

from app.plugins.qatdx import client
from app.plugins.qatdx.provider import QatdxProvider, _ref_price, availability


def test_availability_is_a_soft_check():
    ok, reason = availability()
    assert isinstance(ok, bool)
    assert isinstance(reason, str)
    if not ok:
        assert "pytdx" in reason


def test_daily_keeps_raw_close_and_hand_volume(monkeypatch):
    seen = []

    def stock_bars(market, code, category, count):
        seen.append((market, code, category, count))
        return [{
            "year": 2024, "month": 6, "day": 3,
            "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
            "vol": 436231, "amount": 4_000_000_000.0,
        }]

    monkeypatch.setattr(client, "stock_bars", stock_bars)
    frame = QatdxProvider().get_daily(
        ["600519.SH", "830799.BJ"],
        datetime(2024, 6, 1),
        datetime(2024, 6, 30),
    )
    assert seen[0][:3] == (1, "600519", 9)
    assert seen[1][:3] == (2, "830799", 9)
    assert frame["symbol"].to_list() == ["600519.SH", "830799.BJ"]
    assert frame["close"].to_list() == [10.5, 10.5]
    assert frame["volume"].to_list() == [436231.0, 436231.0]


def test_empty_daily_and_realtime_failure_are_empty(monkeypatch):
    monkeypatch.setattr(client, "stock_bars", lambda *args: [])
    assert QatdxProvider().get_daily(
        ["000001.SZ"], datetime(2024, 6, 1), datetime(2024, 6, 30),
    ).is_empty()

    def boom(*args, **kwargs):
        raise RuntimeError("主站断开")

    monkeypatch.setattr(client, "quotes", boom)
    assert QatdxProvider().get_realtime(symbols=["000001.SZ"]) == []


def test_minute_uses_beijing_wall_clock_and_keeps_hands(monkeypatch):
    seen = []

    def stock_bars(market, code, category, count):
        seen.append(category)
        return [{
            "datetime": "2024-06-03 09:31:00",
            "open": 10, "high": 10.2, "low": 9.9, "close": 10.1,
            "vol": 88, "amount": 1000,
        }]

    monkeypatch.setattr(client, "stock_bars", stock_bars)
    frame = QatdxProvider().get_minute(
        ["000001.SZ"],
        datetime(2024, 6, 3, 9, 30),
        datetime(2024, 6, 3, 15, 0),
        freq="1m",
    )
    assert seen == [8]
    assert frame["datetime"].to_list() == [datetime(2024, 6, 3, 9, 31)]
    assert frame["datetime"].dtype.time_zone is None
    assert frame["volume"].to_list() == [88.0]


def test_depth_volumes_stay_in_hands(monkeypatch):
    monkeypatch.setattr(client, "quotes", lambda pairs: [{
        "code": "000001",
        "bid1": 10.0, "bid2": 9.9, "bid3": 9.8, "bid4": 9.7, "bid5": 9.6,
        "bid_vol1": 10, "bid_vol2": 20, "bid_vol3": 30, "bid_vol4": 40, "bid_vol5": 50,
        "ask1": 10.1, "ask2": 10.2, "ask3": 10.3, "ask4": 10.4, "ask5": 10.5,
        "ask_vol1": 11, "ask_vol2": 21, "ask_vol3": 31, "ask_vol4": 41, "ask_vol5": 51,
    }])
    book = QatdxProvider().get_depth_batch(["000001.SZ"])["000001.SZ"]
    assert book["bid_volumes"] == [10, 20, 30, 40, 50]
    assert book["ask_prices"][0] == 10.1


def test_adj_factor_uses_category_one_and_exchange_reference_price(monkeypatch):
    def xdxr(market, code):
        return [
            {
                "category": 2, "year": 2024, "month": 1, "day": 2,
                "fenhong": 100, "songzhuangu": 0, "peigu": 0, "peigujia": 0,
            },
            {
                "category": 1, "year": 2024, "month": 6, "day": 3,
                "fenhong": 1.0, "songzhuangu": 2.0, "peigu": 1.0, "peigujia": 8.0,
            },
        ]

    def stock_bars(market, code, category, count):
        return [
            {"year": 2024, "month": 5, "day": 31, "close": 10.55, "open": 10.55, "high": 10.6, "low": 10.4, "vol": 1, "amount": 1},
            {"year": 2024, "month": 6, "day": 3, "close": 8.65, "open": 8.65, "high": 8.7, "low": 8.6, "vol": 1, "amount": 1},
        ]

    monkeypatch.setattr(client, "xdxr", xdxr)
    monkeypatch.setattr(client, "stock_bars", stock_bars)
    ref = _ref_price(10.55, 0.1, 0.2, 0.1, 8.0)
    assert ref == 8.65
    frame = QatdxProvider().get_adj_factors(
        ["600519.SH"], datetime(2024, 1, 1), datetime(2024, 12, 31), "stock",
    )
    assert frame.height == 1
    row = frame.to_dicts()[0]
    assert row["trade_date"] == date(2024, 6, 3)
    assert row["dividend"] == 0.1
    assert row["bonus"] == 0.2
    assert row["allot"] == 0.1
    assert row["allot_price"] == 8.0
    assert row["prev_close"] == 10.55
    assert row["ex_factor"] == 10.55 / 8.65
    empty = QatdxProvider().get_adj_factors(
        ["600519.SH"], datetime(2024, 1, 1), datetime(2024, 12, 31), "index",
    )
    assert empty.is_empty()


def test_shares_accept_wan_and_already_converted_shares(monkeypatch):
    def finance(market, code):
        if code == "000001":
            return {
                "liutongguben": 19_405_685_000,
                "zongguben": 19_405_918_750,
                "updated_date": "20260815",
            }
        return {
            "liutongguben": 10000,
            "zongguben": 20000,
            "updated_date": "20240630",
        }

    monkeypatch.setattr(client, "finance", finance)
    converted = QatdxProvider().get_financials("shares", ["000002.SZ"]).to_dicts()[0]
    assert converted["float_shares"] == 100_000_000.0
    assert converted["total_shares"] == 200_000_000.0
    assert converted["period_end"] == date(2024, 6, 30)
    raw_shares = QatdxProvider().get_financials("shares", ["000001.SZ"]).to_dicts()[0]
    assert raw_shares["float_shares"] == 19_405_685_000
    assert raw_shares["total_shares"] == 19_405_918_750
    mixed = {
        "liutongguben": 80_000_000,
        "zongguben": 19_405_918_750,
        "updated_date": "20240630",
    }
    monkeypatch.setattr(client, "finance", lambda market, code: mixed)
    row = QatdxProvider().get_financials("shares", ["000001.SZ"]).to_dicts()[0]
    assert row["float_shares"] == 80_000_000
    assert row["total_shares"] == 19_405_918_750
    assert QatdxProvider().get_financials("income", ["000001.SZ"]).is_empty()
    assert QatdxProvider().get_financials("metrics", ["000001.SZ"]).is_empty()


def test_history_transaction_uses_vendor_argument_order(monkeypatch):
    seen = []

    class _Api:
        def get_history_transaction_data(self, market, code, start, count, trade_day):
            seen.append((market, code, start, count, trade_day))
            if start:
                return []
            return [{"time": "09:31", "price": 11.3, "vol": 1, "buyorsell": 0}]

    monkeypatch.setattr(client, "_call", lambda fn: fn(_Api()))
    rows = client.transactions(0, "000001", date(2024, 6, 3))
    assert seen[0] == (0, "000001", 0, 2000, 20240603)
    assert rows and rows[0]["price"] == 11.3


def test_transactions_use_stock_market_and_keep_hand_volume(monkeypatch):
    seen = []

    def transactions(market, code, trade_day):
        seen.append((market, code, trade_day))
        return [{"time": "09:31:00", "price": 10.5, "vol": 12, "buyorsell": 0}]

    monkeypatch.setattr(client, "transactions", transactions)
    day = date(2024, 6, 3)
    frame = QatdxProvider().get_transactions(["000001.SZ"], day)
    assert seen == [(0, "000001", day)]
    row = frame.to_dicts()[0]
    assert row["datetime"] == datetime(2024, 6, 3, 9, 31)
    assert row["volume"] == 12.0
    assert row["direction"] == "buy"


def test_blocks_industries_and_margin_shapes(monkeypatch):
    monkeypatch.setattr(client, "blocks", lambda: [
        {"code": "600519", "blockname": "白酒", "block_type": "concept"},
    ])
    blocks = QatdxProvider().get_blocks()
    assert blocks.to_dicts() == [{
        "symbol": "600519.SH", "block_name": "白酒", "block_type": "concept",
    }]

    monkeypatch.setattr(client, "industries", lambda: [
        {"line": "0|000001|T1001"},
        {"line": "1|600519|白酒"},
    ])
    industries = QatdxProvider().get_industries()
    assert industries.to_dicts() == [{"symbol": "600519.SH", "industry_name": "白酒"}]

    monkeypatch.setattr(client, "industries", lambda: [{"line": "0|000001|T1001"}])
    assert QatdxProvider().get_industries().is_empty()

    monkeypatch.setattr(client, "margin", lambda day: [{
        "symbol": "600519.SH",
        "trade_date": day.isoformat(),
        "leveraged_balance": 1.0,
        "leveraged_buyout": 2.0,
        "margin_sell": 3.0,
        "margin_left": 4.0,
        "exchange": "SH",
    }])
    margin = QatdxProvider().get_margin(date(2024, 6, 3))
    assert margin["trade_date"].to_list() == [date(2024, 6, 3)]
    assert margin["leveraged_balance"].to_list() == [1.0]


def test_reference_sync_skips_when_plugin_is_missing(monkeypatch, tmp_path):
    from app.services import qatdx_sync

    monkeypatch.setattr(qatdx_sync, "_provider", lambda: None)
    qatdx_sync.sync_reference_if_selected(tmp_path, date(2024, 6, 3))
    assert not (tmp_path / "qatdx").exists()

def test_server_pool_includes_vendored_hq_hosts():
    pool = client._server_pool()
    client._ensure_vendor_path()
    from pytdx.config.hosts import hq_hosts

    wanted = {(str(item[1]), int(item[2])) for item in hq_hosts}
    assert len(wanted) >= 100
    assert wanted <= set(pool)
    assert ("124.71.187.122", 7709) in pool
    assert len(pool) == len(set(pool))


def test_select_server_handshakes_fastest_tcp_first(monkeypatch):
    monkeypatch.setattr(client, "_server_pool", lambda: [("1.1.1.1", 7709), ("2.2.2.2", 7709)])
    monkeypatch.setattr(
        client, "_tcp_latency_ms",
        lambda ip, port, timeout: 5.0 if ip == "2.2.2.2" else 40.0,
    )

    class _Api:
        def __init__(self, raise_exception=True):
            self.ip = None

        def connect(self, ip, port, time_out=2.0):
            self.ip = ip
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_security_count(self, market):
            return 24462 if self.ip == "2.2.2.2" else 0

    monkeypatch.setattr(client, "_import_api", lambda: _Api)
    client.reset_server_cache()
    assert client._select_server() == ("2.2.2.2", 7709)
    client.reset_server_cache()
