# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for assembly verification."""

import math
from collections import defaultdict

import numpy as np
from shapely.geometry import Polygon

from cando.constants.classes import INPUT_CLASSES

from ..tools.data import get_element_center


def cluster_elements_by_class(
    elements: list[dict],
    distance_threshold: float | dict[str, float],
    default_threshold: float = 5.0,
) -> dict[str, list[dict]]:
    """
    Clusters elements by class based on proximity using polygon-to-polygon
    distances.

    Uses shapely's distance calculation to determine the minimum distance
    between polygon boundaries. Elements are grouped together if their
    polygons are within the threshold distance.

    Args:
        elements (List[Dict]): List of element dictionaries, each containing:
            - 'id' (int): Element identifier
            - 'class' (str): Element class name
            - 'polygon' (List[List[float]]): List of [x, y] coordinate pairs
            - 'bbox' (Dict): Bounding box with min_x, min_y, max_x, max_y
            - 'rotation_deg' (float): Rotation in degrees
        distance_threshold (Union[float, Dict[str, float]]): Maximum distance
            between polygon boundaries for elements to be considered in the
            same cluster. Can be either:
            - A single float applied to all classes
            - A dictionary mapping class names to specific thresholds
        default_threshold (float): Default threshold to use for classes not
            specified in distance_threshold dict. Only used when
            distance_threshold is a dict. Defaults to 5.0.

    Returns:
        Dict[str, List[Dict]]: Dictionary mapping class names to lists of
            cluster metadata dictionaries, where each metadata dict contains:
            - 'cluster_index': Index of this cluster
            - 'element_ids': Sorted list of element IDs in the cluster
            - 'cluster_size': Number of elements in the cluster
            - 'elements': List of element dictionaries in the cluster
    """
    # Group elements by class
    elements_by_class = defaultdict(list)
    for element in elements:
        if element["class"] in INPUT_CLASSES:
            continue
        elements_by_class[element["class"]].append(element)

    result = {}

    for class_name, class_elements in elements_by_class.items():
        # Skip clustering if only one element
        if len(class_elements) == 1:
            result[class_name] = [
                {
                    "cluster_index": 0,
                    "element_ids": sorted(
                        [elem["id"] for elem in class_elements]
                    ),
                    "cluster_size": 1,
                    "elements": class_elements,
                }
            ]
            continue

        # Get threshold for this class
        if isinstance(distance_threshold, dict):
            threshold = distance_threshold.get(class_name, default_threshold)
        else:
            threshold = distance_threshold

        n_elements = len(class_elements)

        # Convert polygons to shapely Polygon objects
        shapely_polygons = [
            Polygon(elem["polygon"]) for elem in class_elements
        ]

        # Use Union-Find algorithm for clustering based on distance threshold
        parent = list(range(n_elements))

        def find(x, parent=parent):
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x, y, parent=parent):
            root_x = find(x)
            root_y = find(y)
            if root_x != root_y:
                parent[root_x] = root_y

        # Unite elements whose polygons are within threshold distance
        for i in range(n_elements):
            for j in range(i + 1, n_elements):
                # Calculate distance between polygon boundaries
                distance = shapely_polygons[i].distance(shapely_polygons[j])

                if distance <= threshold:
                    union(i, j)

        # Group elements by their root parent (cluster)
        clusters = defaultdict(list)
        for idx in range(n_elements):
            root = find(idx)
            clusters[root].append(class_elements[idx])

        # Convert to list of structured cluster metadata
        cluster_list = []
        for cluster_idx, label in enumerate(sorted(clusters.keys())):
            cluster_elements = clusters[label]
            cluster_list.append(
                {
                    "cluster_index": cluster_idx,
                    "element_ids": sorted(
                        [elem["id"] for elem in cluster_elements]
                    ),
                    "cluster_size": len(cluster_elements),
                    "elements": cluster_elements,
                }
            )

        result[class_name] = cluster_list

    return result


