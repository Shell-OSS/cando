# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for flow verification."""

from copy import deepcopy
from typing import Any

from networkx import Graph
from shapely.geometry import Polygon

from cando.utils.path_finders import VoronoiPathFinder


def find_routes(
    layout: dict,
    routes: list[list[str]] | list[str],
    average_vehicle_size: tuple[float, float],
    ignored_classes: list[str] | None = None,
    no_backtracking: list[str] | None = None,
) -> tuple[Graph, list[dict], list[dict[str, Any]]]:
    """Finds routes in the layout using Voronoi pathfinding.

    Args:
        layout (dict): The layout data containing elements.
        routes (list[list[str]] | list[str]): List of route class sequences.
        average_vehicle_size (tuple[float, float]): Average vehicle size
            (length, width).
        ignored_classes (list[str] | None): Classes to ignore during
            pathfinding.
        no_backtracking (list[str] | None): Classes where backtracking is not
            allowed.

    Returns:
        tuple[Graph, list[dict], list[dict[str, Any]]]: The navigation graph,
            valid routes, and invalid routes.
    """
    # Initialize path finder
    path_finder = VoronoiPathFinder()

    layout_copy = deepcopy(layout)
    # Convert polygons to shapely objects
    for element in layout_copy.get("elements", []):
        polygon = element.get("polygon", None)
        if polygon is not None:
            element["polygon"] = Polygon(polygon)

    # Find shortest paths
    G, valid_routes, invalid_routes = path_finder.find_shortest_paths(
        layout=layout_copy,
        routes=routes,
        average_vehicle_size=average_vehicle_size,
        ignored_classes=ignored_classes,
        no_backtracking=no_backtracking,
    )

    return G, valid_routes, invalid_routes


def check_path_convexity(graph: Graph, path: list) -> bool | None:
    """Checks if a path forms a convex or concave polygon.

    Args:
        graph (Graph): NetworkX graph with 'pos' node attributes
        path (list): List of node IDs.

    Returns:
        bool: True if convex, False if concave
    """
    # Extract coordinates from node IDs
    coords = []
    for node in path:
        node_data = graph.nodes[node]
        pos = node_data.get("pos")
        if pos:
            coords.append(pos)

    # Create polygon
    if len(coords) >= 3:
        polygon = Polygon(coords)

        # Check convexity
        is_convex = polygon.equals(polygon.convex_hull)
        return is_convex

    return None
