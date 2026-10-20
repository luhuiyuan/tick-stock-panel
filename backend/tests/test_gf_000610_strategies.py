from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl

from app.strategy.engine import StrategyEngine


STRATEGY_IDS = {
    "lab_single_yang_holds": ("single_yang_holds.py", 20),
    "lab_dapandibufantan": ("dapandibufantan.py", 20),
    "lab_strong_breakout_consolidation": ("strong_breakout_consolidation.py", 15),
    "lab_surge_accel_pullback": ("surge_accel_pullback.py", 15),
    "lab_surge_bias_pullback": ("surge_bias_pullback.py", 13),
    "lab_crash_day_dip": ("crash_day_dip.py", 21),
    "lab_volume_peak_pullback": ("volume_peak_pullback.py", 15),
}

CUSTOM_DIR = Path(__file__).resolve().parents[2] / "data" / "strategies" / "custom"


def test_gf_000610_strategies_load_through_existing_engine():
    engine = StrategyEngine(strategy_dirs=[CUSTOM_DIR])

    for strategy_id, (filename, lookback) in STRATEGY_IDS.items():
        assert engine.has(strategy_id), filename
        strategy = engine.get(strategy_id)
        assert strategy.execution_backend == "python_history_legacy"
        assert strategy.lookback_days == lookback
        assert strategy.filter_history_fn is not None
        assert strategy.file_path == CUSTOM_DIR / filename

    assert not [
        error for error in engine.load_errors()
        if Path(error["file"]).name in {filename for filename, _ in STRATEGY_IDS.values()}
    ]


def test_gf_000610_source_formulas_accept_target_history_frame():
    engine = StrategyEngine(strategy_dirs=[CUSTOM_DIR])
    rows = []
    start = date(2026, 1, 1)
    for i in range(30):
        close = 100.0 + i * 0.2
        rows.append({
            "symbol": "000001.SZ",
            "date": start + timedelta(days=i),
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1_000_000.0 + i * 1_000,
            "amount": 100_000_000.0 + i * 100_000,
            "change_pct": 0.002,
            "ma20": close - 0.3,
            "boll_lower": close - 3.0,
            "float_shares": 2_000_000_000.0,
        })
    panel = pl.DataFrame(rows)

    for strategy_id in STRATEGY_IDS:
        strategy = engine.get(strategy_id)
        result = strategy.filter_history_fn(panel, {})
        assert isinstance(result, pl.DataFrame), strategy_id
        assert "symbol" in result.columns and "date" in result.columns
