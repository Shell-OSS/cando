# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Placement utility helpers for generator tools.

Placement utility helpers for geometric calculations and action guards.
"""

import copy
import math
from typing import Literal

from shapely.geometry import LineString

from cando.utils.geometry import calculate_centroid, calculate_edge_length

from .geometry import (
    clamp_ratio,
    polygon_extent_along,
    translate_polygon,
    update_bbox,
)
from .validation import is_within_layout_bounds


def _axis_span_for_side(
    side: Literal["top", "bottom", "left", "right"],
    width: float,
    height: float,
) -> float:
    """Returns the span along the stacking axis for an element."""

    return width if side in ("top", "bottom") else height


def interpolate_along_edge(
    edge_coords: list[list[float]], ratio: float, clamp: bool = True
) -> tuple[float, float, tuple[float, float]]:
    """Interpolates a point along an edge and get its tangent vector.

    Args:
        edge_coords (list[list[float]]): List of coordinates defining the
            edge.
        ratio (float): Position along edge (0 = start, 1 = end). When *clamp*
            is False, values outside [0, 1] are extrapolated linearly beyond
            the edge endpoints using the first / last segment direction.
        clamp (bool): If True (default), ratio is clamped to [0, 1] before
            use. Set to False to allow extrapolation for group-stacking
            scenarios where elements may legitimately extend past the edge
            boundary.

    Returns:
        Tuple of (x, y, (tangent_x, tangent_y)) where tangent is the
            direction vector of the edge at that point.
    """
    if not edge_coords or len(edge_coords) < 2:
        return (0.0, 0.0, (1.0, 0.0))

    if clamp:
        ratio = clamp_ratio(ratio)

    line = LineString(edge_coords)
    total_length = line.length

    if total_length == 0:
        # Degenerate edge (single point)
        return (edge_coords[0][0], edge_coords[0][1], (1.0, 0.0))

    # Handle extrapolation beyond edge bounds
    if ratio < 0:
        # Extrapolate backwards from the start using first segment direction
        p1 = edge_coords[0]
        p2 = edge_coords[1]
        seg_len = math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
        if seg_len > 0:
            tx = (p2[0] - p1[0]) / seg_len
            ty = (p2[1] - p1[1]) / seg_len
        else:
            tx, ty = 1.0, 0.0
        overshoot = ratio * total_length  # negative
        return (p1[0] + tx * overshoot, p1[1] + ty * overshoot, (tx, ty))

    if ratio > 1:
        # Extrapolate forward from the end using last segment direction
        p1 = edge_coords[-2]
        p2 = edge_coords[-1]
        seg_len = math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
        if seg_len > 0:
            tx = (p2[0] - p1[0]) / seg_len
            ty = (p2[1] - p1[1]) / seg_len
        else:
            tx, ty = 1.0, 0.0
        overshoot = (ratio - 1) * total_length  # positive
        return (p2[0] + tx * overshoot, p2[1] + ty * overshoot, (tx, ty))

    # Interpolate along the line
    target_distance = ratio * total_length
    point = line.interpolate(target_distance)

    # Find which segment the point is on and get its direction
    cumulative_dist = 0.0
    for i in range(len(edge_coords) - 1):
        p1 = edge_coords[i]
        p2 = edge_coords[i + 1]
        segment_length = math.sqrt(
            (p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2
        )

        if cumulative_dist + segment_length >= target_distance - 1e-9:
            # This is the segment containing our point
            if segment_length > 0:
                tangent_x = (p2[0] - p1[0]) / segment_length
                tangent_y = (p2[1] - p1[1]) / segment_length
            else:
                tangent_x, tangent_y = 1.0, 0.0
            return (point.x, point.y, (tangent_x, tangent_y))

        cumulative_dist += segment_length

    # Fallback: use last segment direction
    p1 = edge_coords[-2]
    p2 = edge_coords[-1]
    segment_length = math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
    if segment_length > 0:
        tangent_x = (p2[0] - p1[0]) / segment_length
        tangent_y = (p2[1] - p1[1]) / segment_length
    else:
        tangent_x, tangent_y = 1.0, 0.0

    return (point.x, point.y, (tangent_x, tangent_y))


def calculate_stack_position(
    edge_point: tuple[float, float, tuple[float, float]],
    side: Literal["top", "bottom", "left", "right"],
    distance: float,
    elem_width: float,
    elem_height: float,
    is_layout_boundary: bool,
    polygon_coords: list[list[float]] | None = None,
    source_polygon: list[list[float]] | None = None,
) -> tuple[float, float]:
    """Calculates final position for a stacked element.

    Args:
        edge_point (tuple[float, float, tuple[float, float]]): Position and
            direction on the target edge.
        side (Literal["top", "bottom", "left", "right"]): Side of the target
            where stacking occurs.
        distance (float): Distance from the edge to place the element
        elem_width (float): Width of the element to stack (used when
            source_polygon is not provided).
        elem_height (float): Height of the element to stack (used when
            source_polygon is not provided).
        is_layout_boundary (bool): Whether target is the layout boundary
        polygon_coords (list[list[float]] | None): Optional target polygon
            coordinates for accurate outward normal selection.
        source_polygon (list[list[float]] | None): Optional source element
            polygon used to compute the exact half-extent along the normal
            direction.  When supplied the elem_width / elem_height parameters
            are ignored for offset computation, giving correct results for
            arbitrarily rotated elements.

    Returns:
        tuple[float, float]: Centroid position for the stacked element.
    """
    edge_x, edge_y, (tangent_x, tangent_y) = edge_point

    # Calculate both perpendicular (normal) vectors to the edge
    normal_x_opt1 = -tangent_y
    normal_y_opt1 = tangent_x
    normal_x_opt2 = tangent_y
    normal_y_opt2 = -tangent_x

    # Choose the correct normal direction
    if is_layout_boundary and polygon_coords and len(polygon_coords) >= 3:
        # For layout boundaries, we want to stack inward (into the polygon)
        from shapely.geometry import Point, Polygon

        poly = Polygon(polygon_coords)

        # Test both normal options with a reasonable distance
        test_dist = 1.0
        test_point1 = Point(
            edge_x + normal_x_opt1 * test_dist,
            edge_y + normal_y_opt1 * test_dist,
        )
        test_point2 = Point(
            edge_x + normal_x_opt2 * test_dist,
            edge_y + normal_y_opt2 * test_dist,
        )

        # Choose the normal that points inward (into the polygon)
        if poly.contains(test_point1):
            normal_x, normal_y = normal_x_opt1, normal_y_opt1
        elif poly.contains(test_point2):
            normal_x, normal_y = normal_x_opt2, normal_y_opt2
        else:
            # Neither clearly inside - use side-based heuristic
            if side == "top":
                normal_x, normal_y = 0.0, -1.0
            elif side == "bottom":
                normal_x, normal_y = 0.0, 1.0
            elif side == "left":
                normal_x, normal_y = 1.0, 0.0
            else:  # right
                normal_x, normal_y = -1.0, 0.0
    elif (
        not is_layout_boundary and polygon_coords and len(polygon_coords) >= 3
    ):
        # For regular elements, stack outward (away from the polygon)
        from shapely.geometry import Point, Polygon

        poly = Polygon(polygon_coords)

        # Test both normal options
        test_dist = 1.0
        test_point1 = Point(
            edge_x + normal_x_opt1 * test_dist,
            edge_y + normal_y_opt1 * test_dist,
        )
        test_point2 = Point(
            edge_x + normal_x_opt2 * test_dist,
            edge_y + normal_y_opt2 * test_dist,
        )

        # Choose the normal that points outward (away from polygon)
        if not poly.contains(test_point1):
            normal_x, normal_y = normal_x_opt1, normal_y_opt1
        elif not poly.contains(test_point2):
            normal_x, normal_y = normal_x_opt2, normal_y_opt2
        else:
            # Both inside - use side-based heuristic for outward
            if side == "top":
                normal_x, normal_y = 0.0, 1.0
            elif side == "bottom":
                normal_x, normal_y = 0.0, -1.0
            elif side == "left":
                normal_x, normal_y = -1.0, 0.0
            else:  # right
                normal_x, normal_y = 1.0, 0.0
    else:
        # Fallback: use simple side-based heuristic
        if side == "top":
            normal_x, normal_y = (0.0, -1.0 if is_layout_boundary else 1.0)
        elif side == "bottom":
            normal_x, normal_y = (0.0, 1.0 if is_layout_boundary else -1.0)
        elif side == "left":
            normal_x, normal_y = (1.0 if is_layout_boundary else -1.0, 0.0)
        else:  # right
            normal_x, normal_y = (-1.0 if is_layout_boundary else 1.0, 0.0)

    # Calculate offset distance including element size.
    if source_polygon and len(source_polygon) >= 3:
        sx = sum(pt[0] for pt in source_polygon) / len(source_polygon)
        sy = sum(pt[1] for pt in source_polygon) / len(source_polygon)
        centroid_proj = normal_x * sx + normal_y * sy
        half_extent = (
            max(normal_x * pt[0] + normal_y * pt[1] for pt in source_polygon)
            - centroid_proj
        )
        offset_dist = distance + half_extent
    else:
        elem_half_width = elem_width / 2
        elem_half_height = elem_height / 2
        offset_dist = distance + max(
            abs(normal_x) * elem_half_width
            + abs(normal_y) * elem_half_height,
            elem_half_width
            if abs(normal_x) > abs(normal_y)
            else elem_half_height,
        )

    # Apply offset along normal
    new_x = edge_x + normal_x * offset_dist
    new_y = edge_y + normal_y * offset_dist

    return (new_x, new_y)


def find_available_position_single(
    edge_coords: list[list[float]],
    side: Literal["top", "bottom", "left", "right"],
    distance: float,
    elem_width: float,
    elem_height: float,
    other_polygons: list,
    is_layout_boundary: bool,
    polygon_coords: list[list[float]] | None = None,
    source_polygon: list[list[float]] | None = None,
) -> float:
    """Finds first non-overlapping position along an edge for a single
    element.

    Args:
        edge_coords (list[list[float]]): List of [x, y] coordinates defining
            the edge.
        side (Literal["top", "bottom", "left", "right"]): Side of the target.
        distance (float): Distance from edge.
        elem_width (float): Width of element to place.
        elem_height (float): Height of element to place.
        other_polygons (list): List of shapely Polygon objects to avoid.
        is_layout_boundary (bool): Whether target is layout boundary.
        polygon_coords (list[list[float]] | None): Optional full polygon
            coordinates for accurate normal calculation.
        source_polygon (list[list[float]] | None): Optional pre-rotated source
            polygon used for accurate collision detection.  When supplied the
            actual polygon shape (translated to each candidate centroid) is
            tested instead of an axis-aligned bounding box, giving correct
            results for arbitrarily rotated elements.  When ``None`` the
            existing axis-aligned box is used (axis-aligned behaviour is
            unchanged).

    Returns:
        float: Ratio (0-1) along edge for placement
    """
    from shapely.geometry import Polygon as _Polygon
    from shapely.geometry import box

    # Try different positions along the edge
    num_samples = 20
    for i in range(num_samples):
        ratio = i / (num_samples - 1) if num_samples > 1 else 0.5

        # Get position on edge with tangent
        edge_point = interpolate_along_edge(edge_coords, ratio)

        # Calculate stacked position
        new_x, new_y = calculate_stack_position(
            edge_point,
            side,
            distance,
            elem_width,
            elem_height,
            is_layout_boundary,
            polygon_coords,
        )

        # Build candidate shape: use actual pre-rotated polygon when provided
        # for accurate collision detection of rotated elements; otherwise fall
        # back to the axis-aligned bounding box (existing behaviour).
        if source_polygon and len(source_polygon) >= 3:
            src_cx, src_cy = calculate_centroid(source_polygon)
            dx_t = new_x - src_cx
            dy_t = new_y - src_cy
            candidate = _Polygon(
                [[pt[0] + dx_t, pt[1] + dy_t] for pt in source_polygon]
            )
        else:
            candidate = box(
                new_x - elem_width / 2,
                new_y - elem_height / 2,
                new_x + elem_width / 2,
                new_y + elem_height / 2,
            )

        # Check for overlaps
        has_overlap = any(
            candidate.intersects(poly) and not candidate.touches(poly)
            for poly in other_polygons
        )

        if not has_overlap:
            return ratio

    # If no free space found, return center
    return 0.5


def find_available_position_group(
    edge_coords: list[list[float]],
    side: Literal["top", "bottom", "left", "right"],
    distance: float,
    elements_data: list[dict],
    spacing: float,
    other_polygons: list,
    is_layout_boundary: bool,
    polygon_coords: list[list[float]] | None = None,
    tangent: tuple[float, float] | None = None,
    source_polygons: list[list[list[float]]] | None = None,
) -> float:
    """Find first non-overlapping position for a group of elements.

    Args:
        edge_coords (list[list[float]]): List of [x, y] coordinates defining
            the edge.
        side (Literal["top", "bottom", "left", "right"]): Side of the target.
        distance (float): Distance from edge.
        elements_data (list[dict]): List of dicts with 'width' and 'height'
            for each element.
        spacing (float): Spacing between elements.
        other_polygons (list): List of shapely Polygon objects to avoid.
        is_layout_boundary (bool): Whether target is layout boundary.
        polygon_coords (list[list[float]] | None): Optional full polygon
            coordinates for accurate normal calculation.
        tangent (tuple[float, float] | None): Optional pre-computed edge
            tangent vector ``(tx, ty)``.  When supplied together with
            *source_polygons* the element spans are computed by projecting
            each pre-rotated polygon onto this direction, giving correct
            results for tilted edges.  When ``None`` the axis-aligned
            ``_axis_span_for_side`` heuristic is used (existing behaviour).
        source_polygons (list | None): Optional list of pre-rotated source
            polygons (one per element in *elements_data*) used for accurate
            span computation and collision detection.  When ``None`` the
            existing axis-aligned bounding box is used.

    Returns:
        float: Ratio (0-1) along edge for group center placement
    """
    from shapely.geometry import Polygon as _Polygon
    from shapely.geometry import box

    # Calculate total group length using accurate projected spans when
    # tangent + pre-rotated polygons are available, otherwise fall back to
    # the axis-aligned heuristic (preserves existing axis-aligned behaviour).
    if tangent is not None and source_polygons:
        tx, ty = tangent
        group_length = sum(
            polygon_extent_along(sp, tx, ty) for sp in source_polygons
        ) + spacing * (len(source_polygons) - 1)
    else:
        group_length = sum(
            _axis_span_for_side(side, data["width"], data["height"])
            for data in elements_data
        ) + spacing * (len(elements_data) - 1)

    edge_length = calculate_edge_length(edge_coords)

    if edge_length == 0 or group_length > edge_length:
        return 0.5

    # Try different center positions
    num_samples = 15
    for i in range(num_samples):
        group_ratio = i / (num_samples - 1) if num_samples > 1 else 0.5

        # Calculate group start position
        group_center_offset = group_ratio * edge_length
        group_start_offset = group_center_offset - group_length / 2

        # Clamp to valid range
        group_end_offset = group_start_offset + group_length
        if group_start_offset < 0 or group_end_offset > edge_length:
            continue

        # Check if all elements in group fit without overlapping
        cursor_offset = group_start_offset
        all_fit = True

        for _idx, data in enumerate(elements_data):
            _src_poly = (
                source_polygons[_idx]
                if source_polygons and _idx < len(source_polygons)
                else None
            )
            if tangent is not None and _src_poly:
                elem_span = polygon_extent_along(
                    _src_poly, tangent[0], tangent[1]
                )
            else:
                elem_span = _axis_span_for_side(
                    side, data["width"], data["height"]
                )
            elem_center_offset = cursor_offset + elem_span / 2
            if edge_length > 0:
                elem_ratio = elem_center_offset / edge_length
            else:
                elem_ratio = 0.5
            elem_ratio = clamp_ratio(elem_ratio)

            # Get position with tangent
            edge_point = interpolate_along_edge(edge_coords, elem_ratio)
            new_x, new_y = calculate_stack_position(
                edge_point,
                side,
                distance,
                data["width"],
                data["height"],
                is_layout_boundary,
                polygon_coords,
            )

            # Build candidate shape: use actual polygon when provided.
            if _src_poly and len(_src_poly) >= 3:
                _src_cx, _src_cy = calculate_centroid(_src_poly)
                _dx_t = new_x - _src_cx
                _dy_t = new_y - _src_cy
                candidate = _Polygon(
                    [[pt[0] + _dx_t, pt[1] + _dy_t] for pt in _src_poly]
                )
            else:
                candidate = box(
                    new_x - data["width"] / 2,
                    new_y - data["height"] / 2,
                    new_x + data["width"] / 2,
                    new_y + data["height"] / 2,
                )

            if any(
                candidate.intersects(poly) and not candidate.touches(poly)
                for poly in other_polygons
            ):
                all_fit = False
                break

            cursor_offset += elem_span + spacing

        if all_fit:
            return group_ratio

    # If no position works, return center
    return 0.5


def move_element_to(
    layout_data: dict, element_id: int, target_x: float, target_y: float
) -> dict:
    """Moves element centroid to (target_x, target_y).

    Computes the required (dx, dy) offset from the current centroid and
    applies it directly.  Used by stack, align, and cluster functions that
    work with absolute target coordinates.

    Args:
        layout_data (dict): The layout data dictionary.
        element_id (int): ID of the element to move.
        target_x (float): Desired centroid x position.
        target_y (float): Desired centroid y position.

    Returns:
        dict: Updated layout data dictionary.
    """
    layout = copy.deepcopy(layout_data)
    element = layout.get(str(element_id))
    if element is None:
        raise ValueError(f"Element with id {element_id} not found in layout")

    polygon = element.get("polygon", [])
    cx, cy = calculate_centroid(polygon)
    dx = target_x - cx
    dy = target_y - cy

    new_polygon = translate_polygon(polygon, dx, dy)

    if not is_within_layout_bounds(
        layout_data, target_x, target_y, new_polygon
    ):
        return layout

    element["polygon"] = new_polygon
    element["bbox"] = update_bbox(new_polygon)

    return layout
