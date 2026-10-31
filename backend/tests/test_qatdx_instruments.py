"""qatdx 维表同步与 K 线取数回归。

回归背景 (2026-10-31 一次真实排障, 三个独立缺陷叠加, 表面现象都只是
"数据同步没有同步到标的"):

1. `get_security_list` 解析个股名时对 8 字节 GBK 字段严格解码 —— 中文名超过
   4 个字会被协议从中间截断, 半个汉字的字节序列让 strict decode 抛
   UnicodeDecodeError, 整页 (最多 1000 只) 解析失败, 三个市场全挂 → 维表 0 行。
2. `_asset_kind` 只按代码前缀判断品种, 不区分市场 —— 沪市列表里的 000xxx
   (上证系列指数) 与 880xxx/881xxx (通达信板块与统计指数) 被当成股票写进
   维表, 再经标的池落到 kline_daily, 一次性污染 1320 个假标的。
3. `client.stock_bars` 取回最新一页后直接 `[:count]` 截断, 拿到的是**最旧**的
   count 根; 调用方按日期区间一过滤就是空表, 表现为"日K 同步成功但 0 行"。
"""
from __future__ import annotations

import polars as pl
import pytest

from app.plugins.qatdx import client as qclient
from app.plugins.qatdx import provider as qprovider
from app.services import instrument_sync as isync


def test_asset_kind_filters_indices_out_of_shanghai_stocks() -> None:
    """沪市 000xxx 是上证指数、88xxxx 是板块统计, 都不是股票。"""
    assert qprovider._asset_kind("600000", "stock", 1) is True
    assert qprovider._asset_kind("688981", "stock", 1) is True
    assert qprovider._asset_kind("000001", "stock", 1) is False
    assert qprovider._asset_kind("000300", "stock", 1) is False
    assert qprovider._asset_kind("880001", "stock", 1) is False
    assert qprovider._asset_kind("881478", "stock", 1) is False
    assert qprovider._asset_kind("888880", "stock", 1) is False


def test_asset_kind_keeps_shenzhen_and_beijing_stocks() -> None:
    for code in ("000001", "001979", "002594", "300750", "301236"):
        assert qprovider._asset_kind(code, "stock", 0) is True, code
    for code in ("430047", "830799", "871981", "920008"):
        assert qprovider._asset_kind(code, "stock", 2) is True, code
    # 深市 399xxx 是指数, 不能进股票池
    assert qprovider._asset_kind("399001", "stock", 0) is False


def test_get_instruments_skips_shanghai_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    """沪市列表里混着指数时, 只留真股票。"""
    listed = {
        1: [
            {"code": "600000", "name": "浦发银行"},
            {"code": "000001", "name": "上证指数"},
            {"code": "880001", "name": "总市值"},
        ],
        0: [{"code": "000001", "name": "平安银行"}],
        2: [],
    }

    def fake_list(market: int):
        return listed.get(market, [])

    monkeypatch.setattr(qclient, "security_list", fake_list)
    rows = qprovider.QatdxProvider().get_instruments("stock")
    # 市场遍历顺序是 深(0) → 沪(1) → 北(2), 与"沪市指数被剔除"无关
    assert sorted(r["symbol"] for r in rows) == ["000001.SZ", "600000.SH"]


def test_stock_bars_returns_newest_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    """start=0 那一页按时间升序返回, 取末尾 count 根才是"最近 N 根"。"""
    page = [
        {"year": 2023, "month": 7, "day": 17 + i, "hour": 15, "minute": 0,
         "open": 1.0 + i, "close": 1.0 + i, "vol": 1.0}
        for i in range(5)
    ]

    class _FakeAPI:
        def get_security_bars(self, category, market, code, start, count):
            return page if start == 0 else []

    monkeypatch.setattr(qclient, "_call", lambda fn: fn(_FakeAPI()))
    rows = qclient.stock_bars(0, "000001", 9, 2)
    assert [(r["month"], r["day"]) for r in rows] == [(7, 20), (7, 21)]


def test_provider_empty_result_falls_back_to_tickflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 返回 0 行时必须降级, 不能当成"同步成功但 0 只"。"""

    class _EmptyProvider:
        def get_instruments(self, asset_type="stock"):
            return []

    monkeypatch.setattr(
        qprovider, "QatdxProvider", lambda: _EmptyProvider(),
    )
    from app.services import preferences

    monkeypatch.setattr(preferences, "get_daily_data_provider", lambda: "qatdx")
    from app.data_providers import custom as custom_sources
    from app.data_providers import registry

    monkeypatch.setattr(registry, "_PROVIDERS", {})
    monkeypatch.setattr(custom_sources, "is_custom_provider", lambda name: True)
    monkeypatch.setattr(custom_sources, "get_provider", lambda name: _EmptyProvider())

    assert isync._fetch_instruments_via_provider() is None


def test_sync_instruments_keeps_existing_table_when_all_sources_fail(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全源 0 行时返回 0 且不覆盖已有维表(否则会把可用维表清空)。"""
    out = tmp_path / "instruments"
    out.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600000.SH"], "name": ["浦发银行"]}).write_parquet(
        out / "instruments.parquet"
    )
    monkeypatch.setattr(isync, "_fetch_instruments_via_provider", lambda: None)

    class _FailClient:
        class exchanges:  # noqa: N801 - 结构体内联 fake
            @staticmethod
            def get_instruments(ex, instrument_type="stock"):
                raise RuntimeError("boom")

    monkeypatch.setattr(isync, "get_client", lambda: _FailClient())

    assert isync.sync_instruments(tmp_path) == 0
    kept = pl.read_parquet(out / "instruments.parquet")
    assert kept["symbol"].to_list() == ["600000.SH"]
