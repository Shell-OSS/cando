# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for flow planning - spatial availability analysis.

Tools for analyzing spatial availability for flow planning.
"""

from typing import Any

from cando.agents.planners.proximity.tools import (
    identify_blocking_elements_in_radius,
)
from cando.constants.classes import INPUT_CLASSES, UNDERGROUND_CLASSES


def calculate_element_proximity(
    layout_elements: list[dict[str, Any]],
    max_neighbors: int = 5,
    search_radius: float = 20.0,
) -> dict[str, Any]:
    """Calculates proximity data for each element using blocking analysis.

    For each element, identify its closest neighbors with distances by
    using the proximity tool's radius-based blocking element identification.

    Args:
        layout_elements (list): List of all elements in the layout.
        max_neighbors (int): Maximum number of nearest neighbors to return.
        search_radius (float): Radius to search for neighbors (large enough to
            capture sufficient neighbors).

    Returns:
        Dictionary containing:
            - element_proximity: Dict mapping element_id to proximity data
    """
    # Collect all elements (exclude only the layout polygon and underground
    # service facilities, which do not block above-ground flow corridors)
    excluded_classes = [INPUT_CLASSES[0]] + UNDERGROUND_CLASSES
    valid_elements = [
        elem
        for elem in layout_elements
        if elem["class"] not in excluded_classes
    ]

    if len(valid_elements) < 2:
        return {"element_proximity": {}}

    element_proximity = {}

    for elem in valid_elements:
        # Use identify_blocking_elements_in_radius to find nearby elements
        neighbors = identify_blocking_elements_in_radius(
            element=elem,
            radius=search_radius,
            layout_elements=valid_elements,
            excluded_element_ids=[elem["id"]],
        )

        nearest_neighbors = [
            {
                "element_id": n["element_id"],
                "element_class": n["element_class"],
                "distance": n["distance"],
            }
            for n in neighbors[:max_neighbors]
        ]

        element_proximity[elem["id"]] = {
            "element_id": elem["id"],
            "element_class": elem["class"],
            "nearest_neighbors": nearest_neighbors,
        }

    return {"element_proximity": element_proximity}
