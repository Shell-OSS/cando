# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for assembly planning - computing fixes for assembly
violations.

Tools for computing how to fix fragmentation and formation issues.
"""

import math
from collections import defaultdict
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union

from cando.agents.verifiers.tools.data import get_element_center
from cando.constants.classes import INPUT_CLASSES


def get_element_centroid(element: dict) -> tuple[float, float]:
    """
    Get the centroid of an element.

    Args:
        element: Element dictionary with 'polygon' key.

    Returns:
        Tuple of (x, y) centroid coordinates.
    """
    center = get_element_center(element)
    return (center["x"], center["y"])


def get_element_dimensions(element: dict) -> tuple[float, float]:
    """
    Get width and height of an element from its bounding box.

    Args:
        element: Element dictionary with 'bbox' key.

    Returns:
        Tuple of (width, height) in meters.
    """
    bbox = element.get("bbox", {})
    width = bbox.get("max_x", 0) - bbox.get("min_x", 0)
    height = bbox.get("max_y", 0) - bbox.get("min_y", 0)
    return (round(width, 2), round(height, 2))


def calculate_free_space_at_position(
    target_position: tuple[float, float],
    element_size: tuple[float, float],
    layout_elements: list[dict],
    excluded_element_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Check if a target position has free space for placing an element.

    This helps the planner determine if an element can be moved to a
    specific location without collision.

    Args:
        target_position: (x, y) center position to check.
        element_size: (width, height) of the element to place.
        layout_elements: List of all elements in the layout.
        excluded_element_ids: IDs to exclude (e.g., the element being moved).

    Returns:
        Dictionary containing:
            - is_free: Whether the target position is unobstructed
            - blocking_elements: List of element IDs blocking this position
            - overlap_area: Total area of overlap with existing elements
    """
    excluded_element_ids = excluded_element_ids or []

    # Create a rectangle at target position
    half_w, half_h = element_size[0] / 2, element_size[1] / 2
    target_poly = Polygon(
        [
            (target_position[0] - half_w, target_position[1] - half_h),
            (target_position[0] + half_w, target_position[1] - half_h),
            (target_position[0] + half_w, target_position[1] + half_h),
            (target_position[0] - half_w, target_position[1] + half_h),
        ]
    )

    blocking_elements = []
    overlap_area = 0.0

    for elem in layout_elements:
        if elem["id"] in excluded_element_ids:
            continue
        if elem["class"] in INPUT_CLASSES:
            continue

        elem_poly = Polygon(elem.get("polygon", []))
        if not elem_poly.is_valid:
            continue

        if target_poly.intersects(elem_poly):
            intersection = target_poly.intersection(elem_poly)
            if intersection.area > 0.01:  # Small tolerance
                blocking_elements.append(
                    {
                        "element_id": elem["id"],
                        "element_class": elem["class"],
                        "overlap_area": round(intersection.area, 2),
                    }
                )
                overlap_area += intersection.area

    return {
        "is_free": len(blocking_elements) == 0,
        "blocking_elements": blocking_elements,
        "total_overlap_area": round(overlap_area, 2),
    }


