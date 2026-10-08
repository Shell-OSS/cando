# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Data tools to extract geometric information for layout elements."""

import math


def get_element_center(element: dict) -> dict:
    """Returns the geometric center {x, y} of the element polygon.

    Args:
        element (dict): Dictionary with 'polygon' key containing list of
            [x, y] coordinates.

    Returns:
        dict: Dictionary with 'x' and 'y' keys representing the centroid.
    """
    polygon = element.get("polygon", [])
    if not polygon:
        return {"x": 0.0, "y": 0.0}

    x_coords = [p[0] for p in polygon]
    y_coords = [p[1] for p in polygon]

    return {
        "x": sum(x_coords) / len(x_coords),
        "y": sum(y_coords) / len(y_coords),
    }


def get_edge_lengths(element: dict) -> dict:
    """Returns {"long_edge": float, "short_edge": float} based on polygon
    edges.

    Args:
        element (dict): Dictionary with 'polygon' key.

    Returns:
        dict: Dictionary with long_edge and short_edge lengths.
    """
    polygon = element.get("polygon", [])
    if len(polygon) < 2:
        return {"long_edge": 0.0, "short_edge": 0.0}

    edges = get_polygon_edges(element)
    edge_lengths = []

    for p1, p2 in edges:
        length = math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
        edge_lengths.append(length)

    if not edge_lengths:
        return {"long_edge": 0.0, "short_edge": 0.0}

    edge_lengths.sort(reverse=True)

    return {
        "long_edge": edge_lengths[0],
        "short_edge": (
            edge_lengths[-1] if len(edge_lengths) > 1 else edge_lengths[0]
        ),
    }


def get_orientation_angle(element: dict) -> float:
    """Returns primary orientation angle (degrees) of the element.

    Uses rotation_deg if available, otherwise computes from bounding box.

    Args:
        element (dict): Dictionary with optional 'rotation_deg' key.

    Returns:
        float: Orientation angle in degrees (0-360).
    """
    if "rotation_deg" in element:
        return element["rotation_deg"]

    # Compute from polygon edges
    polygon = element.get("polygon", [])
    if len(polygon) < 2:
        return 0.0

    edges = get_polygon_edges(element)
    edge_lengths = []

    for p1, p2 in edges:
        length = math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
        angle = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
        edge_lengths.append((length, angle))

    # Return angle of longest edge
    edge_lengths.sort(key=lambda x: x[0], reverse=True)
    return edge_lengths[0][1] if edge_lengths else 0.0


def get_polygon_edges(element: dict) -> list:
    """Returns list of edges as ((x1, y1), (x2, y2)).

    Args:
        element (dict): Dictionary with 'polygon' key.

    Returns:
        list: List of tuples representing edges.
    """
    polygon = element.get("polygon", [])
    if len(polygon) < 2:
        return []

    edges = []
    for i in range(len(polygon)):
        p1 = tuple(polygon[i])
        p2 = tuple(polygon[(i + 1) % len(polygon)])
        edges.append((p1, p2))

    return edges


def get_edge_by_type(
    element: dict, edge_type: str, threshold_percent: float = 0.15
) -> list[tuple[tuple[float, float], tuple[float, float]]] | None:
    """Gets the edges of specified type from element polygon.

    Types: 'longer', 'shorter', or 'any'.

    For rectangular elements, this identifies the 2 shorter and 2 longer edges
    using a relative threshold. If the second edge of a type exceeds the
    threshold, it is not considered part of that group.

    Args:
        element (dict): Element dictionary with 'polygon' key.
        edge_type (str): Type of edge ('longer', 'shorter', 'any').
        threshold_percent (float): Relative threshold (0-1) for grouping
            edges.
            Default 0.15 means edges within 15% of the reference length are
            considered the same type.
    Returns:
        list[tuple[tuple[float, float], tuple[float, float]]] | None:
        List of edge line segments, each as ((x1, y1), (x2, y2)),
        or None if invalid.
    """
    polygon = element.get("polygon", [])
    if len(polygon) < 3:
        return None

    if edge_type == "any":
        # Return all edges
        edges = []
        for i in range(len(polygon)):
            p1 = tuple(polygon[i])
            p2 = tuple(polygon[(i + 1) % len(polygon)])
            edges.append((p1, p2))
        return edges

    # Calculate all edge lengths
    edge_data = []
    for i in range(len(polygon)):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % len(polygon)]
        length = math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
        edge_data.append((length, (tuple(p1), tuple(p2))))

    if not edge_data:
        return None

    # Sort by length
    edge_data.sort(key=lambda x: x[0])

    if edge_type == "shorter":
        # Get the shortest edge as reference
        reference_length = edge_data[0][0]
        threshold = reference_length * threshold_percent

        # Collect edges within threshold, up to 2 edges (for rectangles)
        matching_edges = []
        for length, edge in edge_data:
            if abs(length - reference_length) <= threshold:
                matching_edges.append(edge)
                if len(matching_edges) >= 2:
                    # Check if there are more edges within threshold
                    # If yes, take only the first 2 (shortest ones)
                    break

        return matching_edges if matching_edges else None

    elif edge_type == "longer":
        # Get the longest edge as reference
        reference_length = edge_data[-1][0]
        threshold = reference_length * threshold_percent

        # Collect edges within threshold, up to 2 edges (for rectangles)
        # Start from the longest and work backwards
        matching_edges = []
        for length, edge in reversed(edge_data):
            if abs(length - reference_length) <= threshold:
                matching_edges.append(edge)
                if len(matching_edges) >= 2:
                    # Check if there are more edges within threshold
                    # If yes, take only the first 2 (longest ones)
                    break

        return matching_edges if matching_edges else None

    else:
        return None


def get_elements_by_class(
    elements: list[dict], class_name: str
) -> list[dict]:
    """Filters elements by a single class name or 'All'.

    Args:
        elements (list[dict]): All elements.
        class_name (str): Class name to filter. If "All", returns all
            elements.

    Returns:
        list[dict]: Filtered elements.
    """
    if class_name == "All":
        return elements
    return [elem for elem in elements if elem.get("class") == class_name]
