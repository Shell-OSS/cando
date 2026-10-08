# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Validation utility helpers for generator tools.

Validation utility helpers for geometric calculations and action guards.
"""

import logging

from shapely.geometry import Point, Polygon

from cando.constants.classes import INPUT_CLASSES

from ..models import (
    Action,
    AlignAction,
    AlignGroupAction,
    ClusterAction,
    MoveAction,
    MoveGroupAction,
    RotateAction,
    RotateGroupAction,
    StackAction,
    StackGroupAction,
)

logger = logging.getLogger(__name__)


def is_immovable_element(element: dict) -> bool:
    """Returns True when the element belongs to an immovable class."""

    return element.get("class") in INPUT_CLASSES


def check_action_involves_immovable_elements(
    layout_data: dict, action: Action
) -> tuple[bool, str]:
    """Returns (True, message) if the action targets an immovable element."""

    element_ids: list[int] = []

    if isinstance(action, (MoveAction, RotateAction)):
        element_ids = [action.element_id]
    elif isinstance(action, (StackAction, AlignAction)):
        element_ids = [action.element_id]
    elif isinstance(
        action,
        (
            ClusterAction,
            AlignGroupAction,
            StackGroupAction,
            MoveGroupAction,
            RotateGroupAction,
        ),
    ):
        element_ids = action.element_ids

    for elem_id in element_ids:
        elem = layout_data.get(str(elem_id))
        if elem and is_immovable_element(elem):
            return (
                True,
                "Skipping action {action_type} on element {elem_id} "
                "({elem_class}): Elements of class {elem_class} are fixed "
                "and cannot be moved or rotated".format(
                    action_type=action.type,
                    elem_id=elem_id,
                    elem_class=elem.get("class"),
                ),
            )

    return (False, "")


def is_within_layout_bounds(
    layout_data: dict,
    x: float,
    y: float,
    polygon_coords: list[list[float]] | None = None,
) -> bool:
    """Checks if a point or polygon lies within the layout boundary.

    When ``polygon_coords`` is provided, the full candidate polygon must be
    covered by the layout polygon. Otherwise only the point ``(x, y)`` is
    checked. Falls back to the layout bbox only when the polygon is missing
    or invalid.
    """

    layout_elem = None
    if layout_data.get("0", {}).get("class") == INPUT_CLASSES[0]:
        layout_elem = layout_data["0"]
    else:
        layout_elem = next(
            (
                elem
                for key, elem in layout_data.items()
                if (
                    isinstance(elem, dict)
                    and elem.get("class") == INPUT_CLASSES[0]
                )
            ),
            None,
        )

    if layout_elem is None:
        return False

    layout_polygon_coords = layout_elem.get("polygon", [])
    if layout_polygon_coords and len(layout_polygon_coords) >= 3:
        try:
            layout_polygon = Polygon(layout_polygon_coords)
            if layout_polygon.is_valid:
                if polygon_coords is not None:
                    candidate_polygon = Polygon(polygon_coords)
                    if candidate_polygon.is_valid:
                        return layout_polygon.covers(candidate_polygon)
                return layout_polygon.covers(Point(x, y))
        except Exception as exc:
            logger.debug(
                "Falling back to bbox check: invalid layout/candidate "
                "polygon (%s)",
                exc,
            )

    bbox = layout_elem.get("bbox", {})
    min_x = bbox.get("min_x", float("-inf"))
    min_y = bbox.get("min_y", float("-inf"))
    max_x = bbox.get("max_x", float("inf"))
    max_y = bbox.get("max_y", float("inf"))

    if polygon_coords is not None:
        return all(
            min_x <= point[0] <= max_x and min_y <= point[1] <= max_y
            for point in polygon_coords
        )

    return min_x <= x <= max_x and min_y <= y <= max_y
