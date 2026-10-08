# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for proximity planning - spatial availability analysis.

Tools for analyzing spatial availability for proximity planning.
"""

from typing import Any

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from cando.agents.verifiers.tools.data import (
    get_edge_by_type,
    get_edge_lengths,
    get_element_center,
    get_elements_by_class,
    get_polygon_edges,
)
from cando.constants.classes import UNDERGROUND_CLASSES


def _is_underground(element: dict[str, Any]) -> bool:
    """Return True if *element* belongs to an underground class."""
    return element.get("class", "") in UNDERGROUND_CLASSES


def _should_skip_as_obstacle(
    candidate: dict[str, Any],
    source_is_underground: bool,
) -> bool:
    """Return True if *candidate* should be ignored as an obstacle.

    Underground elements only interact with other underground elements:
    - When the source IS underground  → skip above-ground candidates.
    - When the source is NOT underground → skip underground candidates.
    """
    candidate_underground = _is_underground(candidate)
    if source_is_underground:
        return not candidate_underground
    return candidate_underground


def calculate_available_space_on_edge(
    target_element: dict[str, Any],
    edge_type: str,
    layout_elements: list[dict[str, Any]],
    stacking_tolerance: float = 0.5,
) -> dict[str, Any]:
    """
    Calculate available space on a specific edge for stacking.

    This helps the planner understand how much free space exists on
    an edge where elements need to be stacked.

    Args:
        target_element: Target element with edges to analyze
        edge_type: Type of edge ("longer", "shorter", or "any")
        layout_elements: List of all elements in the layout
        stacking_tolerance: Maximum distance to consider as "stacked" (meters)

    Returns:
        Dictionary containing:
            - edge_length: Total length of the edge
            - edge_coordinates: [[x1, y1], [x2, y2]] of the edge
            - occupied_length: Length of edge occupied by stacked elements
            - free_length: Length of edge that is free
            - free_percentage: Percentage of free space on edge
            - occupying_elements: List of element IDs stacked on this edge
    """
    # Get the target edge(s)
    target_edges = get_edge_by_type(target_element, edge_type)
    if not target_edges:
        return {
            "edge_length": 0.0,
            "edge_coordinates": [],
            "occupied_length": 0.0,
            "free_length": 0.0,
            "free_percentage": 0.0,
            "occupying_elements": [],
        }

    # Use the first matching edge (in case multiple edges match)
    target_edge = target_edges[0]

    # Calculate edge length - target_edge is ((x1, y1), (x2, y2))
    edge_line = LineString([target_edge[0], target_edge[1]])
    edge_length = edge_line.length

    # Create a buffer around the edge to detect stacked elements
    edge_buffer = edge_line.buffer(stacking_tolerance)

    # Find elements that are stacked on this edge
    occupying_elements = []
    occupied_segments = []

    source_is_underground = _is_underground(target_element)
    for other_elem in layout_elements:
        if other_elem["id"] == target_element["id"]:
            continue
        if _should_skip_as_obstacle(other_elem, source_is_underground):
            continue

        other_poly = Polygon(other_elem["polygon"])

        # Check if element is near the edge (within tolerance)
        if edge_buffer.intersects(other_poly):
            # Project element onto edge to find occupied segment
            # Get the closest points on the edge to the element
            intersection = edge_buffer.intersection(other_poly)

            if intersection.area > 0:
                # Element is occupying space on this edge
                occupying_elements.append(other_elem["id"])

                # Calculate the linear extent along the edge
                # This is approximate - get bounds of intersection
                bounds = intersection.bounds  # (minx, miny, maxx, maxy)

                # Calculate projection parameters (0 to 1 along edge)
                for bound_point in [
                    Point(bounds[0], bounds[1]),
                    Point(bounds[2], bounds[3]),
                ]:
                    # Find closest point on edge
                    closest_point = edge_line.interpolate(
                        edge_line.project(bound_point)
                    )
                    # Get distance along edge (0 to edge_length)
                    distance_along = edge_line.project(closest_point)
                    occupied_segments.append(distance_along)

    # Merge overlapping segments and calculate total occupied length
    if occupied_segments:
        # Sort segments and merge overlapping ones
        occupied_segments.sort()
        merged_segments = []
        current_start = occupied_segments[0]
        current_end = occupied_segments[0]

        for i in range(1, len(occupied_segments)):
            if occupied_segments[i] <= current_end + stacking_tolerance:
                current_end = max(current_end, occupied_segments[i])
            else:
                merged_segments.append((current_start, current_end))
                current_start = occupied_segments[i]
                current_end = occupied_segments[i]

        merged_segments.append((current_start, current_end))

        # Calculate total occupied length
        occupied_length = sum(end - start for start, end in merged_segments)
    else:
        occupied_length = 0.0

    free_length = max(0.0, edge_length - occupied_length)
    free_percentage = (
        (free_length / edge_length * 100) if edge_length > 0 else 0.0
    )

    return {
        "edge_length": round(edge_length, 2),
        "edge_coordinates": [list(target_edge[0]), list(target_edge[1])],
        "occupied_length": round(occupied_length, 2),
        "free_length": round(free_length, 2),
        "free_percentage": round(free_percentage, 2),
        "occupying_elements": list(set(occupying_elements)),
    }


def calculate_free_space_in_radius(
    element: dict[str, Any],
    radius: float,
    layout_elements: list[dict[str, Any]],
    excluded_element_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Calculate the amount of free space within a radius around an element.

    This helps the planner understand spatial availability for clearance
    requirements and placement decisions.

    Args:
        element: Element dictionary with 'id', 'class', 'polygon'
        radius: Radius in meters to check around the element
        layout_elements: List of all elements in the layout
        excluded_element_ids: Element IDs to exclude from occupancy
            calculation (e.g., the element itself)

    Returns:
        Dictionary containing:
            - total_area: Total area of the circular radius (π * r²)
            - occupied_area: Area occupied by other elements
            - free_area: Area not occupied by elements
            - free_percentage: Percentage of free space (0-100)
            - occupying_elements: List of element IDs within the radius
    """
    excluded_element_ids = excluded_element_ids or []

    # Create circular buffer around element
    element_poly = Polygon(element["polygon"])
    element_centroid = element_poly.centroid
    circular_area = element_centroid.buffer(radius)

    total_area = circular_area.area

    # Collect polygons of elements within radius (excluding specified IDs)
    source_is_underground = _is_underground(element)
    occupying_polys = []
    occupying_element_ids = []

    for other_elem in layout_elements:
        if other_elem["id"] in excluded_element_ids:
            continue
        if _should_skip_as_obstacle(other_elem, source_is_underground):
            continue

        other_poly = Polygon(other_elem["polygon"])

        # Check if element intersects with circular area
        if circular_area.intersects(other_poly):
            intersection = circular_area.intersection(other_poly)
            if intersection.area > 0:
                occupying_polys.append(intersection)
                occupying_element_ids.append(other_elem["id"])

    # Calculate occupied area
    if occupying_polys:
        # Union all occupying polygons to avoid double-counting overlaps
        occupied_union = unary_union(occupying_polys)
        occupied_area = occupied_union.area
    else:
        occupied_area = 0.0

    free_area = max(0.0, total_area - occupied_area)
    free_percentage = (
        (free_area / total_area * 100) if total_area > 0 else 0.0
    )

    return {
        "total_area": round(total_area, 2),
        "occupied_area": round(occupied_area, 2),
        "free_area": round(free_area, 2),
        "free_percentage": round(free_percentage, 2),
        "occupying_elements": occupying_element_ids,
    }


