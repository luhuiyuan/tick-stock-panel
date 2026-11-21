"""涨停价缓存服务(limit_price_job)的单元测试。

涨停价纯本地规则计算, 无接口调用。验证: 可用交易日、纯推断落盘与区间过滤、
status 概况、checkpoint 可停止、batch 只返回推断结果。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.services import limit_price_store as lps
from app.services.limit_price_job import LimitPriceCache


def _make_enriched(root: Path, days: list[date], symbols_by_day: dict[date, list[str]]):
    """构造最小的 enriched 分区(只需 symbol 列 + 目录名做日期)。"""
    base = root / "kline_daily_enriched"
    for d in days:
        part = base / f"date={d.isoformat()}"
        part.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"symbol": symbols_by_day.get(d, [])}).write_parquet(part / "part.parquet")


def _make_enriched_priced(root: Path, days: list[date], symbols: list[str]):
    """带完整价格列的 enriched 分区, 供推断阶段(price_facts)使用。"""
    base = root / "kline_daily_enriched"
    n = len(symbols)
    for d in days:
        part = base / f"date={d.isoformat()}"
        part.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({
            "symbol": symbols,
            "date": [d] * n,
            "close": [10.0] * n,
            "raw_close": [10.0] * n,
            "raw_high": [10.5] * n,
            "raw_low": [9.5] * n,
        }).write_parquet(part / "part.parquet")
    inst_dir = root / "instruments"
    inst_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": symbols,
        "as_of": [days[-1]] * n,
        "limit_up": [None] * n,
        "limit_down": [None] * n,
        "name": ["TEST"] * n,
    }).write_parquet(inst_dir / "instruments.parquet")


def test_available_days_and_default_start(tmp_path):
    days = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
    _make_enriched(tmp_path, days, {d: ["600825.SH"] for d in days})
    svc = LimitPriceCache(tmp_path)
    assert svc.available_days() == days
    assert svc.default_start() == days[0]


def test_start_does_not_require_any_gateway(tmp_path, monkeypatch):
    """纯规则任务启动不需检查外部接口可用性。"""
    days = [date(2026, 1, 2)]
    _make_enriched(tmp_path, days, {d: ["600825.SH"] for d in days})
    svc = LimitPriceCache(tmp_path)
    # 屏蔽实际执行, 避免后台线程占用全局重任务槽干扰其它测试。
    monkeypatch.setattr(LimitPriceCache, "_run", lambda self, *a, **k: None)
    # 不 mock 任何 provider / availability; start 不应抛接口不可用错误。
    r = svc.start(None, None)
    assert "job_id" in r and "created" in r


def test_compute_inferred_lands_inferred_column_and_respects_range(tmp_path):
    """推断阶段: 全历史预热后只落请求区间的 inferred 价。"""
    days = [date(2026, 1, d) for d in range(2, 14)]  # 12 个交易日, 满足 cum_count>=6
    syms = ["600825.SH", "000001.SZ"]
    _make_enriched_priced(tmp_path, days, syms)
    svc = LimitPriceCache(tmp_path)

    lo, hi = date(2026, 1, 12), date(2026, 1, 13)
    added = svc._compute_inferred("job", lo, hi, lambda: None)
    assert added > 0

    table = lps.load(tmp_path)
    assert set(table["trade_date"].to_list()) <= {lo, hi}
    assert table["limit_up_inferred"].null_count() == 0
    assert table["limit_down_inferred"].null_count() == 0
    st = svc.status()
    assert st["inferred_keys"] == table.height
    assert st["earliest"] in {lo.isoformat(), hi.isoformat()}
    assert st["latest"] == hi.isoformat()
    assert "api_keys" not in st and "api_available" not in st


def test_batch_only_computes_inferred(tmp_path, monkeypatch):
    """batch 只做规则推断阶段, 返回 inferred_added, 无接口字段。"""
    days = [date(2026, 1, d) for d in range(2, 12)]
    syms = ["600825.SH"]
    _make_enriched_priced(tmp_path, days, syms)
    svc = LimitPriceCache(tmp_path)

    result = svc.batch("job", date(2026, 1, 11), date(2026, 1, 11), lambda: None)
    assert set(result.keys()) == {"inferred_added"}
    assert result["inferred_added"] >= 1
    table = lps.load(tmp_path)
    assert table.height >= 1
    assert set(table.columns) == set(lps.COLUMNS)


def test_batch_checkpoint_can_stop(tmp_path, monkeypatch):
    days = [date(2026, 1, d) for d in range(2, 12)]
    _make_enriched_priced(tmp_path, days, ["600825.SH"])
    svc = LimitPriceCache(tmp_path)

    def cp():
        raise TimeoutError("stop")

    with pytest.raises(TimeoutError):
        svc.batch("job", None, None, cp)