def detect_fragmentation(
    class_name: str, clusters: list[dict], average_group_size: int
) -> dict:
    """
    Detects when elements of a class are fragmented (too many single-element
    clusters). This indicates that elements which should form coherent groups
    are placed too far apart.

    Args:
        class_name (str): The element class being evaluated.
        clusters (List[Dict]): List of cluster metadata dictionaries for
            this class, where each dict contains 'cluster_size',
            'element_ids', etc.
        average_group_size (int): Expected average cluster size from the
            rules.

    Returns:
        Dict: {
            'is_fragmented': bool,
            'total_elements': int,
            'total_clusters': int,
            'singleton_clusters': int,
            'average_cluster_size': float,
            'expected_average_size': int,
            'description': str (human-readable description of the issue)
        }
    """
    total_elements = sum(cluster["cluster_size"] for cluster in clusters)
    total_clusters = len(clusters)
    singleton_clusters = sum(
        1 for cluster in clusters if cluster["cluster_size"] == 1
    )

    if total_elements == 0:
        return {
            "is_fragmented": False,
            "total_elements": 0,
            "total_clusters": 0,
            "singleton_clusters": 0,
            "average_cluster_size": 0.0,
            "expected_average_size": average_group_size,
            "description": "No elements found",
        }

    actual_average_size = (
        total_elements / total_clusters if total_clusters > 0 else 0
    )

    # Consider fragmented if:
    # - More than 50% of clusters are singletons AND
    # - The actual average is significantly below expected (less than 50%)
    singleton_ratio = (
        singleton_clusters / total_clusters if total_clusters > 0 else 0
    )
    is_fragmented = (
        singleton_ratio > 0.5
        and actual_average_size < average_group_size * 0.75
        and total_elements >= 2
    )

    if is_fragmented:
        description = (
            f"{class_name}: {total_elements} elements split into "
            f"{total_clusters} clusters ({singleton_clusters} singletons). "
            "Elements are too far apart and should form larger groups."
        )
    else:
        description = f"{class_name}: grouping is acceptable"

    return {
        "is_fragmented": is_fragmented,
        "total_elements": total_elements,
        "total_clusters": total_clusters,
        "singleton_clusters": singleton_clusters,
        "average_cluster_size": round(actual_average_size, 2),
        "expected_average_size": average_group_size,
        "description": description,
    }


def detect_single_line(elements: list[dict], tolerance: float = 0.5) -> dict:
    """
    Detects if elements form a single line arrangement (1xN or Nx1).

    Args:
        elements (List[Dict]): List of element dictionaries from a single
            cluster.
        tolerance (float): Maximum deviation from line in coordinate units.
            Default is 0.5.

    Returns:
        Dict: {
            'is_single_line': bool,
            'pattern': str (e.g., '1x5', '5x1'),
            'orientation': str ('horizontal', 'vertical', or 'diagonal'),
            'count': int
        }
    """
    n = len(elements)

    if n == 0:
        return {
            "is_single_line": False,
            "pattern": "empty",
            "orientation": "none",
            "count": 0,
        }

    if n == 1:
        return {
            "is_single_line": True,
            "pattern": "1x1",
            "orientation": "single",
            "count": 1,
        }

    if n == 2:
        # Two points always define a line; derive orientation for readability.
        centers = [get_element_center(elem) for elem in elements]
        dx = centers[1]["x"] - centers[0]["x"]
        dy = centers[1]["y"] - centers[0]["y"]

        if abs(dy) > abs(dx) * 2:
            orientation = "vertical"
            pattern = "2x1"
        elif abs(dx) > abs(dy) * 2:
            orientation = "horizontal"
            pattern = "1x2"
        else:
            orientation = "diagonal"
            pattern = "1x2"

        return {
            "is_single_line": True,
            "pattern": pattern,
            "orientation": orientation,
            "count": 2,
        }

    # Get centroids
    centers = [get_element_center(elem) for elem in elements]
    points = np.array([[c["x"], c["y"]] for c in centers])

    centered = points - np.mean(points, axis=0)
    U, S, Vt = np.linalg.svd(centered)
    normal = Vt[1]
    distances = np.abs(np.dot(centered, normal))
    is_line = bool(np.max(distances) < tolerance)

    dx, dy = Vt[0]

    if is_line:
        if abs(dy) > abs(dx) * 2:
            orientation = "vertical"
            pattern = f"{n}x1"
        elif abs(dx) > abs(dy) * 2:
            orientation = "horizontal"
            pattern = f"1x{n}"
        else:
            orientation = "diagonal"
            pattern = f"1x{n}"
    else:
        orientation = "irregular"
        pattern = "irregular"

    # Shape gate for three-point lines.
    #
    # For n=3 we only enforce geometric collinearity quality (line vs L/tri-)
    # in a scale-invariant way. Other concerns (overlap/fragmentation/etc.)
    # are handled by dedicated checks elsewhere.
    if n == 3 and is_line:
        eps = 1e-6
        pairwise_distances = []
        for i in range(n):
            for j in range(i + 1, n):
                pairwise_distances.append(
                    float(np.linalg.norm(points[i] - points[j]))
                )

        max_pairwise_distance = (
            max(pairwise_distances) if pairwise_distances else 0.0
        )
        max_residual = float(np.max(distances))
        normalized_residual = max_residual / max(max_pairwise_distance, eps)

        # Small normalized residual means points are truly line-like rather
        # than forming an L/triangle at any scale.
        if normalized_residual > 0.05:
            is_line = False
            orientation = "irregular"
            pattern = "irregular"

    return {
        "is_single_line": bool(is_line),
        "pattern": pattern,
        "orientation": orientation,
        "count": int(n),
    }


