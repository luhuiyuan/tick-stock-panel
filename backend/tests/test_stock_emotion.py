"""stock_emotion 服务的纯函数测试（个股情绪分）。

不依赖 FastAPI / repo / 真实 parquet：用确定性合成日线直接驱动 ``compute_stock``。
核心契约：

1. 输出结构：n_obs / scores 长度 / 前 WARMUP 天预热留空；指标值在合理区间；
2. **因果性**：相同日期、相同历史前缀 → 相同分数（截断重算逐日一致）；
3. 语义：纯大涨走势平均分 > 纯大跌走势平均分（恐惧↔亢奋方向正确）；
4. 配置覆盖：term 的 low/high 与档位阈值可被 config 覆盖；
5. 参数校验：lookback 不在白名单抛 ValueError；
6. （可选）真实数据：`_load_symbol_rows` 驱动时，与离线试算稿（ta 库）口径一致。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from app.services import stock_emotion as se

SYMBOL = "000006.SZ"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _synth(n: int = 300, seed: int = 1) -> list[dict]:
    """确定性合成日线：几段不同斜率拼接，覆盖涨/跌/横盘。"""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    price = 10.0
    start = dt.date(2022, 1, 4)
    slopes = (0.010, -0.010, 0.0003, 0.012, -0.011)
    for i in range(n):
        slope = slopes[(i // 60) % len(slopes)]
        noise = float(rng.normal(0, 0.002))
        close = price * (1.0 + slope + noise)
        op = close * (1.0 - slope * 0.4)
        high = max(op, close) * 1.01
        low = min(op, close) * 0.99
        price = close
        rows.append(
            {
                "symbol": SYMBOL,
                "date": (start + dt.timedelta(days=i)).isoformat(),
                "open": float(op),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": 1.0e6 * (1.0 + (i % 9) * 0.11),
            }
        )
    return rows


def _segment(n: int, slope: float) -> list[dict]:
    """单调走势：slope>0 持续上涨，slope<0 持续下跌。"""
    rows: list[dict] = []
    price = 10.0
    start = dt.date(2023, 1, 4)
    for i in range(n):
        close = price * (1.0 + slope)
        price = close
        op = close * (1.0 - slope * 0.4)
        high = max(op, close) * 1.01
        low = min(op, close) * 0.99
        rows.append(
            {
                "symbol": SYMBOL,
                "date": (start + dt.timedelta(days=i)).isoformat(),
                "open": float(op),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": 1.0e6,
            }
        )
    return rows


def test_basic_and_warmup():
    rows = _synth(300)
    out = se.compute_stock(rows, SYMBOL, lookback=250)
    assert out["symbol"] == SYMBOL
    assert out["n_obs"] == 250
    assert len(out["scores"]) == 250
    assert out["scores"][: se.WARMUP] == [None] * se.WARMUP
    # 预热期后至少有有效分数
    assert any(s is not None for s in out["scores"])
    # 指标值在合理区间
    for key in ("rsi14", "stoch_k14", "boll_b20", "roc10", "bias20"):
        for v in out["metrics"][key]:
            if v is None:
                continue
            assert -50 <= v <= 150, f"{key} 越界: {v}"
    # 总分在 0..100
    for s in out["scores"]:
        if s is not None:
            assert 0 <= s <= 100


def test_causality():
    """因果性：第 t 天的分数只依赖 t 及之前的数据，与 t 之后的数据无关。

    把窗口内第 t 天之后的数据整体替换为随机噪声（模拟「看不见的未来」），
    验证 t 及之前每一天（含远离窗口起点的天数）分数完全不变。这直接对应
    「不要未来数据、不逐日重放」的口径要求。
    """
    rng = np.random.default_rng(7)
    rows = _synth(520)
    base = se.compute_stock(rows, SYMBOL, lookback=250)  # 窗口 = rows[270:520]
    start_abs = len(rows) - se.DEFAULT_LOOKBACK  # = 270
    t_idx = 130  # 窗口内 index
    t_abs = start_abs + t_idx  # 该天的绝对索引 = 400
    rows2 = [dict(r) for r in rows]
    for j in range(t_abs + 1, len(rows2)):  # 只篡改绝对 401 之后（窗口内 t 之后）
        r = dict(rows2[j])
        r["close"] = float(rng.uniform(5.0, 15.0))
        r["open"] = r["close"] * 0.99
        r["high"] = r["close"] * 1.02
        r["low"] = r["close"] * 0.98
        rows2[j] = r
    alt = se.compute_stock(rows2, SYMBOL, lookback=250)
    for i in range(t_idx + 1):
        assert base["scores"][i] == alt["scores"][i], f"窗口 index {i} 因果不一致"
    assert (t_idx + 1) > 100, "因果性校验覆盖天数过少"


def test_semantics_up_gt_down():
    """纯大涨走势的平均情绪分应高于纯大跌走势（方向正确）。"""
    up = se.compute_stock(_segment(120, 0.015), SYMBOL, lookback=120)
    down = se.compute_stock(_segment(120, -0.015), SYMBOL, lookback=120)
    up_scores = [s for s in up["scores"] if s is not None]
    down_scores = [s for s in down["scores"] if s is not None]
    assert up_scores and down_scores
    assert np.mean(up_scores) > np.mean(down_scores)


def test_config_override():
    rows = _synth(300)
    out = se.compute_stock(rows, SYMBOL, lookback=250, config={"thresholds": {"fear": 25}})
    assert out["thresholds"]["fear"] == 25
    out2 = se.compute_stock(rows, SYMBOL, lookback=250, config={"roc10": {"low": -15, "high": 15}})
    assert out2["terms"]["roc10"]["low"] == -15
    assert out2["terms"]["roc10"]["high"] == 15
    # 覆盖映射区间会改变分数（roc10 区间变窄 → 极端值更易触顶/触底）
    base = se.compute_stock(rows, SYMBOL, lookback=250)
    assert out2["scores"] != base["scores"]


def test_invalid_lookback():
    rows = _synth(300)
    with pytest.raises(ValueError):
        se.compute_stock(rows, SYMBOL, lookback=300)


def test_valid_lookbacks():
    rows = _synth(600)
    for lb in se.LOOKBACKS:
        out = se.compute_stock(rows, SYMBOL, lookback=lb)
        assert out["n_obs"] == lb


@pytest.mark.skipif(not DATA_DIR.exists(), reason="无本地 enriched 数据")
def test_real_data_consistency():
    """真实数据：总分序列与离线试算稿（Wilder RSI 同口径）方向一致。

    离线试算稿用 ta 库的 Wilder RSI / ROC / Stoch / BIAS / Boll%B，本服务复用
    ``wilder_rsi`` 并用 numpy/pandas 复刻其余 4 项，口径一致；这里只校验真实数据
    下分数落在 0..100、预热留空、最新快照有效。
    """
    from app.api.phase_lab import _load_symbol_rows

    rows = _load_symbol_rows(DATA_DIR, SYMBOL)
    out = se.compute_stock(rows, SYMBOL, lookback=250)
    assert out["n_obs"] == 250
    assert out["scores"][: se.WARMUP] == [None] * se.WARMUP
    assert out["current"]["score"] is not None
    for s in out["scores"]:
        if s is not None:
            assert 0 <= s <= 100
