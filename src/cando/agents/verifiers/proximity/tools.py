# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for verifying proximity, stacking, and distance rules.

Tools for layout proximity compliance verification.
"""

import math
from typing import Any

from shapely.geometry import LineString, Point, Polygon

from cando.agents.verifiers.tools.data import (
    get_edge_by_type,
    get_elements_by_class,
)
from cando.agents.verifiers.tools.geometric import (
    calculate_distance_edge_to_edge,
    check_edges_parallel,
)
from cando.constants.classes import (
    INPUT_CLASSES,
    PARKING_SPACE_CLASSES,
    UNDERGROUND_CLASSES,
)
from cando.utils.geometry import (
    calculate_edge_midpoint,
    calculate_edge_projection_overlap,
    get_edge_outward_normal,
    normalize_vector,
)


def get_element_priority(element: dict, priorities: dict) -> int:
    """Gets priority value for an element class from priorities dict.

    Args:
        element (dict): Element dictionary with 'class' key.
        priorities (dict): Dictionary mapping class names to priority values.

    Returns:
        int: Priority value (higher = more priority).
    """
    class_name = element.get("class", "")
    return priorities.get(class_name, 0)


def calculate_polygon_distance(elem1: dict, elem2: dict) -> float:
    """Calculates shortest distance between two elements (polygon boundaries).

    Args:
        elem1 (dict): First element dictionary.
        elem2 (dict): Second element dictionary.

    Returns:
        float: Minimum distance between element boundaries.
    """
    poly1 = Polygon(elem1.get("polygon", []))
    poly2 = Polygon(elem2.get("polygon", []))

    if not poly1.is_valid or not poly2.is_valid:
        return float("inf")

    return poly1.distance(poly2)


def get_edge_length(edge: tuple) -> float:
    """Calculates length of an edge from polygon.

    Args:
        edge (tuple): Edge as ((x1, y1), (x2, y2)).

    Returns:
        float: Length of the edge.
    """
    p1, p2 = edge
    return math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)


def calculate_available_space_on_edge(
    target_element: dict,
    target_edge_type: str,
    all_elements: list[dict],
    tolerance: float = 0.5,
) -> list[dict]:
    """Calculates available (unoccupied) space on target element's edges.

    Args:
        target_element (dict): Target element to check edges on.
        target_edge_type (str): Type of edge to check ('longer', 'shorter',
            'any').
        all_elements (list[dict]): All layout elements.
        tolerance (float): Distance tolerance for considering space occupied.

    Returns:
        list[dict]: List of dicts with edge info, available length, and
            occupying elements.
    """
    target_edges = get_edge_by_type(target_element, target_edge_type)
    if not target_edges:
        return []

    available_spaces = []

    for edge in target_edges:
        edge_line = LineString([edge[0], edge[1]])
        edge_length = get_edge_length(edge)

        # Find elements occupying this edge
        occupying_elements = []

        for elem in all_elements:
            if elem.get("id") == target_element.get("id"):
                continue

            # Skip Layout Polygon (premises boundary) and underground
            # facilities.
            elem_class = elem.get("class", "")
            if elem_class in [INPUT_CLASSES[0]] + UNDERGROUND_CLASSES:
                continue

            elem_poly = Polygon(elem.get("polygon", []))
            if not elem_poly.is_valid:
                continue

            # Check if element is close to this edge (stacked against it)
            distance = edge_line.distance(elem_poly)
            if distance <= tolerance:
                occupying_elements.append(elem)

        # Calculate occupied length by projecting occupying elements
        occupied_length = 0.0
        for occ_elem in occupying_elements:
            # Find the edge of occupying element that is parallel and close
            # to the target edge (the actual stacking edge)
            occ_edges = get_edge_by_type(occ_elem, "any")
            if not occ_edges:
                continue

            # Find parallel edges that are close to the target edge
            best_stacking_edge = None
            min_parallel_dist = float("inf")

            for occ_edge in occ_edges:
                # Check if this edge is parallel to target edge
                if check_edges_parallel(edge, occ_edge, tolerance_deg=5.0):
                    # For parallel edges, calculate distance between them
                    occ_edge_line = LineString([occ_edge[0], occ_edge[1]])
                    dist = edge_line.distance(occ_edge_line)

                    if dist < min_parallel_dist:
                        min_parallel_dist = dist
                        best_stacking_edge = occ_edge

            # Only count the edge if it's actually stacked (close enough)
            if best_stacking_edge and min_parallel_dist <= tolerance:
                occupied_length += get_edge_length(best_stacking_edge)

        available_length = max(0, edge_length - occupied_length)

        available_spaces.append(
            {
                "edge": edge,
                "edge_line": edge_line,
                "total_length": edge_length,
                "available_length": available_length,
                "occupying_elements": occupying_elements,
            }
        )

    return available_spaces


def check_stacking_space_availability(
    source_element: dict,
    source_edge_type: str,
    target_element: dict,
    target_edge_type: str,
    all_elements: list[dict],
    priorities: dict,
    tolerance: float = 0.5,
) -> dict:
    """Checks if there's available space for source to stack on target.

    Args:
        source_element (dict): Element that needs to be stacked.
        source_edge_type (str): Type of edge on source.
        target_element (dict): Element to stack against.
        target_edge_type (str): Type of edge on target.
        all_elements (list[dict]): All layout elements.
        priorities (dict): Dictionary mapping class names to priority values.
        tolerance (float): Distance tolerance.

    Returns:
        dict: Dictionary with availability information.
    """
    # Get source element's edge as specified in the rule
    source_edges = get_edge_by_type(source_element, source_edge_type)
    if not source_edges:
        return {
            "space_available": False,
            "reason": "Could not extract source element edges",
            "can_fit": False,
        }

    # Get required length for source element
    required_length = get_edge_length(source_edges[0])

    # Get available spaces on target edge
    available_spaces = calculate_available_space_on_edge(
        target_element, target_edge_type, all_elements, tolerance
    )

    if not available_spaces:
        return {
            "space_available": False,
            "reason": "No valid edges found on target element",
            "can_fit": False,
        }

    # Check if any edge has enough space
    for space_info in available_spaces:
        total_length = space_info["total_length"]
        available_length = space_info["available_length"]
        occupying_elements = space_info["occupying_elements"]

        # Check if source element can fit in available space
        if available_length >= required_length:
            return {
                "space_available": True,
                "reason": "Sufficient space available",
                "can_fit": True,
                "available_length": available_length,
                "required_length": required_length,
            }

        # Check if edge is long enough but occupied
        if total_length >= required_length:
            # Check priorities of occupying elements
            source_priority = get_element_priority(source_element, priorities)
            lower_priority_blockers = []

            for occ_elem in occupying_elements:
                occ_priority = get_element_priority(occ_elem, priorities)
                if occ_priority < source_priority:
                    lower_priority_blockers.append(occ_elem)

            if lower_priority_blockers:
                return {
                    "space_available": True,
                    "reason": "Space occupied by lower priority elements",
                    "can_fit": True,
                    "blocked_by_lower_priority": True,
                    "blocking_elements": lower_priority_blockers,
                }
            else:
                return {
                    "space_available": False,
                    "reason": (
                        "Space occupied by equal or higher priority elements"
                    ),
                    "can_fit": True,
                    "blocked_by_higher_priority": True,
                    "blocking_elements": occupying_elements,
                }

    # Edge is too short to fit the element
    return {
        "space_available": False,
        "reason": "Target edge too short for source element",
        "can_fit": False,
        "max_available_length": max(
            s["total_length"] for s in available_spaces
        ),
        "required_length": required_length,
    }


def verify_stacking(
    source_element: dict,
    target_element: dict,
    source_edge_type: str = "any",
    target_edge_type: str = "any",
    tolerance: float = 0.5,
) -> dict:
    """Verifies if source element is stacked against target element.

    Stacking means edges are touching or very close (within tolerance).

    Args:
        source_element (dict): Source element dictionary.
        target_element (dict): Target element dictionary.
        source_edge_type (str): Type of edge from source ('longer', 'shorter',
            'any').
        target_edge_type (str): Type of edge from target ('longer', 'shorter',
            'any').
        tolerance (float): Maximum distance to consider as stacked.

    Returns:
        dict: Dictionary with compliance information.
    """
    source_edges = get_edge_by_type(source_element, source_edge_type)
    target_edges = get_edge_by_type(target_element, target_edge_type)

    if not source_edges or not target_edges:
        return {
            "compliant": False,
            "distance": float("inf"),
            "details": "Could not extract edges from elements",
        }

    # Find minimum distance between parallel edge pairs only
    min_distance = float("inf")

    for s_edge in source_edges:
        for t_edge in target_edges:
            if not check_edges_parallel(s_edge, t_edge, tolerance_deg=15.0):
                continue
            distance = calculate_distance_edge_to_edge(s_edge, t_edge)
            min_distance = min(min_distance, distance)

    min_distance = round(min_distance, 2)
    is_stacked = min_distance <= tolerance

    status = "Stacked" if is_stacked else "Not stacked"
    return {
        "compliant": is_stacked,
        "distance": min_distance,
        "details": f"{status} - distance: {min_distance:.2f}",
    }


def verify_adjacency(
    source_element: dict,
    target_element: dict,
    max_distance: float = 2.0,
    tolerance: float = 0.5,
) -> dict:
    """Verifies if source element is proximally adjacent to target element.

    Adjacency means elements are close (within max_distance).

    Args:
        source_element (dict): Source element dictionary.
        target_element (dict): Target element dictionary.
        max_distance (float): Maximum distance to consider as adjacent.
        tolerance (float): Additional tolerance for distance.

    Returns:
        dict: Dictionary with compliance information.
    """
    distance = calculate_polygon_distance(source_element, target_element)

    is_adjacent = distance <= (max_distance + tolerance)

    status = "Adjacent" if is_adjacent else "Not adjacent"
    return {
        "compliant": is_adjacent,
        "distance": round(distance, 2),
        "details": f"{status} - distance: {distance:.2f}",
    }


def verify_distance_constraint(
    source_element: dict,
    target_element: dict,
    min_distance: float | None = None,
    max_distance: float | None = None,
    target_distance: float | None = None,
    tolerance: float = 0.5,
) -> dict:
    """Verifies distance constraint between elements.

    Args:
        source_element (dict): Source element dictionary.
        target_element (dict): Target element dictionary.
        min_distance (float | None): Minimum required distance
            (if specified).
        max_distance (float | None): Maximum allowed distance
            (if specified).
        target_distance (float | None): Target distance with tolerance
            (if specified).
        tolerance (float): Tolerance for distance checks.

    Returns:
        dict: Dictionary with compliance information.
    """
    distance = calculate_polygon_distance(source_element, target_element)

    compliant = True
    violations = []

    if min_distance is not None and distance < (min_distance - tolerance):
        compliant = False
        violations.append(
            f"Distance {distance:.2f} is less than minimum {min_distance:.2f}"
        )

    if max_distance is not None and distance > (max_distance + tolerance):
        compliant = False
        violations.append(
            f"Distance {distance:.2f} exceeds maximum {max_distance:.2f}"
        )

    if target_distance is not None:
        if abs(distance - target_distance) > tolerance:
            compliant = False
            violations.append(
                f"Distance {distance:.2f} deviates from target "
                f"{target_distance:.2f}"
            )

    details = (
        "Distance constraint satisfied"
        if compliant
        else "; ".join(violations)
    )
    return {
        "compliant": compliant,
        "distance": round(distance, 2),
        "violations": violations,
        "details": details,
    }


def verify_clearance(
    source_elements: list[dict],
    all_elements: list[dict],
    clearance_radius: float,
    allowed_classes: list[str],
    tolerance: float = 0.5,
) -> dict:
    """Verifies clearance radius around source elements.

    Check that no disallowed elements are within the clearance radius.

    Args:
        source_elements (list[dict]): List of source element dictionaries.
        all_elements (list[dict]): List of all layout elements.
        clearance_radius (float): Required clearance radius.
        allowed_classes (list[str]): List of element classes allowed within
            clearance.
        tolerance (float): Tolerance for clearance checks.

    Returns:
        dict: Dictionary with compliance information.
    """
    violations = []

    for source_elem in source_elements:
        source_poly = Polygon(source_elem.get("polygon", []))
        if not source_poly.is_valid:
            continue

        # Create buffer around source element
        clearance_zone = source_poly.buffer(clearance_radius + tolerance)

        for elem in all_elements:
            # Skip source element itself
            if elem.get("id") == source_elem.get("id"):
                continue

            elem_class = elem.get("class", "")

            # Skip allowed classes
            if elem_class in allowed_classes:
                continue

            # Check if element is within clearance zone
            elem_poly = Polygon(elem.get("polygon", []))
            if not elem_poly.is_valid:
                continue

            if clearance_zone.intersects(elem_poly):
                distance = source_poly.distance(elem_poly)
                violations.append(
                    {
                        "source_id": source_elem.get("id"),
                        "violating_id": elem.get("id"),
                        "violating_class": elem_class,
                        "distance": round(distance, 2),
                        "required_clearance": clearance_radius,
                    }
                )

    details = (
        "Clearance satisfied"
        if not violations
        else f"{len(violations)} clearance violations"
    )
    return {
        "compliant": len(violations) == 0,
        "violations": violations,
        "details": details,
    }


def _find_stacked_edge_contacts(
    source_element: dict, all_elements: list[dict], tolerance: float
) -> dict[int, list[dict]]:
    """Maps source edge indices to target elements stacked against them."""
    source_edges = get_edge_by_type(source_element, "any") or []
    contacts: dict[int, list[dict]] = {}

    for edge_index, source_edge in enumerate(source_edges):
        source_length = get_edge_length(source_edge)
        min_overlap = max(tolerance / 2.0, source_length * 0.1)

        for target_element in all_elements:
            if target_element.get("id") == source_element.get("id"):
                continue

            target_edges = get_edge_by_type(target_element, "any") or []
            for target_edge in target_edges:
                if not check_edges_parallel(
                    source_edge, target_edge, tolerance_deg=15.0
                ):
                    continue

                distance = calculate_distance_edge_to_edge(
                    source_edge, target_edge
                )
                if distance > tolerance:
                    continue

                overlap = calculate_edge_projection_overlap(
                    source_edge, target_edge
                )
                if overlap < min_overlap:
                    continue

                contacts.setdefault(edge_index, []).append(
                    {
                        "target_id": target_element.get("id"),
                        "target_class": target_element.get("class"),
                        "distance": round(distance, 2),
                    }
                )
                break

    return contacts


def _find_parallel_opposite_edge_index(
    edges: list[tuple[tuple[float, float], tuple[float, float]]],
    stacked_edge_index: int,
) -> int | None:
    """Finds the edge opposite a stacked edge in a rectangle/parallelogram."""
    stacked_edge = edges[stacked_edge_index]
    best_index = None
    best_distance = float("-inf")

    for edge_index, edge in enumerate(edges):
        if edge_index == stacked_edge_index:
            continue
        if not check_edges_parallel(stacked_edge, edge, tolerance_deg=10.0):
            continue

        distance = calculate_distance_edge_to_edge(stacked_edge, edge)
        if distance > best_distance:
            best_distance = distance
            best_index = edge_index

    return best_index


def _rank_exit_edge_candidates(
    element: dict,
    edges: list[tuple[tuple[float, float], tuple[float, float]]],
    stacked_edge_indices: list[int],
) -> list[int]:
    """Returns candidate exit edges ordered by best expected maneuvering."""
    stacked_set = set(stacked_edge_indices)
    candidate_indices = [
        index for index in range(len(edges)) if index not in stacked_set
    ]
    if not candidate_indices:
        return []

    if len(stacked_edge_indices) == 1:
        opposite_index = _find_parallel_opposite_edge_index(
            edges, stacked_edge_indices[0]
        )
        if opposite_index is not None:
            return [opposite_index]

    desired_dx = 0.0
    desired_dy = 0.0
    for stacked_edge_index in stacked_edge_indices:
        normal = get_edge_outward_normal(element, edges[stacked_edge_index])
        if normal is None:
            continue
        desired_dx -= normal[0]
        desired_dy -= normal[1]

    desired_direction = normalize_vector(desired_dx, desired_dy)
    if desired_direction is None:
        return candidate_indices

    def candidate_score(edge_index: int) -> float:
        normal = get_edge_outward_normal(element, edges[edge_index])
        if normal is None:
            return float("-inf")
        return (
            normal[0] * desired_direction[0]
            + normal[1] * desired_direction[1]
        )

    return sorted(candidate_indices, key=candidate_score, reverse=True)


def _get_exit_reference_edges(
    stacked_contacts: dict[int, list[dict]],
    tolerance: float,
) -> list[int]:
    """Returns stacked edges that should define the parking exit direction."""
    non_parking_edges = []

    for edge_index, contacts in stacked_contacts.items():
        if any(
            contact.get("target_class") not in PARKING_SPACE_CLASSES
            for contact in contacts
        ):
            non_parking_edges.append(edge_index)

    candidate_edges = non_parking_edges or sorted(stacked_contacts.keys())
    if not candidate_edges:
        return []

    min_distances = {
        edge_index: min(
            contact.get("distance", float("inf"))
            for contact in stacked_contacts[edge_index]
        )
        for edge_index in candidate_edges
    }
    best_distance = min(min_distances.values())
    distance_slack = tolerance / 2.0

    return [
        edge_index
        for edge_index in candidate_edges
        if min_distances[edge_index] <= best_distance + distance_slack
    ]


def _build_exit_clearance_zone(
    element: dict,
    edge: tuple[tuple[float, float], tuple[float, float]],
    clearance_radius: float,
    tolerance: float,
) -> Polygon | None:
    """Builds a directional semicircular clearance zone from an exit edge."""
    normal = get_edge_outward_normal(element, edge)
    if normal is None:
        return None

    midpoint = calculate_edge_midpoint(edge)
    radius = clearance_radius + tolerance
    disk = Point(midpoint).buffer(radius, resolution=32)

    perpendicular = (-normal[1], normal[0])
    extent = radius * 2.0
    half_plane = Polygon(
        [
            (
                midpoint[0] + perpendicular[0] * extent,
                midpoint[1] + perpendicular[1] * extent,
            ),
            (
                midpoint[0] + normal[0] * extent + perpendicular[0] * extent,
                midpoint[1] + normal[1] * extent + perpendicular[1] * extent,
            ),
            (
                midpoint[0] + normal[0] * extent - perpendicular[0] * extent,
                midpoint[1] + normal[1] * extent - perpendicular[1] * extent,
            ),
            (
                midpoint[0] - perpendicular[0] * extent,
                midpoint[1] - perpendicular[1] * extent,
            ),
        ]
    )
    clearance_zone = disk.intersection(half_plane)
    if clearance_zone.is_empty or not isinstance(clearance_zone, Polygon):
        return None
    return clearance_zone


def verify_stacked_parking_clearance(
    source_elements: list[dict],
    all_elements: list[dict],
    clearance_radius: float,
    allowed_classes: list[str],
    tolerance: float = 0.5,
) -> dict:
    """Verifies directional maneuvering clearance for stacked parking bays."""
    violations = []
    evaluated_sources = []

    for source_elem in source_elements:
        source_poly = Polygon(source_elem.get("polygon", []))
        if not source_poly.is_valid:
            continue

        edges = get_edge_by_type(source_elem, "any") or []
        if not edges:
            continue

        stacked_contacts = _find_stacked_edge_contacts(
            source_elem, all_elements, tolerance
        )
        stacked_edge_indices = sorted(stacked_contacts.keys())
        if not stacked_edge_indices:
            continue

        evaluated_sources.append(source_elem.get("id"))
        exit_reference_edges = _get_exit_reference_edges(
            stacked_contacts,
            tolerance,
        )
        exit_candidates = _rank_exit_edge_candidates(
            source_elem, edges, exit_reference_edges
        )

        if not exit_candidates:
            violations.append(
                {
                    "source_id": source_elem.get("id"),
                    "violating_id": None,
                    "violating_class": None,
                    "distance": None,
                    "required_clearance": clearance_radius,
                    "details": "Could not determine an exit edge",
                    "stacked_edges": stacked_edge_indices,
                    "exit_edge_index": None,
                }
            )
            continue

        candidate_intrusions: list[dict] = []
        candidate_summary = None

        for exit_edge_index in exit_candidates:
            exit_edge = edges[exit_edge_index]
            clearance_zone = _build_exit_clearance_zone(
                source_elem, exit_edge, clearance_radius, tolerance
            )
            if clearance_zone is None or clearance_zone.is_empty:
                continue

            intrusions = []
            for elem in all_elements:
                if elem.get("id") == source_elem.get("id"):
                    continue

                elem_class = elem.get("class", "")
                if elem_class in allowed_classes:
                    continue

                elem_poly = Polygon(elem.get("polygon", []))
                if not elem_poly.is_valid:
                    continue

                if clearance_zone.intersects(elem_poly):
                    intrusions.append(
                        {
                            "source_id": source_elem.get("id"),
                            "violating_id": elem.get("id"),
                            "violating_class": elem_class,
                            "distance": round(
                                source_poly.distance(elem_poly), 2
                            ),
                            "required_clearance": clearance_radius,
                            "details": (
                                "Obstacle intrudes into exit maneuver zone"
                            ),
                            "stacked_edges": stacked_edge_indices,
                            "exit_edge_index": exit_edge_index,
                        }
                    )

            if not intrusions:
                candidate_intrusions = []
                candidate_summary = {
                    "source_id": source_elem.get("id"),
                    "stacked_edges": stacked_edge_indices,
                    "exit_edge_index": exit_edge_index,
                }
                break

            intrusions.sort(
                key=lambda item: (
                    item["distance"]
                    if item["distance"] is not None
                    else float("inf"),
                    item["violating_id"]
                    if item["violating_id"] is not None
                    else -1,
                )
            )
            if not candidate_intrusions or len(intrusions) < len(
                candidate_intrusions
            ):
                candidate_intrusions = intrusions
                candidate_summary = {
                    "source_id": source_elem.get("id"),
                    "stacked_edges": stacked_edge_indices,
                    "exit_edge_index": exit_edge_index,
                }

        if candidate_intrusions:
            violations.extend(candidate_intrusions)
        elif (
            candidate_summary and candidate_summary["exit_edge_index"] is None
        ):
            violations.append(
                {
                    "source_id": source_elem.get("id"),
                    "violating_id": None,
                    "violating_class": None,
                    "distance": None,
                    "required_clearance": clearance_radius,
                    "details": "Could not determine an exit edge",
                    "stacked_edges": candidate_summary["stacked_edges"],
                    "exit_edge_index": None,
                }
            )

    details = (
        "Clearance satisfied"
        if not violations
        else f"{len(violations)} clearance violations"
    )
    return {
        "compliant": len(violations) == 0,
        "violations": violations,
        "details": details,
        "evaluated_source_ids": evaluated_sources,
    }


def verify_proximity_preference(
    source_element: dict,
    proximity_targets: list[dict],
) -> dict:
    """Verifies if source element is positioned close to proximity targets.

    Checks positioning while being stacked/positioned against anchor element.

    Args:
        source_element (dict): Source element dictionary.
        proximity_targets (list[dict]): List of elements source should be
            close to.

    Returns:
        dict: Dictionary with compliance information.
    """
    if not proximity_targets:
        return {
            "compliant": True,
            "details": "No proximity targets specified",
        }

    # Calculate average distance to proximity targets
    distances = [
        calculate_polygon_distance(source_element, target)
        for target in proximity_targets
    ]

    avg_distance = sum(distances) / len(distances) if distances else 0

    return {
        "compliant": True,  # This is a preference, not a hard constraint
        "average_distance": round(avg_distance, 2),
        "distances": [round(d, 2) for d in distances],
        "details": (
            f"Average distance to proximity targets: {avg_distance:.2f}"
        ),
    }


def _check_entrance_proximate(
    source_elem: dict,
    target_elem: dict,
    all_elements: list[dict],
    tolerance: float = 0.5,
) -> tuple[bool, str | None]:
    """Check that Priority Parking has the minimum entrance distance among
    all parking elements stacked against the same Shop.

    For each parking element stacked against ``target_elem``, the minimum
    distance to any Entrance is computed.  The rule is satisfied when
    ``source_elem`` has a distance that is no greater than the minimum found
    across all other stacked parking elements (allowing for ``tolerance``
    slack so a tie is not penalised).

    Args:
        source_elem (dict): Priority Parking element being evaluated.
        target_elem (dict): Shop element it is stacked against.
        all_elements (list[dict]): All layout elements.
        tolerance (float): Slack applied when comparing entrance distances.

    Returns:
        tuple[bool, str | None]: (compliant, reason_if_not_compliant)
    """
    entrance_elements = [
        e for e in all_elements if e.get("class") == "Entrance"
    ]
    if not entrance_elements:
        return True, None

    def _min_entrance_dist(elem: dict) -> float:
        return min(
            calculate_polygon_distance(elem, ent) for ent in entrance_elements
        )

    source_dist = _min_entrance_dist(source_elem)
    source_id = source_elem.get("id")
    target_id = target_elem.get("id")

    # Find all *parking* elements stacked against the same target (Shop),
    # excluding the source element itself.
    closest_other_id = None
    closest_other_class = None
    closest_other_dist = float("inf")

    for elem in all_elements:
        elem_id = elem.get("id")
        if elem_id == source_id or elem_id == target_id:
            continue
        if elem.get("class") not in PARKING_SPACE_CLASSES:
            continue
        if calculate_polygon_distance(elem, target_elem) > tolerance:
            continue
        d = _min_entrance_dist(elem)
        if d < closest_other_dist:
            closest_other_dist = d
            closest_other_id = elem_id
            closest_other_class = elem.get("class")

    if closest_other_id is None:
        # No other parking stacked on this Shop — trivially compliant.
        return True, None

    if source_dist <= closest_other_dist + tolerance:
        return True, None

    return False, (
        f"element {closest_other_id} ({closest_other_class}) "
        f"stacked on the same Shop is {closest_other_dist:.2f}m from the "
        f"nearest Entrance, closer than Priority Parking "
        f"({source_dist:.2f}m)"
    )


def evaluate_stacking_rule(
    elements: list[dict], rule: dict, priorities: dict | None = None
) -> dict:
    """Evaluates a stacking rule against the layout.

    Args:
        elements (list[dict]): List of all element dictionaries.
        rule (dict): Rule dictionary from rules.json.
        priorities (dict): Dictionary mapping class names to priority values.

    Returns:
        dict: Dictionary with evaluation results.
    """
    if priorities is None:
        priorities = {}

    source_class = rule.get("source_class", "All")
    source_edge = rule.get("source_edge", "any")
    target_edge = rule.get("target_edge", "any")
    tolerance = rule.get("parameters", {}).get("stacking_tolerance", 0.5)

    source_elements = get_elements_by_class(elements, source_class)

    if not source_elements:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": (
                f"No elements of class '{source_class}' found in layout"
            ),
        }

    # Handle single or multiple target classes
    target_classes = rule.get("target_classes", [])
    if not target_classes:
        target_class = rule.get("target_class")
        if target_class:
            target_classes = [target_class]

    if not target_classes:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": "No target classes specified in rule",
        }

    all_elements = elements
    results = []
    all_compliant = True
    violations = []

    # Pre-compute values for optional post-stacking checks
    max_prox_dist = rule.get("parameters", {}).get("max_proximity_distance")
    entrance_proximate = rule.get("entrance_proximate", False)
    proximity_target_elements = []
    if max_prox_dist is not None and "proximity_targets" in rule:
        for _prox_cls in rule["proximity_targets"]:
            proximity_target_elements.extend(
                get_elements_by_class(elements, _prox_cls)
            )

    for source_elem in source_elements:
        elem_compliant: bool | None = False
        best_result = None
        matched_target = None
        min_distance = float("inf")
        space_check_result = None

        # Check against all possible target classes
        for target_class in target_classes:
            target_elements = get_elements_by_class(elements, target_class)

            for target_elem in target_elements:
                # First check if stacked
                result = verify_stacking(
                    source_elem,
                    target_elem,
                    source_edge,
                    target_edge,
                    tolerance,
                )

                if result["compliant"]:
                    elem_compliant = True
                    best_result = result
                    matched_target = target_elem
                    space_check_result = None
                    break

                # Track best attempt and check space availability
                if result["distance"] < min_distance:
                    min_distance = result["distance"]
                    best_result = result

                    # Check if space is available for stacking
                    space_check_result = check_stacking_space_availability(
                        source_elem,
                        source_edge,
                        target_elem,
                        target_edge,
                        all_elements,
                        priorities,
                        tolerance,
                    )

            if elem_compliant:
                break

        # Post-stacking compliance checks (only when stacking succeeded)
        if elem_compliant is True:
            if max_prox_dist is not None and proximity_target_elements:
                min_prox = min(
                    calculate_polygon_distance(source_elem, pt)
                    for pt in proximity_target_elements
                )
                if min_prox > max_prox_dist:
                    elem_compliant = False
                    all_compliant = False
                    prox_classes = rule.get("proximity_targets", [])
                    violations.append(
                        {
                            "source_id": source_elem.get("id"),
                            "source_class": source_class,
                            "target_classes": target_classes,
                            "reason": (
                                f"{source_class} "
                                f"(ID: {source_elem.get('id')}) "
                                f"is stacked but {min_prox:.2f}m from the "
                                f"nearest "
                                f"{' or '.join(prox_classes)}, "
                                f"exceeding the {max_prox_dist}m limit"
                            ),
                            "actual_distance": round(min_prox, 2),
                            "required_distance": max_prox_dist,
                        }
                    )

            if (
                elem_compliant is True
                and entrance_proximate
                and matched_target is not None
            ):
                on_side, _reason = _check_entrance_proximate(
                    source_elem, matched_target, elements, tolerance
                )
                if not on_side:
                    elem_compliant = False
                    all_compliant = False
                    violations.append(
                        {
                            "source_id": source_elem.get("id"),
                            "source_class": source_class,
                            "target_classes": target_classes,
                            "reason": (
                                f"{source_class} "
                                f"(ID: {source_elem.get('id')}) "
                                f"is not stacked on the entrance-facing side "
                                f"of {matched_target.get('class', '?')} "
                                f"(ID: {matched_target.get('id', '?')})"
                                + (f": {_reason}" if _reason else "")
                            ),
                        }
                    )

        # Determine if this is truly a violation (stacking itself failed)
        if not elem_compliant and matched_target is None:
            # Check if space was available
            if space_check_result:
                if not space_check_result.get("can_fit"):
                    # Edge too short - not a violation
                    elem_compliant = None  # Mark as not applicable
                elif space_check_result.get("blocked_by_higher_priority"):
                    # Blocked by higher priority - not a violation
                    elem_compliant = None
                elif space_check_result.get(
                    "space_available"
                ) and not space_check_result.get("blocked_by_lower_priority"):
                    # Space available but not stacked - violation
                    all_compliant = False
                    violations.append(
                        {
                            "source_id": source_elem.get("id"),
                            "source_class": source_class,
                            "target_classes": target_classes,
                            "reason": (
                                f"{source_class} "
                                f"(ID: {source_elem.get('id')}) "
                                f"is not stacked against "
                                f"{' or '.join(target_classes)}"
                            ),
                            "space_available": True,
                            "distance": (
                                best_result.get("distance")
                                if best_result
                                else None
                            ),
                        }
                    )
                elif space_check_result.get("blocked_by_lower_priority"):
                    # Space available but blocked by lower priority -
                    # violation
                    all_compliant = False
                    blocking_classes = [
                        e.get("class")
                        for e in space_check_result.get(
                            "blocking_elements", []
                        )
                    ]
                    violations.append(
                        {
                            "source_id": source_elem.get("id"),
                            "source_class": source_class,
                            "target_classes": target_classes,
                            "reason": (
                                f"{source_class} "
                                f"(ID: {source_elem.get('id')}) "
                                f"is not stacked against "
                                f"{' or '.join(target_classes)}"
                            ),
                            "space_available": True,
                            "blocked_by_lower_priority": True,
                            "blocking_elements": blocking_classes,
                        }
                    )
            else:
                # No space check performed (shouldn't happen)
                all_compliant = False
                violations.append(
                    {
                        "source_id": source_elem.get("id"),
                        "source_class": source_class,
                        "target_classes": target_classes,
                        "reason": (
                            f"{source_class} (ID: {source_elem.get('id')}) "
                            f"is not stacked against "
                            f"{' or '.join(target_classes)}"
                        ),
                        "distance": (
                            best_result.get("distance")
                            if best_result
                            else None
                        ),
                    }
                )

        results.append(
            {
                "source_id": source_elem.get("id"),
                "compliant": elem_compliant,
                "result": best_result,
                "space_check": space_check_result,
            }
        )

    # Check proximity preference if specified
    proximity_info = None
    if "proximity_targets" in rule:
        proximity_targets_classes = rule["proximity_targets"]
        proximity_targets = []
        for pt_class in proximity_targets_classes:
            proximity_targets.extend(
                get_elements_by_class(elements, pt_class)
            )

        if proximity_targets:
            proximity_distances = []
            for source_elem in source_elements:
                prox_result = verify_proximity_preference(
                    source_elem, proximity_targets
                )
                proximity_distances.append(
                    prox_result.get("average_distance", 0)
                )

            avg_prox_dist = (
                round(sum(proximity_distances) / len(proximity_distances), 2)
                if proximity_distances
                else None
            )
            proximity_info = {"average_distance_to_targets": avg_prox_dist}

    return {
        "rule_id": rule.get("id"),
        "applicable": True,
        "compliant": all_compliant,
        "violations": violations,
        "element_results": results,
        "proximity_info": proximity_info,
    }


def _calculate_edge_type_distance(
    source_elem: dict,
    target_elem: dict,
    source_edge_type: str,
    target_edge_type: str,
) -> float:
    """Calculates minimum distance between specific edge types of two
    elements.

    Falls back to full polygon distance when edge types are 'any' or edges
    cannot be extracted.

    Args:
        source_elem (dict): Source element dictionary.
        target_elem (dict): Target element dictionary.
        source_edge_type (str): Edge type on source ('longer', 'shorter',
            'any').
        target_edge_type (str): Edge type on target ('longer', 'shorter',
            'any').

    Returns:
        float: Minimum distance between the specified edges.
    """
    if source_edge_type == "any" and target_edge_type == "any":
        return calculate_polygon_distance(source_elem, target_elem)

    source_edges = get_edge_by_type(source_elem, source_edge_type)
    target_edges = get_edge_by_type(target_elem, target_edge_type)

    if not source_edges or not target_edges:
        return calculate_polygon_distance(source_elem, target_elem)

    min_dist = float("inf")
    for s_edge in source_edges:
        for t_edge in target_edges:
            dist = calculate_distance_edge_to_edge(s_edge, t_edge)
            min_dist = min(min_dist, dist)
    return min_dist


def evaluate_adjacency_rule(
    elements: list[dict], groups: dict, rule: dict
) -> dict:
    """Evaluates an adjacency rule against the layout.

    Supports both single class (`source_class`/`target_class`) and
    multi-class (`source_classes`/`target_classes`) rule definitions.

    For group_satisfaction rules, every source cluster (across all source
    classes) must have at least one element adjacent to any target element.

    Args:
        elements (list[dict]): List of all element dictionaries.
        groups (dict): Dictionary mapping class names to lists of clusters.
        rule (dict): Rule dictionary from rules.json.

    Returns:
        dict: Dictionary with evaluation results.
    """
    # Resolve source/target class lists (support both singular and plural)
    raw_sources = rule.get("source_classes") or [
        rule.get("source_class", "NA")
    ]
    raw_targets = rule.get("target_classes") or [
        rule.get("target_class", "NA")
    ]
    source_label = ", ".join(raw_sources)
    target_label = ", ".join(raw_targets)

    params = rule.get("parameters", {})
    source_edge_type = rule.get("source_edge", "any")
    target_edge_type = rule.get("target_edge", "any")

    max_distance = params.get("max_distance", 2.0)
    target_distance = params.get("target_distance")
    tolerance = params.get("tolerance", 0.5)

    source_elements = []
    for sc in raw_sources:
        source_elements.extend(get_elements_by_class(elements, sc))

    target_elements = []
    for tc in raw_targets:
        target_elements.extend(get_elements_by_class(elements, tc))

    if not source_elements:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": f"No elements of class '{source_label}' found",
        }

    if not target_elements:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": f"No elements of class '{target_label}' found",
        }

    # Group satisfaction: within each source cluster (across all source
    # classes), at least one element must be adjacent to any target element.
    group_satisfaction = rule.get("group_satisfaction", False)
    if group_satisfaction:
        # Collect (class_name, group) pairs across all source classes
        all_source_groups = []
        for sc in raw_sources:
            for grp in groups.get(sc, []):
                all_source_groups.append((sc, grp))

        if all_source_groups:
            all_groups_satisfied = True
            group_violations = []
            group_results_out = []

            for sc, source_group in all_source_groups:
                source_group_elements = source_group.get("elements", [])
                cluster_idx = source_group.get("cluster_index", "?")
                group_satisfied = False

                for sg_elem in source_group_elements:
                    for target_elem in target_elements:
                        edge_distance = _calculate_edge_type_distance(
                            sg_elem,
                            target_elem,
                            source_edge_type,
                            target_edge_type,
                        )
                        if target_distance is not None:
                            satisfies = (
                                abs(edge_distance - target_distance)
                                <= tolerance
                            )
                        else:
                            satisfies = edge_distance <= (
                                max_distance + tolerance
                            )
                        if satisfies:
                            group_satisfied = True
                            break
                    if group_satisfied:
                        break

                group_results_out.append(
                    {
                        "source_class": sc,
                        "cluster_index": cluster_idx,
                        "compliant": group_satisfied,
                    }
                )
                if not group_satisfied:
                    all_groups_satisfied = False
                    group_violations.append(
                        {
                            "source_class": sc,
                            "cluster_index": cluster_idx,
                            "reason": (
                                f"No element in {sc} cluster "
                                f"{cluster_idx} is adjacent to any "
                                f"{target_label}"
                            ),
                        }
                    )

            return {
                "rule_id": rule.get("id"),
                "applicable": True,
                "compliant": all_groups_satisfied,
                "violations": group_violations,
                "group_results": group_results_out,
            }

    # Evaluate individual elements
    results = []
    all_compliant = True
    violations = []

    for source_elem in source_elements:
        elem_source_class = next(
            (sc for sc in raw_sources if sc == source_elem.get("class")),
            source_label,
        )
        elem_compliant = False
        best_result = None
        min_distance_found = float("inf")

        for target_elem in target_elements:
            # Compute distance using specified edge types when applicable
            edge_distance = _calculate_edge_type_distance(
                source_elem, target_elem, source_edge_type, target_edge_type
            )

            if target_distance is not None:
                within_tolerance = (
                    abs(edge_distance - target_distance) <= tolerance
                )
                result = {
                    "compliant": within_tolerance,
                    "distance": round(edge_distance, 2),
                    "violations": (
                        []
                        if within_tolerance
                        else [
                            f"Edge distance {edge_distance:.2f} deviates "
                            f"from target {target_distance:.2f} "
                            f"{target_edge_type} edge"
                        ]
                    ),
                    "details": (
                        "Distance constraint satisfied"
                        if within_tolerance
                        else (
                            f"Edge distance {edge_distance:.2f} deviates "
                            f"from target {target_distance:.2f} "
                            f"{target_edge_type} edge"
                        )
                    ),
                }
            else:
                is_adjacent = edge_distance <= (max_distance + tolerance)
                status = "Adjacent" if is_adjacent else "Not adjacent"
                result = {
                    "compliant": is_adjacent,
                    "distance": round(edge_distance, 2),
                    "details": f"{status} - distance: {edge_distance:.2f}",
                }

            if result["compliant"]:
                elem_compliant = True
                best_result = result
                break

            if result["distance"] < min_distance_found:
                min_distance_found = result["distance"]
                best_result = result

        if not elem_compliant:
            all_compliant = False
            violations.append(
                {
                    "source_id": source_elem.get("id"),
                    "source_class": elem_source_class,
                    "target_class": target_label,
                    "reason": (
                        f"{elem_source_class} (ID: {source_elem.get('id')}) "
                        f"is not proximally adjacent to {target_label}"
                    ),
                    "actual_distance": (
                        best_result.get("distance") if best_result else None
                    ),
                    "required_distance": (
                        max_distance
                        if not target_distance
                        else target_distance
                    ),
                }
            )

        results.append(
            {
                "source_id": source_elem.get("id"),
                "compliant": elem_compliant,
                "result": best_result,
            }
        )

    return {
        "rule_id": rule.get("id"),
        "applicable": True,
        "compliant": all_compliant,
        "violations": violations,
        "element_results": results,
    }


def evaluate_distance_rule(elements: list[dict], rule: dict) -> dict:
    """Evaluates a distance rule against the layout.

    Args:
        elements (list[dict]): List of all element dictionaries.
        rule (dict): Rule dictionary from rules.json.

    Returns:
        dict: Dictionary with evaluation results.
    """
    source_class = rule.get("source_class", "NA")
    target_classes = rule.get("target_classes", [])
    if not target_classes:
        target_class = rule.get("target_class", "NA")
        if target_class:
            target_classes = [target_class]
    params = rule.get("parameters", {})

    min_distance = params.get("min_distance")
    max_distance = params.get("max_distance")
    tolerance = params.get("tolerance", 0.5)

    source_elements = get_elements_by_class(elements, source_class)

    if not source_elements:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": (
                f"No elements of class '{source_class}' found in layout"
            ),
        }

    # Collect all target elements
    target_elements = []
    for target_class in target_classes:
        target_elements.extend(get_elements_by_class(elements, target_class))

    if not target_elements:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": "No elements of target classes found in layout",
        }

    results = []
    all_compliant = True
    violations = []

    for source_elem in source_elements:
        # Check distance to all target elements
        elem_violations = []

        for target_elem in target_elements:
            result = verify_distance_constraint(
                source_elem,
                target_elem,
                min_distance=min_distance,
                max_distance=max_distance,
                tolerance=tolerance,
            )

            if not result["compliant"]:
                elem_violations.append(
                    {
                        "target_id": target_elem.get("id"),
                        "target_class": target_elem.get("class"),
                        "distance": result["distance"],
                        "violations": result["violations"],
                    }
                )

        elem_compliant = len(elem_violations) == 0
        if not elem_compliant:
            all_compliant = False
            for v in elem_violations:
                violations.append(
                    {
                        "source_id": source_elem.get("id"),
                        "source_class": source_class,
                        "target_id": v["target_id"],
                        "target_class": v["target_class"],
                        "reason": (
                            f"{source_class} (ID: {source_elem.get('id')}) "
                            f"violates distance constraint with "
                            f"{v['target_class']}"
                        ),
                        "actual_distance": v["distance"],
                        "constraint": (
                            f"min: {min_distance}"
                            if min_distance
                            else f"max: {max_distance}"
                        ),
                    }
                )

        results.append(
            {
                "source_id": source_elem.get("id"),
                "compliant": elem_compliant,
                "violations": elem_violations,
            }
        )

    return {
        "rule_id": rule.get("id"),
        "applicable": True,
        "compliant": all_compliant,
        "violations": violations,
        "element_results": results,
    }


def evaluate_clearance_rule(elements: list[dict], rule: dict) -> dict:
    """Evaluates a clearance rule against the layout.

    Args:
        elements (list[dict]): List of all element dictionaries.
        rule (dict): Rule dictionary from rules.json.

    Returns:
        dict: Dictionary with evaluation results.
    """
    source_classes = rule.get("source_classes")
    if not source_classes:
        source_class = rule.get("source_class", "NA")
        source_classes = [source_class]
    else:
        source_class = ", ".join(source_classes)
    clearance_radius = rule.get("clearance_radius", 6.0)
    allowed_classes = rule.get("allowed_classes_in_clearance", [])
    tolerance = rule.get("parameters", {}).get("tolerance", 0.5)
    condition = rule.get("condition")
    clearance_mode = rule.get("clearance_mode", "radius")

    source_elements = []
    seen_source_ids = set()
    for class_name in source_classes:
        for element in get_elements_by_class(elements, class_name):
            element_id = element.get("id")
            if element_id in seen_source_ids:
                continue
            seen_source_ids.add(element_id)
            source_elements.append(element)

    if not source_elements:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": (
                f"No elements of class '{source_class}' found in layout"
            ),
        }

    # Filter source elements based on condition if specified
    if condition in {"stacked_against_layout", "stacked"}:
        filtered_sources = []

        for source_elem in source_elements:
            stacked_contacts = _find_stacked_edge_contacts(
                source_elem, elements, tolerance
            )
            if not stacked_contacts:
                continue

            if condition == "stacked":
                filtered_sources.append(source_elem)
                continue

            if any(
                contact["target_class"] == INPUT_CLASSES[0]
                for contacts in stacked_contacts.values()
                for contact in contacts
            ):
                filtered_sources.append(source_elem)

        source_elements = filtered_sources

    if not source_elements:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": f"No elements satisfy condition '{condition}'",
        }

    all_elements = elements

    # Elements of the same class as the source are always allowed within the
    # clearance zone (they are part of the same block being checked)
    effective_allowed = list(allowed_classes)
    for class_name in source_classes:
        if class_name not in effective_allowed:
            effective_allowed.append(class_name)

    if clearance_mode == "stacked_parking_exit" or set(
        source_classes
    ).issubset(set(PARKING_SPACE_CLASSES)):
        result = verify_stacked_parking_clearance(
            source_elements,
            all_elements,
            clearance_radius,
            effective_allowed,
            tolerance,
        )
        if not result.get("evaluated_source_ids"):
            return {
                "rule_id": rule.get("id"),
                "applicable": False,
                "compliant": None,
                "details": f"No elements satisfy condition '{condition}'",
            }
    else:
        result = verify_clearance(
            source_elements,
            all_elements,
            clearance_radius,
            effective_allowed,
            tolerance,
        )

    # Format violations with reasons
    formatted_violations = []
    for v in result.get("violations", []):
        source_violation_class = next(
            (
                elem.get("class")
                for elem in source_elements
                if elem.get("id") == v["source_id"]
            ),
            source_class,
        )
        formatted_violations.append(
            {
                "source_id": v["source_id"],
                "source_class": source_violation_class,
                "violating_id": v["violating_id"],
                "violating_class": v["violating_class"],
                "reason": (
                    f"{source_violation_class} "
                    f"(ID: {v['source_id']}) violates clearance with "
                    f"{v['violating_class']} (ID: {v['violating_id']})"
                ),
                "actual_distance": v["distance"],
                "required_clearance": clearance_radius,
                "stacked_edges": v.get("stacked_edges"),
                "exit_edge_index": v.get("exit_edge_index"),
                "details": v.get("details"),
            }
        )

    return {
        "rule_id": rule.get("id"),
        "applicable": True,
        "compliant": result["compliant"],
        "violations": formatted_violations,
        "details": result["details"],
    }


def evaluate_stacking_or_adjacency_rule(
    elements: list[dict], rule: dict
) -> dict:
    """Evaluates a rule with primary stacking and alternative adjacency.

    Args:
        elements (list[dict]): List of all element dictionaries.
        rule (dict): Rule dictionary from rules.json.

    Returns:
        dict: Dictionary with evaluation results.
    """
    source_class = rule.get("source_class", "NA")
    source_edge = rule.get("source_edge", "any")
    primary_target = rule.get("primary_target", {})
    alternative_target = rule.get("alternative_target", {})
    params = rule.get("parameters", {})

    stacking_tolerance = params.get("stacking_tolerance", 0.5)
    adjacency_tolerance = params.get("adjacency_tolerance", 2.0)

    source_elements = get_elements_by_class(elements, source_class)

    if not source_elements:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": (
                f"No elements of class '{source_class}' found in layout"
            ),
        }

    results = []
    all_compliant = True
    violations = []

    for source_elem in source_elements:
        elem_compliant = False
        satisfaction_method = None
        best_result = None
        min_distance = float("inf")

        # Try primary target
        if primary_target:
            primary_classes = primary_target.get(
                "classes",
                [primary_target["class"]]
                if primary_target.get("class")
                else [],
            )
            primary_edge = primary_target.get("edge", "any")
            primary_relation = primary_target.get("relation", "stacking")
            primary_source_edge = primary_target.get(
                "source_edge", source_edge
            )

            for primary_class in primary_classes:
                if elem_compliant:
                    break
                primary_elements = get_elements_by_class(
                    elements, primary_class
                )

                for target_elem in primary_elements:
                    if primary_relation == "stacking":
                        result = verify_stacking(
                            source_elem,
                            target_elem,
                            primary_source_edge,
                            primary_edge,
                            stacking_tolerance,
                        )
                    else:
                        edge_dist = _calculate_edge_type_distance(
                            source_elem,
                            target_elem,
                            primary_source_edge,
                            primary_edge,
                        )
                        is_adjacent = edge_dist <= (adjacency_tolerance + 0.5)
                        status = "Adjacent" if is_adjacent else "Not adjacent"
                        result = {
                            "compliant": is_adjacent,
                            "distance": round(edge_dist, 2),
                            "details": (
                                f"{status} - distance: {edge_dist:.2f}"
                            ),
                        }

                    if result["compliant"]:
                        elem_compliant = True
                        satisfaction_method = "primary_" + primary_relation
                        best_result = result
                        break

                    if result["distance"] < min_distance:
                        min_distance = result["distance"]
                        best_result = result

        # Try alternative target — relation determines stacking vs adjacency.
        # Respects per-target source_edge override.
        if not elem_compliant and alternative_target:
            alt_class = alternative_target.get("class")
            alt_edge = alternative_target.get("edge", "any")
            alt_relation = alternative_target.get("relation", "adjacency")
            alt_source_edge = alternative_target.get(
                "source_edge", source_edge
            )
            alt_elements = get_elements_by_class(elements, alt_class)

            for target_elem in alt_elements:
                if alt_relation == "stacking":
                    result = verify_stacking(
                        source_elem,
                        target_elem,
                        alt_source_edge,
                        alt_edge,
                        stacking_tolerance,
                    )
                else:
                    edge_dist = _calculate_edge_type_distance(
                        source_elem, target_elem, alt_source_edge, alt_edge
                    )
                    is_adjacent = edge_dist <= (adjacency_tolerance + 0.5)
                    status = "Adjacent" if is_adjacent else "Not adjacent"
                    result = {
                        "compliant": is_adjacent,
                        "distance": round(edge_dist, 2),
                        "details": f"{status} - distance: {edge_dist:.2f}",
                    }

                if result["compliant"]:
                    elem_compliant = True
                    satisfaction_method = "alternative_" + alt_relation
                    best_result = result
                    break

                if result["distance"] < min_distance:
                    min_distance = result["distance"]
                    best_result = result

        if not elem_compliant:
            all_compliant = False
            if primary_target:
                _primary_classes = primary_target.get(
                    "classes",
                    [primary_target["class"]]
                    if primary_target.get("class")
                    else [],
                )
                _primary_classes_str = (
                    " or ".join(_primary_classes) or "primary target"
                )
                primary_desc = (
                    f"{primary_target.get('relation', 'stacking')} against "
                    f"{_primary_classes_str}"
                )
            else:
                primary_desc = "primary condition"
            alt_desc = (
                f"{alternative_target.get('relation', 'adjacency')} against "
                f"{alternative_target.get('class', 'alternative target')}"
                if alternative_target
                else "alternative condition"
            )
            violations.append(
                {
                    "source_id": source_elem.get("id"),
                    "source_class": source_class,
                    "reason": (
                        f"{source_class} (ID: {source_elem.get('id')}) "
                        f"satisfies neither {primary_desc} "
                        f"nor {alt_desc}"
                    ),
                    "actual_distance": (
                        best_result.get("distance") if best_result else None
                    ),
                    "required_distance": adjacency_tolerance,
                }
            )

        results.append(
            {
                "source_id": source_elem.get("id"),
                "compliant": elem_compliant,
                "satisfaction_method": satisfaction_method,
                "result": best_result,
            }
        )

    return {
        "rule_id": rule.get("id"),
        "applicable": True,
        "compliant": all_compliant,
        "violations": violations,
        "element_results": results,
    }


def evaluate_rule(
    elements: list[dict],
    groups: dict,
    rule: dict,
    priorities: dict | None = None,
) -> dict:
    """Evaluates a single rule against the layout based on its type.

    Args:
        elements (list[dict]): List of all element dictionaries.
        groups (dict): Dictionary mapping class names to lists of clusters.
        rule (dict): Rule dictionary from rules.json.
        priorities (dict): Dictionary mapping class names to priority values.

    Returns:
        dict: Dictionary with evaluation results.
    """
    if priorities is None:
        priorities = {}

    rule_type = rule.get("type")

    if rule_type == "stacking":
        return evaluate_stacking_rule(elements, rule, priorities)
    elif rule_type == "adjacency":
        return evaluate_adjacency_rule(elements, groups, rule)
    elif rule_type == "distance":
        return evaluate_distance_rule(elements, rule)
    elif rule_type == "clearance":
        return evaluate_clearance_rule(elements, rule)
    elif rule_type == "stacking_or_adjacency":
        return evaluate_stacking_or_adjacency_rule(elements, rule)
    else:
        return {
            "rule_id": rule.get("id"),
            "applicable": False,
            "compliant": None,
            "details": f"Unknown rule type: {rule_type}",
        }


def evaluate_all_rules(
    elements: list[dict], groups: dict, rules: dict
) -> dict:
    """Evaluates all rules against the layout.

    Args:
        elements (list[dict]): List of all element dictionaries.
        groups (dict): Dictionary mapping class names to lists of clusters.
        rules (dict): Rules dictionary from rules.json with 'priorities' and
            'rules'.

    Returns:
        dict: Dictionary organized by rule category with violations.
    """
    # Extract priorities and rules list
    priorities = rules.get("priorities", {})
    rules_list = rules.get("rules", [])

    # Initialize category-wise violations
    result: dict[str, list[dict[str, Any]]] = {
        "stacking": [],
        "adjacency": [],
        "distance": [],
        "clearance": [],
    }

    for rule in rules_list:
        rule_result = evaluate_rule(elements, groups, rule, priorities)

        if not rule_result.get("applicable"):
            continue

        rule_type = rule.get("type")
        # Map rule types to categories
        if rule_type == "stacking" or rule_type == "stacking_or_adjacency":
            category = "stacking"
        elif rule_type == "adjacency":
            category = "adjacency"
        elif rule_type == "distance":
            category = "distance"
        elif rule_type == "clearance":
            category = "clearance"
        else:
            continue

        # Add violations to appropriate category
        if not rule_result.get("compliant"):
            for violation in rule_result.get("violations", []):
                result[category].append(
                    {
                        "rule_id": rule.get("id"),
                        "description": rule.get("description"),
                        "violation": violation.get("reason"),
                        "details": violation,
                    }
                )

    return result
