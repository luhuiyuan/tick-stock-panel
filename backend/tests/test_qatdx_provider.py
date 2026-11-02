"""QATdx provider 契约测试。全部打桩 client，不连行情主站。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import polars as pl
import pytest

from app.plugins.qatdx import client
from app.plugins.qatdx.provider import (
    QatdxProvider,
    _ref_price,
    _resample_bars,
    _session_minutes,
    _ticks_frame_to_bars,
    _ticks_to_bars,
    availability,
)

_CN_TZ = timezone(timedelta(hours=8))


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


def _recent_weekday(days_back: int = 3) -> date:
    """最近的工作日。必须落在 K 线接口的 20800 根窗口内, 否则会切到历史逐笔通道。"""
    cursor = date.today() - timedelta(days=days_back)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def test_minute_takes_aware_bounds_and_converts_shares_to_hands(monkeypatch):
    """端点带 CN_TZ 而回包是 naive 墙钟: 必须同口径比较, 成交量必须由股换手。

    两个都是「拉不到分钟数据」的成因:
    - naive/aware 直接比较抛 TypeError, 被 _try_custom_minute 记成源故障后
      fail-closed, 页面只剩「该日暂无分钟数据（数据源未提供）」;
    - 分钟内盘给的是股, 面板契约是手, 不换与日K 差 100 倍。
    """
    day = _recent_weekday()
    seen = []

    def stock_bars(market, code, category, count):
        seen.append((category, count))
        return [{
            "datetime": f"{day.isoformat()} 09:31:00",
            "open": 10, "high": 10.2, "low": 9.9, "close": 10.1,
            "vol": 8800, "amount": 1000,
        }]

    monkeypatch.setattr(client, "stock_bars", stock_bars)
    frame = QatdxProvider().get_minute(
        ["000001.SZ"],
        datetime.combine(day, time(9, 30), tzinfo=_CN_TZ),
        datetime.combine(day, time(15, 0), tzinfo=_CN_TZ),
        freq="1m",
    )
    assert seen[0][0] == 8
    assert frame["datetime"].to_list() == [datetime.combine(day, time(9, 31))]
    assert frame["datetime"].dtype.time_zone is None
    # 8800 股 = 88 手
    assert frame["volume"].to_list() == [88.0]


def test_minute_count_counts_back_from_today_for_historical_dates(monkeypatch):
    """count 是「从最新一根往回数」的根数, 不是查询区间的宽度。

    按区间宽度算(一天 → 240 根)时, 请求历史日期取回的永远是最近这一天, 过滤后
    恒为空; 必须从今天倒推到区间起点。
    """
    seen = []

    def stock_bars(market, code, category, count):
        seen.append(count)
        return []

    monkeypatch.setattr(client, "stock_bars", stock_bars)
    past = date.today() - timedelta(days=60)
    QatdxProvider().get_minute(
        ["000001.SZ"],
        datetime.combine(past, time(9, 25)),
        datetime.combine(past, time(15, 5)),
        freq="1m",
    )
    # 61 个自然日 ≈ 50 个交易日 × 240 根; 只取一天的量会得到 240 而拉不到历史
    assert seen[0] >= 40 * 240


def test_stock_bars_zeroes_pytdx_decompression_residue(monkeypatch):
    """pytdx 的 get_volume 对高位字节为 0 的压缩值返回 2^-127(≈5.877e-39), 不是 0。

    成交量为 0 的那一分钟会落成一根「0 < vol < 1」的假值, 直接进分时量柱和均价;
    最小成交单位是 1 手, 小于 1 的正数一律归 0。
    """
    rows = [{
        "year": 2024, "month": 6, "day": 3, "hour": 14, "minute": 59,
        "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
        "vol": 5.877471754111438e-39, "amount": 5.877471754111438e-39,
    }]
    monkeypatch.setattr(client, "_call", lambda fn: rows)
    out = client.stock_bars(0, "000001", 8, 1)
    assert out[0]["vol"] == 0.0
    assert out[0]["amount"] == 0.0


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

    monkeypatch.setattr(client, "_call", lambda fn, **kwargs: fn(_Api()))
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
        {"market": "0", "code": "000001", "industry_code": "T1001", "sw_code": "X500102"},
        {"market": "1", "code": "600519", "industry_code": "T100101", "sw_code": "X100101"},
    ])
    industries = QatdxProvider().get_industries()
    # 主站只给行业代码, 不给中文名, 所以 industry_name 落的就是代码。
    assert {(row["symbol"], row["industry_name"]) for row in industries.to_dicts()} == {
        ("000001.SZ", "T1001"), ("600519.SH", "T100101"),
    }

    monkeypatch.setattr(client, "industries", lambda: [
        {"market": "0", "code": "000001", "industry_code": "", "sw_code": ""},
    ])
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


def test_ticks_to_bars_folds_auction_and_drops_after_hours():
    """逐笔聚合的边界规则, 全部照官方分钟 K 的口径。

    002172.SZ 2026-10-30 实测: 逐笔 09:25 只有 1 笔(集合竞价 105,603 手, 价 5.61),
    官方首根 09:31 的 open 正是 5.61、vol 412,597 手 = 09:25 + 09:30 的 20 笔。
    09:15~09:24 是集合竞价过程的虚拟撮合(价格不代表开盘价), 15:00 之后的
    15:05~15:24 是盘后定价交易, 两者都不属于任何分钟 K。
    """
    day = date(2026, 10, 30)
    rows = [
        {"time": "09:15", "price": 5.90, "vol": 100.0},     # 虚拟撮合, 丢弃
        {"time": "09:25", "price": 5.61, "vol": 105603.0},  # 集合竞价 -> 09:31
        {"time": "09:30", "price": 5.62, "vol": 200.0},     # -> 09:31
        {"time": "09:31", "price": 5.59, "vol": 300.0},     # -> 09:32
        {"time": "14:59", "price": 5.91, "vol": 400.0},     # -> 15:00
        {"time": "15:00", "price": 5.91, "vol": 2245.0},    # 收盘竞价 -> 15:00
        {"time": "15:24", "price": 5.91, "vol": 10.0},      # 盘后定价, 丢弃
    ]
    bars = _ticks_to_bars(rows, "002172.SZ", day)

    assert bars["dt"].to_list() == [
        datetime(2026, 10, 30, 9, 31),
        datetime(2026, 10, 30, 9, 32),
        datetime(2026, 10, 30, 15, 0),
    ]
    head = bars.row(0, named=True)
    assert head["open"] == 5.61      # 集合竞价价就是首根 open
    assert head["volume"] == 105803.0
    tail = bars.row(2, named=True)
    assert tail["volume"] == 2645.0
    assert tail["amount"] == pytest.approx((400 + 2245) * 5.91 * 100)


def test_ticks_to_bars_folds_lunch_close_into_1130():
    """11:30 的成交必须并进 11:30, 不能顶出一根不存在的 11:31。

    逐笔是成交时刻, 官方分钟 K 标区间结束, 所以整体 +1 分钟; 但 11:30 那笔 +1 后
    是 11:31 —— 官方没有这根, 上午收盘的成交算在 11:30 里。实测 2025 年 30 只样本
    里 43% 的股票日会因为不夹午休而多出这根幽灵 bar。
    """
    day = date(2025, 1, 2)
    rows = [
        {"time": "11:29", "price": 10.0, "vol": 100.0},   # -> 11:30
        {"time": "11:30", "price": 10.1, "vol": 200.0},   # -> 11:30 (不是 11:31)
        {"time": "13:00", "price": 10.2, "vol": 300.0},   # -> 13:01
        {"time": "13:01", "price": 10.3, "vol": 400.0},   # -> 13:02
    ]
    bars = _ticks_to_bars(rows, "000001.SZ", day)

    assert bars["dt"].to_list() == [
        datetime(2025, 1, 2, 11, 30),
        datetime(2025, 1, 2, 13, 1),
        datetime(2025, 1, 2, 13, 2),
    ]
    lunch = bars.row(0, named=True)
    assert lunch["volume"] == 300.0
    assert lunch["close"] == 10.1


def test_ticks_to_bars_drops_closing_auction_virtual_match():
    """14:57~14:59 的集合竞价虚拟撮合必须丢掉。

    这几分钟的 ``buyorsell=8`` 记录成交量恒为 0、price 是收盘集合竞价过程的试探价。
    它们的 ``minute_of_day`` 是 897~899, 落在合法区间内; 14:59 那笔 +1 分钟后正好是
    15:00, 会被 ``min(stamp, last_bar)`` 夹进收盘那根, 把真实竞价的 open 和 low 拽到
    试探价上。实测 2025 年 30 只样本里 19.7% 的股票日中招
    (000001.SZ 2025-07-22: 未过滤时 open/low=12.48, 官方为 12.49)。
    """
    day = date(2025, 7, 22)
    rows = [
        {"time": "14:56", "price": 12.50, "vol": 100.0, "buyorsell": 0},
        {"time": "14:57", "price": 12.48, "vol": 0.0, "buyorsell": 8},    # 虚拟撮合, 丢弃
        {"time": "14:59", "price": 12.48, "vol": 0.0, "buyorsell": 8},    # 虚拟撮合, 丢弃
        {"time": "15:00", "price": 12.49, "vol": 10959.0, "buyorsell": 2},  # 收盘竞价
    ]
    bars = _ticks_to_bars(rows, "000001.SZ", day)

    # 虚拟撮合不产生 bar: 只剩 14:57(来自 14:56) 与 15:00 两根。
    assert bars["dt"].to_list() == [
        datetime(2025, 7, 22, 14, 57),
        datetime(2025, 7, 22, 15, 0),
    ]
    tail = bars.row(1, named=True)
    assert (tail["open"], tail["high"], tail["low"], tail["close"]) == (12.49, 12.49, 12.49, 12.49)
    assert tail["volume"] == 10959.0


def test_ticks_to_bars_drops_zero_volume_snapshot():
    """成交量为 0 的行情快照不能参与聚合, 哪怕它的 buyorsell 不是 8。

    15:00 那批快照的 ``buyorsell`` 是 2(不是 8), 成交量 0, price 是集合竞价的参考价,
    与真实成交价偶尔差 1 分钱。混进去会把收盘那根的 high/low 拽偏 —— 实测 2025 年
    有 8 个股票日中招(600388.SH 2025-03-06 收盘 12.78, 快照带 12.77)。
    """
    day = date(2025, 3, 6)
    rows = [
        {"time": "14:56", "price": 12.77, "vol": 50.0, "buyorsell": 0},
        {"time": "15:00", "price": 12.78, "vol": 562.0, "buyorsell": 2},  # 收盘竞价成交
        {"time": "15:00", "price": 12.77, "vol": 0.0, "buyorsell": 2},    # 快照, 丢弃
        {"time": "15:00", "price": 12.78, "vol": 0.0, "buyorsell": 2},    # 快照, 丢弃
    ]
    bars = _ticks_to_bars(rows, "600388.SH", day)

    tail = bars.filter(pl.col("dt") == datetime(2025, 3, 6, 15, 0)).row(0, named=True)
    assert (tail["open"], tail["high"], tail["low"], tail["close"]) == (12.78, 12.78, 12.78, 12.78)
    assert tail["volume"] == 562.0


def test_ticks_frame_to_bars_matches_row_version():
    """向量化版本与逐行版本必须逐根一致, 包括新加的零成交量过滤。"""
    day = date(2025, 3, 6)
    rows = [
        {"time": "09:25", "price": 12.60, "vol": 0.0, "buyorsell": 2},
        {"time": "09:25", "price": 12.61, "vol": 800.0, "buyorsell": 2},
        {"time": "09:31", "price": 12.70, "vol": 120.0, "buyorsell": 0},
        {"time": "09:31", "price": 12.68, "vol": 90.0, "buyorsell": 1},
        {"time": "11:30", "price": 12.80, "vol": 300.0, "buyorsell": 0},
        {"time": "13:01", "price": 12.75, "vol": 210.0, "buyorsell": 1},
        {"time": "14:57", "price": 12.90, "vol": 0.0, "buyorsell": 8},
        {"time": "15:00", "price": 12.78, "vol": 562.0, "buyorsell": 2},
        {"time": "15:00", "price": 12.77, "vol": 0.0, "buyorsell": 2},
    ]
    expected = _ticks_to_bars(rows, "600388.SH", day)
    frame = pl.DataFrame({
        "symbol": ["600388.SH"] * len(rows),
        "date": [day] * len(rows),
        "time": [r["time"] for r in rows],
        "price": [r["price"] for r in rows],
        "volume": [r["vol"] for r in rows],
        "direction": [r["buyorsell"] for r in rows],
    })
    got = _ticks_frame_to_bars(frame)

    assert got["dt"].to_list() == expected["dt"].to_list()
    for col in ("open", "high", "low", "close", "volume", "amount"):
        assert got[col].to_list() == expected[col].to_list(), col


def test_resample_5m_stops_at_lunch_break():
    """5 分钟桶在午休两侧独立: 上午 09:35..11:30, 下午 13:05..15:00。

    按自然时间连续切会让下午 13:01 落进 11:35 之后的空桶, 5 分钟只能得到 24 根
    而不是 48 根。判定依据必须是一个交易日内的真实交易分钟数。
    """
    day = date(2026, 10, 30)
    stamps = _session_minutes(day)
    assert len(stamps) == 240
    frame = pl.DataFrame({
        "symbol": ["000001.SZ"] * 240,
        "dt": stamps,
        "open": [1.0] * 240,
        "high": [2.0] * 240,
        "low": [0.5] * 240,
        "close": [1.5] * 240,
        "volume": [10.0] * 240,
        "amount": [1500.0] * 240,
    })

    out = _resample_bars(frame, 5)

    labels = [stamp.strftime("%H:%M") for stamp in out["dt"].to_list()]
    assert out.height == 48
    assert labels[:2] == ["09:35", "09:40"]
    assert "11:30" in labels and "13:05" in labels
    assert "11:35" not in labels and "12:00" not in labels
    assert labels[-1] == "15:00"
    # 一根 5 分钟 = 5 个 1 分钟
    assert out["volume"].to_list() == [50.0] * 48
    assert out["high"].to_list() == [2.0] * 48


def test_minute_beyond_kline_window_reads_history_ticks(monkeypatch):
    """1 分钟 K 只有 20800 根(约 87 个交易日), 更早的日期必须走按日期取数的逐笔。

    K 线接口是「从最新一根往回数」, 请求 2020 年的日期它永远返回空, 调多大 count
    都没用; 逐笔按日期取, 实测能回溯到 2008-01-02。
    """
    calls: list = []

    def stock_bars(*args, **kwargs):
        calls.append("bars")
        return []

    def history_ticks(market, code, dates):
        calls.append(("ticks", list(dates)))
        return {
            day: [
                {"time": "09:31", "price": 10.0, "vol": 100.0},
                {"time": "15:00", "price": 10.5, "vol": 200.0},
            ]
            for day in dates
        }

    monkeypatch.setattr(client, "stock_bars", stock_bars)
    monkeypatch.setattr(client, "history_ticks", history_ticks)

    frame = QatdxProvider().get_minute(
        ["000001.SZ"],
        datetime(2020, 6, 2, 9, 25, tzinfo=_CN_TZ),
        datetime(2020, 6, 2, 15, 5, tzinfo=_CN_TZ),
        freq="1m",
    )

    assert "bars" not in calls
    assert calls[0] == ("ticks", [date(2020, 6, 2)])
    # 09:31 的成交归到区间结束标签 09:32; 15:00 的收盘竞价钳回 15:00 而不是 15:01。
    assert frame["datetime"].to_list() == [datetime(2020, 6, 2, 9, 32), datetime(2020, 6, 2, 15, 0)]
    assert frame["volume"].to_list() == [100.0, 200.0]


def test_minute_line_maps_history_series_onto_session_stamps(monkeypatch):
    """分时接口只有 close + volume, 要按 09:31..15:00 的 240 个交易时点铺开。

    条数与 240 对不上时(半天市)宁可跳过也不猜序号, 否则整段分时的时刻全错位。
    """
    def history_minute_time(market, code, dates):
        return {
            day: [{"price": 10.0 + index / 100, "vol": 100.0} for index in range(240)]
            for day in dates
        }

    monkeypatch.setattr(client, "history_minute_time", history_minute_time)

    frame = QatdxProvider().get_minute_line(
        ["000001.SZ"],
        datetime(2020, 6, 2, 9, 25, tzinfo=_CN_TZ),
        datetime(2020, 6, 2, 15, 5, tzinfo=_CN_TZ),
    )

    assert frame.height == 240
    assert frame["datetime"][0] == datetime(2020, 6, 2, 9, 31)
    assert frame["datetime"][-1] == datetime(2020, 6, 2, 15, 0)
    assert frame["close"][0] == 10.0
    assert frame["volume"].sum() == 24000.0


def test_sixty_minute_beyond_500_days_also_switches_to_history(monkeypatch):
    """60m 每天只有 4 根, 用「需要的根数」判会漏判。

    服务器对 15m/30m/60m 只给 500 个交易日(8000/4000/2000 根)。60m 请求 2015 年时
    「需要的根数」是 4 × 3400 ≈ 13600, 小于 20800, 按根数判会继续走 K 线接口然后
    拿回空表 —— 判据必须是覆盖的**交易日数**。
    """
    calls: list = []

    def stock_bars(*args, **kwargs):
        calls.append("bars")
        return []

    def history_ticks(market, code, dates):
        return {day: [{"time": "09:31", "price": 10.0, "vol": 100.0}] for day in dates}

    monkeypatch.setattr(client, "stock_bars", stock_bars)
    monkeypatch.setattr(client, "history_ticks", history_ticks)

    frame = QatdxProvider().get_minute(
        ["000001.SZ"],
        datetime(2015, 1, 5, 9, 25, tzinfo=_CN_TZ),
        datetime(2015, 1, 5, 15, 5, tzinfo=_CN_TZ),
        freq="60m",
    )

    assert "bars" not in calls
    assert frame.height == 1
    # 09:31 的成交 -> 09:32 -> 落在 09:30~10:30 那根, 标签 10:30
    assert frame["datetime"].to_list() == [datetime(2015, 1, 5, 10, 30)]


# ---------------------------------------------------------------------------
# 2026-11-01 体检修复的回归用例
# ---------------------------------------------------------------------------

def test_daily_count_counts_back_from_today(monkeypatch):
    """日线 count 要从「今天」倒推到 start, 不是用查询区间宽度。

    接口是「从最新一根往回数 N 根」: 查 2015-01~2015-03 按区间宽度只需 69 根,
    拿回来的是最近 69 个交易日, 再按区间过滤就恒为空。
    """
    captured = {}

    def stock_bars(market, code, category, count):
        captured["count"] = count
        return []

    monkeypatch.setattr(client, "stock_bars", stock_bars)
    QatdxProvider()._daily_one("600519.SH", datetime(2015, 1, 1), datetime(2015, 3, 1), "stock")
    assert captured["count"] > 3000


def test_index_bars_go_through_index_endpoint(monkeypatch):
    """指数回包字段布局与股票不同, 必须走 get_index_bars。"""
    calls = []

    def stock_bars(*args):
        calls.append("stock")
        return []

    def index_bars(market, code, category, count):
        calls.append("index")
        return [{
            "year": 2026, "month": 10, "day": 30, "hour": 15, "minute": 0,
            "open": 3605.65, "high": 3660.21, "low": 3595.26, "close": 3599.02,
            "vol": 497973940, "amount": 764227190864.0,
            "up_count": 455, "down_count": 1855,
        }]

    monkeypatch.setattr(client, "stock_bars", stock_bars)
    monkeypatch.setattr(client, "index_bars", index_bars)
    provider = QatdxProvider()

    frame = provider.get_daily(["000001.SH"], datetime(2026, 10, 29), datetime(2026, 10, 31), "index")
    assert calls == ["index"]
    assert frame["close"].to_list() == [3599.02]

    calls.clear()
    minute = provider.get_minute(
        ["000001.SH"], datetime(2026, 10, 30), datetime(2026, 10, 31), "index", freq="60m",
    )
    assert calls == ["index"]
    assert minute.height == 1

    calls.clear()
    provider.get_daily(["600519.SH"], datetime(2026, 10, 29), datetime(2026, 10, 31), "stock")
    assert calls == ["stock"]


def _synthetic_block_file(records):
    """按真实 layout 造一个板块文件: 384 字节头 + num + 每块 2813 字节。"""
    import struct

    data = bytearray(384)
    data += struct.pack("<H", len(records))
    for name, codes in records:
        record = bytearray(2813)
        raw = name.encode("gbk")
        record[:len(raw)] = raw
        record[9:11] = struct.pack("<H", len(codes))
        record[11:13] = struct.pack("<H", 0)
        pos = 13
        for code in codes:
            encoded = code.encode()
            record[pos:pos + len(encoded)] = encoded
            pos += 7
        data += record
    return data


def test_block_reader_steps_by_2813_bytes():
    """每块 13 字节记录头 + 400 个 7 字节代码槽 = 2813。

    原实现按 2800 步进, 第 2 条起全体错位, 名称读到代码槽、成分数读到垃圾。
    """
    client._ensure_vendor_path()
    from pytdx.reader.block_reader import BlockReader, BlockReader_TYPE_FLAT

    payload = _synthetic_block_file([
        ("通达信88", ["000408", "000538"]),
        ("海峡西岸", ["600519", "600036", "000001"]),
    ])
    rows = BlockReader().get_data(bytearray(payload), BlockReader_TYPE_FLAT)
    assert [row["blockname"] for row in rows] == [
        "通达信88", "通达信88", "海峡西岸", "海峡西岸", "海峡西岸",
    ]
    assert {row["blockname"]: row["code_index"] for row in rows}["海峡西岸"] == 2
    assert [row["code"] for row in rows] == ["000408", "000538", "600519", "600036", "000001"]


def test_block_info_advances_by_actual_chunk_size():
    """主站每页回 60000 字节, 要按实际回包长度推进, 不能用固定 0x7530 步长。

    按 30000 推进会请求到上一页中间, 拼出重叠文件, 一个 28 万字节的 block.dat
    能产出 80 万行垃圾。
    """
    client._ensure_vendor_path()
    from pytdx.parser.get_block_info import get_and_parse_block_info

    records = [(f"B{i:02d}", [f"60{i:04d}"]) for i in range(30)]
    payload = _synthetic_block_file(records)

    class _Client:
        def __init__(self):
            self.offsets = []

        def get_block_info_meta(self, name):
            return {"size": len(payload)}

        def get_block_info(self, name, start, size):
            self.offsets.append(start)
            return bytes(payload[start:start + 60000])

    fake = _Client()
    rows = get_and_parse_block_info(fake, "block.dat")
    assert fake.offsets == [0, 60000]
    assert len({row["blockname"] for row in rows}) == 30
    assert rows[0]["blockname"] == "B00"


def test_etf_prefixes_are_scoped_per_market(monkeypatch):
    """520/563 在沪市是 ETF, 在深市是债券; 深市只认 15x 段。"""
    client._UNSUPPORTED_MARKETS.clear()

    def security_list(market):
        if market == 0:
            return [
                {"code": "159915", "name": "创业板"},
                {"code": "520000", "name": "26伊犁02"},
                {"code": "563000", "name": "山西2430"},
                {"code": "160105", "name": "南方积配"},
            ]
        return [
            {"code": "510300", "name": "300ETF"},
            {"code": "520500", "name": "恒生新药"},
            {"code": "588000", "name": "科创50"},
            {"code": "508000", "name": "某REIT"},
        ]

    monkeypatch.setattr(client, "security_list", security_list)
    symbols = {row["symbol"] for row in QatdxProvider().get_instruments("etf")}
    assert symbols == {"159915.SZ", "510300.SH", "520500.SH", "588000.SH"}


def test_realtime_fills_name_and_amplitude(monkeypatch):
    monkeypatch.setattr(client, "quotes", lambda pairs: [{
        "code": "000001", "price": 11.0, "last_close": 10.0, "open": 10.0,
        "high": 11.5, "low": 9.5, "vol": 100, "amount": 1000.0,
    }])
    provider = QatdxProvider()
    provider._names["000001.SZ"] = "平安银行"
    row = provider.get_realtime(symbols=["000001.SZ"])[0]
    assert row["name"] == "平安银行"
    assert row["amplitude"] == pytest.approx(0.2)


def test_sh_margin_restores_utf16_cells():
    """上交所 xls 的字符串是 UTF-16LE 字节按单字节解出来的, 每个字符都夹 \\x00。"""
    assert client._restore_cell("5\x001\x000\x000\x005\x000\x00") == "510050"
    assert client._restore_cell("510050") == "510050"
    assert client._restore_cell(None) == ""


def test_sz_margin_paginates_units_and_retries(monkeypatch):
    import json

    def page_body(records, page_count=2):
        return json.dumps([{
            "metadata": {"name": "融资融券交易明细", "pagecount": page_count},
            "data": records,
        }]).encode()

    pages = {
        1: page_body([{"zqdm": "000001", "zqjc": "平安银行", "jrrzmr": "1.49",
                       "jrrzye": "41.59", "jrrjmc": "22.55", "jrrjyl": "1,638.23"}]),
        2: page_body([{"zqdm": "000002", "zqjc": "万科A", "jrrzmr": "0.55",
                       "jrrzye": "22.45", "jrrjmc": "5.8", "jrrjyl": "198.8"}]),
    }
    attempts = {"2": 0}

    def fake_get(url, timeout=10.0):
        page = int(url.rsplit("=", 1)[-1])
        if page == 2:
            attempts["2"] += 1
            if attempts["2"] == 1:
                raise TimeoutError("timed out")  # 第一次故意失败, 走重试分支
        return pages[page]

    monkeypatch.setattr(client, "_http_get", fake_get)
    rows = client._parse_sz_margin(date(2026, 10, 29))
    assert [row["symbol"] for row in rows] == ["000001.SZ", "000002.SZ"]
    # 深交所给的是「亿元 / 亿股」, 要还原成元 / 股。
    assert rows[0]["leveraged_balance"] == pytest.approx(4_159_000_000.0)
    assert rows[0]["margin_left"] == pytest.approx(163_823_000_000.0)
    assert attempts["2"] >= 2


def test_history_server_cache_respects_probe_depth():
    """缓存的历史主站必须「覆盖得足够早」才能复用。"""
    import time as _time

    client.reset_server_cache()
    client._CACHE.update(
        history_ip="1.1.1.1", history_port=7709,
        history_at=_time.time(), history_from=20080102,
    )
    assert client._select_server(need_history=True, probe_ymd=20150105) == ("1.1.1.1", 7709)
    client.reset_server_cache()


def test_fallback_server_does_not_poison_history_cache(monkeypatch):
    """全部主站探测失败时用的 fallback 主站, 不能被记成满足该探测日的历史深度。

    实测事故: 2015-01-01 是元旦休市, ``_supports_history`` 对每台主站都返回
    False, 走 fallback 时把 ``history_from=20150101`` 写进缓存。此后所有
    ``probe_ymd >= 20150101`` 的请求都认为缓存主站合格并复用, 2015 年全年逐笔
    与分时静默返回 0 行。重置缓存后同一批日期立刻有数据 —— 说明主站是好的,
    坏的只是这条缓存。
    """
    import time as _time

    client.reset_server_cache()
    monkeypatch.setattr(client, "_tcp_latency_ms", lambda ip, port, timeout: 1.0)

    class _Api:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            import contextlib
            return contextlib.nullcontext()

        def get_security_count(self, market):
            return 5000

    monkeypatch.setattr(client, "_import_api", lambda: _Api)
    monkeypatch.setattr(client, "_supports_history", lambda api, probe_ymd=None: False)

    ip, port = client._select_server(need_history=True, probe_ymd=20150101)
    assert port  # 仍然返回一台能连上的主站, 不能整体失败
    # 关键断言: 它没通过探测, 覆盖下限不能被写成 20150101。
    assert int(client._CACHE["history_from"] or 0) != 20150101
    # 后续同深度请求不能命中这台未验证的主站。
    assert client._CACHE["history_ip"] != ip or int(client._CACHE["history_from"] or 0) > 20150101
    client.reset_server_cache()


def test_test_dataset_covers_every_declared_set(monkeypatch):
    provider = QatdxProvider()
    frame = pl.DataFrame({"x": [1]})
    for name in ("get_daily", "get_minute", "get_adj_factors", "get_financials",
                 "get_transactions", "get_blocks", "get_industries", "get_margin",
                 "get_realtime"):
        monkeypatch.setattr(provider, name, lambda *a, **k: frame)
    monkeypatch.setattr(provider, "get_depth_batch", lambda symbols: {"000001.SZ": {"bid": 1.0}})
    monkeypatch.setattr(provider, "get_instruments", lambda asset="stock": [{"symbol": "000001.SZ"}])
    for dataset in provider.datasets:
        result = provider.test_dataset(dataset)
        assert result.get("error") != "未支持的数据集", dataset
