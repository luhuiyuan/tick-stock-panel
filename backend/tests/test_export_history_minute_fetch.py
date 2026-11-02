"""``export_history_minute.fetch_ticks`` 的完整性保证。

背景：2025 年那一轮抓取在三处静默丢数据——主站瞬时返回空表时 ``break`` 当没事
（460 个股票日全落在 2025-01-10 一天），分片却照常被标记完成、整年标记也写了。
这些用例把「取不全必须报错」和「盘口口径」钉住。
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


def test_fetch_ticks_retries_empty_day_until_data_arrives():
    """主站抖一下返回空表时，换主站重试，不能把这一天当成没数据。"""
    api = _FakeApi([[], [_row("09:25", 10.0, 100.0), _row("10:00", 10.1, 200.0)]])
    ep = _FakeEndpoint(api)
    got = exh.fetch_ticks(ep, 0, "000001", [(DAY, 20250110, 301.0)])
    assert len(got[DAY]) == 2
    assert ep.drops == 1


def test_fetch_ticks_raises_when_day_stays_empty():
    """连续取不到就必须抛错，让该股票进 failed、不写分片完成标记。"""
    ep = _FakeEndpoint(_FakeApi([[]]))
    with pytest.raises(exh.IncompleteDayError):
        exh.fetch_ticks(ep, 0, "300008", [(DAY, 20250110, 2302.0)])
    assert ep.drops == exh._DAY_ATTEMPTS


def test_fetch_ticks_raises_on_truncated_page():
    """页尾被截断时成交量对不上日K，同样算失败（非空 ≠ 完整）。"""
    ep = _FakeEndpoint(_FakeApi([[_row("14:59", 10.0, 100.0)]]))
    with pytest.raises(exh.IncompleteDayError):
        exh.fetch_ticks(ep, 0, "600010", [(DAY, 20250110, 3866.0)])


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
    got = exh.fetch_ticks(ep, 1, "688278", [(DAY, 20250410, 21990.0)])
    assert len(got[DAY]) == 5  # 盘后记录照样保留在产物里
    assert ep.drops == 0


def test_fetch_ticks_accepts_empty_when_daily_volume_is_zero():
    """日K 记了当天但成交量为 0（不足一手）时，空表就是正确答案。"""
    ep = _FakeEndpoint(_FakeApi([[]]))
    got = exh.fetch_ticks(ep, 0, "000014", [(DAY, 20250110, 0.0)])
    assert got == {}
    assert ep.drops == 0
