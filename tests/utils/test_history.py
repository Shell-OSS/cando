# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for src/cando/utils/history.py format_history_for_prompt()."""

from cando.utils.history import format_history_for_prompt


def _summary(iteration: int, **overrides) -> dict:
    summary = {
        "iteration": iteration,
        "score_before": 0.5,
        "score_after": 0.6,
        "score_delta": 0.1,
        "is_regression": False,
        "restored_from_iteration": None,
        "planner_actions": {},
        "winning_sequence": [],
        "category_deltas": {},
    }
    summary.update(overrides)
    return summary


def test_empty_history_returns_empty_string() -> None:
    assert format_history_for_prompt([]) == ""


def test_formats_basic_iteration_header() -> None:
    history = [{"summary": _summary(1), "layout_snapshot": {}}]
    text = format_history_for_prompt(history)
    assert "Iteration 1" in text
    assert "0.50" in text and "0.60" in text
    assert "+0.10" in text


def test_regression_tag_present() -> None:
    history = [
        {"summary": _summary(2, is_regression=True), "layout_snapshot": {}}
    ]
    text = format_history_for_prompt(history)
    assert "REGRESSION" in text


def test_restored_from_iteration_tag_present() -> None:
    history = [
        {
            "summary": _summary(3, restored_from_iteration=1),
            "layout_snapshot": {},
        }
    ]
    text = format_history_for_prompt(history)
    assert "PATIENCE EXHAUSTED (restored to iter 1)" in text


def test_window_truncates_to_most_recent_entries() -> None:
    history = [
        {"summary": _summary(i), "layout_snapshot": {}} for i in range(10)
    ]
    text = format_history_for_prompt(history, window=3)
    for i in range(7):
        assert f"Iteration {i} " not in text
    for i in range(7, 10):
        assert f"Iteration {i} " in text


def test_planner_suggestions_and_winning_sequence_rendered() -> None:
    history = [
        {
            "summary": _summary(
                4,
                planner_actions={"assembly": ["cluster A/B"]},
                winning_sequence=["move(1, dx=2, dy=0)"],
            ),
            "layout_snapshot": {},
        }
    ]
    text = format_history_for_prompt(history)
    assert "[assembly] cluster A/B" in text
    assert "move(1, dx=2, dy=0)" in text