def calculate_free_space_around_element(
    element: dict,
    radius: float,
    layout_elements: list[dict],
    excluded_element_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Calculate free space around an element within a given radius.

    This helps identify if there's room to consolidate other elements
    near this one.

    Args:
        element: Element dictionary.
        radius: Radius in meters to check around the element.
        layout_elements: List of all elements in the layout.
        excluded_element_ids: IDs to exclude from occupancy calculation.

    Returns:
        Dictionary containing:
            - total_area: Total area of the circular radius
            - occupied_area: Area occupied by other elements
            - free_area: Area not occupied
            - free_percentage: Percentage of free space (0-100)
            - nearby_elements: List of element IDs within the radius
    """
    excluded_element_ids = excluded_element_ids or []

    elem_poly = Polygon(element.get("polygon", []))
    if not elem_poly.is_valid:
        return {
            "total_area": 0.0,
            "occupied_area": 0.0,
            "free_area": 0.0,
            "free_percentage": 0.0,
            "nearby_elements": [],
        }

    # Create circular buffer around element centroid
    centroid = elem_poly.centroid
    circular_area = centroid.buffer(radius)
    total_area = circular_area.area

    # Collect polygons of elements within radius
    occupying_polys = []
    nearby_elements = []

    for other_elem in layout_elements:
        if other_elem["id"] in excluded_element_ids:
            continue
        if other_elem["id"] == element["id"]:
            continue
        if other_elem["class"] in INPUT_CLASSES:
            continue

        other_poly = Polygon(other_elem.get("polygon", []))
        if not other_poly.is_valid:
            continue

        if circular_area.intersects(other_poly):
            intersection = circular_area.intersection(other_poly)
            if intersection.area > 0:
                occupying_polys.append(intersection)
                nearby_elements.append(
                    {
                        "element_id": other_elem["id"],
                        "element_class": other_elem["class"],
                    }
                )

    # Calculate occupied area
    if occupying_polys:
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
        "nearby_elements": nearby_elements,
    }


def compute_move_to_target(
    element: dict,
    target_position: tuple[float, float],
    layout_elements: list[dict],
) -> dict[str, Any]:
    """
    Compute the move required to relocate an element to a target position.

    Checks if the target position is free or obstructed.

    Args:
        element: Element dictionary to move.
        target_position: Target (x, y) center position.
        layout_elements: All layout elements for obstacle checking.

    Returns:
        Dictionary containing:
            - element_id: ID of the element to move
            - current_position: Current (x, y) position
            - target_position: Target (x, y) position
            - delta: (dx, dy) movement vector
            - distance: Total distance to move
            - is_obstructed: Whether the target has obstacles
            - blocking_elements: Elements blocking the target (if any)
    """
    current_pos = get_element_centroid(element)
    elem_size = get_element_dimensions(element)

    dx = round(target_position[0] - current_pos[0], 2)
    dy = round(target_position[1] - current_pos[1], 2)
    distance = round(math.sqrt(dx**2 + dy**2), 2)

    # Check if target is free
    free_space = calculate_free_space_at_position(
        target_position=target_position,
        element_size=elem_size,
        layout_elements=layout_elements,
        excluded_element_ids=[element["id"]],
    )

    return {
        "element_id": element["id"],
        "element_class": element["class"],
        "current_position": list(current_pos),
        "target_position": list(target_position),
        "delta": [dx, dy],
        "distance": distance,
        "is_obstructed": not free_space["is_free"],
        "blocking_elements": free_space["blocking_elements"],
    }


def compute_consolidation_positions(
    anchor_element: dict,
    elements_to_move: list[dict],
    spacing: float,
    layout_elements: list[dict],
) -> list[dict]:
    """
    Compute target positions to consolidate elements around an anchor.

    Given an anchor element (that stays put), compute where other
    elements should move to form a consolidated group.

    Args:
        anchor_element: Element to consolidate around (stays in place).
        elements_to_move: Elements that need to move toward anchor.
        spacing: Desired spacing between elements.
        layout_elements: All layout elements for obstacle checking.

    Returns:
        List of move dictionaries for each element to consolidate.
    """
    if not elements_to_move:
        return []

    anchor_pos = get_element_centroid(anchor_element)
    anchor_width, anchor_height = get_element_dimensions(anchor_element)

    moves = []

    for i, elem in enumerate(elements_to_move):
        # Stack elements below the anchor, accounting for each element's
        # height
        elem_width, elem_height = get_element_dimensions(elem)
        target_x = anchor_pos[0]
        target_y = anchor_pos[1] - (i + 1) * (elem_height + spacing)

        move = compute_move_to_target(
            element=elem,
            target_position=(target_x, target_y),
            layout_elements=layout_elements,
        )
        moves.append(move)

    return moves


def compute_line_formation_positions(
    elements: list[dict],
    spacing: float,
    layout_elements: list[dict],
    orientation: str = "auto",
) -> list[dict]:
    """
    Compute target positions to arrange elements in a single line.

    First element is the anchor (stays put). Other elements move
    to form a line.

    Args:
        elements: Elements to arrange (first is anchor).
        spacing: Desired spacing between elements.
        layout_elements: All layout elements for obstacle checking.
        orientation: 'horizontal', 'vertical', or 'auto'.

    Returns:
        List of move dictionaries (empty for anchor, moves for others).
    """
    if len(elements) <= 1:
        return []

    # Determine orientation if auto
    if orientation == "auto":
        centroids = [get_element_centroid(e) for e in elements]
        xs = [c[0] for c in centroids]
        ys = [c[1] for c in centroids]
        x_spread = max(xs) - min(xs) if xs else 0
        y_spread = max(ys) - min(ys) if ys else 0
        orientation = "horizontal" if x_spread >= y_spread else "vertical"

    anchor = elements[0]
    anchor_pos = get_element_centroid(anchor)
    anchor_width, anchor_height = get_element_dimensions(anchor)

    # Track the outer edge of the last placed element so spacing
    # accounts for each element's actual dimensions instead of
    # reusing the anchor size for all intervals.
    if orientation == "horizontal":
        # Right edge of anchor (center x + half width)
        current_edge = anchor_pos[0] + anchor_width / 2.0
    else:
        # Bottom edge of anchor (center y - half height)
        current_edge = anchor_pos[1] - anchor_height / 2.0

    moves = []

    for i, elem in enumerate(elements[1:], start=1):
        elem_width, elem_height = get_element_dimensions(elem)
        if orientation == "horizontal":
            # Place element to the right of the current edge
            target_x = current_edge + spacing + elem_width / 2.0
            target_y = anchor_pos[1]
            # Update edge to the right side of this element
            current_edge = target_x + elem_width / 2.0
        else:
            # Place element below the current edge
            target_x = anchor_pos[0]
            target_y = current_edge - spacing - elem_height / 2.0
            # Update edge to the bottom side of this element
            current_edge = target_y - elem_height / 2.0

        move = compute_move_to_target(
            element=elem,
            target_position=(target_x, target_y),
            layout_elements=layout_elements,
        )
        move["formation_position"] = i
        move["orientation"] = orientation
        moves.append(move)

    return moves


def compute_rectangular_formation_positions(
    elements: list[dict],
    spacing: float,
    layout_elements: list[dict],
) -> list[dict]:
    """
    Compute target positions to arrange elements in a rectangular grid.

    First element is the anchor (position 0,0). Other elements fill
    the grid row by row.

    Args:
        elements: Elements to arrange (first is anchor).
        spacing: Desired spacing between elements.
        layout_elements: All layout elements for obstacle checking.

    Returns:
        List of move dictionaries with grid positions.
    """
    n = len(elements)
    if n <= 1:
        return []

    # Calculate grid dimensions (approximate square)
    cols = math.ceil(math.sqrt(n))

    anchor = elements[0]
    anchor_pos = get_element_centroid(anchor)

    # Use the maximum element dimensions to define a uniform grid cell size.
    # This avoids overlaps or excessive gaps when element sizes vary.
    max_width = 0.0
    max_height = 0.0
    for el in elements:
        el_width, el_height = get_element_dimensions(el)
        if el_width > max_width:
            max_width = el_width
        if el_height > max_height:
            max_height = el_height

    moves = []

    for i, elem in enumerate(elements[1:], start=1):
        row = i // cols
        col = i % cols

        target_x = anchor_pos[0] + col * (max_width + spacing)
        target_y = anchor_pos[1] - row * (max_height + spacing)

        move = compute_move_to_target(
            element=elem,
            target_position=(target_x, target_y),
            layout_elements=layout_elements,
        )
        move["grid_position"] = {"row": row, "col": col}
        moves.append(move)

    return moves


def augment_layout_data_for_planning(
    elements: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compute fix suggestions for assembly violations.

    This function assumes the verifier has already identified violations.
    It computes HOW to fix them - target positions, moves, and obstacles.

    The LLM reads the verifier feedback to know WHAT is wrong, then uses
    this spatial analysis to determine HOW to fix it.

    Args:
        elements: List of element dictionaries from the layout.
        rules: List of assembly rule dictionaries.

    Returns:
        Dictionary containing fix computations:
            - per_class_fixes: For each class, consolidation and formation
              moves with obstacle information
    """
    # Extract rule parameters
    rule_params = {}
    for rule in rules:
        if rule.get("type") == "assembly_constraint":
            element_class = rule.get("element")
            conditions = rule.get("conditions", {})

            if element_class:
                arrangement = conditions.get("arrangement", {})
                rule_params[element_class] = {
                    "distance_threshold": conditions.get(
                        "distance_threshold", 5.0
                    ),
                    "pattern": arrangement.get("pattern", "single_line"),
                    "rules_for_counts": arrangement.get("rules_for_counts"),
                }

    # Group elements by class
    excluded_classes = INPUT_CLASSES
    elements_by_class = defaultdict(list)
    for elem in elements:
        if elem["class"] not in excluded_classes:
            elements_by_class[elem["class"]].append(elem)

    spatial_analysis = {}

    for class_name, class_elements in elements_by_class.items():
        if len(class_elements) == 0:
            continue

        params = rule_params.get(
            class_name,
            {
                "distance_threshold": 5.0,
                "pattern": "single_line",
            },
        )
        spacing = params["distance_threshold"] / 2

        # Find anchor element (element closest to group centroid)
        centroids = [get_element_centroid(e) for e in class_elements]
        cx = sum(c[0] for c in centroids) / len(centroids)
        cy = sum(c[1] for c in centroids) / len(centroids)

        min_dist = float("inf")
        anchor_element = class_elements[0]
        for elem in class_elements:
            elem_c = get_element_centroid(elem)
            dist = math.sqrt((elem_c[0] - cx) ** 2 + (elem_c[1] - cy) ** 2)
            if dist < min_dist:
                min_dist = dist
                anchor_element = elem

        # Compute consolidation moves (for fragmentation fix)
        elements_to_consolidate = [
            e for e in class_elements if e["id"] != anchor_element["id"]
        ]
        consolidation_moves = compute_consolidation_positions(
            anchor_element=anchor_element,
            elements_to_move=elements_to_consolidate,
            spacing=spacing,
            layout_elements=elements,
        )

        # Check free space around anchor for consolidation
        anchor_free_space = calculate_free_space_around_element(
            element=anchor_element,
            radius=params["distance_threshold"],
            layout_elements=elements,
            excluded_element_ids=[e["id"] for e in class_elements],
        )

        # Determine effective pattern
        pattern = params["pattern"]
        count_rules = params.get("rules_for_counts")
        if count_rules:
            count = len(class_elements)
            if count % 2 == 1:
                effective_pattern = count_rules.get("odd", "single_line")
            else:
                effective_pattern = count_rules.get(
                    "even", "rectangular_blocks"
                )
        else:
            effective_pattern = pattern

        # Compute formation moves based on pattern
        if effective_pattern in ["single_line", "rectangular_or_single_line"]:
            formation_moves = compute_line_formation_positions(
                elements=class_elements,
                spacing=spacing,
                layout_elements=elements,
                orientation="auto",
            )
        else:  # rectangular_blocks
            formation_moves = compute_rectangular_formation_positions(
                elements=class_elements,
                spacing=spacing,
                layout_elements=elements,
            )

        spatial_analysis[class_name] = {
            "element_count": len(class_elements),
            "element_ids": [e["id"] for e in class_elements],
            "anchor_element_id": anchor_element["id"],
            "anchor_position": list(get_element_centroid(anchor_element)),
            "anchor_free_space": anchor_free_space,
            "consolidation_moves": consolidation_moves,
            "target_pattern": effective_pattern,
            "formation_moves": formation_moves,
        }

    return {"per_class_fixes": spatial_analysis}
