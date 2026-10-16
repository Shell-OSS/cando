# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Geometry utility helpers for generator tools.

Geometry utility helpers for geometric calculations and action guards.
"""

import math
from typing import Literal

from shapely.geometry import LineString

from cando.utils.geometry import get_edge_by_type


def clamp_ratio(value: float) -> float:
    """Clamps a ratio to the [0, 1] interval."""

    return max(0.0, min(1.0, value))


def translate_polygon(
    polygon: list[list[float]], dx: float, dy: float
) -> list[list[float]]:
    """Translates all polygon vertices by the given offsets."""

    return [[pt[0] + dx, pt[1] + dy] for pt in polygon]


def rotate_polygon(
    polygon: list[list[float]],
    angle_deg: float,
    center_x: float,
    center_y: float,
) -> list[list[float]]:
    """Rotates a polygon around a center point by angle_deg clockwise."""

    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    rotated = []
    for pt in polygon:
        x = pt[0] - center_x
        y = pt[1] - center_y
        x_new = x * cos_a + y * sin_a
        y_new = -x * sin_a + y * cos_a
        rotated.append([x_new + center_x, y_new + center_y])

    return rotated


def update_bbox(polygon: list[list[float]]) -> dict:
    """Returns an axis-aligned bounding box for the given polygon."""

    if not polygon:
        return {"min_x": 0, "min_y": 0, "max_x": 0, "max_y": 0}

    xs = [pt[0] for pt in polygon]
    ys = [pt[1] for pt in polygon]

    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
    }


def polygon_extent_along(
    polygon: list[list[float]], dx: float, dy: float
) -> float:
    """Returns the full span of a polygon projected onto unit vector (dx, dy).

    Args:
        polygon (list[list[float]]): List of [x, y] coordinate pairs.
        dx (float): x-component of the unit direction vector.
        dy (float): y-component of the unit direction vector.

    Returns:
        float: The difference between the maximum and minimum projections.
    """
    projections = [dx * pt[0] + dy * pt[1] for pt in polygon]
    return max(projections) - min(projections)


def get_required_edge(
    element: dict,
    edge_type: Literal["longer", "shorter", "any"],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Returns element's edge(s) of the given type, raising if degenerate.

    Thin wrapper around :func:`~cando.utils.geometry.get_edge_by_type` for
    call sites that require at least one matching edge to proceed (as
    opposed to :func:`get_edge_unit_vector`, which falls back gracefully).

    Args:
        element (dict): Element dict with a `polygon` key.
        edge_type (Literal["longer", "shorter", "any"]): Edge type to select.

    Returns:
        list[tuple[tuple[float, float], tuple[float, float]]]: Matching
        edge segments as `((x1, y1), (x2, y2))` tuples.

    Raises:
        ValueError: If the element's polygon is degenerate (fewer than 3
            vertices) or has no edges of the requested type.
    """
    edges = get_edge_by_type(element, edge_type)
    if edges is None:
        raise ValueError(
            f"Element with id {element.get('id', '?')} has an invalid or "
            f"degenerate polygon: cannot determine '{edge_type}' edge"
        )
    return edges


def get_edge_unit_vector(
    layout: dict, element_id: int, edge: Literal["shorter", "longer"]
) -> tuple[float, float]:
    """Returns the normalised tangent vector of the first matching edge.

    The direction vector encodes both the orientation of the element's
    selected edge and is used to define the row/column or line axes in
    cluster operations: the *line* (or row-separation) direction is the
    perpendicular of this vector, and the *column* (or along-edge) direction
    is the vector itself.

    Falls back to (1.0, 0.0) if the edge type cannot be extracted.

    Args:
        layout (dict): The flat layout dict keyed by string element ID.
        element_id (int): ID of the element whose edge direction is queried.
        edge (str): `'shorter'` or `'longer'`.

    Returns:
        tuple[float, float]: Normalised (dx, dy) tangent of the first matching
            edge.
    """
    element = layout.get(str(element_id))
    if element is None:
        return (1.0, 0.0)
    edges = get_edge_by_type(element, edge)
    if not edges:
        return (1.0, 0.0)
    (ax, ay), (bx, by) = edges[0]
    raw_dx, raw_dy = bx - ax, by - ay
    length = math.sqrt(raw_dx**2 + raw_dy**2)
    if length < 1e-9:
        return (1.0, 0.0)
    return (raw_dx / length, raw_dy / length)


