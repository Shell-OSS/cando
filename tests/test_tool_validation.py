# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for src/cando/agents/generators/generator/utils/validation.py."""

from cando.agents.generators.generator.models import MoveAction
from cando.agents.generators.generator.utils.validation import (
    check_action_involves_immovable_elements,
    is_immovable_element,
    is_within_layout_bounds,
)


def test_is_immovable_element_true_for_input_classes() -> None:
    assert is_immovable_element({"class": "Entrance"}) is True
    assert is_immovable_element({"class": "Layout Polygon"}) is True


def test_is_immovable_element_false_for_movable_class() -> None:
    assert is_immovable_element({"class": "Shop"}) is False


def _layout_data() -> dict:
    return {
        "0": {
            "class": "Layout Polygon",
            "polygon": [[0, 0], [0, 10], [10, 10], [10, 0]],
        }
    }


def test_point_within_layout_bounds_via_polygon() -> None:
    assert is_within_layout_bounds(_layout_data(), 5, 5) is True


def test_point_outside_layout_bounds_via_polygon() -> None:
    assert is_within_layout_bounds(_layout_data(), 50, 50) is False


def test_candidate_polygon_covered_by_layout() -> None:
    polygon_coords = [[1, 1], [1, 2], [2, 2], [2, 1]]
    assert (
        is_within_layout_bounds(
            _layout_data(), 0, 0, polygon_coords=polygon_coords
        )
        is True
    )


def test_candidate_polygon_not_covered_by_layout() -> None:
    polygon_coords = [[9, 9], [9, 20], [20, 20], [20, 9]]
    assert (
        is_within_layout_bounds(
            _layout_data(), 0, 0, polygon_coords=polygon_coords
        )
        is False
    )


def test_falls_back_to_bbox_when_polygon_missing() -> None:
    layout_data = {
        "0": {
            "class": "Layout Polygon",
            "polygon": [],
            "bbox": {"min_x": 0, "min_y": 0, "max_x": 10, "max_y": 10},
        }
    }
    assert is_within_layout_bounds(layout_data, 5, 5) is True
    assert is_within_layout_bounds(layout_data, 50, 50) is False


def test_returns_false_when_no_layout_polygon_present() -> None:
    assert is_within_layout_bounds({}, 5, 5) is False


def test_check_action_flags_immovable_target() -> None:
    layout_data = {"1": {"class": "Entrance"}}
    action = MoveAction(element_id=1, dx=1.0, dy=1.0)

    flagged, message = check_action_involves_immovable_elements(
        layout_data, action
    )

    assert flagged is True
    assert "fixed" in message


def test_check_action_allows_movable_target() -> None:
    layout_data = {"1": {"class": "Shop"}}
    action = MoveAction(element_id=1, dx=1.0, dy=1.0)

    flagged, message = check_action_involves_immovable_elements(
        layout_data, action
    )

    assert flagged is False
    assert message == ""
