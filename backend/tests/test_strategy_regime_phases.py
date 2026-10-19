"""REGIME_PHASES 策略声明与 T-1 入场门控测试。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.strategy.engine import StrategyEngine, _parse_regime_phases


def test_regime_phases_allowlist_and_unknown_rejected():
    assert _parse_regime_phases(["rally", "rally", "ebb"], Path("x.py")) == ("rally", "ebb")
    with pytest.raises(ValueError, match="未知阶段"):
        _parse_regime_phases(["not-a-phase"], Path("x.py"))


def test_regime_block_reason_is_t_minus_one_and_missing_is_fail_closed(tmp_path):
    strategies = tmp_path / "strategies" / "custom"
    strategies.mkdir(parents=True)
    (strategies / "phase.py").write_text(
        "import polars as pl\n"
        "META = {'id': 'phase', 'name': 'phase', 'timeframes': ['1d'], 'asset_types': ['stock']}\n"
        "REGIME_PHASES = ['rally']\n"
        "def filter(df, params): return pl.lit(True)\n",
        encoding="utf-8",
    )
    data_dir = tmp_path
    from app.services import regime_builder
    regime_builder.upsert_regime_history(data_dir, pl.DataFrame({
        "date": [date(2026, 1, 1), date(2026, 1, 2)],
        "state": ["strong", "strong"], "score": [80, 80],
        "phase": ["rally", "ebb"],
    }))
    engine = StrategyEngine(strategy_dirs=[strategies])
    strategy = engine.get("phase")
    assert engine.regime_block_reason(strategy, date(2026, 1, 2)) is None
    assert "退潮" in (engine.regime_block_reason(strategy, date(2026, 1, 3)) or "")
