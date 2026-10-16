from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app.backtest.matrix import valid_shift
from app.strategy.engine import StrategyEngine


STRATEGY_PATH = (
    __import__("pathlib").Path(__file__).resolve().parents[2]
    / "data/strategies/custom/custom_despair_reversal_leader.py"
)


def _load_strategy_module():
    return StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy


def test_rolling_any_includes_first_bar_of_full_window():
    strategy = _load_strategy_module()
    mask = np.array([[True], [False], [False], [False], [False]], dtype=bool)

    result = strategy.__class__.__module__
    del result
    # Call the helper through the loaded module so the test covers the migrated file.
    import importlib.util

    spec = importlib.util.spec_from_file_location("custom_despair_test_module", STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    np.testing.assert_array_equal(module._rolling_any_2d(mask, 5), np.ones_like(mask, dtype=bool))


def test_strategy_contract_and_entry_exit_signals(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("custom_despair_signal_module", STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    rows, cols = 8, 1
    amount = np.array([[100.0]] * 5 + [[200.0], [200.0], [200.0]])
    boards = np.array([[0.0]] * 5 + [[2.0], [0.0], [0.0]])
    amplitude = np.array([[0.08]] * rows)
    prev_close = np.array([[10.0]] * rows)
    ma10 = np.array([[9.0]] * rows)
    momentum = np.zeros((rows, cols), dtype=float)
    open_ = np.array([[9.0]] * rows)
    close = np.array([[10.0]] * 5 + [[10.8], [9.5], [9.0]])

    features = {
        "amount": amount,
        "consecutive_limit_ups": boards,
        "amplitude": amplitude,
        "prev_close": prev_close,
        "ma10": ma10,
        "momentum_5d": momentum,
    }
    monkeypatch.setattr(module, "matrix_feature", lambda _market, name: features[name])
    monkeypatch.setattr(module, "shift", valid_shift)

    market = SimpleNamespace(
        shape=(rows, cols),
        amount=amount,
        open=open_,
        close=close,
        limit_up_locked=np.array([[False]] * 5 + [[True], [False], [False]]),
    )
    signals = module.MATRIX_STRATEGY.compute_signals(market, {})

    assert bool(signals.entry[5, 0])
    assert bool(signals.exit[7, 0])
    assert module.MATRIX_STRATEGY.required_warmup_bars({}) == 60
    assert module.MATRIX_STRATEGY.required_fields() == frozenset(
        {"consecutive_limit_ups", "amount", "open", "close"}
    )
