# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for executing layout modification actions."""

import copy
import logging
import math
from typing import Literal

from cando.constants.classes import INPUT_CLASSES
from cando.utils.geometry import (
    calculate_centroid,
    calculate_edge_length,
    get_edge_by_type,
)

from .models import (
    Action,
    ActionSequence,
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
from .utils import (
    calculate_best_rotation_any_edge,
    calculate_rotation_to_align_edges,
    calculate_stack_position,
    check_action_involves_immovable_elements,
    clamp_ratio,
    extract_polygon_edge,
    find_available_position_group,
    find_available_position_single,
    get_bbox_edge_coords,
    get_edge_unit_vector,
    get_polygon_edge_coords_by_index,
    get_required_edge,
    infer_side_from_edge,
    interpolate_along_edge,
    is_within_layout_bounds,
    move_element_to,
    polygon_extent_along,
    rotate_polygon,
    translate_polygon,
    update_bbox,
)

logger = logging.getLogger(__name__)

# Requested rotations below this are treated as a no-op (rotate_element,
# rotate_group_element).
MIN_ROTATION_DEGREES = 0.1
# Computed alignment rotations below this are treated as floating-point
# noise and skipped rather than applied.
NEGLIGIBLE_ROTATION_DEGREES = 0.01


def move_element(
    layout_data: dict, element_id: int, dx: float, dy: float
) -> dict:
    """Translates an element by a (dx, dy) offset.

    Args:
        layout_data (dict): The layout data dictionary.
        element_id (int): ID of the element to move.
        dx (float): Translation along the x-axis in meters
            (positive = right, negative = left).
        dy (float): Translation along the y-axis in meters
            (positive = up, negative = down).

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If element_id is not found in layout.
    """
    layout = copy.deepcopy(layout_data)

    # Get the element directly from dict
    element = layout.get(str(element_id))
    if element is None:
        raise ValueError(f"Element with id {element_id} not found in layout")

    # Calculate new centroid position
    polygon = element.get("polygon", [])
    current_cx, current_cy = calculate_centroid(polygon)
    new_cx = current_cx + dx
    new_cy = current_cy + dy

    # Translate polygon
    new_polygon = translate_polygon(polygon, dx, dy)

    # Check if new position is within layout bounds
    if not is_within_layout_bounds(layout_data, new_cx, new_cy, new_polygon):
        # Skip the move
        return layout

    element["polygon"] = new_polygon

    # Update bounding box
    element["bbox"] = update_bbox(new_polygon)

    return layout


def rotate_element(
    layout_data: dict, element_id: int, degrees: float
) -> dict:
    """Rotates an element by specified degrees.

    Args:
        layout_data (dict): The layout data dictionary.
        element_id (int): ID of the element to rotate.
        degrees (float): Rotation angle in degrees (clockwise).

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If element_id is not found in layout.
    """
    if abs(degrees) < MIN_ROTATION_DEGREES:
        return layout_data

    layout = copy.deepcopy(layout_data)

    # Get the element directly from dict
    element = layout.get(str(element_id))
    if element is None:
        raise ValueError(f"Element with id {element_id} not found in layout")

    # Get polygon and calculate centroid
    polygon = element.get("polygon", [])
    cx, cy = calculate_centroid(polygon)

    # Rotate polygon around its centroid
    new_polygon = rotate_polygon(polygon, degrees, cx, cy)
    element["polygon"] = new_polygon

    # Update bounding box
    element["bbox"] = update_bbox(new_polygon)

    # Update rotation_deg (add to current rotation)
    current_rotation = element.get("rotation_deg", 0.0)
    element["rotation_deg"] = (current_rotation + degrees) % 360

    return layout


def _resolve_target_edge(
    target_element: dict,
    side: Literal["top", "bottom", "left", "right"] | None,
    target_edge_index: int | None,
) -> tuple[list[list[float]], Literal["top", "bottom", "left", "right"]]:
    """Resolves a concrete target edge and a coarse side label."""
    target_polygon_coords = target_element.get("polygon", [])
    target_bbox = target_element.get("bbox", {})

    if target_edge_index is not None:
        edge_coords = get_polygon_edge_coords_by_index(
            target_polygon_coords, target_edge_index
        )
        if not edge_coords:
            raise ValueError(
                "Target edge index {edge_index} is invalid for element "
                "{element_id}".format(
                    edge_index=target_edge_index,
                    element_id=target_element.get("id", "unknown"),
                )
            )
        effective_side = side or infer_side_from_edge(
            target_polygon_coords, edge_coords
        )
        return edge_coords, effective_side

    if side is None:
        raise ValueError(
            "Either side or target_edge_index must be provided for stacking"
        )

    edge_line = extract_polygon_edge(target_polygon_coords, side)

    if edge_line is None or edge_line.is_empty:
        edge_coords = get_bbox_edge_coords(target_bbox, side)
        edge_line = extract_polygon_edge(edge_coords, side)

    edge_coords_list = list(edge_line.coords) if edge_line else []
    if not edge_coords_list:
        edge_coords_list = get_bbox_edge_coords(target_bbox, side)

    return edge_coords_list, side


def stack_element(
    layout_data: dict,
    element_id: int,
    target_element_id: int,
    side: Literal["top", "bottom", "left", "right"] | None,
    distance: float,
    source_edge: Literal["longer", "shorter", "any"] = "any",
    location: Literal[
        "center", "available_space", "exact_location"
    ] = "center",
    location_ratio: float | None = None,
    target_edge_index: int | None = None,
) -> dict:
    """Stacks an element against another element's side or exact edge.

    Stacks the source element against the target element's specified side
    at the given distance. The position along the edge is determined by
    the location parameter.

    Args:
        layout_data (dict): The layout data dictionary.
        element_id (int): ID of the element to position (source).
        target_element_id (int): ID of the element to stack against (target).
        side (str | None): Optional coarse side of the target element to
            stack against ('top', 'bottom', 'left', 'right'). When
            target_edge_index is provided, side is only used as a fallback
            label for placement heuristics.
        distance (float): Distance between the source element and target edge.
        source_edge (str): Which edge of the source element to align:
            'longer' = use longer edge, 'shorter' = use shorter edge,
            'any' = automatic based on target side.
        location (str): Positioning mode along the target edge:
            - 'center': Position at the center of the edge
            - 'available_space': Find first available non-overlapping position
            - 'exact_location': Use location_ratio to specify position
        location_ratio (float | None): Ratio (0-1) for exact edge placement.
            0 = start of edge, 1 = end of edge, 0.5 = center.
        target_edge_index (int | None): Optional exact target polygon edge
            index, based on the target polygon vertex order.

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If element IDs are not found in layout.
    """
    from shapely.geometry import Polygon

    layout = copy.deepcopy(layout_data)

    # Get elements
    element = layout.get(str(element_id))
    target_element = layout.get(str(target_element_id))

    if element is None:
        raise ValueError(f"Element with id {element_id} not found in layout")
    if target_element is None:
        raise ValueError(
            f"Target element with id {target_element_id} not found in layout"
        )

    # Get element dimensions
    elem_bbox = element.get("bbox", {})
    elem_width = elem_bbox.get("max_x", 0) - elem_bbox.get("min_x", 0)
    elem_height = elem_bbox.get("max_y", 0) - elem_bbox.get("min_y", 0)

    # Get target polygon
    target_polygon_coords = target_element.get("polygon", [])
    edge_coords_list, effective_side = _resolve_target_edge(
        target_element, side, target_edge_index
    )

    # Determine position ratio along edge
    if location == "exact_location":
        edge_ratio = clamp_ratio(
            location_ratio if location_ratio is not None else 0.5
        )
    elif location == "available_space":
        # Collect other elements as shapely polygons for collision checking
        other_polygons = []
        for key, elem in layout.items():
            if (
                not isinstance(elem, dict)
                or key == str(element_id)
                or key == str(target_element_id)
            ):
                continue
            if "polygon" in elem:
                other_polygons.append(Polygon(elem["polygon"]))

        # Pre-rotate source element using center tangent as rotation proxy so
        # the scan uses the actual post-rotation polygon shape for collision
        # detection.  For axis-aligned edges the rotation is ~0° and the
        # polygon is unchanged, preserving existing behaviour.
        _center_ep = interpolate_along_edge(edge_coords_list, 0.5)
        _, _, (_ctx, _cty) = _center_ep
        if source_edge in ("longer", "shorter"):
            _ea, _eb = get_required_edge(element, source_edge)[0]
            _pre_rot = calculate_rotation_to_align_edges(_ea, _eb, _ctx, _cty)
        else:
            _pre_rot = calculate_best_rotation_any_edge(element, _ctx, _cty)
        if abs(_pre_rot) > NEGLIGIBLE_ROTATION_DEGREES:
            _src_cx, _src_cy = calculate_centroid(element.get("polygon", []))
            _pre_rotated = rotate_polygon(
                element.get("polygon", []), _pre_rot, _src_cx, _src_cy
            )
        else:
            _pre_rotated = element.get("polygon", [])

        edge_ratio = find_available_position_single(
            edge_coords_list,
            effective_side,
            distance,
            elem_width,
            elem_height,
            other_polygons,
            target_element.get("class") == INPUT_CLASSES[0],
            target_polygon_coords,
            _pre_rotated,
        )
    else:  # center
        edge_ratio = 0.5

    # Calculate position along edge
    edge_point = interpolate_along_edge(edge_coords_list, edge_ratio)

    # Extract tangent vector from edge_point
    _, _, (tangent_x, tangent_y) = edge_point

    # Calculate rotation to align the requested source edge with the target
    # edge direction.
    if source_edge in ("longer", "shorter"):
        edge_a, edge_b = get_required_edge(element, source_edge)[0]
        rotation_angle = calculate_rotation_to_align_edges(
            edge_a, edge_b, tangent_x, tangent_y
        )
    else:  # Pick the edge requiring least rotation
        rotation_angle = calculate_best_rotation_any_edge(
            element, tangent_x, tangent_y
        )

    if abs(rotation_angle) > NEGLIGIBLE_ROTATION_DEGREES:
        layout = rotate_element(layout, element_id, rotation_angle)
        # Update element reference after rotation
        element = layout.get(str(element_id))
        if element is None:
            raise ValueError(
                f"Element with id {element_id} not found in layout"
            )
        # Recalculate dimensions after rotation
        elem_bbox = element.get("bbox", {})
        elem_width = elem_bbox.get("max_x", 0) - elem_bbox.get("min_x", 0)
        elem_height = elem_bbox.get("max_y", 0) - elem_bbox.get("min_y", 0)

    # Calculate final position using the rotated source polygon for an exact
    # half-extent projection (works for any rotation angle).
    source_polygon = element.get("polygon", [])
    new_x, new_y = calculate_stack_position(
        edge_point,
        effective_side,
        distance,
        elem_width,
        elem_height,
        target_element.get("class") == INPUT_CLASSES[0],
        target_polygon_coords,
        source_polygon,
    )

    return move_element_to(layout, element_id, new_x, new_y)


def stack_group_elements(
    layout_data: dict,
    element_ids: list[int],
    target_element_id: int,
    side: Literal["top", "bottom", "left", "right"] | None,
    distance: float,
    source_edge: Literal["longer", "shorter", "any"] = "any",
    spacing: float = 0.0,
    location: Literal[
        "center", "available_space", "exact_location"
    ] = "center",
    location_ratio: float | None = None,
    target_edge_index: int | None = None,
) -> dict:
    """Stacks a group of elements together along a target side or edge.

    Stacks multiple elements against the target element's specified side,
    treating them as a group. Elements are distributed along the edge with
    the specified spacing between them.

    Args:
        layout_data (dict): The layout data dictionary.
        element_ids (list[int]): List of IDs of elements to stack as a group.
        target_element_id (int): ID of the element to stack against (target).
        side (str | None): Optional coarse side of the target element to
            stack against ('top', 'bottom', 'left', 'right'). When
            target_edge_index is provided, side is only used as a fallback
            label for placement heuristics.
        distance (float): Distance between the stacked elements and target
            edge.
        source_edge (str): Which edge of the source elements to align:
            'longer' = use longer edge, 'shorter' = use shorter edge,
            'any' = automatic based on target side.
        spacing (float): Gap in meters between the closest two points of each
            pair of consecutive elements in the group.
        location (str): Positioning mode for the group along the target edge:
            - 'center': Position group center at edge center
            - 'available_space': Find first available non-overlapping position
            - 'exact_location': Use location_ratio for group center placement
        location_ratio (float | None): Ratio (0-1) for exact edge placement of
            group center.
        target_edge_index (int | None): Optional exact target polygon edge
            index, based on the target polygon vertex order.

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If any element ID is not found in layout.
    """
    from shapely.geometry import Polygon

    layout = copy.deepcopy(layout_data)

    # Get target element
    target_element = layout.get(str(target_element_id))
    if target_element is None:
        raise ValueError(
            f"Target element with id {target_element_id} not found in layout"
        )

    # Get target polygon and edge
    target_polygon_coords = target_element.get("polygon", [])
    edge_coords, effective_side = _resolve_target_edge(
        target_element, side, target_edge_index
    )

    # Collect element data
    elements_data = []
    for elem_id in element_ids:
        element = layout.get(str(elem_id))
        if element is None:
            raise ValueError(f"Element with id {elem_id} not found in layout")

        bbox = element.get("bbox", {})
        width = bbox.get("max_x", 0) - bbox.get("min_x", 0)
        height = bbox.get("max_y", 0) - bbox.get("min_y", 0)

        elements_data.append(
            {"id": elem_id, "width": width, "height": height}
        )

    if not elements_data:
        return layout

    # Compute edge length upfront
    edge_length = calculate_edge_length(edge_coords)

    # Pre-rotate each element using the center tangent as a rotation proxy.
    # This ensures the available_space scan uses accurate post-rotation
    # polygon shapes for span and collision checks.  For axis-aligned edges
    # the rotation is ~0° and polygons are unchanged, preserving existing
    # behaviour.
    _center_ep = interpolate_along_edge(edge_coords, 0.5)
    _, _, (_ctx, _cty) = _center_ep
    _pre_rotated_polygons: list[list[list[float]]] = []
    for _data in elements_data:
        _elem = layout.get(str(_data["id"]))
        if _elem is None:
            raise ValueError(
                f"Element with id {_data['id']} not found in layout"
            )
        _polygon = _elem.get("polygon", [])
        if source_edge in ("longer", "shorter"):
            _ref_ea, _ref_eb = get_required_edge(_elem, source_edge)[0]
            _pre_rot = calculate_rotation_to_align_edges(
                _ref_ea, _ref_eb, _ctx, _cty
            )
        else:
            _pre_rot = calculate_best_rotation_any_edge(_elem, _ctx, _cty)
        if abs(_pre_rot) > NEGLIGIBLE_ROTATION_DEGREES:
            _pcx, _pcy = calculate_centroid(_polygon)
            _pre_rotated_polygons.append(
                rotate_polygon(_polygon, _pre_rot, _pcx, _pcy)
            )
        else:
            _pre_rotated_polygons.append(_polygon)

    # Determine group center position ratio
    if location == "exact_location":
        group_ratio = clamp_ratio(
            location_ratio if location_ratio is not None else 0.5
        )
    elif location == "available_space":
        # Collect other elements for collision checking
        other_polygons = []
        for key, elem in layout.items():
            if (
                not isinstance(elem, dict)
                or key in [str(eid) for eid in element_ids]
                or key == str(target_element_id)
            ):
                continue
            if "polygon" in elem:
                other_polygons.append(Polygon(elem["polygon"]))

        group_ratio = find_available_position_group(
            edge_coords,
            effective_side,
            distance,
            elements_data,
            spacing,
            other_polygons,
            target_element.get("class") == INPUT_CLASSES[0],
            target_polygon_coords,
            tangent=(_ctx, _cty),
            source_polygons=_pre_rotated_polygons,
        )
    else:  # center
        group_ratio = 0.5

    ref_edge_point = interpolate_along_edge(
        edge_coords, clamp_ratio(group_ratio)
    )
    _, _, (ref_tx, ref_ty) = ref_edge_point

    accurate_spans: list[float] = []
    for data in elements_data:
        element = layout.get(str(data["id"]))
        if element is None:
            raise ValueError(
                f"Element with id {data['id']} not found in layout"
            )
        polygon = element.get("polygon", [])
        if source_edge in ("longer", "shorter"):
            ref_ea, ref_eb = get_required_edge(element, source_edge)[0]
            ref_rot = calculate_rotation_to_align_edges(
                ref_ea, ref_eb, ref_tx, ref_ty
            )
        else:  # Pick the edge requiring least rotation
            ref_rot = calculate_best_rotation_any_edge(
                element, ref_tx, ref_ty
            )
        if abs(ref_rot) > NEGLIGIBLE_ROTATION_DEGREES:
            ref_cx, ref_cy = calculate_centroid(polygon)
            tmp_poly = rotate_polygon(polygon, ref_rot, ref_cx, ref_cy)
        else:
            tmp_poly = polygon
        accurate_spans.append(polygon_extent_along(tmp_poly, ref_tx, ref_ty))

    group_length = sum(accurate_spans) + spacing * (len(accurate_spans) - 1)

    # Calculate group start position along edge
    effective_location = location
    if edge_length > 0:
        group_center_offset = group_ratio * edge_length
        group_start_offset = group_center_offset - group_length / 2

        # If group doesn't fit, fall back to available_space
        if location == "exact_location" and (
            group_start_offset < 0
            or group_start_offset + group_length > edge_length
        ):
            effective_location = "available_space"
            # Collect other elements for collision checking
            other_polygons = []
            for key, elem in layout.items():
                if (
                    not isinstance(elem, dict)
                    or key in [str(eid) for eid in element_ids]
                    or key == str(target_element_id)
                ):
                    continue
                if "polygon" in elem:
                    other_polygons.append(Polygon(elem["polygon"]))
            group_ratio = find_available_position_group(
                edge_coords,
                effective_side,
                distance,
                elements_data,
                spacing,
                other_polygons,
                target_element.get("class") == INPUT_CLASSES[0],
                target_polygon_coords,
                tangent=(_ctx, _cty),
                source_polygons=_pre_rotated_polygons,
            )
            group_center_offset = group_ratio * edge_length
            group_start_offset = group_center_offset - group_length / 2

        if effective_location != "exact_location":
            group_start_offset = max(
                0, min(group_start_offset, edge_length - group_length)
            )
    else:
        group_start_offset = 0

    # Place each element in the group
    cursor_offset = group_start_offset
    for idx, data in enumerate(elements_data):
        elem_span_est = accurate_spans[idx]
        elem_center_offset = cursor_offset + elem_span_est / 2

        # Convert offset to ratio
        if edge_length > 0:
            elem_ratio = elem_center_offset / edge_length
        else:
            elem_ratio = 0.5

        allow_extrapolation = effective_location == "exact_location"
        if not allow_extrapolation:
            elem_ratio = clamp_ratio(elem_ratio)

        # Get position on edge
        edge_point = interpolate_along_edge(
            edge_coords, elem_ratio, clamp=not allow_extrapolation
        )

        # Extract tangent vector from edge_point
        _, _, (tangent_x, tangent_y) = edge_point

        # Calculate rotation to align the requested source edge
        element = layout.get(str(data["id"]))
        if element is None:
            raise ValueError(
                f"Element with id {data['id']} not found in layout"
            )
        if source_edge in ("longer", "shorter"):
            edge_a, edge_b = get_required_edge(element, source_edge)[0]
            rotation_angle = calculate_rotation_to_align_edges(
                edge_a, edge_b, tangent_x, tangent_y
            )
        else:  # Pick the edge requiring least rotation
            rotation_angle = calculate_best_rotation_any_edge(
                element, tangent_x, tangent_y
            )

        if abs(rotation_angle) > NEGLIGIBLE_ROTATION_DEGREES:
            layout = rotate_element(layout, data["id"], rotation_angle)
            # Update element reference after rotation
            element = layout.get(str(data["id"]))
            if element is None:
                raise ValueError(
                    f"Element with id {data['id']} not found in layout"
                )
            # Recalculate dimensions after rotation
            elem_bbox = element.get("bbox", {})
            elem_width = elem_bbox.get("max_x", 0) - elem_bbox.get("min_x", 0)
            elem_height = elem_bbox.get("max_y", 0) - elem_bbox.get(
                "min_y", 0
            )
        else:
            elem_width = data["width"]
            elem_height = data["height"]

        # Calculate final position using the rotated source polygon.
        source_polygon = layout.get(str(data["id"]), {}).get("polygon", [])
        new_x, new_y = calculate_stack_position(
            edge_point,
            effective_side,
            distance,
            elem_width,
            elem_height,
            target_element.get("class") == INPUT_CLASSES[0],
            target_polygon_coords,
            source_polygon,
        )

        layout = move_element_to(layout, data["id"], new_x, new_y)

        actual_span = polygon_extent_along(
            source_polygon, tangent_x, tangent_y
        )
        cursor_offset += actual_span + spacing

    return layout


def align_element(
    layout_data: dict,
    element_id: int,
    target_element_id: int,
    edge: Literal["shorter", "longer"],
    target_edge: Literal["shorter", "longer"] | None = None,
) -> dict:
    """Rotates an element so its `edge` becomes parallel to the target's edge.

    Computes the minimum rotation needed to make the source element's
    `edge`-type edges parallel to the target element's edge, then applies
    that rotation around the source element's centroid.  No translation is
    performed.

    If `target_edge` is provided the source `edge` is aligned to the target's
    `target_edge`-type edges instead of its same-type edges.

    Args:
        layout_data (dict): The layout data dictionary.
        element_id (int): ID of the element to rotate (source).
        target_element_id (int): ID of the element to align with (target).
        edge (Literal["shorter", "longer"]): Which edge type of the source
            element to align.
        target_edge (Literal["shorter", "longer"] | None): Which edge type of
            the target element to align to. If None, uses the same type as
            `edge`.

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If element IDs are not found in layout, or if the
            requested edge type cannot be extracted from either element.
    """
    source_element = layout_data.get(str(element_id))
    if source_element is None:
        raise ValueError(f"Element with id {element_id} not found in layout")

    target_element = layout_data.get(str(target_element_id))
    if target_element is None:
        raise ValueError(
            f"Target element with id {target_element_id} not found in layout"
        )

    # Extract the requested edge type from both elements
    effective_target_edge = target_edge if target_edge is not None else edge
    source_edges = get_edge_by_type(source_element, edge)
    target_edges = get_edge_by_type(target_element, effective_target_edge)

    if not source_edges:
        raise ValueError(
            f"Could not extract '{edge}' edges from element {element_id}"
        )
    if not target_edges:
        raise ValueError(
            f"Could not extract '{effective_target_edge}' edges from target "
            f"element {target_element_id}"
        )

    # Use the first representative edge from each element
    source_a, source_b = source_edges[0]
    target_a, target_b = target_edges[0]

    # Compute target edge tangent vector
    target_tangent_x = target_b[0] - target_a[0]
    target_tangent_y = target_b[1] - target_a[1]

    # Calculate the minimum rotation to make the source edge parallel to
    # target
    rotation_angle = calculate_rotation_to_align_edges(
        source_a, source_b, target_tangent_x, target_tangent_y
    )

    if abs(rotation_angle) < NEGLIGIBLE_ROTATION_DEGREES:
        return layout_data

    return rotate_element(layout_data, element_id, rotation_angle)


def cluster_elements(
    layout_data: dict,
    element_ids: list[int],
    formation: Literal["grid", "line"],
    edge: Literal["shorter", "longer"],
    spacing: float,
    rows: int | None = None,
    cols: int | None = None,
) -> dict:
    """Arranges multiple elements in a cluster formation.

    For `line` formation the elements are first aligned by `edge` type
    (so their selected edges become parallel) and then distributed along the
    direction *perpendicular* to those edges.  The `edge` faces the adjacent
    element in the line.

    For `grid` formation an explicit `rows` x `cols` layout is used.
    All elements are first rotated so their selected edges become parallel
    before being placed on the grid. The `edge` type defines the *row*
    neighbors: rows are separated perpendicular to the selected edge direction
    and columns run along it.

    In both cases `spacing` is the gap between the closest two points of
    each pair of consecutive / neighboring elements.

    Args:
        layout_data (dict): The layout data dictionary.
        element_ids (list[int]): List of element IDs to cluster.
        formation (Literal["grid", "line"]): `grid` or `line`.
        edge (Literal["shorter", "longer"]): Neighboring edge type
            (`shorter` or `longer`).
        spacing (float): Distance in meters between the closest two points of
            consecutive neighboring elements.
        rows (int | None): Number of rows (required for grid formation).
        cols (int | None): Number of columns (required for grid formation).

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If any element ID is not found in layout, or if `rows`
            / `cols` are missing or insufficient for a grid formation.
    """
    if not element_ids:
        return layout_data

    layout = copy.deepcopy(layout_data)
    for elem_id in element_ids:
        if layout.get(str(elem_id)) is None:
            raise ValueError(f"Element with id {elem_id} not found in layout")

    # Line formation
    if formation == "line":
        # Align all elements so their chosen edges become parallel
        layout = align_group_elements(layout, element_ids, edge)

        # Derive the edge tangent direction from the (now-aligned) first
        # element, then compute the perpendicular line direction
        edge_dx, edge_dy = get_edge_unit_vector(layout, element_ids[0], edge)

        # The cluster's line is perpendicular to the edge direction
        line_dx, line_dy = -edge_dy, edge_dx

        # Compute the current group centroid
        centroids = [
            calculate_centroid(layout[str(eid)]["polygon"])
            for eid in element_ids
        ]
        group_cx = sum(c[0] for c in centroids) / len(centroids)
        group_cy = sum(c[1] for c in centroids) / len(centroids)

        # Project group centroid onto line direction and onto edge direction
        group_line_proj = line_dx * group_cx + line_dy * group_cy
        group_edge_proj = edge_dx * group_cx + edge_dy * group_cy

        # Compute each element's half-extent along the line direction
        half_extents = [
            polygon_extent_along(
                layout[str(eid)]["polygon"], line_dx, line_dy
            )
            / 2
            for eid in element_ids
        ]

        # Compute total span and starting projection along line
        n = len(element_ids)
        total_span = sum(2 * he for he in half_extents) + spacing * (n - 1)
        start_proj = group_line_proj - total_span / 2

        # Place elements
        cursor = start_proj
        for i, elem_id in enumerate(element_ids):
            elem = layout[str(elem_id)]
            cx, cy = calculate_centroid(elem["polygon"])

            target_line = cursor + half_extents[i]  # centroid along line dir
            curr_line = line_dx * cx + line_dy * cy
            curr_edge = edge_dx * cx + edge_dy * cy

            delta_line = target_line - curr_line
            delta_edge = group_edge_proj - curr_edge

            dx_move = delta_line * line_dx + delta_edge * edge_dx
            dy_move = delta_line * line_dy + delta_edge * edge_dy

            layout = move_element(layout, elem_id, dx_move, dy_move)
            cursor += 2 * half_extents[i] + spacing

        return layout

    # Grid formation
    if formation == "grid":
        n = len(element_ids)

        if rows is None or cols is None:
            raise ValueError(
                "Both 'rows' and 'cols' must be provided for grid formation."
            )
        if rows * cols < n:
            raise ValueError(
                f"Grid size {rows}x{cols}={rows * cols} is smaller than "
                f"the number of elements ({n})."
            )

        # Align all elements so their chosen edges become parallel
        layout = align_group_elements(layout, element_ids, edge)

        # Determine row / column directions from the first element's edge
        edge_dx, edge_dy = get_edge_unit_vector(layout, element_ids[0], edge)
        # Row direction = perpendicular to edge (edge faces row neighbors)
        row_dx, row_dy = -edge_dy, edge_dx
        # Col direction = along the edge
        col_dx, col_dy = edge_dx, edge_dy

        # Gather per-element extents in row and col directions
        elem_row_extents = []
        elem_col_extents = []
        for elem_id in element_ids:
            polygon = layout[str(elem_id)]["polygon"]
            elem_row_extents.append(
                polygon_extent_along(polygon, row_dx, row_dy)
            )
            elem_col_extents.append(
                polygon_extent_along(polygon, col_dx, col_dy)
            )

        # Compute max extent per row and per column for table-style layout
        row_heights = []
        for r in range(rows):
            extents = [
                elem_row_extents[idx] for idx in range(n) if idx // cols == r
            ]
            row_heights.append(max(extents) if extents else 0.0)

        col_widths = []
        for c in range(cols):
            extents = [
                elem_col_extents[idx] for idx in range(n) if idx % cols == c
            ]
            col_widths.append(max(extents) if extents else 0.0)

        # Compute cumulative center positions along each axis
        row_centers = []
        cursor = 0.0
        for _r, rh in enumerate(row_heights):
            row_centers.append(cursor + rh / 2)
            cursor += rh + spacing
        total_row_span = cursor - spacing

        col_centers = []
        cursor = 0.0
        for _c, cw in enumerate(col_widths):
            col_centers.append(cursor + cw / 2)
            cursor += cw + spacing
        total_col_span = cursor - spacing

        # Align the grid so that its centre coincides with the current group
        # centroid projected onto the respective axes
        all_centroids = [
            calculate_centroid(layout[str(eid)]["polygon"])
            for eid in element_ids
        ]
        group_cx = sum(c[0] for c in all_centroids) / len(all_centroids)
        group_cy = sum(c[1] for c in all_centroids) / len(all_centroids)

        group_row_proj = row_dx * group_cx + row_dy * group_cy
        group_col_proj = col_dx * group_cx + col_dy * group_cy

        row_origin = group_row_proj - total_row_span / 2
        col_origin = group_col_proj - total_col_span / 2

        # Place each element at its grid (row, col) position
        for idx, elem_id in enumerate(element_ids):
            r = idx // cols
            c = idx % cols

            target_row = row_origin + row_centers[r]
            target_col = col_origin + col_centers[c]

            # Convert back to (x, y) coordinates
            target_x = target_row * row_dx + target_col * col_dx
            target_y = target_row * row_dy + target_col * col_dy

            layout = move_element_to(layout, elem_id, target_x, target_y)

        return layout

    return layout


def align_group_elements(
    layout_data: dict,
    element_ids: list[int],
    edge: Literal["shorter", "longer"],
    target_element_id: int | None = None,
    target_edge: Literal["shorter", "longer"] | None = None,
) -> dict:
    """Rotates each element in a group so their selected edges become
    parallel.

    Two modes:

    - **With target** (`target_element_id` is given): each element is
      individually aligned to the target using :func:`align_element`.
    - **Without target**: a median reference angle is computed from all
      `edge`-type edges across every element in the group, and each
      element is rotated to match that reference.

    When `target_edge` is provided and a `target_element_id` is given, each
    source element's `edge` is aligned to the target's `target_edge` instead
    of its same-type edges.

    Only rotation is applied; element positions are not changed.

    Args:
        layout_data (dict): The layout data dictionary.
        element_ids (list[int]): List of element IDs to align.
        edge (Literal["shorter", "longer"]): Which edge type of the source
            elements to align, `shorter` or `longer`.
        target_element_id (int | None): Optional ID of a target element to
            align to. If `None`, elements are aligned with each other using a
            median reference angle computed from the group.
        target_edge (Literal["shorter", "longer"] | None): Optional edge type
            of the target element to align to. If `None`, uses the same type
            as `edge`. Only relevant when `target_element_id` is provided.

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If any element ID is not found in layout, or if the
            requested edge type cannot be extracted from an element.
    """
    if not element_ids:
        return layout_data

    # Align each element to a target element
    if target_element_id is not None:
        layout = layout_data
        for elem_id in element_ids:
            layout = align_element(
                layout, elem_id, target_element_id, edge, target_edge
            )
        return layout

    # Align elements with each other via median angle
    edge_angles: list[float] = []
    for elem_id in element_ids:
        element = layout_data.get(str(elem_id))
        if element is None:
            raise ValueError(f"Element with id {elem_id} not found in layout")
        edges = get_edge_by_type(element, edge)
        if not edges:
            raise ValueError(
                f"Could not extract '{edge}' edges from element {elem_id}"
            )
        a, b = edges[0]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        angle = math.degrees(math.atan2(dy, dx)) % 180.0
        edge_angles.append(angle)

    # Compute median angle
    sorted_angles = sorted(edge_angles)
    n = len(sorted_angles)
    if n % 2 == 0:
        median_angle = (sorted_angles[n // 2 - 1] + sorted_angles[n // 2]) / 2
    else:
        median_angle = sorted_angles[n // 2]

    # Derive a unit tangent vector from the median angle
    rad = math.radians(median_angle)
    target_tangent_x = math.cos(rad)
    target_tangent_y = math.sin(rad)

    # Rotate each element to match the median angle
    layout = layout_data
    for elem_id in element_ids:
        element = layout.get(str(elem_id))
        if element is None:
            continue
        edges = get_edge_by_type(element, edge)
        if not edges:
            continue
        source_a, source_b = edges[0]
        rotation_angle = calculate_rotation_to_align_edges(
            source_a, source_b, target_tangent_x, target_tangent_y
        )
        if abs(rotation_angle) >= NEGLIGIBLE_ROTATION_DEGREES:
            layout = rotate_element(layout, elem_id, rotation_angle)

    return layout


def move_group_elements(
    layout_data: dict, element_ids: list[int], dx: float, dy: float
) -> dict:
    """Translates a group of elements by the same (dx, dy) offset.

    All elements are shifted by exactly the same vector, preserving the
    relative positions and orientations within the group.  Elements that
    would land outside the layout boundary are skipped with a warning.

    Args:
        layout_data (dict): The layout data dictionary.
        element_ids (list[int]): List of element IDs to move together.
        dx (float): Translation along the x-axis in meters
            (positive = right, negative = left).
        dy (float): Translation along the y-axis in meters
            (positive = up, negative = down).

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If any element ID is not found in layout.
    """
    layout = copy.deepcopy(layout_data)

    for elem_id in element_ids:
        element = layout.get(str(elem_id))
        if element is None:
            raise ValueError(f"Element with id {elem_id} not found in layout")

        polygon = element.get("polygon", [])
        cx, cy = calculate_centroid(polygon)
        new_cx = cx + dx
        new_cy = cy + dy

        new_polygon = translate_polygon(polygon, dx, dy)

        # Skip elements that would leave the layout bounds
        if not is_within_layout_bounds(
            layout_data, new_cx, new_cy, new_polygon
        ):
            logger.warning(
                "Skipping move_group action for element %s: target "
                "position (%.2f, %.2f) is outside layout boundaries",
                elem_id,
                new_cx,
                new_cy,
            )
            continue

        element["polygon"] = new_polygon
        element["bbox"] = update_bbox(new_polygon)

    return layout


def rotate_group_elements(
    layout_data: dict,
    element_ids: list[int],
    degrees: float,
    pivot: str = "individual",
) -> dict:
    """Rotate a group of elements by the same angle.

    Two pivot modes are supported:

    - `individual`: each element rotates around its own centroid,
      so only the orientations change and relative positions are preserved.
    - `group_centroid`: every element orbits around the shared
      centroid of the whole group; both positions *and* orientations are
      updated, keeping the group's internal geometry intact.

    Args:
        layout_data (dict): The layout data dictionary.
        element_ids (list[int]): List of element IDs to rotate together.
        degrees (float): Rotation angle in degrees (clockwise).
        pivot (str): `individual` or `group_centroid`.

    Returns:
        dict: Updated layout data dictionary.

    Raises:
        ValueError: If any element ID is not found in layout.
    """
    if abs(degrees) < MIN_ROTATION_DEGREES:
        return layout_data

    layout = copy.deepcopy(layout_data)

    # Collect current centroids to compute group centroid if needed
    centroids = []
    elements = []
    for elem_id in element_ids:
        element = layout.get(str(elem_id))
        if element is None:
            raise ValueError(f"Element with id {elem_id} not found in layout")
        polygon = element.get("polygon", [])
        cx, cy = calculate_centroid(polygon)
        centroids.append((cx, cy))
        elements.append(element)

    if not elements:
        return layout

    if pivot == "group_centroid":
        # Shared pivot = mean of all element centroids
        gx = sum(c[0] for c in centroids) / len(centroids)
        gy = sum(c[1] for c in centroids) / len(centroids)

        for element, (_cx, _cy) in zip(elements, centroids, strict=True):
            polygon = element.get("polygon", [])

            # Rotate element polygon around the group centroid
            new_polygon = rotate_polygon(polygon, degrees, gx, gy)
            element["polygon"] = new_polygon
            element["bbox"] = update_bbox(new_polygon)

            # Update rotation_deg
            current_rotation = element.get("rotation_deg", 0.0)
            element["rotation_deg"] = (current_rotation + degrees) % 360

    else:  # individual
        for element in elements:
            polygon = element.get("polygon", [])
            cx, cy = calculate_centroid(polygon)

            new_polygon = rotate_polygon(polygon, degrees, cx, cy)
            element["polygon"] = new_polygon
            element["bbox"] = update_bbox(new_polygon)

            current_rotation = element.get("rotation_deg", 0.0)
            element["rotation_deg"] = (current_rotation + degrees) % 360

    return layout


def execute_action(layout_data: dict, action: Action) -> dict:
    """Executes a single action on the layout.

    If the action involves immovable elements (INPUT_CLASSES), it will be
    skipped with a warning message and the layout will be returned unchanged.

    Args:
        layout_data (dict): The layout data dictionary.
        action (Action): The action to execute.

    Returns:
        dict: Updated layout data dictionary.
    """
    # Check if action involves immovable elements
    is_immovable, warning_msg = check_action_involves_immovable_elements(
        layout_data, action
    )
    if is_immovable:
        logger.warning(warning_msg)
        return layout_data

    # Execute action
    if isinstance(action, MoveAction):
        return move_element(
            layout_data, action.element_id, action.dx, action.dy
        )

    elif isinstance(action, RotateAction):
        return rotate_element(layout_data, action.element_id, action.degrees)

    elif isinstance(action, StackAction):
        return stack_element(
            layout_data,
            action.element_id,
            action.target_element_id,
            action.side,
            action.distance,
            action.source_edge,
            action.location,
            action.location_ratio,
            action.target_edge_index,
        )

    elif isinstance(action, StackGroupAction):
        return stack_group_elements(
            layout_data,
            action.element_ids,
            action.target_element_id,
            action.side,
            action.distance,
            action.source_edge,
            action.spacing,
            action.location,
            action.location_ratio,
            action.target_edge_index,
        )

    elif isinstance(action, AlignAction):
        return align_element(
            layout_data,
            action.element_id,
            action.target_element_id,
            action.edge,
            action.target_edge,
        )

    elif isinstance(action, ClusterAction):
        return cluster_elements(
            layout_data,
            action.element_ids,
            action.formation,
            action.edge,
            action.spacing,
            action.rows,
            action.cols,
        )

    elif isinstance(action, AlignGroupAction):
        return align_group_elements(
            layout_data,
            action.element_ids,
            action.edge,
            action.target_element_id,
            action.target_edge,
        )

    elif isinstance(action, MoveGroupAction):
        return move_group_elements(
            layout_data, action.element_ids, action.dx, action.dy
        )

    elif isinstance(action, RotateGroupAction):
        return rotate_group_elements(
            layout_data, action.element_ids, action.degrees, action.pivot
        )

    else:
        raise ValueError(f"Unknown action type: {type(action)}")


def execute_sequence(
    layout_data: dict, action_sequence: ActionSequence
) -> dict:
    """Executes a sequence of actions on the layout.

    Transforms the layout data to a flat dict format for efficient element
    lookup during action execution, then converts back to the original format.

    Args:
        layout_data (dict): The layout data dictionary with 'elements' list.
        action_sequence (ActionSequence): The sequence of actions to execute.

    Returns:
        dict: Updated layout data dictionary after applying all actions.
    """
    # Extract elements list and other properties
    elements_list = layout_data.get("elements", [])

    # Create flat dict with elements keyed by string ID
    layout = {str(elem["id"]): copy.deepcopy(elem) for elem in elements_list}

    for action in action_sequence.actions:
        try:
            layout = execute_action(layout, action)
        except Exception as e:
            # Log the error but continue with remaining actions
            logger.warning("Failed to execute action %s: %s", action.type, e)
            continue

    # Convert flat dict back to original format with elements list,
    # preserving all other top-level keys
    result = {k: v for k, v in layout_data.items() if k != "elements"}
    result["elements"] = list(layout.values())

    return result