def detect_rectangular_block(
    elements: list[dict], tolerance: float = 1.5
) -> dict:
    """
    Detects if elements form a rectangular block pattern (MxN where M,N > 1).

    Args:
        elements (List[Dict]): List of element dictionaries from a single
            cluster.
        tolerance (float): Tolerance for coordinate alignment. Default is 0.5.

    Returns:
        Dict: {
            'is_rectangular': bool,
            'rows': int,
            'cols': int,
            'pattern': str (e.g., '2x3', '3x2'),
            'count': int
        }
    """
    n = len(elements)

    if n == 0:
        return {
            "is_rectangular": False,
            "rows": 0,
            "cols": 0,
            "pattern": "empty",
            "count": 0,
        }

    if n == 1:
        return {
            "is_rectangular": False,
            "rows": 1,
            "cols": 1,
            "pattern": "1x1",
            "count": 1,
        }

    # Get centroids
    centers = [get_element_center(elem) for elem in elements]

    # Align grid with the first element's orientation
    theta = 0.0
    if elements:
        poly = elements[0].get("polygon", [])
        if len(poly) >= 2:
            p1, p2 = poly[0], poly[1]
            theta = math.atan2(p2[1] - p1[1], p2[0] - p1[0])

    c_angle = math.cos(-theta)
    s_angle = math.sin(-theta)

    x_coords = []
    y_coords = []
    for center in centers:
        cx, cy = center["x"], center["y"]
        x_coords.append(cx * c_angle - cy * s_angle)
        y_coords.append(cx * s_angle + cy * c_angle)

    # Cluster coordinates to find rows and columns
    def cluster_coordinates(coords, tol):
        if not coords:
            return []
        coords_sorted = sorted(coords)
        clusters = [[coords_sorted[0]]]
        for coord in coords_sorted[1:]:
            if coord - clusters[-1][-1] < tol:
                clusters[-1].append(coord)
            else:
                clusters.append([coord])
        return [np.mean(cluster) for cluster in clusters]

    x_clusters = cluster_coordinates(x_coords, tolerance)
    y_clusters = cluster_coordinates(y_coords, tolerance)

    rows = len(y_clusters)
    cols = len(x_clusters)

    # Check if it forms a valid rectangular grid (both dimensions > 1)
    is_rectangular = (rows * cols == n) and rows > 1 and cols > 1

    return {
        "is_rectangular": bool(is_rectangular),
        "rows": int(rows),
        "cols": int(cols),
        "pattern": f"{rows}x{cols}" if is_rectangular else "not_rectangular",
        "count": int(n),
    }


