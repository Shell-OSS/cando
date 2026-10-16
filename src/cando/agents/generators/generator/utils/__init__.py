# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Geometry, placement, and validation helpers for the Generator agent."""

from .geometry import (
    calculate_best_rotation_any_edge,
    calculate_rotation_to_align_edges,
    clamp_ratio,
    extract_polygon_edge,
    get_bbox_edge_coords,
    get_edge_unit_vector,
    get_polygon_edge_coords_by_index,
    get_required_edge,
    infer_side_from_edge,
    polygon_extent_along,
    rotate_polygon,
    translate_polygon,
    update_bbox,
)
from .placement import (
    calculate_stack_position,
    find_available_position_group,
    find_available_position_single,
    interpolate_along_edge,
    move_element_to,
)
from .validation import (
    check_action_involves_immovable_elements,
    is_within_layout_bounds,
)

__all__ = [
    "calculate_best_rotation_any_edge",
    "calculate_rotation_to_align_edges",
    "clamp_ratio",
    "extract_polygon_edge",
    "get_bbox_edge_coords",
    "get_edge_unit_vector",
    "get_polygon_edge_coords_by_index",
    "get_required_edge",
    "infer_side_from_edge",
    "polygon_extent_along",
    "rotate_polygon",
    "translate_polygon",
    "update_bbox",
    "calculate_stack_position",
    "find_available_position_group",
    "find_available_position_single",
    "interpolate_along_edge",
    "move_element_to",
    "check_action_involves_immovable_elements",
    "is_within_layout_bounds",
]