def extract_polygon_edge(
    polygon_coords: list[list[float]],
    side: Literal["top", "bottom", "left", "right"],
) -> LineString | None:
    """Extracts the polygon edge whose outward normal best matches the side.

    Works for arbitrary (non-rectangular) polygons by finding the edge
    segment whose outward-facing normal is most aligned with the requested
    direction.

    `side` maps to a desired outward normal direction:
    - `left`   → `(-1, 0)`
    - `right`  → `(1, 0)`
    - `top`    → `(0, 1)`
    - `bottom` → `(0, -1)`

    Args:
        polygon_coords (list[list[float]]): List of coordinate pairs.
        side (Literal["top", "bottom", "left", "right"]): Which side of the
            polygon to extract.

    Returns:
        LineString | None: `LineString` of the best-matching edge, or
        `None` for degenerate input.
    """
    if not polygon_coords or len(polygon_coords) < 3:
        return None

    # Desired outward normal for each side
    desired: dict[str, tuple[float, float]] = {
        "left": (-1.0, 0.0),
        "right": (1.0, 0.0),
        "top": (0.0, 1.0),
        "bottom": (0.0, -1.0),
    }
    dx_desired, dy_desired = desired[side]

    # Polygon centroid (used to determine which perpendicular is outward)
    n = len(polygon_coords)
    cx = sum(pt[0] for pt in polygon_coords) / n
    cy = sum(pt[1] for pt in polygon_coords) / n

    best_score = float("-inf")
    best_edge: list[list[float]] | None = None

    for i in range(n):
        v1 = polygon_coords[i]
        v2 = polygon_coords[(i + 1) % n]

        edx = v2[0] - v1[0]
        edy = v2[1] - v1[1]
        length = math.sqrt(edx * edx + edy * edy)
        if length < 1e-9:
            continue

        # Two candidate outward normals (perpendiculars to the edge)
        norm1 = (-edy / length, edx / length)
        norm2 = (edy / length, -edx / length)

        # Midpoint of the edge
        mx = (v1[0] + v2[0]) / 2.0
        my = (v1[1] + v2[1]) / 2.0

        # Outward normal is the one pointing away from the centroid
        dot1 = norm1[0] * (mx - cx) + norm1[1] * (my - cy)
        outward = norm1 if dot1 >= 0 else norm2

        # Alignment with the requested side direction
        score = outward[0] * dx_desired + outward[1] * dy_desired

        if score > best_score:
            best_score = score
            best_edge = [v1, v2]

    if best_edge is None:
        return None

    return LineString(best_edge)


def get_polygon_edge_coords_by_index(
    polygon_coords: list[list[float]],
    edge_index: int,
) -> list[list[float]] | None:
    """Returns the polygon edge at the given index.

    Edge indices follow the polygon vertex order, so edge `i` spans from
    vertex `i` to vertex `(i + 1) % len(polygon_coords)`.
    """
    if len(polygon_coords) < 2:
        return None
    if edge_index < 0 or edge_index >= len(polygon_coords):
        return None

    return [
        polygon_coords[edge_index],
        polygon_coords[(edge_index + 1) % len(polygon_coords)],
    ]


def infer_side_from_edge(
    polygon_coords: list[list[float]],
    edge_coords: list[list[float]],
) -> Literal["top", "bottom", "left", "right"]:
    """Infers the closest cardinal side label for a concrete polygon edge.

    This is only used as a fallback label for existing stack placement
    helpers. Exact geometry still comes from the concrete edge coordinates.
    """
    if len(edge_coords) < 2:
        return "top"

    edge_mid_x = (edge_coords[0][0] + edge_coords[1][0]) / 2
    edge_mid_y = (edge_coords[0][1] + edge_coords[1][1]) / 2

    if len(polygon_coords) >= 3:
        centroid_x = sum(pt[0] for pt in polygon_coords) / len(polygon_coords)
        centroid_y = sum(pt[1] for pt in polygon_coords) / len(polygon_coords)
        delta_x = edge_mid_x - centroid_x
        delta_y = edge_mid_y - centroid_y
    else:
        delta_x = edge_coords[1][0] - edge_coords[0][0]
        delta_y = edge_coords[1][1] - edge_coords[0][1]

    if abs(delta_x) >= abs(delta_y):
        return "right" if delta_x >= 0 else "left"
    return "top" if delta_y >= 0 else "bottom"