def identify_blocking_elements_in_radius(
    element: dict[str, Any],
    radius: float,
    layout_elements: list[dict[str, Any]],
    allowed_classes: list[str] | None = None,
    excluded_element_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """
    Identify elements that are blocking/intruding into a clearance zone.

    This helps the planner identify which elements need to be moved to
    satisfy clearance requirements.

    Args:
        element: Element dictionary with 'id', 'class', 'polygon'
        radius: Clearance radius in meters
        layout_elements: List of all elements in the layout
        allowed_classes: List of element classes allowed within the radius
            (e.g., for conditional clearance rules). If None, all classes
            are considered violations
        excluded_element_ids: Element IDs to exclude from blocking analysis
            (e.g., the element itself)

    Returns:
        List of blocking element dictionaries, each containing:
            - element_id: ID of the blocking element
            - element_class: Class of the blocking element
            - distance: Distance from element centroid in meters
            - intrusion_area: Area of overlap with clearance zone
    """
    excluded_element_ids = excluded_element_ids or []
    allowed_classes = allowed_classes or []

    # Create circular buffer around element
    element_poly = Polygon(element["polygon"])
    element_centroid = element_poly.centroid
    clearance_zone = element_centroid.buffer(radius)

    source_is_underground = _is_underground(element)
    blocking_elements = []

    for other_elem in layout_elements:
        if other_elem["id"] in excluded_element_ids:
            continue
        if _should_skip_as_obstacle(other_elem, source_is_underground):
            continue

        # Check if element class is allowed
        if allowed_classes and other_elem["class"] in allowed_classes:
            continue

        other_poly = Polygon(other_elem["polygon"])
        other_centroid = other_poly.centroid

        # Check if element intersects with clearance zone
        if clearance_zone.intersects(other_poly):
            intersection = clearance_zone.intersection(other_poly)
            intrusion_area = intersection.area

            if intrusion_area > 0:
                distance = element_centroid.distance(other_centroid)

                blocking_elements.append(
                    {
                        "element_id": other_elem["id"],
                        "element_class": other_elem["class"],
                        "distance": round(distance, 2),
                        "intrusion_area": round(intrusion_area, 2),
                    }
                )

    # Sort by distance (closest first)
    blocking_elements.sort(key=lambda x: x["distance"])

    return blocking_elements


def calculate_space_between_elements(
    source_element: dict[str, Any],
    target_element: dict[str, Any],
    layout_elements: list[dict[str, Any]],
    corridor_width: float = 2.0,
) -> dict[str, Any]:
    """
    Analyze the free space in the corridor between two elements.

    This helps the planner understand if elements can be moved closer
    together or what obstacles exist between them.

    Args:
        source_element: Source element dictionary with 'id', 'class',
            'polygon'
        target_element: Target element dictionary with 'id', 'class',
            'polygon'
        layout_elements: List of all elements in the layout
        corridor_width: Width of the corridor to analyze in meters

    Returns:
        Dictionary containing:
            - direct_distance: Straight-line distance between centroids
            - corridor_area: Total area of the corridor
            - free_area: Area of the corridor not occupied by elements
            - occupied_area: Area occupied by blocking elements
            - free_percentage: Percentage of free space in corridor
            - blocking_elements: List of element IDs in the corridor
    """
    source_poly = Polygon(source_element["polygon"])
    target_poly = Polygon(target_element["polygon"])

    source_centroid = source_poly.centroid
    target_centroid = target_poly.centroid

    direct_distance = source_centroid.distance(target_centroid)

    # Create corridor as buffered line between centroids
    corridor_line = (
        Point(source_centroid).union(Point(target_centroid)).convex_hull
    )
    corridor = corridor_line.buffer(corridor_width / 2)

    corridor_area = corridor.area

    # Exclude source and target elements
    excluded_ids = [source_element["id"], target_element["id"]]

    # Collect blocking elements in corridor
    # Underground elements only block other underground elements.
    source_is_underground = _is_underground(source_element)
    blocking_polys = []
    blocking_element_ids = []

    for other_elem in layout_elements:
        if other_elem["id"] in excluded_ids:
            continue
        if _should_skip_as_obstacle(other_elem, source_is_underground):
            continue

        other_poly = Polygon(other_elem["polygon"])

        if corridor.intersects(other_poly):
            intersection = corridor.intersection(other_poly)
            if intersection.area > 0:
                blocking_polys.append(intersection)
                blocking_element_ids.append(other_elem["id"])

    # Calculate occupied area
    if blocking_polys:
        occupied_union = unary_union(blocking_polys)
        occupied_area = occupied_union.area
    else:
        occupied_area = 0.0

    free_area = max(0.0, corridor_area - occupied_area)
    free_percentage = (
        (free_area / corridor_area * 100) if corridor_area > 0 else 0.0
    )

    return {
        "direct_distance": round(direct_distance, 2),
        "corridor_area": round(corridor_area, 2),
        "free_area": round(free_area, 2),
        "occupied_area": round(occupied_area, 2),
        "free_percentage": round(free_percentage, 2),
        "blocking_elements": blocking_element_ids,
    }


def _compute_edge_clearance_depth(
    p1: tuple[float, float],
    p2: tuple[float, float],
    target_poly: "Polygon",
    layout_elements: list[dict[str, Any]],
    excluded_ids: set[int],
    n_samples: int = 10,
    source_depth: float = 0.0,
) -> dict[str, Any]:
    """Find the available clearance depth on the exterior side of an edge.

    Evenly distributes `n_samples` points along the edge, shifts each
    slightly outward along the edge's outward normal (away from the target
    element's centroid), then casts a **directional ray** in the outward
    perpendicular direction and measures the distance to the first
    intersection with any other element.

    The *minimum* clearance is the tightest gap along the edge (most
    constrained spot); the *average* clearance reflects the typical available
    depth across the full edge face.

    Args:
        p1, p2: Edge endpoints.
        target_poly: Polygon of the target element (determines outward
            direction).
        layout_elements: All layout elements.
        excluded_ids: Element IDs to ignore (source and target).
        n_samples: Number of sample points along the edge.
        source_depth: Perpendicular depth (m) the source element will
            occupy when stacked.  Used to populate `fits_source_depth`.

    Returns:
        {
            "min_clearance": float,
            "avg_clearance": float,
            "fits_source_depth": bool,
        }  (metres)
    """
    edge_vec_x = p2[0] - p1[0]
    edge_vec_y = p2[1] - p1[1]
    length = (edge_vec_x**2 + edge_vec_y**2) ** 0.5 or 1.0

    # Perpendicular unit vector
    perp_x = -edge_vec_y / length
    perp_y = edge_vec_x / length

    # Point the perpendicular AWAY from the target element's centroid
    centroid = target_poly.centroid
    mid_x = (p1[0] + p2[0]) / 2.0
    mid_y = (p1[1] + p2[1]) / 2.0
    dot = perp_x * (centroid.x - mid_x) + perp_y * (centroid.y - mid_y)
    if dot > 0:  # perp points toward centroid to flip outward
        perp_x, perp_y = -perp_x, -perp_y

    # Tiny epsilon pushes sample points just past the edge boundary
    eps = 0.05
    t_values = (
        [i / (n_samples - 1) for i in range(n_samples)]
        if n_samples > 1
        else [0.5]
    )

    # Ray length: longer than the layout diagonal so it always exits the site.
    all_xs = [p[0] for e in layout_elements for p in e["polygon"]]
    all_ys = [p[1] for e in layout_elements for p in e["polygon"]]
    if all_xs and all_ys:
        ray_len = (
            (max(all_xs) - min(all_xs)) ** 2
            + (max(all_ys) - min(all_ys)) ** 2
        ) ** 0.5 * 2 + 10.0
    else:
        ray_len = 200.0

    clearances = []
    for t in t_values:
        sx = p1[0] + t * edge_vec_x + eps * perp_x
        sy = p1[1] + t * edge_vec_y + eps * perp_y
        sample_pt = Point(sx, sy)

        # Cast a directional ray outward along the perpendicular
        ray_end = Point(sx + ray_len * perp_x, sy + ray_len * perp_y)
        ray = LineString([sample_pt, ray_end])

        min_dist = float("inf")
        for elem in layout_elements:
            if elem["id"] in excluded_ids:
                continue
            # Intersect only with the exterior ring
            exterior = Polygon(elem["polygon"]).exterior
            if ray.intersects(exterior):
                inter = ray.intersection(exterior)
                if not inter.is_empty:
                    d = sample_pt.distance(inter)
                    if d < min_dist:
                        min_dist = d
        clearances.append(min_dist if min_dist != float("inf") else 999.0)

    min_clearance = round(min(clearances), 2)
    fits_source_depth = source_depth <= 0.0 or min_clearance >= source_depth
    return {
        "min_clearance": min_clearance,
        "avg_clearance": round(sum(clearances) / len(clearances), 2),
        "fits_source_depth": fits_source_depth,
    }


def calculate_optimal_stacking_edges(
    source_element: dict[str, Any],
    target_element: dict[str, Any],
    proximity_targets: list[str],
    layout_elements: list[dict[str, Any]],
    source_edge_length: float = 0.0,
    source_edge_type: str = "any",
    stacking_tolerance: float = 0.5,
    n_edge_samples: int = 10,
) -> dict[str, Any]:
    """Rank target element edges for stacking and return the best short/long.

    For each edge of the target element the function computes four metrics:

    1. **Edge space** — how much of the edge face is already occupied by
       stacked neighbours (uses a buffer of `stacking_tolerance` around the
       edge line); reports free metres, free %, and whether the remaining
       space can fit the source element's relevant edge (`fits_source`).
    2. **Clearance depth** — perpendicular depth available on the exterior
       side before hitting another element.  `n_edge_samples` points are
       distributed along the edge face and the minimum/average distance to
       the nearest neighbour is measured (growing outward from the edge).
    3. **Distance to proximity targets** — average centroid-to-edge-midpoint
       distance for each class listed in `proximity_targets`.
    4. **Distance from source** — edge midpoint distance from the source
       element's centroid (proxy for movement cost).

    A composite score (lower = better) is computed from these metrics.  Edges
    are classified as "short" or "long" relative to the median edge length of
    the target polygon (for a rectangle: shorter pair = short, longer pair =
    long; for a square: all edges are long).  Each edge entry also carries a
    `position` label ("top" / "bottom" / "left" / "right") relative to the
    target element's centroid.

    Args:
        source_element: Element to be stacked.
        target_element: Target element to stack against.
        proximity_targets: Class names that should be nearby (may be empty).
        layout_elements: All elements in the layout.
        source_edge_length: Length (m) of the source edge that will be
            stacked.  Used to determine `edge_space.fits_source`.
        source_edge_type: Which edge of the source element is stacked
            ("longer", "shorter", or "any").  Determines the perpendicular
            depth the source element will occupy, which is used to compute
            `clearance_depth.fits_source_depth`.
        stacking_tolerance: Buffer (m) around the edge line used to detect
            already-stacked neighbours.
        n_edge_samples: Number of clearance-depth sample points per edge.

    Returns:
        Dictionary containing:
            - best_short_edge: Lowest-scoring "short" edge entry, or None.
            - best_long_edge: Lowest-scoring "long" edge entry, or None.
            Each entry has:
                - edge_index: index of the target polygon edge
                - edge_coordinates: [[x1, y1], [x2, y2]] of that edge
                - position: "top" | "bottom" | "left" | "right"
                - edge_length: length of target edge (m)
                - edge_space:
                    free_length (m), occupied_length (m),
                    free_percentage (0-100), fits_source (bool),
                    occupying_elements (list[int])
                - clearance_depth:
                    min_clearance (m) — tightest gap from edge exterior,
                    avg_clearance (m) — average gap,
                    fits_source_depth (bool) — True if min_clearance >=
                    source element's perpendicular depth
                - avg_distance_to_targets: float (0.0 if no targets)
                - distance_from_source: float
                - composite_score: float (lower = better)
    """
    target_poly = Polygon(target_element["polygon"])
    target_edges = get_polygon_edges(target_element)

    # Source element centroid
    source_center = get_element_center(source_element)
    source_point = Point(source_center["x"], source_center["y"])

    # IDs to ignore when measuring occupancy (source and target)
    excluded_ids = {source_element["id"], target_element["id"]}

    # Source element perpendicular depth when stacked
    _src_lengths = get_edge_lengths(source_element)
    if source_edge_type == "shorter":
        source_depth = _src_lengths["long_edge"]
    elif source_edge_type == "longer":
        source_depth = _src_lengths["short_edge"]
    else:  # "any"
        source_depth = _src_lengths["short_edge"]

    # Raw edge lengths for short / long classification
    raw_edge_lengths = [
        LineString([p1, p2]).length for p1, p2 in target_edges
    ]

    # Use the median length as the boundary between "short" and "long".
    if raw_edge_lengths:
        sorted_lengths = sorted(raw_edge_lengths)
        n = len(sorted_lengths)
        median_length = (
            (sorted_lengths[n // 2 - 1] + sorted_lengths[n // 2]) / 2
            if n % 2 == 0
            else sorted_lengths[n // 2]
        )
    else:
        median_length = 0.0

    # Proximity target elements (may be empty list)
    proximity_target_elements: list[dict[str, Any]] = []
    for target_class in proximity_targets:
        proximity_target_elements.extend(
            get_elements_by_class(layout_elements, target_class)
        )

    # Per-edge spatial analysis
    _POSITION_MAP = {
        "north": "top",
        "south": "bottom",
        "east": "right",
        "west": "left",
    }
    MAX_PENALTY = 100.0

    # Edges with min_clearance >= REF_CLEARANCE are considered fully
    # unobstructed.
    REF_CLEARANCE = 10.0

    # REF_DISTANCE: Computed as the diagonal of the bounding box of all
    # layout elements so it automatically scales with the actual site size.
    all_xs = [p[0] for e in layout_elements for p in e["polygon"]]
    all_ys = [p[1] for e in layout_elements for p in e["polygon"]]
    if all_xs and all_ys:
        REF_DISTANCE = (
            (max(all_xs) - min(all_xs)) ** 2
            + (max(all_ys) - min(all_ys)) ** 2
        ) ** 0.5 or 1.0
    else:
        REF_DISTANCE = 50.0  # fallback

    edge_entries: list[dict[str, Any]] = []
    centroid = target_poly.centroid

    for idx, (p1, p2) in enumerate(target_edges):
        edge_length = raw_edge_lengths[idx]
        edge_center_x = (p1[0] + p2[0]) / 2
        edge_center_y = (p1[1] + p2[1]) / 2

        #  Cardinal direction & position label
        dx = abs(p2[0] - p1[0])
        dy = abs(p2[1] - p1[1])
        if dx > dy:  # horizontal edge
            cardinal = "north" if edge_center_y > centroid.y else "south"
        else:  # vertical edge
            cardinal = "east" if edge_center_x > centroid.x else "west"
        position = _POSITION_MAP[cardinal]
        if edge_length < median_length:
            edge_category = "short"
        elif edge_length > median_length:
            edge_category = "long"
        else:
            # Square (or degenerate polygon): use orientation as
            # tie-breaker so both categories are always populated.
            # horizontal edges (top / bottom) to "short"
            # vertical edges   (left / right) to "long"
            edge_category = (
                "short" if cardinal in ("north", "south") else "long"
            )

        #  Edge space (linear occupancy)
        edge_line = LineString([p1, p2])
        edge_buffer = edge_line.buffer(stacking_tolerance)

        occupied_segments: list[float] = []
        occupying_element_ids: list[int] = []

        for other_elem in layout_elements:
            if other_elem["id"] in excluded_ids:
                continue
            other_poly = Polygon(other_elem["polygon"])
            if edge_buffer.intersects(other_poly):
                inter = edge_buffer.intersection(other_poly)
                if inter.area > 0:
                    occupying_element_ids.append(other_elem["id"])
                    bounds = inter.bounds  # (minx, miny, maxx, maxy)
                    for bpt in [
                        Point(bounds[0], bounds[1]),
                        Point(bounds[2], bounds[3]),
                    ]:
                        d = edge_line.project(bpt)
                        occupied_segments.append(d)

        if occupied_segments:
            occupied_segments.sort()
            merged: list[tuple[float, float]] = []
            cs, ce = occupied_segments[0], occupied_segments[0]
            for s in occupied_segments[1:]:
                if s <= ce + stacking_tolerance:
                    ce = max(ce, s)
                else:
                    merged.append((cs, ce))
                    cs, ce = s, s
            merged.append((cs, ce))
            occupied_length = sum(e - s for s, e in merged)
        else:
            occupied_length = 0.0

        free_length = max(0.0, edge_length - occupied_length)
        edge_free_pct = (
            (free_length / edge_length * 100) if edge_length > 0 else 0.0
        )
        fits_source = (source_edge_length <= 0) or (
            free_length >= source_edge_length
        )

        edge_space = {
            "free_length": round(free_length, 2),
            "occupied_length": round(occupied_length, 2),
            "free_percentage": round(edge_free_pct, 2),
            "fits_source": fits_source,
            "occupying_elements": list(set(occupying_element_ids)),
        }

        #  Clearance depth (perpendicular, outward)
        clearance_depth = _compute_edge_clearance_depth(
            p1=p1,
            p2=p2,
            target_poly=target_poly,
            layout_elements=layout_elements,
            excluded_ids=excluded_ids,
            n_samples=n_edge_samples,
            source_depth=source_depth,
        )

        #  Distances to proximity targets -
        edge_point = Point(edge_center_x, edge_center_y)
        total_target_dist = 0.0
        distances_to_targets: list[dict[str, Any]] = []
        for prox_elem in proximity_target_elements:
            c = get_element_center(prox_elem)
            d = edge_point.distance(Point(c["x"], c["y"]))
            total_target_dist += d
            distances_to_targets.append(
                {
                    "target_class": prox_elem["class"],
                    "target_id": prox_elem["id"],
                    "distance": round(d, 2),
                }
            )
        avg_dist_to_targets = (
            total_target_dist / len(proximity_target_elements)
            if proximity_target_elements
            else 0.0
        )

        #  Distance from source centroid
        dist_from_source = source_point.distance(edge_point)

        #  Composite score (lower = better)
        # All terms are normalised to [0, MAX_PENALTY]
        # - fit_penalty: disqualifies the edge when the source edge cannot
        #                fit in the available free length
        # - depth_penalty: disqualifies the edge when the source element's
        #                  perpendicular depth exceeds the available clearance
        # - target_dist_pen: normalised avg distance to proximity targets
        # - source_dist_pen: normalised distance from source centroid
        # - clearance_penalty: normalised perpendicular depth
        # - edge_space_penalty: residual free-length ratio
        fit_penalty = MAX_PENALTY if not fits_source else 0.0
        depth_penalty = (
            MAX_PENALTY if not clearance_depth["fits_source_depth"] else 0.0
        )
        target_dist_pen = MAX_PENALTY * min(
            1.0, avg_dist_to_targets / REF_DISTANCE
        )
        source_dist_pen = MAX_PENALTY * min(
            1.0, dist_from_source / REF_DISTANCE
        )
        clearance_penalty = MAX_PENALTY * max(
            0.0, 1.0 - clearance_depth["min_clearance"] / REF_CLEARANCE
        )
        edge_space_penalty = MAX_PENALTY * (1.0 - edge_free_pct / 100.0)
        composite_score = (
            fit_penalty
            + depth_penalty
            + 0.35 * target_dist_pen
            + 0.30 * source_dist_pen
            + 0.25 * clearance_penalty
            + 0.10 * edge_space_penalty
        )

        edge_entries.append(
            {
                "edge_index": idx,
                "edge_coordinates": [list(p1), list(p2)],
                "position": position,
                "edge_category": edge_category,
                "edge_length": round(edge_length, 2),
                "edge_space": edge_space,
                "clearance_depth": clearance_depth,
                "avg_distance_to_targets": round(avg_dist_to_targets, 2),
                "distances_to_targets": distances_to_targets,
                "distance_from_source": round(dist_from_source, 2),
                "composite_score": round(composite_score, 2),
            }
        )

    # Pick best per category (lowest composite score)
    short_edges = sorted(
        [e for e in edge_entries if e["edge_category"] == "short"],
        key=lambda x: x["composite_score"],
    )
    long_edges = sorted(
        [e for e in edge_entries if e["edge_category"] == "long"],
        key=lambda x: x["composite_score"],
    )

    return {
        "best_short_edge": short_edges[0] if short_edges else None,
        "best_long_edge": long_edges[0] if long_edges else None,
    }


def augment_layout_data_for_planning(
    elements: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Augment layout data with spatial availability analysis for planning.

    This function analyzes the layout to provide spatial information
    that helps the planner make decisions based on verifier feedback.

    Args:
        elements: List of element dictionaries from the layout
        rules: List of proximity rule dictionaries

    Returns:
        Dictionary containing spatial availability data:
            - clearance_analysis: Per-element clearance space analysis
            - stacking_analysis: Edge availability for stacking rules,
                including proximity-based edge selection when applicable
            - adjacency_corridors: Space analysis between element pairs
    """
    spatial_analysis: dict[str, dict[str, Any]] = {
        "clearance_analysis": {},
        "stacking_analysis": {},
        "adjacency_corridors": {},
    }

    # Analyze clearance requirements from rules
    clearance_rules = [r for r in rules if r.get("type") == "clearance"]

    for rule in clearance_rules:
        source_class = rule.get("source_class", "")
        clearance_distance = rule.get("clearance_radius", 0.0)
        allowed_classes = rule.get("allowed_classes_in_clearance", [])

        source_elements = get_elements_by_class(elements, source_class)

        for source_elem in source_elements:
            element_id = source_elem["id"]

            # Calculate free space in clearance radius
            free_space = calculate_free_space_in_radius(
                element=source_elem,
                radius=clearance_distance,
                layout_elements=elements,
                excluded_element_ids=[element_id],
            )

            # Identify blocking elements
            blocking = identify_blocking_elements_in_radius(
                element=source_elem,
                radius=clearance_distance,
                layout_elements=elements,
                allowed_classes=allowed_classes,
                excluded_element_ids=[element_id],
            )

            spatial_analysis["clearance_analysis"][element_id] = {
                "rule_id": rule.get("id"),
                "element_class": source_class,
                "required_clearance": clearance_distance,
                "free_space": free_space,
                "blocking_elements": blocking,
            }

    # Analyze adjacency/distance/stacking requirements
    adjacency_rules = [
        r
        for r in rules
        if r.get("type")
        in ["adjacency", "distance", "stacking", "stacking_or_adjacency"]
    ]

    stacking_analysis = {}

    for rule in adjacency_rules:
        source_class = rule.get("source_class", "")
        rule_type = rule.get("type", "")
        params = rule.get("parameters", {})

        targets: list[
            tuple[str, str, str]
        ] = []  # (class, src_edge, relation)

        if rule_type == "stacking_or_adjacency":
            primary = rule.get("primary_target", {})
            if primary.get("class"):
                targets.append(
                    (
                        primary["class"],
                        rule.get("source_edge", "any"),
                        primary.get("relation", "stacking"),
                    )
                )
            alternative = rule.get("alternative_target", {})
            if alternative.get("class"):
                targets.append(
                    (
                        alternative["class"],
                        rule.get("source_edge", "any"),
                        alternative.get("relation", "adjacency"),
                    )
                )
        else:
            # stacking, adjacency, distance
            if rule.get("target_classes"):
                for tc in rule["target_classes"]:
                    targets.append(
                        (
                            tc,
                            rule.get("source_edge", "any"),
                            rule_type,
                        )
                    )
            elif rule.get("target_class"):
                targets.append(
                    (
                        rule["target_class"],
                        rule.get("source_edge", "any"),
                        rule_type,
                    )
                )

        source_elements = get_elements_by_class(elements, source_class)

        for target_class, source_edge_type, relation in targets:
            target_elements = get_elements_by_class(elements, target_class)

            for source_elem in source_elements:
                for target_elem in target_elements:
                    pair_key = f"{source_elem['id']}-{target_elem['id']}"

                    # Per-edge stacking analysis
                    if relation in ("stacking", "stacking_or_adjacency"):
                        tolerance = params.get("stacking_tolerance", 0.5)
                        proximity_targets = rule.get("proximity_targets", [])

                        # Derive the source edge length for fit checks
                        src_edge_lengths = get_edge_lengths(source_elem)
                        if source_edge_type == "longer":
                            src_edge_len = src_edge_lengths["long_edge"]
                        elif source_edge_type == "shorter":
                            src_edge_len = src_edge_lengths["short_edge"]
                        else:
                            # "any": conservative estimate with shorter edge
                            src_edge_len = src_edge_lengths["short_edge"]

                        edge_analysis = calculate_optimal_stacking_edges(
                            source_element=source_elem,
                            target_element=target_elem,
                            proximity_targets=proximity_targets,
                            layout_elements=elements,
                            source_edge_length=src_edge_len,
                            source_edge_type=source_edge_type,
                            stacking_tolerance=tolerance,
                        )

                        stacking_analysis[pair_key] = {
                            "rule_id": rule.get("id"),
                            "source_element_id": source_elem["id"],
                            "target_element_id": target_elem["id"],
                            "source_edge_length": round(src_edge_len, 2),
                            "best_short_edge": edge_analysis[
                                "best_short_edge"
                            ],
                            "best_long_edge": edge_analysis["best_long_edge"],
                        }

                    # Corridor / space-between analysis for all rule types
                    corridor_analysis = calculate_space_between_elements(
                        source_element=source_elem,
                        target_element=target_elem,
                        layout_elements=elements,
                    )

                    spatial_analysis["adjacency_corridors"][pair_key] = {
                        "rule_id": rule.get("id"),
                        "rule_type": rule_type,
                        "source_element_id": source_elem["id"],
                        "target_element_id": target_elem["id"],
                        "corridor_analysis": corridor_analysis,
                    }

    spatial_analysis["stacking_analysis"] = stacking_analysis

    return spatial_analysis
