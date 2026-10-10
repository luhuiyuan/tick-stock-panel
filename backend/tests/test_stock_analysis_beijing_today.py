"""个股分析 / 关键价位的日K窗口右端必须是北京日期, 不能用服务器本地 date.today()。

CONTRIBUTING §3.3: A 股交易时段按北京时间, 服务器时区不能成为隐式输入。
助手工具 get_stock_daily / get_stock_analysis 已改用 cn_today()
(test_assistant_beijing_today), 但个股分析页的两条读路径仍是 date.today():

- stock_analyzer._load_kline: AI 四维分析注入的最近 N 根日 K
- stock_analysis.get_levels: 图表关键价位 / markLine 数据源

美西主机整个 A 股交易时段、UTC 主机北京 00:00-08:00, 本地日历日比北京
早一天: 管道已写入的当日官方 K 被排除, 分析与价位停在昨天。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
from fastapi import HTTPException

from app.api import stock_analysis as stock_analysis_api
from app.services import stock_analyzer

BJ = date(2026, 3, 2)  # 钉死的北京日期, 不会碰巧等于跑测试那天的 date.today()


class _Repo:
    def __init__(self) -> None:
        self.windows: list[tuple[date, date]] = []

    def resolve_asset_type(self, symbol: str) -> str:
        return "stock"

    def get_daily_asset(self, asset_type, symbol, start, end, columns=None) -> pl.DataFrame:
        self.windows.append((start, end))
        return pl.DataFrame()


def test_load_kline_window_ends_on_beijing_today(monkeypatch) -> None:
    """AI 个股分析读日K的窗口右端必须是北京今天。

    raising=False: 未修复代码没有调用 cn_today, 钉了也不会被用到,
    仍走 date.today() → 与 2026-03-02 不等。
    """
    monkeypatch.setattr(stock_analyzer, "cn_today", lambda: BJ, raising=False)
    repo = _Repo()
    stock_analyzer._load_kline(repo, "600000.SH")
    assert repo.windows, "应查询日K"
    _start, end = repo.windows[0]
    assert end == BJ, f"窗口右端必须是北京日期 {BJ}, 实际 {end} (服务器本地 {date.today()})"
    assert end != date.today()


def test_levels_window_ends_on_beijing_today(monkeypatch) -> None:
    """/api/stock-analysis/levels 未传日期时窗口右端必须是北京今天。

    raising=False: 未修复代码没有调用 cn_today, 钉了也不会被用到。
    """
    monkeypatch.setattr(stock_analysis_api, "cn_today", lambda: BJ, raising=False)
    repo = _Repo()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))
    stock_analysis_api.get_levels(req, symbol="600000.SH", days=120)
    assert repo.windows, "应查询日K"
    _start, end = repo.windows[0]
    assert end == BJ, f"窗口右端必须是北京日期 {BJ}, 实际 {end} (服务器本地 {date.today()})"
    assert end != date.today()


def test_levels_includes_dp_structure_without_replacing_existing_fields(monkeypatch) -> None:
    """The levels response keeps existing fields and adds the DP payload."""
    df = pl.DataFrame({
        "date": [date(2026, 1, index) for index in range(1, 5)],
        "close": [100.0, 110.0, 90.0, 120.0],
        "atr_14": [1.0, 1.0, 1.0, 1.0],
    })

    class Repo(_Repo):
        def get_daily_asset(self, asset_type, symbol, start, end, columns=None) -> pl.DataFrame:
            self.windows.append((start, end))
            return df

    monkeypatch.setattr(stock_analysis_api, "compute_levels", lambda frame: {"sr": []})
    monkeypatch.setattr(stock_analysis_api, "summarize_levels", lambda levels, close: "ok")
    monkeypatch.setattr(stock_analysis_api, "compute_market_structure", lambda frame, **kwargs: {"trend": "unknown"})
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=Repo())))

    result = stock_analysis_api.get_levels(req, symbol="600000.SH", days=120)

    assert result["levels"] == {"sr": []}
    assert result["structure"] == {"trend": "unknown"}
    assert set(result["wave_structure"]) == {"L0", "L1", "L2"}
    assert result["wave_structure"]["L0"]["turning_points"] == []
    assert set(result["dp_structure"]) == {"L0", "L1", "L2", "L3"}
    assert result["dp_structure"]["L0"]["current_tail"]["state"] == "UP"


def test_levels_accepts_explicit_date_range(monkeypatch) -> None:
    """Explicit dates override the rolling days window."""
    repo = _Repo()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))
    monkeypatch.setattr(stock_analysis_api, "compute_levels", lambda frame: {"sr": []})
    monkeypatch.setattr(stock_analysis_api, "summarize_levels", lambda levels, close: "ok")
    monkeypatch.setattr(stock_analysis_api, "compute_market_structure", lambda frame, **kwargs: {"trend": "unknown"})
    monkeypatch.setattr(stock_analysis_api, "compute_dp_structure", lambda frame: {})

    stock_analysis_api.get_levels(
        req,
        symbol="600000.SH",
        days=120,
        start_date="2025-01-02",
        end_date="2025-06-30",
    )

    assert repo.windows == [(date(2025, 1, 2), date(2025, 6, 30))]


def test_levels_rejects_invalid_date_range() -> None:
    repo = _Repo()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))

    try:
        stock_analysis_api.get_levels(
            req,
            symbol="600000.SH",
            days=120,
            start_date="2025-07-01",
            end_date="2025-06-30",
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("expected invalid date range to be rejected")


def test_period_levels_returns_aligned_candles_and_uses_longer_history(monkeypatch):
    from datetime import timedelta

    class Repo(_Repo):
        def get_daily_asset(self, asset_type, symbol, start, end, columns=None):
            self.windows.append((start, end))
            dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(90)]
            return pl.DataFrame({"date": dates, "open": [10.0] * 90,
                                 "high": [12.0] * 90, "low": [9.0] * 90,
                                 "close": [11.0] * 90, "volume": [100.0] * 90})

    monkeypatch.setattr(stock_analysis_api, "cn_today", lambda: BJ)
    repo = Repo()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))
    for timeframe, days in (("W", 1826), ("M", 3652)):
        result = stock_analysis_api.get_levels(req, symbol="600000.SH", days=120, timeframe=timeframe)
        assert repo.windows[-1] == (BJ - timedelta(days=days), BJ)
        assert result["rows"]
        assert result["dates"] == [row["date"] for row in result["rows"]]
        assert result["structure"]["timeframe"] == timeframe
        assert result["structure"]["data_through_date"] == result["rows"][-1]["date"]
        assert result["levels"] == {} and result["series"] == {}
        assert "wave_structure" not in result


def test_empty_period_response_includes_safe_chart_contract():
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=_Repo())))
    result = stock_analysis_api.get_levels(req, symbol="600000.SH", days=120, timeframe="M")
    assert result["rows"] == [] and result["close"] is None
    assert result["structure"]["last_high"] is None
