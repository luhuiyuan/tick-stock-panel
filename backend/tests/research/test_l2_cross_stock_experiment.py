from scripts.run_l2_cross_stock_experiment import _split_rows, _split_symbols, _stats


def test_split_symbols_is_disjoint_and_deterministic() -> None:
    symbols = [f"S{i:02d}" for i in range(8)]
    train_a, test_a = _split_symbols(symbols, 0.25)
    train_b, test_b = _split_symbols(list(reversed(symbols)), 0.25)
    assert train_a == train_b
    assert test_a == test_b
    assert not set(train_a) & set(test_a)
    assert set(train_a) | set(test_a) == set(symbols)
    assert len(test_a) == 2


def test_split_rows_keeps_train_prefix_and_unseen_stock_test_tail() -> None:
    rows = [
        {"symbol": "TRAIN", "date": "2024-01-01"},
        {"symbol": "TRAIN", "date": "2024-01-11"},
        {"symbol": "TEST", "date": "2024-01-01"},
        {"symbol": "TEST", "date": "2024-01-11"},
    ]
    train, test = _split_rows(rows, {"TRAIN"}, {"TRAIN": "2024-01-05", "TEST": "2024-01-05"})
    assert [row["date"] for row in train] == ["2024-01-01"]
    assert [row["date"] for row in test] == ["2024-01-11"]


def test_stats_ignores_unavailable_future_labels() -> None:
    rows = [
        {"future_return_20d": 0.1},
        {"future_return_20d": -0.05},
        {"future_return_20d": None},
    ]
    result = _stats(rows, 20)
    assert result["sample_count"] == 2
    assert result["positive_probability"] == 0.5
    assert result["average_return"] == 0.025
