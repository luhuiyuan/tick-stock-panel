"""``export_history_minute.fetch_ticks`` 的完整性保证。

背景：2025 年那一轮抓取在三处静默丢数据——主站瞬时返回空表时 ``break`` 当没事
（460 个股票日全落在 2025-01-10 一天），分片却照常被标记完成、整年标记也写了。
2024 年那一轮反过来，把「偏差」当成了「缺失」：601162.SH 2024-10-10 一天
3,694 万手只差 3 手就被判失败，连带这只股票整年 242 天全丢（5,069 只变 5,068 只）。

所以这里钉两件事：**取不全的那一天要留下痕迹（gaps），而不是让整只股票消失**；
**成交量容差要随成交规模放大**，小成交日用绝对手数、巨量日用相对比例。
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_history_minute.py"
_spec = importlib.util.spec_from_file_location("export_history_minute", _SCRIPT)
exh = importlib.util.module_from_spec(_spec)
# dataclass 装饰器要回查 sys.modules 里的模块命名空间, 不注册会直接抛 AttributeError。
sys.modules[_spec.name] = exh
_spec.loader.exec_module(exh)


def _row(clock: str, price: float, vol: float, direction: int = 0) -> dict:
    return {"time": clock, "price": price, "vol": vol, "buyorsell": direction}


class _FakeApi:
    """按预设序列回放每天的数据，用完后一直返回最后一个。"""

    def __init__(self, responses: list[list[dict]]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def get_history_transaction_data(self, market, code, offset, count, ymd):  # noqa: ANN001
        self.calls += 1
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class _FakeEndpoint:
    def __init__(self, api: _FakeApi) -> None:
        self._api = api
        self.drops = 0

    def api(self):
        return self._api

    def drop(self) -> None:
        self.drops += 1


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(exh.time, "sleep", lambda _s: None)


DAY = date(2025, 1, 10)
PREV = date(2025, 1, 9)


def test_fetch_ticks_retries_empty_day_until_data_arrives():
    """主站抖一下返回空表时，换主站重试，不能把这一天当成没数据。"""
    api = _FakeApi([[], [_row("09:25", 10.0, 100.0), _row("10:00", 10.1, 200.0)]])
    ep = _FakeEndpoint(api)
    rows, gaps = exh.fetch_ticks(ep, 0, "000001", [(DAY, 20250110, 301.0)])
    assert len(rows[DAY]) == 2
    assert gaps == []
    assert ep.drops == 1


def test_fetch_ticks_keeps_good_days_when_one_day_stays_empty():
    """某一天连续取不到只丢那一天，其余日子照常产出，并留下 gaps 记录。"""
    api = _FakeApi([
        [_row("09:25", 10.0, 100.0), _row("10:00", 10.1, 200.0)],  # 01-09 正常
        [_row("14:59", 10.0, 100.0)],                               # 01-10 一直不全
    ])
    ep = _FakeEndpoint(api)
    rows, gaps = exh.fetch_ticks(
        ep, 0, "300008", [(PREV, 20250109, 301.0), (DAY, 20250110, 2302.0)]
    )
    assert list(rows) == [PREV]
    assert [g[0] for g in gaps] == [DAY]
    assert "成交量不符" in gaps[0][1]


def test_fetch_ticks_raises_when_no_day_can_be_read():
    """一整天都没取到说明是代码/主站/市场号的问题，整只判失败。"""
    ep = _FakeEndpoint(_FakeApi([[]]))
    with pytest.raises(exh.TicksFetchError):
        exh.fetch_ticks(ep, 0, "300008", [(DAY, 20250110, 2302.0)])
    assert ep.drops == exh._DAY_ATTEMPTS


def test_fetch_ticks_raises_on_truncated_page_when_only_day():
    """页尾被截断时成交量对不上日K，同样算失败（非空 ≠ 完整）。"""
    ep = _FakeEndpoint(_FakeApi([[_row("14:59", 10.0, 100.0)]]))
    with pytest.raises(exh.TicksFetchError):
        exh.fetch_ticks(ep, 0, "600010", [(DAY, 20250110, 3866.0)])


def test_volume_tolerance_scales_with_daily_volume():
    """容差随成交量放大：3,694 万手的日子差 3 手不能判失败，缺一截仍要判失败。"""
    want = 36_945_360.0
    assert exh._volume_tolerance(want) > 3.0

    # 601162.SH 2024-10-10 的真实形态：两笔凑出 36,945,363 手，差 3 手
    ep = _FakeEndpoint(_FakeApi([[
        _row("09:25", 5.84, 18_472_682.0, 2),
        _row("14:57", 4.78, 18_472_681.0),
    ]]))
    rows, gaps = exh.fetch_ticks(ep, 1, "601162", [(DAY, 20241010, want)])
    assert len(rows[DAY]) == 2
    assert gaps == []

    # 同一只票少了一整页（约 15% 的量）必须被拦下：换成两天看，好的那天留下、坏的那天进 gaps
    ep = _FakeEndpoint(_FakeApi([
        [_row("14:57", 4.80, 1_000_000.0)],        # 01-09 正常
        [_row("14:57", 4.78, 31_000_000.0)],       # 01-10 掉了约 6,000 万手
    ]))
    rows, gaps = exh.fetch_ticks(
        ep, 1, "601162", [(PREV, 20241009, 1_000_000.0), (DAY, 20241010, want)]
    )
    assert list(rows) == [PREV]
    assert [g[0] for g in gaps] == [DAY]


def test_volume_tolerance_floor_on_small_days():
    """小成交日仍用绝对 2 手，1 手差放过、3 手差拦下。"""
    assert exh._volume_tolerance(0.0) == 2.0
    assert exh._volume_tolerance(50_000.0) == 2.0

    ep = _FakeEndpoint(_FakeApi([[_row("10:00", 10.0, 1_003.0)]]))
    rows, _ = exh.fetch_ticks(ep, 0, "000001", [(DAY, 20250110, 1_002.0)])
    assert len(rows[DAY]) == 1

    ep = _FakeEndpoint(_FakeApi([
        [_row("10:00", 10.0, 1_003.0)],   # 01-09 差 1 手，放过
        [_row("10:00", 10.0, 1_005.0)],   # 01-10 差 3 手，拦下
    ]))
    rows, gaps = exh.fetch_ticks(
        ep, 0, "000001", [(PREV, 20250109, 1_002.0), (DAY, 20250110, 1_002.0)]
    )
    assert list(rows) == [PREV]
    assert [g[0] for g in gaps] == [DAY]


def test_fetch_ticks_excludes_after_hours_fixed_price_trades():
    """科创板盘后固定价格交易（15:18/15:19）计在逐笔里但不计在日K 里。"""
    rows = [
        _row("09:25", 75.0, 235.0, 2),
        _row("10:00", 75.5, 21755.0),
        _row("15:00", 75.8, 1.0, 2),
        _row("15:18", 75.8, 1000.0),
        _row("15:19", 75.8, 3000.0),
    ]
    ep = _FakeEndpoint(_FakeApi([rows]))
    # 盘内 235 + 21755 + 1 = 21991，盘后 4000 不参与比较
    got, gaps = exh.fetch_ticks(ep, 1, "688278", [(DAY, 20250410, 21990.0)])
    assert len(got[DAY]) == 5  # 盘后记录照样保留在产物里
    assert gaps == []
    assert ep.drops == 0


def test_fetch_ticks_counts_closing_auction_labelled_1501():
    """收盘竞价可能被标成 15:01（发布晚一分钟），它属于盘内成交量。

    实测 000002.SZ 2024-11-07 的收盘竞价 52,551 手标成 15:01，占当天成交量的
    1.03%。若校验截到 15:00，这天会被误判成「成交量不符」。
    """
    rows = [
        _row("09:25", 9.85, 2000.0, 2),
        _row("14:57", 9.89, 700.0),
        _row("15:01", 9.89, 52551.0, 2),   # 收盘竞价，时标慢了 1 分钟
    ]
    ep = _FakeEndpoint(_FakeApi([rows]))
    got, gaps = exh.fetch_ticks(ep, 0, "000002", [(DAY, 20241107, 55251.0)])
    assert len(got[DAY]) == 3
    assert gaps == []
    assert ep.drops == 0


def test_fetch_ticks_accepts_empty_when_daily_volume_is_zero():
    """日K 记了当天但成交量为 0（不足一手）时，空表就是正确答案。"""
    ep = _FakeEndpoint(_FakeApi([[]]))
    got, gaps = exh.fetch_ticks(ep, 0, "000014", [(DAY, 20250110, 0.0)])
    assert got == {}
    assert gaps == []
    assert ep.drops == 0
