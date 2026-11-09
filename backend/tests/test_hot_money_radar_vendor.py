"""hot_money_radar vendored 规则核心的一致性/冒烟测试。

两类断言：
1. 自身冒烟(始终执行)：import、PATTERNS 完整性、rows_to_bars 字段映射、
   match_patterns / dominant_phase 在合成数据上可运行且 PIT 形状正确。
2. 与上游逐字一致性(本地上游副本存在时执行)：把同一批 bars 分别喂给
   vendored ``radar_core`` 与本地克隆的 ``stock_hot_money_radar.py``，
   断言 ``match_patterns`` 输出逐条相等；副本缺失时 skip，不影响 CI 主流程。

上游完整副本目录(不入库)：``/workspace/reference/financial-analysis``。
详见 ``backend/app/indicators/vendor/hot_money_radar/SOURCE.md``。
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from app.indicators.vendor.hot_money_radar import adapter, radar_core

_UPSTREAM_DIR = Path("/workspace/reference/financial-analysis")
_UPSTREAM_FILE = _UPSTREAM_DIR / "stock_hot_money_radar.py"


def _synth(seed: int, kind: str, n: int = 200) -> list[dict]:
    """生成能触发不同形态的日线行(项目列名：turnover_rate)。"""
    rng = np.random.default_rng(seed)
    price = 10.0
    base = _dt.date(2024, 1, 1)
    rows: list[dict] = []
    for i in range(n):
        if kind == "uptrend":
            drift = 0.004
        elif kind == "downtrend":
            drift = -0.004
        elif kind == "limitup":
            drift = 0.099 if 150 <= i < 155 else 0.001
        elif kind == "highvol":
            drift = 0.0
        else:
            drift = 0.001
        chg = drift + rng.normal(0, 0.045 if kind == "highvol" else 0.02)
        close = price * (1 + chg)
        price = close
        op = price * (1 + rng.normal(0, 0.005))
        high = max(op, close) * (1 + abs(rng.normal(0, 0.01)))
        low = min(op, close) * (1 - abs(rng.normal(0, 0.01)))
        vol = 1e6 * (1 + abs(rng.normal(0, 0.5))) * (3 if kind == "highvol" and i > 180 else 1)
        rows.append(
            {
                "symbol": "000001.SZ",
                "date": (base + _dt.timedelta(days=i)).isoformat(),
                "open": float(op),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(vol),
                "amount": float(vol * close * 100),
                "chg": float(chg),
                "turnover_rate": float(np.clip(vol / 1e7 * 8, 0.3, 25)),
            }
        )
    return rows


def _load_upstream():
    """按需加载本地上游完整副本，缺依赖/缺文件返回 None。

    上游 ``stock_hot_money_radar.py`` 顶层 import ``requests`` /
    ``stock_storage`` / ``stock_etf_pool``(后两者又会拉起 akshare 等爬虫依赖)，
    但 ``match_patterns`` 依赖闭包完全不碰这些模块。为了在干净环境里做
    「逐字一致性对比」而不往项目 venv 装无关依赖，这里给这 3 个模块注入
    轻量 stub；模块级唯一被求值的属性是 ``stock_storage.DEFAULT_DB_FILE``，
    一并补齐。stub 只在 sys.modules 中占位，不改动上游规则代码。
    """
    if not _UPSTREAM_FILE.exists():
        return None
    d = str(_UPSTREAM_DIR)
    if d not in sys.path:
        sys.path.insert(0, d)
    for name in ("requests", "stock_storage", "stock_etf_pool"):
        if name not in sys.modules:
            try:
                __import__(name)
            except ModuleNotFoundError:
                sys.modules[name] = types.ModuleType(name)
    # from stock_etf_pool import load_etf_pool 需可导入名
    pool = sys.modules.get("stock_etf_pool")
    if pool is not None and not hasattr(pool, "load_etf_pool"):
        pool.load_etf_pool = lambda *a, **k: []
    # 模块级: DB_FILE = stock_storage.DEFAULT_DB_FILE
    storage = sys.modules.get("stock_storage")
    if storage is not None and not hasattr(storage, "DEFAULT_DB_FILE"):
        storage.DEFAULT_DB_FILE = _UPSTREAM_DIR / "stock_data.sqlite3"
    try:
        spec = importlib.util.spec_from_file_location("_upstream_hot_money_radar", _UPSTREAM_FILE)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # 上游依赖/结构变化时跳过对比，不让 vendored 冒烟测试失败
        return None


# ---------------------------------------------------------------------------
# 1. 自身冒烟
# ---------------------------------------------------------------------------

def test_patterns_table_complete():
    assert len(radar_core.PATTERNS) == 26
    codes = [p[0] for p in radar_core.PATTERNS]
    assert codes == [f"P{i}" for i in range(1, 27)], "规则编号 P1-P26 应完整且有序"
    assert radar_core.MIN_BARS == 40
    assert radar_core.LOOKBACK == 180


def test_rows_to_bars_field_mapping():
    rows = _synth(0, "uptrend", n=60)
    bars = adapter.rows_to_bars(rows)
    assert len(bars) == 60
    b = bars[0]
    for k in ("date", "open", "high", "low", "close", "volume", "amount", "chg", "turnover", "_chip_avg_price"):
        assert k in b, f"缺少上游必需字段 {k}"
    assert isinstance(b["date"], str)
    assert b["turnover"] == pytest.approx(rows[0]["turnover_rate"])
    assert b["close"] == pytest.approx(rows[0]["close"])
    # _chip_avg_price 由 vendored _bar_avg_price 逐字等价预计算
    assert b["_chip_avg_price"] is not None


def test_match_patterns_and_phase_runnable():
    rows = _synth(0, "highvol", n=200)
    fired = adapter.match_patterns("000001.SZ", rows)
    assert isinstance(fired, list)
    for item in fired:
        assert isinstance(item, dict)
    result = adapter.dominant_phase("000001.SZ", rows)
    assert set(result) == {"fired", "phase"}
    assert isinstance(result["phase"], str)


def test_short_history_returns_empty():
    rows = _synth(1, "uptrend", n=10)
    assert adapter.match_patterns("000001.SZ", rows) == []


# ---------------------------------------------------------------------------
# 2. 与上游逐字一致性
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["uptrend", "downtrend", "limitup", "highvol"])
@pytest.mark.parametrize("seed", [0, 2, 3, 4])
def test_match_patterns_matches_upstream(kind, seed):
    upstream = _load_upstream()
    if upstream is None:
        pytest.skip("本地上游副本不可用，跳过逐字一致性对比")

    rows = _synth(seed, kind, n=200)
    bars = adapter.rows_to_bars(rows)
    code = "000001"

    theirs = upstream.match_patterns(code, bars, p1_market_gate=False)
    ours = radar_core.match_patterns(code, bars, p1_market_gate=False)
    assert ours == theirs, f"{kind}#{seed}: vendored 与上游输出不一致"