def detect_irregular_pattern(
    elements: list[dict], tolerance: float = 1.5
) -> dict:
    """
    Analyzes patterns that don't match single-line or rectangular block
    arrangements. Provides detailed information about irregular arrangements.

    Args:
        elements (List[Dict]): List of element dictionaries from a single
            cluster.
        tolerance (float): Tolerance for coordinate alignment.

    Returns:
        Dict: {
            'is_irregular': bool,
            'rows': int (estimated),
            'cols': int (estimated),
            'count': int,
            'description': str (human-readable description of the arrangement)
        }
    """
    n = len(elements)

    if n == 0:
        return {
            "is_irregular": False,
            "rows": 0,
            "cols": 0,
            "count": 0,
            "description": "No elements in cluster",
        }

    if n == 1:
        return {
            "is_irregular": False,
            "rows": 1,
            "cols": 1,
            "count": 1,
            "description": "Single element",
        }

    # Get centroids for analysis
    centers = [get_element_center(elem) for elem in elements]

    # Align grid with the first element's orientation
    theta = 0.0
    if elements:
        poly = elements[0].get("polygon", [])
        if len(poly) >= 2:
            p1, p2 = poly[0], poly[1]
            theta = math.atan2(p2[1] - p1[1], p2[0] - p1[0])

    c_angle = math.cos(-theta)
    s_angle = math.sin(-theta)

    x_coords = []
    y_coords = []
    for center in centers:
        cx, cy = center["x"], center["y"]
        x_coords.append(cx * c_angle - cy * s_angle)
        y_coords.append(cx * s_angle + cy * c_angle)

    # Cluster coordinates to estimate structure
    def cluster_coordinates(coords, tol):
        if not coords:
            return []
        coords_sorted = sorted(coords)
        clusters = [[coords_sorted[0]]]
        for coord in coords_sorted[1:]:
            if coord - clusters[-1][-1] < tol:
                clusters[-1].append(coord)
            else:
                clusters.append([coord])
        return [np.mean(cluster) for cluster in clusters]

    x_clusters = cluster_coordinates(x_coords, tolerance)
    y_clusters = cluster_coordinates(y_coords, tolerance)

    rows = len(y_clusters)
    cols = len(x_clusters)

    # Analyze why it's irregular
    expected_elements = rows * cols

    if expected_elements == n and rows > 1 and cols > 1:
        return {
            "is_irregular": False,
            "rows": int(rows),
            "cols": int(cols),
            "count": int(n),
            "description": f"Regular {rows}x{cols} grid",
        }

    if expected_elements > n:
        description = (
            f"Incomplete grid: expected {rows}x{cols}={expected_elements} "
            f"elements but found {n}"
        )
    elif expected_elements < n:
        description = (
            f"Elements scattered: {n} elements across approximately {rows} "
            f"rows and {cols} columns"
        )
    elif rows == 1 or cols == 1:
        description = (
            f"Line-like arrangement with irregularities: {n} elements"
        )
    else:
        description = (
            f"Grid-like arrangement with irregularities: approximately "
            f"{rows}x{cols} but {rows}x{cols}={expected_elements} != {n}"
        )

    return {
        "is_irregular": True,
        "rows": int(rows),
        "cols": int(cols),
        "count": int(n),
        "description": description,
    }


def analyze_cluster_patterns(
    groups: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """
    Preprocesses all clusters to detect their geometric patterns.

    For each cluster, runs all three pattern detection functions and stores
    the results. This allows the LLM to access all pattern information without
    needing to call tools.

    Args:
        groups (Dict[str, List[Dict]]): Dictionary mapping class names to
            lists of cluster metadata dicts (from cluster_elements_by_class).

    Returns:
        Dict[str, List[Dict]]: Dictionary mapping class names to lists of
            pattern analysis results, where each result contains:
            - 'cluster_index': Index of the cluster (references the cluster in
              groups)
            - 'single_line': Result from detect_single_line
            - 'rectangular': Result from detect_rectangular_block
            - 'irregular': Result from detect_irregular_pattern
    """
    pattern_analysis = {}

    for class_name, clusters in groups.items():
        class_patterns = []

        for cluster_metadata in clusters:
            cluster_elements = cluster_metadata["elements"]

            # Run all pattern detection functions
            single_line = detect_single_line(cluster_elements)
            rectangular = detect_rectangular_block(cluster_elements)
            irregular = detect_irregular_pattern(cluster_elements)

            analysis = {
                "cluster_index": cluster_metadata["cluster_index"],
                "single_line": single_line,
                "rectangular": rectangular,
                "irregular": irregular,
            }

            class_patterns.append(analysis)

        pattern_analysis[class_name] = class_patterns

    return pattern_analysis
