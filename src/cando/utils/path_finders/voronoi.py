# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Path finding utilities using Voronoi diagrams.

Voronoi-based path finding for layouts.
"""

import copy
import logging
from typing import Any, Literal

import networkx as nx
import numpy as np
from scipy.spatial import Voronoi, cKDTree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from cando.constants.classes import (
    INPUT_CLASSES,
    PARKING_SPACE_CLASSES,
    SERVICE_AREA_CLASSES,
    UNDERGROUND_CLASSES,
)

logger = logging.getLogger(__name__)

LAYOUT_CLASS = INPUT_CLASSES[0]
ENTRANCE_CLASS = INPUT_CLASSES[1]
EXIT_CLASS = INPUT_CLASSES[2]
CAR_SERVICE_AREA_CLASS = SERVICE_AREA_CLASSES[0]


class _DiagnosticContext:
    """Per-computation diagnostic state for path finding."""

    def __init__(
        self,
        original_layout: dict[str, Any],
        average_vehicle_size: tuple[float, float],
        dividers: Any | None = None,
    ) -> None:
        self.original_layout = original_layout
        self.average_vehicle_size = average_vehicle_size
        self.dividers = dividers
        self.connected_medial_positions: list[tuple[float, float]] = []
        self.connected_kdtree: cKDTree | None = None


class VoronoiPathFinder:
    def __init__(
        self, sampling_density: float = 10.0, csa_entrance_side: str = "long"
    ) -> None:
        """Initializes path finder with sampling density and
        service-area entrance side.

        Args:
            sampling_density (float): Density for sampling obstacle
                boundaries.
            csa_entrance_side (str): Preferred service-area entrance side;
                `short` uses shorter edges as blocked, `LONG` uses longer
                edges.
        """
        self.sampling_density = sampling_density
        self.csa_entrance_side = csa_entrance_side.lower()

    def find_shortest_paths(
        self,
        layout: dict[str, Any],
        routes: list[list[str]] | list[str],
        average_vehicle_size: tuple[float, float] = (4.5, 1.8),
        ignored_classes: list[str] | None = None,
        no_backtracking: list[str] | None = None,
        validation_mode: Literal["any", "all"] = "any",
    ) -> tuple[nx.Graph, list[dict[str, Any]], list[dict[str, Any]]]:
        """Finds the shortest paths for the given routes.

        Args:
            layout (dict): The layout dictionary.
            routes (list[str] | list[list[str]]): Single route or multiple
                candidate routes specifying classes to visit.
            average_vehicle_size (tuple[float, float]): Vehicle dimensions.
            ignored_classes (list[str] | None): Classes to ignore.
            no_backtracking (list[str] | None): Classes where backtracking
                is not allowed.
            validation_mode (str): "any" to keep routes with at least
                one valid path to access points, "all" to require all
                paths in a route to be valid.

        Returns:
            tuple: Graph, route results, and invalid routes. Each invalid
            route is a dict with 'route' (list of waypoint identifiers) and
            'reason' (explanation of why the route failed).
        """
        if not routes:
            return nx.Graph(), [], []

        if isinstance(routes[0], str):
            normalized_routes = [routes]
        else:
            normalized_routes = routes

        results: list[dict[str, Any]] = []
        invalid_routes: list[dict[str, Any]] = []
        combined_graph = nx.Graph()
        is_multi_route = len(normalized_routes) > 1

        for route_index, route in enumerate(normalized_routes):
            classes_of_interest = list({*route, ENTRANCE_CLASS, EXIT_CLASS})

            route_graph, _, dividers = self.create_graph(
                layout,
                classes_of_interest,
                average_vehicle_size,
                ignored_classes,
            )

            # Non-route classes stay as obstacles for this route's context.
            ctx = _DiagnosticContext(
                original_layout=layout,
                average_vehicle_size=average_vehicle_size,
                dividers=dividers,
            )
            self._precompute_connected_nodes(route_graph, ctx)

            sequence = [ENTRANCE_CLASS] + route + [EXIT_CLASS]

            paths, invalid = self._find_all_path_combinations(
                route_graph,
                sequence,
                no_backtracking,
                ctx,
                validation_mode=validation_mode,
            )
            invalid_routes.extend(invalid)

            if is_multi_route:
                route_graph, paths = self._namespace_route_graph(
                    route_graph, paths, route_index
                )

            combined_graph = nx.compose(combined_graph, route_graph)

            results.append({"route": route, "paths": paths})

        return combined_graph, results, invalid_routes

    def _namespace_route_graph(
        self,
        graph: nx.Graph,
        paths: list[dict[str, Any]],
        route_index: int,
    ) -> tuple[nx.Graph, list[dict[str, Any]]]:
        """Prefixes graph node ids so multiple route graphs can coexist."""
        node_mapping: dict[Any, Any] = {}
        for node in graph.nodes():
            if isinstance(node, str):
                node_mapping[node] = f"route_{route_index}_{node}"
            else:
                node_mapping[node] = f"route_{route_index}_node_{node}"

        relabeled_graph = nx.relabel_nodes(graph, node_mapping, copy=True)

        relabeled_paths = []
        for path_data in paths:
            relabeled_path_data = path_data.copy()
            relabeled_path_data["path"] = [
                node_mapping.get(node, node)
                for node in path_data.get("path", [])
            ]
            relabeled_path_data["waypoints"] = {
                waypoint_type: node_mapping.get(node, node)
                for waypoint_type, node in path_data.get(
                    "waypoints", {}
                ).items()
            }
            relabeled_paths.append(relabeled_path_data)

        return relabeled_graph, relabeled_paths

    def create_graph(
        self,
        layout: dict[str, Any],
        classes_of_interest: list[str],
        average_vehicle_size: tuple[float, float] = (4.5, 1.8),
        ignored_classes: list[str] | None = None,
    ) -> tuple[nx.Graph, list[LineString], Any]:
        """Creates a navigation graph from the layout.

        Args:
            layout (dict): The layout dictionary.
            classes_of_interest (list[str]): Classes that are traversable and
                should appear as waypoints.
            average_vehicle_size (tuple[float, float]): Vehicle length and
                width used to buffer obstacles.
            ignored_classes (list[str] | None): Classes ignored during path
                finding but kept in layout.

        Returns:
            tuple: Graph of medial axis, list of medial edges, and
                divider geometry (or `None` when no dividers exist).
        """
        if ignored_classes is None:
            ignored_classes = []

        processed_layout = copy.deepcopy(layout)
        self._identify_obstacles(
            processed_layout, ignored_classes, classes_of_interest
        )
        self._add_csa_obstacles(processed_layout)
        self._add_parking_obstacles(processed_layout)
        self._buffer_obstacles(
            processed_layout, classes_of_interest, average_vehicle_size
        )

        divider_polys = [
            e["polygon"]
            for e in processed_layout["elements"]
            if e.get("csa_divider") or e.get("parking_divider")
        ]
        dividers = unary_union(divider_polys) if divider_polys else None

        self._split_csa_areas(processed_layout)
        self._combine_obstacles(processed_layout)

        G, medial_edges = self._compute_voronoi_graph(
            processed_layout, ignored_classes
        )
        G_simplified = self._simplify_to_junction_graph(G)

        return G_simplified, medial_edges, dividers

    def _identify_obstacles(
        self,
        layout: dict[str, Any],
        ignored_classes: list[str],
        non_blocking_classes: list[str],
    ) -> None:
        """Flags blocking elements as obstacles and marks layout boundary.

        Args:
            layout (dict): Layout to mutate.
            ignored_classes (list[str]): Classes to keep untouched.
            non_blocking_classes (list[str]): Classes allowed as traversable.
        """
        scenario_elements = []
        for element in layout["elements"]:
            if (
                element["class"] in non_blocking_classes
                or element["class"] == LAYOUT_CLASS
            ):
                scenario_elements.append(element)
            elif element["class"] in ignored_classes:
                scenario_elements.append(element)
            else:
                new_element = element.copy()
                new_element["class"] = "OBSTACLE"
                scenario_elements.append(new_element)

        layout_element = next(
            (e for e in scenario_elements if e["class"] == LAYOUT_CLASS), None
        )
        if layout_element:
            scenario_elements.remove(layout_element)
            scenario_elements.append(
                {
                    "class": "OBSTACLE LAYOUT",
                    "polygon": layout_element["polygon"],
                    "id": layout_element["id"],
                }
            )

        layout["elements"] = scenario_elements

    def _add_csa_obstacles(
        self, layout: dict[str, Any], csa_entrance_side: str | None = None
    ) -> None:
        """Inserts thin obstacles around service areas to control approach.

        Args:
            layout (dict): Layout to mutate.
            csa_entrance_side (str | None): Which service-area sides to
                block;
                defaults to the instance preference when `None`.
        """
        csas = [
            e
            for e in layout["elements"]
            if e["class"] in SERVICE_AREA_CLASSES
        ]

        if not csas:
            return

        csa_obstacles = []
        for csa in csas:
            polygon = csa["polygon"]
            coords = list(polygon.exterior.coords[:-1])

            if len(coords) < 3:
                continue

            sides = []
            for i in range(len(coords)):
                p1 = coords[i]
                p2 = coords[(i + 1) % len(coords)]
                length = np.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
                sides.append({"start": p1, "end": p2, "length": length})

            # For non-car service areas, entrance is always on the short side.
            if csa["class"] != CAR_SERVICE_AREA_CLASS:
                target_entrance_side = "short"
            else:
                target_entrance_side = (
                    csa_entrance_side
                    if csa_entrance_side is not None
                    else self.csa_entrance_side
                )

            if target_entrance_side.lower() == "long":
                sides_sorted = sorted(sides, key=lambda x: x["length"])
            else:
                sides_sorted = sorted(
                    sides, key=lambda x: x["length"], reverse=True
                )

            blocked_sides = sides_sorted[:2]

            for i, side in enumerate(blocked_sides):
                line = LineString([side["start"], side["end"]])
                csa_obstacles.append(
                    {
                        "class": "OBSTACLE",
                        "polygon": line.buffer(0.1),
                        "id": int(csa["id"]) * 100 + i,
                        "csa_divider": True,
                    }
                )

            # Add middle splitter to discourage crossing through service
            # areas.
            long_sides = [side for side in sides if side not in blocked_sides]
            if (
                len(long_sides) >= 2
                and csa["class"] == CAR_SERVICE_AREA_CLASS
            ):
                mid1 = (
                    (long_sides[0]["start"][0] + long_sides[0]["end"][0]) / 2,
                    (long_sides[0]["start"][1] + long_sides[0]["end"][1]) / 2,
                )
                mid2 = (
                    (long_sides[1]["start"][0] + long_sides[1]["end"][0]) / 2,
                    (long_sides[1]["start"][1] + long_sides[1]["end"][1]) / 2,
                )
                middle_line = LineString([mid1, mid2])
                csa_obstacles.append(
                    {
                        "class": "OBSTACLE",
                        "polygon": middle_line.buffer(0.1),
                        "id": int(csa["id"]) * 100 + 2,
                        "csa_divider": True,
                    }
                )

        layout["elements"].extend(csa_obstacles)

    def _add_parking_obstacles(
        self,
        layout: dict[str, Any],
    ) -> None:
        """Inserts thin obstacles on long sides of parking spaces.

        Args:
            layout (dict): Layout to mutate.
        """
        parking_spaces = [
            e
            for e in layout["elements"]
            if e["class"] in PARKING_SPACE_CLASSES
        ]

        if not parking_spaces:
            return

        parking_obstacles = []
        for parking in parking_spaces:
            polygon = parking["polygon"]
            coords = list(polygon.exterior.coords[:-1])

            if len(coords) < 3:
                continue

            sides = []
            for i in range(len(coords)):
                p1 = coords[i]
                p2 = coords[(i + 1) % len(coords)]
                length = np.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
                sides.append({"start": p1, "end": p2, "length": length})

            sides_sorted = sorted(
                sides, key=lambda x: x["length"], reverse=True
            )
            blocked_sides = sides_sorted[:2]

            for i, side in enumerate(blocked_sides):
                line = LineString([side["start"], side["end"]])
                parking_obstacles.append(
                    {
                        "class": "OBSTACLE",
                        "polygon": line.buffer(0.1),
                        "id": int(parking["id"]) * 100 + i,
                        "parking_divider": True,
                    }
                )

        layout["elements"].extend(parking_obstacles)

    def _buffer_obstacles(
        self,
        layout: dict[str, Any],
        non_blocking_classes: list[str],
        average_vehicle_size: tuple[float, float] = (4.5, 1.8),
    ) -> None:
        """Grows or shrinks polygons so vehicles keep clearance.

        Two-speed buffering strategy:
        - Structural obstacles and layout boundary use the full turning-radius
          buffer (circumscribed circle radius), because vehicles need room to
          manoeuvre around corners and walls.
        - Service-area dividers and parking dividers use only half the
          vehicle width, because vehicles approach those in a straight line
          and do not turn inside them.

        Args:
            layout (dict): Layout to mutate.
            non_blocking_classes (list[str]): Traversable classes not
                buffered.
            average_vehicle_size (tuple[float, float]): Vehicle dimensions.
        """
        veh_length, w = average_vehicle_size

        # Circumscribed radius — full turning clearance for structural
        # obstacles.
        turning_buffer = np.sqrt(veh_length**2 + w**2) / 2

        # Width-only clearance for service-area / parking dividers.
        # Vehicles travel straight into those spaces.
        approach_buffer = w / 2.0

        buffered_elements = []
        for element in layout["elements"]:
            if element["class"] in non_blocking_classes:
                buffered_elements.append(element)
            elif element["class"] == "OBSTACLE LAYOUT":
                # Shrink the driveable boundary inward so the vehicle centre
                # never gets closer than the turning radius to the outer wall.
                buffered_polygon = element["polygon"].buffer(
                    -turning_buffer / 2
                )
                new_element = element.copy()
                new_element["polygon"] = buffered_polygon
                buffered_elements.append(new_element)
            elif element["class"] == "OBSTACLE" and (
                element.get("csa_divider") or element.get("parking_divider")
            ):
                # Service-area / parking dividers: straight-line approach
                # only.
                buffered_polygon = element["polygon"].buffer(
                    approach_buffer / 2
                )
                new_element = element.copy()
                new_element["polygon"] = buffered_polygon
                buffered_elements.append(new_element)
            elif element["class"] == "OBSTACLE":
                # Structural obstacles get full turning buffer.
                buffered_polygon = element["polygon"].buffer(
                    turning_buffer / 2
                )
                new_element = element.copy()
                new_element["polygon"] = buffered_polygon
                buffered_elements.append(new_element)
            else:
                buffered_elements.append(element)

        layout["elements"] = buffered_elements

    def _combine_obstacles(self, layout: dict[str, Any]) -> None:
        """Merges overlapping obstacle polygons for simpler geometry.

        Args:
            layout (dict): Layout to mutate in place.
        """
        obstacle_polygons = []
        non_obstacle_elements = []

        for element in layout["elements"]:
            if element["class"] == "OBSTACLE" and not element.get(
                "csa_divider"
            ):
                obstacle_polygons.append(element["polygon"])
            else:
                non_obstacle_elements.append(element)

        if obstacle_polygons:
            merged_geometry = unary_union(obstacle_polygons)
            if isinstance(merged_geometry, MultiPolygon):
                for i, poly in enumerate(merged_geometry.geoms):
                    non_obstacle_elements.append(
                        {
                            "class": "OBSTACLE",
                            "polygon": poly,
                            "id": 10000 + i,
                        }
                    )
            elif isinstance(merged_geometry, Polygon):
                non_obstacle_elements.append(
                    {
                        "class": "OBSTACLE",
                        "polygon": merged_geometry,
                        "id": 10000,
                    }
                )

        layout["elements"] = non_obstacle_elements

    def _split_csa_areas(self, layout: dict[str, Any]) -> None:
        """Splits service areas using only service-area divider obstacles.

        Args:
            layout (dict): Layout to mutate in place.
        """
        split_elements = []
        car_service_areas = []
        other_elements = []

        for element in layout["elements"]:
            if element["class"] == CAR_SERVICE_AREA_CLASS:
                car_service_areas.append(element)
            else:
                other_elements.append(element)

        all_obstacles = unary_union(
            [
                e["polygon"]
                for e in other_elements
                if e["class"] == "OBSTACLE" and e.get("csa_divider")
            ]
        )

        for service_area in car_service_areas:
            cleaned_service_area = service_area["polygon"].difference(
                all_obstacles
            )
            if cleaned_service_area.is_empty:
                continue
            elif isinstance(cleaned_service_area, MultiPolygon):
                for i, poly in enumerate(cleaned_service_area.geoms):
                    if not poly.is_empty and poly.area > 0.01:
                        split_elements.append(
                            {
                                "class": CAR_SERVICE_AREA_CLASS,
                                "polygon": poly,
                                "id": int(service_area["id"]) * 100 + i,
                            }
                        )
            elif isinstance(cleaned_service_area, Polygon):
                if (
                    not cleaned_service_area.is_empty
                    and cleaned_service_area.area > 0.01
                ):
                    split_elements.append(
                        {
                            "class": CAR_SERVICE_AREA_CLASS,
                            "polygon": cleaned_service_area,
                            "id": service_area["id"],
                        }
                    )

        split_elements.extend(other_elements)
        layout["elements"] = split_elements

    def _compute_voronoi_graph(
        self,
        layout: dict[str, Any],
        ignored_classes: list[str] | None = None,
    ) -> tuple[nx.Graph, list[LineString]]:
        """Builds medial-axis graph from buffered obstacles and waypoints.

        Args:
            layout (dict): Layout with obstacles and traversable polygons.
            ignored_classes (list[str] | None): Classes to skip as waypoints.

        Returns:
            tuple: Navigation graph and medial edges as shapely lines.
        """
        if ignored_classes is None:
            ignored_classes = []

        obstacles = []
        waypoints = {}
        layout_poly = None

        for element in layout["elements"]:
            if element["class"] == "OBSTACLE LAYOUT":
                layout_poly = element["polygon"]
            elif element["class"] == "OBSTACLE":
                obstacles.append(element["polygon"])
            elif element["class"] in ignored_classes:
                continue
            else:
                centroid = element["polygon"].centroid
                waypoints[f"{element['class']}_{element['id']}"] = {
                    "point": centroid,
                    "polygon": element["polygon"],
                    "type": element["class"],
                    "id": element["id"],
                }

        if layout_poly is None:
            return nx.Graph(), []

        boundary_points = []
        for obstacle in obstacles + [layout_poly]:
            exterior = obstacle.exterior
            length = exterior.length
            num_points = max(8, int(length * self.sampling_density))
            for i in range(num_points):
                distance = (i + 0.5) * length / num_points
                point = exterior.interpolate(distance)
                boundary_points.append((point.x, point.y))

        if len(boundary_points) < 4:
            return nx.Graph(), []

        vor = Voronoi(boundary_points)

        # Build medial axis by keeping Voronoi ridges separating obstacles
        obstacle_union = unary_union(obstacles)
        free_space = layout_poly.difference(obstacle_union)

        points_to_obstacle = {}
        for point_coords in boundary_points:
            point = Point(point_coords)
            min_dist = float("inf")
            closest_obstacle = None

            if layout_poly.boundary.distance(point) < 1e-6:
                closest_obstacle = 0
            else:
                for i, obstacle in enumerate(obstacles, 1):
                    dist = obstacle.boundary.distance(point)
                    if dist < min_dist:
                        min_dist = dist
                        closest_obstacle = i
            points_to_obstacle[tuple(point_coords)] = closest_obstacle

        medial_edges = []
        for ridge_idx, ridge in enumerate(vor.ridge_vertices):
            if -1 in ridge:
                continue

            seed_pair = vor.ridge_points[ridge_idx]
            seed1, seed2 = seed_pair[0], seed_pair[1]

            if seed1 >= len(boundary_points) or seed2 >= len(boundary_points):
                continue

            obstacle1 = points_to_obstacle.get(
                tuple(boundary_points[seed1]), None
            )
            obstacle2 = points_to_obstacle.get(
                tuple(boundary_points[seed2]), None
            )

            is_valid = False
            if obstacle1 is not None and obstacle2 is not None:
                if obstacle1 != obstacle2:
                    is_valid = True
                else:
                    # Same-obstacle ridge across open space: keep if the
                    # seeds are far apart.
                    min_seed_separation = 2.0  # meters
                    p1 = boundary_points[seed1]
                    p2 = boundary_points[seed2]
                    dist_sq = (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2
                    if dist_sq > min_seed_separation**2:
                        is_valid = True

            if is_valid:
                v1, v2 = vor.vertices[ridge[0]], vor.vertices[ridge[1]]
                edge_line = LineString([v1, v2])

                num_checks = 5
                edge_in_free_space = True
                for i in range(num_checks):
                    t = i / (num_checks - 1) if num_checks > 1 else 0.5
                    point = edge_line.interpolate(t, normalized=True)
                    if not free_space.intersects(point):
                        edge_in_free_space = False
                        break

                if edge_in_free_space:
                    medial_edges.append(edge_line)

        G = nx.Graph()
        vertex_to_id = {}
        next_id = 0

        for edge in medial_edges:
            coords = list(edge.coords)
            start, end = coords[0], coords[1]

            if start not in vertex_to_id:
                vertex_to_id[start] = next_id
                G.add_node(next_id, pos=start)
                next_id += 1
            if end not in vertex_to_id:
                vertex_to_id[end] = next_id
                G.add_node(next_id, pos=end)
                next_id += 1

            node1 = vertex_to_id[start]
            node2 = vertex_to_id[end]
            distance = edge.length
            G.add_edge(node1, node2, weight=distance)

        # Keep only the largest connected component, so waypoints never
        # attach to fragments unreachable from the rest of the layout.
        if G.number_of_nodes() > 0:
            components = list(nx.connected_components(G))
            if len(components) > 1:
                largest_component = max(components, key=len)
                nodes_to_remove = [
                    n for n in G.nodes() if n not in largest_component
                ]
                G.remove_nodes_from(nodes_to_remove)

        # Connect each waypoint to the nearest node with a clear
        # (non-obstacle-crossing) line of sight.
        node_positions = nx.get_node_attributes(G, "pos")
        node_ids = list(node_positions.keys())
        node_coords = list(node_positions.values())

        if node_coords:
            tree = cKDTree(node_coords)
            for wp_name, wp_data in waypoints.items():
                wp_point = wp_data["point"]
                wp_coords = (wp_point.x, wp_point.y)

                # Query nodes by distance to find the first with a clear path.
                distances, indices = tree.query(wp_coords, k=len(node_coords))

                connected = False
                for dist, idx in zip(distances, indices, strict=True):
                    nearest_node = node_ids[idx]
                    edge_line = LineString(
                        [wp_point, Point(node_positions[nearest_node])]
                    )

                    if not obstacle_union.intersects(edge_line):
                        wp_node_id = f"wp_{wp_name}"
                        G.add_node(
                            wp_node_id,
                            pos=wp_coords,
                            type=wp_data["type"],
                            waypoint_name=wp_name,
                            waypoint_id=wp_data["id"],
                            polygon=wp_data["polygon"],
                        )
                        G.add_edge(
                            wp_node_id,
                            nearest_node,
                            weight=dist,
                            geometry=edge_line,
                        )
                        connected = True
                        break

                if not connected:
                    wp_node_id = f"wp_{wp_name}"
                    G.add_node(
                        wp_node_id,
                        pos=wp_coords,
                        type=wp_data["type"],
                        waypoint_name=wp_name,
                        waypoint_id=wp_data["id"],
                        polygon=wp_data["polygon"],
                        isolated=True,
                    )

        return G, medial_edges

    def _simplify_to_junction_graph(self, G: nx.Graph) -> nx.Graph:
        """Simplify a graph to a junction graph by collapsing linear paths.

        A junction node is defined as:
        - A node with degree != 2 (dead ends, intersections, or waypoints)
        - A waypoint node (identified by node with 'type' attribute)

        Args:
            G (nx.Graph): NetworkX graph with 'pos' node attributes

        Returns:
            nx.Graph: Graph containing only junction nodes with edges
                representing collapsed paths. Edge attributes include:
                - 'weight': total distance of the collapsed path (geometric)
                - 'path': list of intermediate node IDs (including start/end)
                - 'geometry': LineString of the full path
        """
        G_simplified = nx.Graph()

        junction_nodes = set()
        for node in G.nodes():
            degree = G.degree(node)
            is_waypoint = G.nodes[node].get("type") is not None

            if degree != 2 or is_waypoint:
                junction_nodes.add(node)
                G_simplified.add_node(node, **G.nodes[node])

        # For each junction node, trace paths to other junction nodes
        for start_junction in junction_nodes:
            for neighbor in G.neighbors(start_junction):
                # Skip if neighbor is also a junction (direct connection)
                if neighbor in junction_nodes:
                    if not G_simplified.has_edge(start_junction, neighbor):
                        edge_data = G[start_junction][neighbor]
                        weight = edge_data.get("weight", 0)

                        pos1 = G.nodes[start_junction]["pos"]
                        pos2 = G.nodes[neighbor]["pos"]
                        geometry = LineString([pos1, pos2])

                        G_simplified.add_edge(
                            start_junction,
                            neighbor,
                            weight=weight,
                            path=[start_junction, neighbor],
                            geometry=geometry,
                        )
                    continue

                # Trace path through degree-2 nodes until another junction
                path = [start_junction, neighbor]
                total_weight = G[start_junction][neighbor].get("weight", 0)
                current = neighbor
                previous = start_junction

                while current not in junction_nodes:
                    neighbors = list(G.neighbors(current))
                    next_nodes = [n for n in neighbors if n != previous]

                    if len(next_nodes) != 1:
                        # Shouldn't happen if degree is 2, but handle it
                        break

                    next_node = next_nodes[0]
                    total_weight += G[current][next_node].get("weight", 0)
                    path.append(next_node)

                    previous = current
                    current = next_node

                end_junction = current
                if not G_simplified.has_edge(start_junction, end_junction):
                    path_coords = [G.nodes[node]["pos"] for node in path]
                    geometry = LineString(path_coords)

                    G_simplified.add_edge(
                        start_junction,
                        end_junction,
                        weight=total_weight,
                        path=path,
                        geometry=geometry,
                    )

        return G_simplified

    def _compute_turns(
        self,
        graph: nx.Graph,
        path_nodes: list[Any],
        normal_turn_threshold: float = 20.0,
        sharp_turn_threshold: float = 90.0,
    ) -> tuple[int, int]:
        """Computes the number of turns and sharp turns in a path.

        Args:
            graph (nx.Graph): Navigation graph with node positions.
            path_nodes (list[Any]): List of node IDs in the path.
            normal_turn_threshold (float): Angle threshold for normal turns.
            sharp_turn_threshold (float): Angle threshold for sharp turns.

        Returns:
            tuple[int, int]: Number of turns and number of sharp turns.
        """
        if len(path_nodes) < 3:
            return 0, 0

        coordinates = []
        for node in path_nodes:
            if node in graph.nodes:
                pos = graph.nodes[node].get("pos")
                if pos:
                    coordinates.append(np.array(pos))

        if len(coordinates) < 3:
            return 0, 0

        turns = 0
        sharp_turns = 0

        for i in range(1, len(coordinates) - 1):
            p1 = coordinates[i - 1]
            p2 = coordinates[i]
            p3 = coordinates[i + 1]

            v1 = p2 - p1
            v2 = p3 - p2

            norm1 = np.linalg.norm(v1)
            norm2 = np.linalg.norm(v2)

            if norm1 < 1e-6 or norm2 < 1e-6:
                continue

            v1 = v1 / norm1
            v2 = v2 / norm2

            dot_product = np.clip(np.dot(v1, v2), -1.0, 1.0)
            angle = np.degrees(np.arccos(dot_product))

            if angle > normal_turn_threshold:
                turns += 1
            if angle > sharp_turn_threshold:
                sharp_turns += 1

        return turns, sharp_turns

    def _get_path_coordinates(
        self, graph: nx.Graph, path_nodes: list[Any]
    ) -> list[tuple[float, float]]:
        """Extracts (x, y) coordinates for path nodes.

        Args:
            graph (nx.Graph): Navigation graph with node positions.
            path_nodes (list[Any]): List of node IDs in the path.

        Returns:
            list[tuple(float, float)]: List of (x, y) coordinates.
        """
        coordinates: list[tuple[float, float]] = []
        for node in path_nodes:
            if node in graph.nodes:
                pos = graph.nodes[node].get("pos")
                if pos:
                    coordinates.append((round(pos[0], 2), round(pos[1], 2)))
        return coordinates

    def _get_csa_siblings_and_connectors(
        self, graph: nx.Graph, csa_node: Any
    ) -> list[Any]:
        """Gets the sibling service-area waypoint and nearby medial nodes.

        Args:
            graph (nx.Graph): Navigation graph.
            csa_node (Any): A car service-area waypoint node.

        Returns:
            list[Any]: List of nodes to exclude (sibling waypoint and medial
                axis nodes within the sibling service area).
        """
        node_data = graph.nodes[csa_node]
        if node_data.get("type") != CAR_SERVICE_AREA_CLASS:
            return []

        csa_id = node_data.get("waypoint_id")
        if csa_id is None:
            return []

        # Extract original service-area ID before splitting.
        original_csa_id = int(csa_id) // 100

        sibling_node = None

        for node, data in graph.nodes(data=True):
            if data.get("type") == CAR_SERVICE_AREA_CLASS:
                node_csa_id = data.get("waypoint_id")
                if node_csa_id is not None:
                    node_original_id = int(node_csa_id) // 100
                    if (
                        node_original_id == original_csa_id
                        and node != csa_node
                    ):
                        sibling_node = node
                        break

        if sibling_node is None:
            return []

        sibling_data = graph.nodes[sibling_node]
        sibling_polygon = sibling_data.get("polygon")

        if sibling_polygon is None:
            return [sibling_node]

        nodes_to_exclude = [sibling_node]
        # Buffer the polygon slightly to catch nearby nodes.
        buffered_polygon = sibling_polygon.buffer(0.1)

        for node, data in graph.nodes(data=True):
            # Skip waypoint nodes, only check medial axis nodes.
            if str(node).startswith("wp_"):
                continue

            node_pos = data.get("pos")
            if node_pos is not None:
                point = Point(node_pos)
                if buffered_polygon.contains(point):
                    nodes_to_exclude.append(node)

        return nodes_to_exclude

    def _diagnose_unreachable_waypoint(
        self,
        graph: nx.Graph,
        waypoint_node: Any,
        layout: dict[str, Any],
        ctx: _DiagnosticContext | None = None,
    ) -> str:
        """Diagnose why a specific waypoint is unreachable.

        Two possible cases:
        1. Isolated waypoint: couldn't connect to any medial axis node
        2. Unreachable waypoint: connected to medial axis, but that part
           of the medial axis is disconnected from entrances

        Both cases use the same diagnostic: find obstacles blocking
        connection to the connected (entrance-reachable) medial axis.

        Args:
            graph (nx.Graph): Navigation graph.
            waypoint_node: The waypoint node to diagnose.
            layout (dict): Original layout with elements.

        Returns:
            str: Detailed reason for unreachability.
        """
        node_data = graph.nodes[waypoint_node]
        wp_type = node_data.get("type", "")

        waypoint_poly = node_data.get("polygon")

        if waypoint_poly is None:
            return (
                "it is placed outside of the layout or has invalid geometry"
            )

        layout_poly = None
        for element in layout["elements"]:
            if element.get("class") == LAYOUT_CLASS:
                layout_poly = element["polygon"]
                break

        if layout_poly and not layout_poly.contains(waypoint_poly):
            return "it is placed outside of the layout"

        blocking_elements = self._find_blocking_obstacles(
            waypoint_poly, layout, wp_type, ctx
        )

        if blocking_elements:
            if len(blocking_elements) == 1:
                elem = blocking_elements[0]
                orig_id = self._get_original_id(elem["class"], elem["id"])
                return (
                    f"it is isolated by Element "
                    f"'{elem['class']}' with id: {orig_id} which forms a "
                    f"barrier"
                )
            else:
                elem_names = [
                    f"'{e['class']}' with id: "
                    f"{self._get_original_id(e['class'], e['id'])}"
                    for e in blocking_elements[:3]
                ]
                if len(blocking_elements) > 3:
                    elem_str = (
                        f"{', '.join(elem_names)}, and "
                        f"{len(blocking_elements) - 3} other element(s)"
                    )
                else:
                    elem_str = ", ".join(elem_names)

                return (
                    f"it is isolated by a barrier formed by Elements "
                    f"{elem_str}"
                )

        return (
            "there is no navigable corridor to reach it "
            "(too narrow or completely blocked)"
        )

    def _get_original_id(self, element_class: str, graph_id: Any) -> Any:
        """Converts preprocessed graph ID back to original layout ID.

        Args:
            element_class (str): The element class.
            graph_id: The ID from the graph (possibly preprocessed).

        Returns:
            The original layout ID.
        """
        try:
            graph_id_int = int(graph_id)
        except (ValueError, TypeError):
            return graph_id

        # Car service areas are split with id * 100 + i, convert back.
        if element_class == CAR_SERVICE_AREA_CLASS and graph_id_int >= 100:
            return graph_id_int // 100

        # Merged obstacles have ids >= 10000, not in original layout.
        if element_class == "OBSTACLE" and graph_id_int >= 10000:
            return graph_id

        return graph_id

    def _precompute_connected_nodes(
        self, graph: nx.Graph, ctx: _DiagnosticContext
    ) -> None:
        """Populates diagnostic context with connected-node data.

        Computes reachable medial-axis positions and builds a KDTree
        for efficient nearest-neighbour queries during diagnostics.

        Args:
            graph (nx.Graph): Navigation graph.
            ctx (_DiagnosticContext): Context to populate.
        """
        entrance_nodes = [
            n
            for n, d in graph.nodes(data=True)
            if d.get("type") == ENTRANCE_CLASS
        ]

        if not entrance_nodes:
            ctx.connected_medial_positions = []
            ctx.connected_kdtree = None
            return

        connected_nodes = set()
        for entrance in entrance_nodes:
            try:
                reachable = nx.single_source_shortest_path_length(
                    graph, entrance
                )
                connected_nodes.update(reachable.keys())
            except Exception as exc:
                logger.debug(
                    "Skipping unreachable entrance node %s: %s", entrance, exc
                )
                continue

        if not connected_nodes:
            ctx.connected_medial_positions = []
            ctx.connected_kdtree = None
            return

        connected_medial_positions = []
        for node in connected_nodes:
            # Skip waypoint nodes, only consider medial axis nodes.
            if not str(node).startswith("wp_"):
                node_data = graph.nodes.get(node, {})
                pos = node_data.get("pos")
                if pos:
                    connected_medial_positions.append(pos)

        if not connected_medial_positions:
            ctx.connected_medial_positions = []
            ctx.connected_kdtree = None
            return

        positions = np.array(connected_medial_positions)
        ctx.connected_medial_positions = connected_medial_positions
        ctx.connected_kdtree = cKDTree(positions)

    def _get_entrance_edges(
        self, polygon: Polygon, wp_type: str
    ) -> list[LineString]:
        """Returns the entrance/exit edge LineStrings for a waypoint polygon.

        - Service areas: governed by `csa_entrance_side`.
        - Parking spaces: the two shortest sides are always the entrances.

        Args:
            polygon (Polygon): The waypoint polygon (may be a split half
                for a service area).
            wp_type (str): The waypoint class/type.

        Returns:
            list[LineString]: Entrance/exit edge lines (typically 2 for
                rectangular elements). Empty list for unsupported types.
        """
        is_csa = wp_type in SERVICE_AREA_CLASSES
        is_parking = wp_type in PARKING_SPACE_CLASSES

        if not (is_csa or is_parking):
            return []

        coords = list(polygon.exterior.coords[:-1])
        if len(coords) < 3:
            return []

        # Compute side lengths
        sides = []
        for i in range(len(coords)):
            p1 = coords[i]
            p2 = coords[(i + 1) % len(coords)]
            length = np.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
            sides.append(
                {
                    "start": p1,
                    "end": p2,
                    "length": length,
                    "line": LineString([p1, p2]),
                }
            )

        if is_csa:
            # For non-car service areas, entrance is always on the short side.
            if wp_type != CAR_SERVICE_AREA_CLASS:
                target_entrance_side = "short"
            else:
                target_entrance_side = self.csa_entrance_side

            # Entrance sides are those NOT blocked by service-area dividers.
            if target_entrance_side == "long":
                sides_sorted = sorted(
                    sides, key=lambda x: x["length"], reverse=True
                )
            else:
                sides_sorted = sorted(sides, key=lambda x: x["length"])
        else:
            # Parking: long sides are blocked → short sides are entrances.
            sides_sorted = sorted(sides, key=lambda x: x["length"])

        return [s["line"] for s in sides_sorted[:2]]

    def _find_entrance_exit_blockers(
        self,
        waypoint_poly: Polygon,
        wp_type: str,
        layout: dict[str, Any],
        ctx: _DiagnosticContext | None = None,
    ) -> list[dict[str, Any]]:
        """Identifies elements that physically block entrance/exit edges.

        Buffers the entrance/exit edges of the waypoint polygon by the
        vehicle clearance radius and checks which layout elements (also
        buffered) overlap with those corridors. This catches cases where
        an obstacle closes a doorway rather than blocking a line-of-sight
        path to the medial axis.

        For parking spaces, backtracking is allowed, so both entrance
        edges must be obstructed before any blocker is surfaced.
        For service areas, a single blocked entrance is sufficient.

        Args:
            waypoint_poly (Polygon): The polygon of the unreachable
                waypoint.
            wp_type (str): The waypoint class/type.
            layout (dict): Original layout with elements.

        Returns:
            list[dict]: Elements blocking entrance/exit corridors, each
                with `'class'` and `'id'` keys.
        """
        entrance_edges = self._get_entrance_edges(waypoint_poly, wp_type)
        if not entrance_edges:
            return []

        # Same approach-buffer as _buffer_obstacles: straight-line entry
        # needs only half the vehicle width as lateral clearance.
        avg_size = ctx.average_vehicle_size if ctx is not None else (4.5, 1.8)
        _, w = avg_size
        buffer_size = w / 2.0  # width-only clearance

        buffered_entrances = [
            edge.buffer(buffer_size) for edge in entrance_edges
        ]

        blockers_per_entrance: list[set] = [set() for _ in entrance_edges]
        for element in layout["elements"]:
            elem_class = element.get("class")
            # Skip the waypoint's own class and underground elements.
            if (
                elem_class == wp_type
                or elem_class in UNDERGROUND_CLASSES
                or not element.get("polygon")
            ):
                continue

            obs_poly = element["polygon"]
            if elem_class == LAYOUT_CLASS:
                check_geom = obs_poly.boundary
            else:
                check_geom = obs_poly.buffer(buffer_size / 2)

            for i, buffered_entrance in enumerate(buffered_entrances):
                if buffered_entrance.intersects(check_geom):
                    blockers_per_entrance[i].add(
                        (elem_class, element.get("id", ""))
                    )

        is_parking = wp_type in PARKING_SPACE_CLASSES
        if is_parking:
            # Parking allows backtracking: only flag blockers when every
            # entrance/exit edge is obstructed.
            if not all(len(b) > 0 for b in blockers_per_entrance):
                return []

        all_blockers: dict[tuple, dict[str, Any]] = {}
        for entrance_blockers in blockers_per_entrance:
            for obs_key in entrance_blockers:
                obs_class, obs_id = obs_key
                if obs_key not in all_blockers:
                    obs = next(
                        (
                            e
                            for e in layout["elements"]
                            if e.get("class") == obs_class
                            and e.get("id", "") == obs_id
                        ),
                        None,
                    )
                    dist = (
                        waypoint_poly.distance(obs["polygon"])
                        if obs
                        else float("inf")
                    )
                    all_blockers[obs_key] = {
                        "class": obs_class,
                        "id": obs_id,
                        "distance": dist,
                        "count": 0,
                    }
                all_blockers[obs_key]["count"] += 1

        if not all_blockers:
            return []

        # Sort by how many entrances are blocked (desc), then distance (asc).
        sorted_blockers = sorted(
            all_blockers.values(), key=lambda x: (-x["count"], x["distance"])
        )

        return [
            {"class": b["class"], "id": b["id"]} for b in sorted_blockers[:5]
        ]

    def _find_blocking_obstacles(
        self,
        waypoint_poly: Polygon,
        layout: dict[str, Any],
        wp_type: str,
        ctx: _DiagnosticContext | None = None,
    ) -> list[dict[str, Any]]:
        """Identifies obstacles that block connection to connected medial
        axis.

        Finds obstacles by drawing lines from the waypoint to nearby medial
        axis nodes that are connected to entrances, then checking which
        obstacles intersect those lines.

        Args:
            waypoint_poly (Polygon): The polygon of the unreachable waypoint.
            layout (dict): Original layout with elements.
            wp_type (str): The waypoint type/class.

        Returns:
            list[dict]: List of element dicts that block access,
                each with 'class' and 'id' keys.
        """
        obstacles = []

        for element in layout["elements"]:
            elem_class = element.get("class")
            # Everything but the waypoint's own class is potential obstacle,
            # unless underground.
            if elem_class == wp_type or elem_class in UNDERGROUND_CLASSES:
                continue

            if elem_class and element.get("polygon"):
                obstacles.append(
                    {
                        "class": elem_class,
                        "id": element.get("id", ""),
                        "polygon": element.get("polygon"),
                    }
                )

        if not obstacles:
            return []

        if (
            ctx is None
            or not ctx.connected_medial_positions
            or ctx.connected_kdtree is None
        ):
            return []

        waypoint_centroid = waypoint_poly.centroid
        wp_coords = np.array([waypoint_centroid.x, waypoint_centroid.y])

        # Find k nearest CONNECTED and ACCESSIBLE medial axis nodes.
        k_target = 5
        k_pool = min(10, len(ctx.connected_medial_positions))
        _, pool_indices = ctx.connected_kdtree.query(wp_coords, k=k_pool)

        # Normalise to a 1-D iterable (cKDTree returns a scalar when k=1).
        if np.ndim(pool_indices) == 0:
            pool_indices = [int(pool_indices)]

        valid_indices = []
        fallback_indices = []
        for idx in pool_indices:
            nearest_pos = ctx.connected_medial_positions[idx]
            test_line = LineString(
                [(waypoint_centroid.x, waypoint_centroid.y), nearest_pos]
            )
            if ctx.dividers is None or not test_line.intersects(ctx.dividers):
                valid_indices.append(idx)
                if len(valid_indices) == k_target:
                    break
            else:
                fallback_indices.append(idx)

        chosen_indices = valid_indices if valid_indices else fallback_indices
        obstacle_counts: dict[tuple[Any, Any], int] = {}

        for idx in chosen_indices:
            nearest_pos = ctx.connected_medial_positions[idx]
            connection_line = LineString(
                [(waypoint_centroid.x, waypoint_centroid.y), nearest_pos]
            )

            for obstacle in obstacles:
                obs_poly = obstacle["polygon"]
                # The layout polygon is hollow.
                check_geom = (
                    obs_poly.boundary
                    if obstacle["class"] == LAYOUT_CLASS
                    else obs_poly
                )
                if obs_poly and connection_line.intersects(check_geom):
                    obs_key = (obstacle["class"], obstacle["id"])
                    obstacle_counts[obs_key] = (
                        obstacle_counts.get(obs_key, 0) + 1
                    )

        # Prioritize obstacles blocking multiple directions, then by distance.
        blocking_obstacles = []
        for (obs_class, obs_id), count in obstacle_counts.items():
            obs = next(
                (
                    o
                    for o in obstacles
                    if o["class"] == obs_class and o["id"] == obs_id
                ),
                None,
            )
            if obs:
                dist = waypoint_poly.distance(obs["polygon"])
                blocking_obstacles.append(
                    {
                        "class": obs_class,
                        "id": obs_id,
                        "count": count,
                        "distance": dist,
                    }
                )

        blocking_obstacles.sort(key=lambda x: (-x["count"], x["distance"]))

        # Also check whether an obstacle physically closes an entrance or
        # exit edge (handles cases the centroid-to-medial tracing misses).
        entrance_blockers = self._find_entrance_exit_blockers(
            waypoint_poly, wp_type, layout, ctx
        )

        # Merge: entrance/exit blockers take priority; de-duplicate by key.
        seen_keys: set = set()
        combined: list[dict[str, Any]] = []
        for item in entrance_blockers:
            key = (item["class"], item["id"])
            if key not in seen_keys:
                seen_keys.add(key)
                combined.append(item)
        for obs in blocking_obstacles:
            key = (obs["class"], obs["id"])
            if key not in seen_keys:
                seen_keys.add(key)
                combined.append({"class": obs["class"], "id": obs["id"]})

        return combined[:5]

    def _find_all_path_combinations(
        self,
        graph: nx.Graph,
        sequence: list[str],
        no_backtracking: list[str] | None = None,
        ctx: _DiagnosticContext | None = None,
        validation_mode: Literal["any", "all"] = "any",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Finds paths for all combinations of waypoints in the sequence.

        Args:
            graph (nx.Graph): Navigation graph with waypoint metadata.
            sequence (list[str]): Class types to visit in order.
            no_backtracking (list[str], optional): List of waypoint types
                where backtracking is not allowed. Defaults to None.

        Returns:
            tuple: Valid paths and list of invalid route dicts with 'route'
            and 'reason'.
        """
        from itertools import product

        nodes_by_type: dict[str, list[Any]] = {}
        for node, data in graph.nodes(data=True):
            node_type = data.get("type")
            if node_type:
                if node_type not in nodes_by_type:
                    nodes_by_type[node_type] = []
                nodes_by_type[node_type].append(node)

        for node_type in sequence:
            if node_type not in nodes_by_type:
                invalid_entry = {
                    "route": sequence,
                    "reason": (
                        f"Element '{node_type}' is missing from the layout"
                    ),
                }
                return [], [invalid_entry]

        layers = [nodes_by_type[node_type] for node_type in sequence]
        all_combinations = list(product(*layers))

        results = []
        invalid_routes = []

        from collections import defaultdict

        core_tuple_success = defaultdict(bool)
        core_tuple_failures = defaultdict(list)

        for combination in all_combinations:
            path, length = self._find_path_through_waypoints(
                graph, combination, sequence, no_backtracking
            )

            core_tuple = combination[1:-1]

            if path:
                waypoint_map = {}
                for i, node_type in enumerate(sequence):
                    waypoint_map[node_type] = combination[i]

                num_turns, num_sharp_turns = self._compute_turns(graph, path)
                coordinates = self._get_path_coordinates(graph, path)

                results.append(
                    {
                        "waypoints": waypoint_map,
                        "path": path,
                        "length": length,
                        "num_turns": num_turns,
                        "num_sharp_turns": num_sharp_turns,
                        "coordinates": coordinates,
                    }
                )

                if validation_mode == "any":
                    core_tuple_success[core_tuple] = True
            else:
                route_with_ids = []
                unreachable_element = None
                unreachable_node = None

                # Identify which segment failed.
                for i in range(len(combination) - 1):
                    current = combination[i]
                    next_wp = combination[i + 1]
                    try:
                        nx.shortest_path_length(
                            graph, current, next_wp, weight="weight"
                        )
                    except nx.NetworkXNoPath:
                        node_data = graph.nodes[next_wp]
                        wp_id = node_data.get("waypoint_id", "")
                        _orig = self._get_original_id(sequence[i + 1], wp_id)
                        unreachable_element = f"{sequence[i + 1]}_{_orig}"
                        unreachable_node = next_wp
                        break

                for i, node in enumerate(combination):
                    node_data = graph.nodes[node]
                    wp_id = node_data.get("waypoint_id", "")
                    node_type = sequence[i]
                    orig_wp_id = self._get_original_id(node_type, wp_id)
                    route_with_ids.append(f"{node_type}_{orig_wp_id}")

                if unreachable_element is None:
                    unreachable_node = (
                        combination[1]
                        if len(combination) > 1
                        else combination[0]
                    )
                    node_data = graph.nodes[unreachable_node]
                    wp_id = node_data.get("waypoint_id", "")
                    node_type = node_data.get("type", "")
                else:
                    unreachable_node_data = graph.nodes[unreachable_node]
                    node_type = unreachable_node_data.get("type", "")
                    wp_id = unreachable_node_data.get("waypoint_id", "")

                orig_id = self._get_original_id(node_type, wp_id)

                detailed_reason = self._diagnose_unreachable_waypoint(
                    graph,
                    unreachable_node,
                    ctx.original_layout if ctx else {},
                    ctx,
                )

                invalid_entry = {
                    "route": route_with_ids,
                    "reason": (
                        f"Element '{node_type}' with id: {orig_id} is not "
                        f"reachable because {detailed_reason}"
                    ),
                }

                if validation_mode == "any":
                    core_tuple_failures[core_tuple].append(invalid_entry)
                else:
                    invalid_routes.append(invalid_entry)

        if validation_mode == "any":
            for core_tuple, failures in core_tuple_failures.items():
                if not core_tuple_success[core_tuple]:
                    invalid_routes.append(failures[0])

        return results, invalid_routes

    def _find_path_through_waypoints(
        self,
        graph: nx.Graph,
        waypoints: tuple[Any, ...],
        sequence: list[str],
        no_backtracking: list[str] | None = None,
    ) -> tuple[list[Any], float]:
        """Finds the shortest path through a specific sequence of waypoints.

        Args:
            graph (nx.Graph): Navigation graph.
            waypoints (tuple[Any, ...]): Concrete waypoint nodes to visit.
            sequence (list[str]): Class types corresponding to the waypoints.
            no_backtracking (list[str], optional): List of waypoint types
                where backtracking is not allowed. Defaults to None.

        Returns:
            tuple[list[Any], float]: Path nodes and accumulated length, or an
            empty path with `inf` if no connection exists.
        """
        full_path: list[Any] = []
        total_length = 0.0
        approach_nodes: list[Any] = []

        if no_backtracking is None:
            no_backtracking = []

        for i in range(len(waypoints) - 1):
            current_waypoint = waypoints[i]
            next_waypoint = waypoints[i + 1]
            current_type = sequence[i]

            is_csa = current_type == CAR_SERVICE_AREA_CLASS
            backtracking_restricted = current_type in no_backtracking

            try:
                # Enforce no-backtracking constraints via edge penalties
                MAX_PENALTY = 1000000.0
                needs_modification = bool(
                    is_csa or (backtracking_restricted and approach_nodes)
                )

                if needs_modification:
                    temp_graph = graph.copy()

                    # If leaving a car service area, exclude its siblings and
                    # connectors (completely remove them as they are illegal)
                    if is_csa:
                        siblings_and_connectors = (
                            self._get_csa_siblings_and_connectors(
                                temp_graph, current_waypoint
                            )
                        )
                        temp_graph.remove_nodes_from(siblings_and_connectors)

                    # Prevent backtracking through the approach corridor by
                    # penalizing the final edge instead of removing nodes.
                    if (is_csa or backtracking_restricted) and approach_nodes:
                        if temp_graph.has_edge(
                            approach_nodes[-1], approach_nodes[-2]
                        ):
                            temp_graph[approach_nodes[-1]][
                                approach_nodes[-2]
                            ]["weight"] += MAX_PENALTY
                    try:
                        segment = nx.shortest_path(
                            temp_graph,
                            current_waypoint,
                            next_waypoint,
                            weight="weight",
                        )
                        segment_length = nx.shortest_path_length(
                            temp_graph,
                            current_waypoint,
                            next_waypoint,
                            weight="weight",
                        )
                    except nx.NetworkXNoPath:
                        # inf length triggers the MAX_PENALTY handling below.
                        segment_length = float("inf")

                    if segment_length >= MAX_PENALTY:
                        # The penalized edge was the only option, so the
                        # backtracking constraint could not be satisfied.
                        return [], float("inf")

                else:
                    segment = nx.shortest_path(
                        graph,
                        current_waypoint,
                        next_waypoint,
                        weight="weight",
                    )
                    segment_length = nx.shortest_path_length(
                        graph,
                        current_waypoint,
                        next_waypoint,
                        weight="weight",
                    )

                if full_path:
                    full_path.extend(segment[1:])
                else:
                    full_path = segment

                total_length += segment_length

                # Track approach nodes to discourage service-area
                # backtracking.
                for node in segment:
                    if not str(node).startswith("wp_"):
                        approach_nodes.append(node)

            except nx.NetworkXNoPath:
                return [], float("inf")

        return full_path, total_length

    def visualize(
        self,
        layout: dict[str, Any],
        graph: nx.Graph | None = None,
        results: list[dict[str, Any]] | None = None,
        show: bool = True,
        save_path: str | None = None,
        return_base64: bool = False,
        return_figure: bool = False,
    ) -> str | tuple | None:
        """Visualizes the layout and paths from find_shortest_paths.

        Args:
            layout (dict): The layout dictionary.
            graph: The navigation graph from find_shortest_paths.
            results (list): List of result dicts with 'route' and 'paths'
                keys (the 'results' from find_shortest_paths return).
            show (bool): Whether to display the plot.
            save_path (str, optional): Path to save the plot.
            return_base64 (bool): Whether to return base64-encoded PNG.
            return_figure (bool): Whether to return the matplotlib figure.

        Returns:
            str | None: Base64-encoded PNG string if return_base64 is True,
                else None.
            tuple | None: (fig, ax) if return_figure is True, else None.
        """
        all_paths = None
        if results:
            all_paths = []
            for res in results:
                if "paths" in res:
                    all_paths.extend(res["paths"])

        from cando.utils.visualizer import plot_layout

        return plot_layout(
            layout,
            graph=graph,
            shortest_paths=all_paths,
            show=show,
            save_path=save_path,
            return_base64=return_base64,
            return_figure=return_figure,
        )
