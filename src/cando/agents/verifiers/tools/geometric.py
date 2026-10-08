# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Geometric data tools for layout elements."""

import math

from shapely.geometry import LineString

from .data import get_edge_by_type, get_element_center, get_orientation_angle


def are_edges_parallel(
    elemA: dict, elemB: dict, tolerance_deg: float = 5.0
) -> bool:
    """Checks if elements' longer edges are approximately parallel.

    Args:
        elemA (dict): First element dictionary.
        elemB (dict): Second element dictionary.
        tolerance_deg (float): Angular tolerance in degrees.

    Returns:
        bool: True if longer edges are parallel within tolerance.
    """
    angleA = get_orientation_angle(elemA)
    angleB = get_orientation_angle(elemB)

    # Normalize angles to 0-180 range
    angleA = angleA % 180
    angleB = angleB % 180

    angle_diff = abs(angleA - angleB)
    # Check both direct difference and complementary difference
    angle_diff = min(angle_diff, 180 - angle_diff)

    return angle_diff <= tolerance_deg


def calculate_orientation_relation(elemA: dict, elemB: dict) -> str:
    """Returns 'longer_edges_adjacent', 'shorter_edges_adjacent', or
    'neither'.

    Args:
        elemA (dict): First element dictionary.
        elemB (dict): Second element dictionary.

    Returns:
        str: String indicating the orientation relationship.
    """
    centerA = get_element_center(elemA)
    centerB = get_element_center(elemB)

    # Vector between centers
    dx = centerB["x"] - centerA["x"]
    dy = centerB["y"] - centerA["y"]
    connection_angle = math.degrees(math.atan2(dy, dx)) % 180

    # Orientation of elements
    angleA = get_orientation_angle(elemA) % 180
    angleB = get_orientation_angle(elemB) % 180

    # Check if connection is perpendicular to longer edges
    angle_diffA = abs((connection_angle - angleA + 90) % 180 - 90)
    angle_diffB = abs((connection_angle - angleB + 90) % 180 - 90)

    if angle_diffA < 30 and angle_diffB < 30:
        return "longer_edges_adjacent"

    # Check if connection is parallel to longer edges
    angle_diffA = abs((connection_angle - angleA) % 180 - 90)
    angle_diffB = abs((connection_angle - angleB) % 180 - 90)

    if angle_diffA > 60 and angle_diffB > 60:
        return "shorter_edges_adjacent"

    return "neither"


def calculate_distance_between_centers(elemA: dict, elemB: dict) -> float:
    """Returns Euclidean distance between element centers.

    Args:
        elemA (dict): First element dictionary.
        elemB (dict): Second element dictionary.

    Returns:
        float: Distance in the same units as the polygon coordinates.
    """
    centerA = get_element_center(elemA)
    centerB = get_element_center(elemB)

    return math.sqrt(
        (centerB["x"] - centerA["x"]) ** 2
        + (centerB["y"] - centerA["y"]) ** 2
    )


def calculate_distance_edge_to_edge(
    edge1: tuple[tuple[float, float], tuple[float, float]],
    edge2: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    """Calculates minimum distance between two edges (line segments).

    Args:
        edge1 (Tuple[Tuple[float, float], Tuple[float, float]]): First edge
            as tuple of two points.
        edge2 (Tuple[Tuple[float, float], Tuple[float, float]]): Second edge
            as tuple of two points.

    Returns:
        float: Minimum distance between edges.
    """
    line1 = LineString([edge1[0], edge1[1]])
    line2 = LineString([edge2[0], edge2[1]])
    return line1.distance(line2)


def get_edge_angle(
    edge: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    """Calculates the angle of an edge in degrees.

    Args:
        edge (Tuple[Tuple[float, float], Tuple[float, float]]): Edge as tuple
            of two points ((x1, y1), (x2, y2)).

    Returns:
        float: Angle in degrees (0-180 range for consistent comparison).
    """
    p1, p2 = edge
    angle = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
    # Normalize to 0-180 range (direction doesn't matter for parallelism)
    return angle % 180


def check_edges_parallel(
    edge1: tuple[tuple[float, float], tuple[float, float]],
    edge2: tuple[tuple[float, float], tuple[float, float]],
    tolerance_deg: float = 5.0,
) -> bool:
    """Checks if two edges are parallel within tolerance.

    Args:
        edge1 (Tuple[Tuple[float, float], Tuple[float, float]]): First edge.
        edge2 (Tuple[Tuple[float, float], Tuple[float, float]]): Second edge.
        tolerance_deg (float): Angular tolerance in degrees.

    Returns:
        bool: True if edges are parallel within tolerance.
    """
    angle1 = get_edge_angle(edge1)
    angle2 = get_edge_angle(edge2)

    angle_diff = abs(angle1 - angle2)
    # Check both direct difference and complementary difference
    angle_diff = min(angle_diff, 180 - angle_diff)

    return angle_diff <= tolerance_deg


def get_edge_line(
    element: dict, edge_type: str = "any"
) -> list[LineString] | None:
    """Gets edge line segments from element.

    Args:
        element (dict): Element dictionary.
        edge_type (str): Type of edge ('longer', 'shorter', 'any').

    Returns:
        Optional[List[LineString]]: List of LineString objects representing
            edges.
    """
    edges = get_edge_by_type(element, edge_type)
    if not edges:
        return None

    return [LineString([edge[0], edge[1]]) for edge in edges]
