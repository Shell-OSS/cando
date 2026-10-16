# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for hard constraints planning - spatial availability analysis.

Tools for analyzing spatial availability for hard constraints planning.
"""

from typing import Any

from cando.agents.planners.proximity.tools import (
    calculate_free_space_in_radius,
    calculate_space_between_elements,
    identify_blocking_elements_in_radius,
)

# Default radius for analyzing free space around violating elements (meters)
_DEFAULT_ANALYSIS_RADIUS = 5.0

# Max violations fed into spatial analysis; keeps planner-worker prompts
# bounded regardless of how many violations the verifier reports.
_MAX_VIOLATIONS_FOR_PLANNING = 10


def augment_layout_data_for_planning(
    elements: list[dict[str, Any]],
    validation_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Augment layout data with spatial availability analysis for planning.

    Analyzes spatial context around elements flagged by the verifier,
    without re-running verification. Provides free-space, blocking-element,
    and overlap-corridor information to help the LLM plan fixes.

    Uses shared spatial analysis tools from the proximity planner.

    Args:
        elements: List of element dictionaries from the layout.
        validation_data: Raw structured data from the hard constraints
            verifier containing 'overlaps' and 'out_of_boundaries' lists.

    Returns:
        Dictionary containing spatial availability data:
            - space_analysis: Per-element free space and blocking info
                for elements mentioned in violations.
            - overlap_corridors: Space analysis between overlapping pairs.
            - summary: High-level statistics.
    """
    spatial_analysis: dict[str, Any] = {
        "space_analysis": {},
        "overlap_corridors": {},
        "summary": {},
    }

    if not validation_data:
        return spatial_analysis

    overlaps = validation_data.get("overlaps", [])[
        :_MAX_VIOLATIONS_FOR_PLANNING
    ]
    out_of_boundaries = validation_data.get("out_of_boundaries", [])[
        :_MAX_VIOLATIONS_FOR_PLANNING
    ]

    # Extract violating element IDs from structured data
    violating_ids: list[int] = []
    for item in out_of_boundaries:
        violating_ids.append(item["element_id"])
    for item in overlaps:
        violating_ids.append(item["element_a_id"])
        violating_ids.append(item["element_b_id"])
    # Deduplicate preserving order
    violating_ids = list(dict.fromkeys(violating_ids))

    # Extract overlap pairs from structured data
    overlap_pairs: list[tuple[int, int]] = [
        (item["element_a_id"], item["element_b_id"]) for item in overlaps
    ]

    # Build element lookup
    element_lookup = {elem["id"]: elem for elem in elements}

    # --- Per-element free space and blocking analysis ---
    for elem_id in violating_ids:
        elem = element_lookup.get(elem_id)
        if elem is None:
            continue

        free_space = calculate_free_space_in_radius(
            element=elem,
            radius=_DEFAULT_ANALYSIS_RADIUS,
            layout_elements=elements,
            excluded_element_ids=[elem_id],
        )

        blocking = identify_blocking_elements_in_radius(
            element=elem,
            radius=_DEFAULT_ANALYSIS_RADIUS,
            layout_elements=elements,
            excluded_element_ids=[elem_id],
        )

        spatial_analysis["space_analysis"][elem_id] = {
            "element_class": elem.get("class"),
            "free_space": free_space,
            "blocking_elements": blocking,
        }

    # --- Overlap corridor analysis ---
    for elem_a_id, elem_b_id in overlap_pairs:
        elem_a = element_lookup.get(elem_a_id)
        elem_b = element_lookup.get(elem_b_id)
        if elem_a is None or elem_b is None:
            continue

        corridor = calculate_space_between_elements(
            source_element=elem_a,
            target_element=elem_b,
            layout_elements=elements,
        )

        pair_key = f"{elem_a_id}-{elem_b_id}"
        spatial_analysis["overlap_corridors"][pair_key] = {
            "source_element_id": elem_a_id,
            "target_element_id": elem_b_id,
            "corridor_analysis": corridor,
        }

    # --- Summary ---
    spatial_analysis["summary"] = {
        "elements_analyzed": len(spatial_analysis["space_analysis"]),
        "overlap_pairs_analyzed": len(spatial_analysis["overlap_corridors"]),
        "elements_with_limited_space": sum(
            1
            for data in spatial_analysis["space_analysis"].values()
            if data.get("free_space", {}).get("free_percentage", 100) < 50
        ),
    }

    return spatial_analysis
