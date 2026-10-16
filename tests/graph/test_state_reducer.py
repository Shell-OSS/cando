# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for the `completed_branch_states` reducer in
src/cando/graph/state.py."""

from cando.graph.state import _branches_reducer


def test_reducer_resets_to_empty_list_on_none_update() -> None:
    """reduce_node returns None for completed_branch_states to reset the
    accumulator ahead of the next fan-out round."""
    assert _branches_reducer(["stale", "data"], None) == []


def test_reducer_appends_update_to_existing() -> None:
    existing = [{"branch_id": 0}]
    update = [{"branch_id": 1}]
    assert _branches_reducer(existing, update) == [
        {"branch_id": 0},
        {"branch_id": 1},
    ]


def test_reducer_does_not_mutate_existing_list_in_place() -> None:
    existing = [{"branch_id": 0}]
    result = _branches_reducer(existing, [{"branch_id": 1}])
    assert existing == [{"branch_id": 0}]
    assert result is not existing


def test_reducer_starting_from_empty_list() -> None:
    assert _branches_reducer([], [{"branch_id": 0}]) == [{"branch_id": 0}]