def get_bbox_edge_coords(
    bbox: dict, side: Literal["top", "bottom", "left", "right"]
) -> list[list[float]]:
    """Gets edge coordinates from a bounding box as a polygon edge.

    Args:
        bbox (dict): Bounding box dictionary with min_x, max_x, min_y, max_y.
        side (Literal["top", "bottom", "left", "right"]): Which edge to
            extract.

    Returns:
        list[list[float]]: List of [x, y] coordinate pairs representing the
            edge.
    """
    min_x = bbox.get("min_x", 0)
    max_x = bbox.get("max_x", 0)
    min_y = bbox.get("min_y", 0)
    max_y = bbox.get("max_y", 0)

    if side == "top":
        return [[min_x, max_y], [max_x, max_y]]
    elif side == "bottom":
        return [[min_x, min_y], [max_x, min_y]]
    elif side == "left":
        return [[min_x, min_y], [min_x, max_y]]
    else:  # right
        return [[max_x, min_y], [max_x, max_y]]


def calculate_rotation_to_align_edges(
    source_edge_a: tuple[float, float],
    source_edge_b: tuple[float, float],
    target_tangent_x: float,
    target_tangent_y: float,
) -> float:
    """Calculates the rotation (degrees) to make edge AB parallel to a target.

    Because a line segment is invariant under a 180° flip, the returned angle
    is the *minimum* rotation, normalised to `[-90°, 90°]`.

    Args:
        source_edge_a (tuple[float, float]): Start point `(x, y)` of the
            source edge.
        source_edge_b (tuple[float, float]): End point `(x, y)` of the
            source edge.
        target_tangent_x (float): X component of the target edge tangent.
        target_tangent_y (float): Y component of the target edge tangent.

    Returns:
        float: Rotation in degrees to apply to the source element (clockwise
            positive, consistent with :func:`rotate_polygon`).  Returns `0.0`
            for degenerate edges or when already aligned.
    """
    dx = source_edge_b[0] - source_edge_a[0]
    dy = source_edge_b[1] - source_edge_a[1]

    # Degenerate source edge – nothing to align
    if math.sqrt(dx * dx + dy * dy) < 1e-9:
        return 0.0

    target_angle = math.degrees(
        math.atan2(target_tangent_y, target_tangent_x)
    )
    source_angle = math.degrees(math.atan2(dy, dx))
    rotation = source_angle - target_angle

    # Normalise to [-180°, 180°]
    rotation = rotation % 360.0
    if rotation > 180.0:
        rotation -= 360.0

    # Reduce to [-90°, 90°] – a line is invariant under 180° rotation
    if rotation > 90.0:
        rotation -= 180.0
    elif rotation < -90.0:
        rotation += 180.0

    return rotation


def calculate_best_rotation_any_edge(
    element: dict,
    target_tangent_x: float,
    target_tangent_y: float,
) -> float:
    """Returns the rotation that aligns the element's edge requiring the least
    turn.

    Tries both the longer and shorter edges of the element and returns
    whichever rotation has the smallest absolute value, i.e. the edge that is
    already closest to being parallel with the target direction.

    Args:
        element (dict): Element dict containing polygon geometry used by
            :func:`~cando.utils.geometry.get_edge_by_type`.
        target_tangent_x (float): X component of the target edge tangent.
        target_tangent_y (float): Y component of the target edge tangent.

    Returns:
        float: Rotation in degrees to apply (clockwise positive, normalised to
        `[-90°, 90°]`).
    """
    longer_ea, longer_eb = get_required_edge(element, "longer")[0]
    shorter_ea, shorter_eb = get_required_edge(element, "shorter")[0]
    rot_longer = calculate_rotation_to_align_edges(
        longer_ea, longer_eb, target_tangent_x, target_tangent_y
    )
    rot_shorter = calculate_rotation_to_align_edges(
        shorter_ea, shorter_eb, target_tangent_x, target_tangent_y
    )
    return rot_longer if abs(rot_longer) <= abs(rot_shorter) else rot_shorter
