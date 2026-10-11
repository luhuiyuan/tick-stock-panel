from app.research.l2_expression_experiment import (
    build_expression,
    encode_segment,
    event_deduplicate,
    summarize_rows,
)


def _segment(state: str, duration: int, net_return: float) -> dict:
    return {"state": state, "duration": duration, "net_return": net_return}


def test_fine_amplitude_does_not_collapse_all_large_moves() -> None:
    assert encode_segment(_segment("UP", 20, 0.21), variant="state_amp", amplitude_scheme="fine") == "U_25"
    assert encode_segment(_segment("UP", 20, 0.49), variant="state_amp", amplitude_scheme="fine") == "U_50"
    assert encode_segment(_segment("UP", 20, 0.72), variant="state_amp", amplitude_scheme="fine") == "U_60"


def test_expression_can_omit_time_or_include_multiple_prior_segments() -> None:
    history = [_segment("DOWN", 40, -0.28), _segment("UP", 25, 0.34)]
    current = _segment("DOWN", 9, -0.11)
    assert build_expression(history, current, context_k=2, variant="state_amp", amplitude_scheme="fine") == "D_35,U_35#D_12"
    assert build_expression(history, current, context_k=2, variant="state_time_amp", amplitude_scheme="fine") == "D_20_35,U_20_35#D_10_12"


def test_event_deduplicate_keeps_one_row_per_current_l2_segment() -> None:
    rows = [
        {"symbol": "A", "date": "2024-01-03", "current_start_date": "2024-01-01", "variant": "state", "amplitude_scheme": "fine", "context_k": 1, "expression": "U#D"},
        {"symbol": "A", "date": "2024-01-04", "current_start_date": "2024-01-01", "variant": "state", "amplitude_scheme": "fine", "context_k": 1, "expression": "U#D"},
    ]
    assert [row["date"] for row in event_deduplicate(rows)] == ["2024-01-03"]


def test_summary_reports_direction_and_conditional_means() -> None:
    rows = [
        {"symbol": "A", "variant": "state", "amplitude_scheme": "fine", "context_k": 1, "expression": "U#D", "future_return_20d": 0.10},
        {"symbol": "B", "variant": "state", "amplitude_scheme": "fine", "context_k": 1, "expression": "U#D", "future_return_20d": -0.05},
    ]
    result = summarize_rows(rows, horizon=20)[0]
    assert result["sample_count"] == 2
    assert result["positive_probability"] == 0.5
    assert result["positive_mean"] == 0.10
    assert result["negative_mean"] == -0.05
