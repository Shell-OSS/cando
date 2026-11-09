# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

import networkx as nx

from cando.utils.path_finders.voronoi import VoronoiPathFinder

ENTRANCE, HGV, EXIT = "wp_Entrance_1", "wp_HGV Service Area_5", "wp_Exit_2"
SEQUENCE = ["Entrance", "HGV Service Area", "Exit"]
NO_BACKTRACKING = ["HGV Service Area"]


def _graph(edges):
    graph = nx.Graph()
    graph.add_weighted_edges_from(edges)
    return graph


def test_shared_junction_without_corridor_finds_path():
    # Entrance and service area both hang off junction 1: no corridor edge
    graph = _graph(
        [(ENTRANCE, 1, 1.0), (HGV, 1, 1.0), (1, 2, 1.0), (2, EXIT, 1.0)]
    )

    path, length = VoronoiPathFinder()._find_path_through_waypoints(
        graph, (ENTRANCE, HGV, EXIT), SEQUENCE, NO_BACKTRACKING
    )

    assert path == [ENTRANCE, 1, HGV, 1, 2, EXIT]
    assert length == 5.0


def test_backtracking_through_corridor_is_rejected():
    # The only way to the exit is back along the approach edge 2 -> 1
    graph = _graph(
        [(ENTRANCE, 1, 1.0), (1, 2, 1.0), (2, HGV, 1.0), (1, EXIT, 1.0)]
    )

    path, length = VoronoiPathFinder()._find_path_through_waypoints(
        graph, (ENTRANCE, HGV, EXIT), SEQUENCE, NO_BACKTRACKING
    )

    assert path == []
    assert length == float("inf")
