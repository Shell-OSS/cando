# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Shared geometry utility functions.

Common polygon/geometry helpers shared across agents.
"""

import math
from math import isfinite
from statistics import mean
from typing import Literal

from shapely import Polygon


def calculate_centroid(polygon: list[list[float]]) -> tuple[float, float]:
    """Computes the centroid of a polygon defined by [x, y] pairs."""

    if not polygon:
        return (0.0, 0.0)

    x_sum = sum(pt[0] for pt in polygon)
    y_sum = sum(pt[1] for pt in polygon)
    n = len(polygon)

    return (x_sum / n, y_sum / n)


def calculate_edge_length(
    edge_coords: list[list[float]],
) -> float:
    """Calculates the length of an edge.

    Args:
        edge_coords (list[list[float]]): Coordinates defining the edges.

    Returns:
        float: Length of the edge
    """
    from shapely.geometry import LineString

    if not edge_coords or len(edge_coords) < 2:
        return 0.0

    line = LineString(edge_coords)
    return line.length


def normalize_vector(
    dx: float,
    dy: float,
) -> tuple[float, float] | None:
    """Returns a normalized 2D vector, or None for zero-length input."""
    magnitude = math.hypot(dx, dy)
    if magnitude == 0:
        return None
    return (dx / magnitude, dy / magnitude)


def calculate_edge_midpoint(
    edge: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[float, float]:
    """Returns the midpoint of an edge segment."""
    (x1, y1), (x2, y2) = edge
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def calculate_edge_projection_overlap(
    edge1: tuple[tuple[float, float], tuple[float, float]],
    edge2: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    """Returns the overlap length of two approximately parallel segments."""
    direction = normalize_vector(
        edge1[1][0] - edge1[0][0],
        edge1[1][1] - edge1[0][1],
    )
    if direction is None:
        return 0.0

    def project_point(point: tuple[float, float]) -> float:
        return point[0] * direction[0] + point[1] * direction[1]

    edge1_values = [project_point(edge1[0]), project_point(edge1[1])]
    edge2_values = [project_point(edge2[0]), project_point(edge2[1])]

    start = max(min(edge1_values), min(edge2_values))
    end = min(max(edge1_values), max(edge2_values))
    return max(0.0, end - start)


def get_edge_outward_normal(
    element: dict,
    edge: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[float, float] | None:
    """Returns the outward unit normal for an edge of an element polygon."""
    polygon = Polygon(element.get("polygon", []))
    if not polygon.is_valid:
        return None

    centroid = polygon.centroid
    midpoint = calculate_edge_midpoint(edge)
    edge_dx = edge[1][0] - edge[0][0]
    edge_dy = edge[1][1] - edge[0][1]

    candidate_normals = [
        normalize_vector(-edge_dy, edge_dx),
        normalize_vector(edge_dy, -edge_dx),
    ]

    best_normal = None
    best_alignment = float("-inf")
    for normal in candidate_normals:
        if normal is None:
            continue
        alignment = (midpoint[0] - centroid.x) * normal[0] + (
            midpoint[1] - centroid.y
        ) * normal[1]
        if alignment > best_alignment:
            best_alignment = alignment
            best_normal = normal

    return best_normal


def get_edge_by_type(
    element: dict,
    edge_type: Literal["longer", "shorter", "any"],
    threshold_percent: float = 0.15,
) -> list[tuple[tuple[float, float], tuple[float, float]]] | None:
    """Gets the edges of specified type from element polygon.

    Types: 'longer', 'shorter', or 'any'.

    For rectangular elements, identifies the 2 shorter and 2 longer edges
    using a relative threshold.  Because annotation polygons come from
    hand-drawn annotations the paired edges may not be exactly equal; edges
    within `threshold_percent` of the reference length are considered the
    same type.

    Args:
        element (dict): Element dictionary with a `'polygon'` key containing
            a list of `[x, y]` coordinate pairs.
        edge_type (Literal['longer', 'shorter', 'any']): Type of edge.
        threshold_percent (float): Relative threshold (0-1) for grouping
            edges.
            Default 0.15 means edges within 15 % of the reference length are
            considered the same type.

    Returns:
        list: List of edge segments as `((x1, y1), (x2, y2))` tuples, or
        `None` if the polygon is invalid or no matching edges are found.
    """
    polygon = element.get("polygon", [])
    if len(polygon) < 3:
        return None

    if edge_type == "any":
        edges = []
        for i in range(len(polygon)):
            p1 = tuple(polygon[i])
            p2 = tuple(polygon[(i + 1) % len(polygon)])
            edges.append((p1, p2))
        return edges

    # Calculate all edge lengths
    edge_data: list[tuple[float, tuple, tuple]] = []
    for i in range(len(polygon)):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % len(polygon)]
        length = math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
        edge_data.append((length, tuple(p1), tuple(p2)))

    if not edge_data:
        return None

    # Sort ascending by length
    edge_data.sort(key=lambda x: x[0])

    if edge_type == "shorter":
        reference_length = edge_data[0][0]
        threshold = reference_length * threshold_percent

        matching_edges = []
        for length, p1, p2 in edge_data:
            if abs(length - reference_length) <= threshold:
                matching_edges.append((p1, p2))
                if len(matching_edges) >= 2:
                    break

        return matching_edges if matching_edges else None

    elif edge_type == "longer":
        reference_length = edge_data[-1][0]
        threshold = reference_length * threshold_percent

        matching_edges = []
        for length, p1, p2 in reversed(edge_data):
            if abs(length - reference_length) <= threshold:
                matching_edges.append((p1, p2))
                if len(matching_edges) >= 2:
                    break

        return matching_edges if matching_edges else None

    return None


def make_polygon(coordinates: list) -> Polygon | None:
    """Creates and validates a Shapely Polygon from a list of coordinate
    pairs.

    Returns None if the polygon is degenerate or empty.
    """
    if len(coordinates) < 3:
        return None

    polygon = Polygon(coordinates)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)

    if polygon.is_empty:
        return None

    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda geom: geom.area)

    if polygon.geom_type != "Polygon" or polygon.area <= 0:
        return None

    return polygon


def polygon_vertices(polygon: "Polygon") -> list[tuple[float, float]]:
    """Returns the exterior vertices of a Shapely Polygon
    (closing point excluded).
    """
    return list(polygon.exterior.coords)[:-1]


def polygon_side_lengths(polygon: "Polygon") -> list[float]:
    """Returns the side lengths of a Shapely Polygon in vertex order."""
    vertices = polygon_vertices(polygon)
    lengths = []
    n = len(vertices)
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        lengths.append(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    return lengths


def _minimum_rotated_bbox_width_height(
    polygon: Polygon,
) -> tuple[float, float]:
    """Returns (short_side, long_side) of the minimum rotated bounding box."""
    rotated_bbox = polygon.minimum_rotated_rectangle
    side_lengths = sorted(polygon_side_lengths(rotated_bbox))
    return side_lengths[0], side_lengths[-1]


def quadrilateral_dimensions(
    polygon: Polygon,
) -> tuple[float, float] | tuple[None, None]:
    """Returns (width, height) for a 4-vertex polygon by averaging paired
    sides.

    Width is the mean of the two shorter sides, height is the mean of the two
    longer sides.  Returns `(None, None)` if the polygon is not a
    quadrilateral.
    """
    vertices = polygon_vertices(polygon)
    if len(vertices) != 4:
        return None, None

    side_lengths = sorted(polygon_side_lengths(polygon))
    width = mean(side_lengths[:2])
    height = mean(side_lengths[2:])
    return round(width, 1), round(height, 1)


def rotated_bbox_dimensions(
    polygon: Polygon,
) -> tuple[float, float] | tuple[None, None]:
    """Returns (width, height) via the minimum rotated bounding rectangle."""
    width, height = _minimum_rotated_bbox_width_height(polygon)

    if not (isfinite(width) and isfinite(height)):
        return None, None
    return round(width, 1), round(height, 1)


def shop_dimensions_from_polygon(
    polygon: Polygon,
) -> tuple[float, float] | tuple[None, None]:
    """Returns (width, height) for a Shop polygon.

    Height is the longest side; width is derived as area / height.
    Returns `(None, None)` if the computation is not finite or valid.
    """
    vertices = polygon_vertices(polygon)
    if len(vertices) < 2:
        return None, None

    side_lengths = polygon_side_lengths(polygon)
    if not side_lengths:
        return None, None

    height = max(side_lengths)
    area = polygon.area
    if not (isfinite(height) and isfinite(area)) or height <= 0 or area <= 0:
        return None, None

    width = area / height
    if not isfinite(width) or width <= 0:
        return None, None

    return round(width, 1), round(height, 1)


def extract_element_dimensions(
    element: dict,
) -> tuple[float, float] | tuple[None, None]:
    """Extracts (width, height) from an element dict.

    Handles:
    - Explicit `width`/`height` keys (returned as-is).
    - `polygon` or `coordinates` keys (computed via geometry for
      arbitrary/rotated shapes).  Uses quadrilateral averaging for 4-vertex
      polygons, the Shop-specific formula for `class == 'Shop'`, and the
      minimum rotated bounding rectangle for all other cases.

    Returns `(None, None)` when dimensions cannot be determined.
    """
    if "width" in element and "height" in element:
        return float(element["width"]), float(element["height"])

    polygon_points = element.get("polygon") or element.get("coordinates")
    if not polygon_points:
        return None, None

    polygon = make_polygon(polygon_points)
    if polygon is None:
        return None, None

    class_name = element.get("class")
    if class_name == "Shop":
        return shop_dimensions_from_polygon(polygon)

    vertices = polygon_vertices(polygon)
    if len(vertices) == 4:
        return quadrilateral_dimensions(polygon)

    dimensions = rotated_bbox_dimensions(polygon)
    if dimensions == (None, None):
        return None, None

    return dimensions
