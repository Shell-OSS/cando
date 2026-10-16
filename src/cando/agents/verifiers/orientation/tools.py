# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for orientation verification."""

import math

from cando.agents.verifiers.tools.data import get_elements_by_class


def get_element_edges_with_angles(element: dict) -> list[dict]:
    """Extracts all edges from an element polygon with angles and lengths.

    Args:
        element (dict): Element dictionary containing 'polygon' key with list
            of [x, y] coordinates.

    Returns:
        list[dict]: List of edge dictionaries, each containing:
            - 'start': [x, y] start point
            - 'end': [x, y] end point
            - 'length': float length of the edge
            - 'angle_deg': float angle in degrees (0-180)
            - 'midpoint': [x, y] midpoint of the edge
    """
    polygon = element.get("polygon", [])
    if len(polygon) < 2:
        return []

    edges = []
    for i in range(len(polygon)):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % len(polygon)]

        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.sqrt(dx**2 + dy**2)

        # Calculate angle (0-180 range, normalized)
        angle = math.degrees(math.atan2(dy, dx)) % 180

        midpoint = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2]

        edges.append(
            {
                "start": p1,
                "end": p2,
                "length": length,
                "angle_deg": angle,
                "midpoint": midpoint,
            }
        )

    return edges


def get_primary_orientation(element: dict) -> float:
    """Gets the primary orientation angle based on the longest edge.

    Args:
        element (dict): Element dictionary.

    Returns:
        float: Primary orientation angle in degrees (0-180).
    """
    edges = get_element_edges_with_angles(element)
    if not edges:
        return 0.0

    # Find longest edge
    longest_edge = max(edges, key=lambda e: e["length"])
    return longest_edge["angle_deg"]


def get_shorter_edge_orientation(element: dict) -> float:
    """Gets the orientation angle of the shorter edge of an element.

    Args:
        element (dict): Element dictionary.

    Returns:
        float: Shorter edge orientation angle in degrees (0-180).
    """
    edges = get_element_edges_with_angles(element)
    if not edges:
        return 0.0

    # Find shortest edge
    shortest_edge = min(edges, key=lambda e: e["length"])
    return shortest_edge["angle_deg"]


def are_angles_parallel(
    angle1: float, angle2: float, tolerance_deg: float = 5.0
) -> bool:
    """Checks if two angles are parallel within tolerance.

    Args:
        angle1 (float): First angle in degrees.
        angle2 (float): Second angle in degrees.
        tolerance_deg (float): Tolerance in degrees.

    Returns:
        bool: True if angles are parallel within tolerance.
    """
    # Normalize to 0-180 range
    angle1 = angle1 % 180
    angle2 = angle2 % 180

    # Calculate minimum angular difference
    diff = abs(angle1 - angle2)
    diff = min(diff, 180 - diff)

    return diff <= tolerance_deg


def are_angles_perpendicular(
    angle1: float, angle2: float, tolerance_deg: float = 5.0
) -> bool:
    """Checks if two angles are perpendicular within tolerance.

    Args:
        angle1 (float): First angle in degrees.
        angle2 (float): Second angle in degrees.
        tolerance_deg (float): Tolerance in degrees.

    Returns:
        bool: True if angles are perpendicular within tolerance.
    """
    # Normalize to 0-180 range
    angle1 = angle1 % 180
    angle2 = angle2 % 180

    # Calculate angular difference
    diff = abs(angle1 - angle2)

    # Check if close to 90 degrees
    perpendicular_diff = min(abs(diff - 90), abs(diff + 90 - 180))

    return perpendicular_diff <= tolerance_deg


def are_angles_parallel_or_perpendicular(
    angle1: float, angle2: float, tolerance_deg: float = 5.0
) -> tuple[bool, str]:
    """Checks if two angles are parallel or perpendicular within tolerance.

    Args:
        angle1 (float): First angle in degrees.
        angle2 (float): Second angle in degrees.
        tolerance_deg (float): Tolerance in degrees.

    Returns:
        tuple[bool, str]: (is_compliant, relationship_type)
            - is_compliant: True if parallel or perpendicular
            - relationship_type: 'parallel', 'perpendicular', or 'neither'
    """
    if are_angles_parallel(angle1, angle2, tolerance_deg):
        return True, "parallel"
    elif are_angles_perpendicular(angle1, angle2, tolerance_deg):
        return True, "perpendicular"
    else:
        return False, "neither"


def analyze_orientation_patterns(
    rules: list[dict], elements: list[dict], groups: dict[str, list[dict]]
) -> dict:
    """Processes all orientation rules and returns analysis results.

    Args:
        rules (list[dict]): List of rule dictionaries from rules.json.
        elements (list[dict]): All layout elements.
        groups (dict[str, list[dict]]): Groups from assembly verifier.

    Returns:
        dict: Mapping from rule identifiers to analysis results.
    """
    orientation_analysis = {}

    for idx, rule in enumerate(rules):
        rule_analysis = process_orientation_rule(rule, elements, groups)

        orientation_analysis[f"rule_{idx}"] = {
            "rule": rule,
            "analysis": rule_analysis,
        }

    return orientation_analysis


def handle_within_group_consistency_rule(
    rule: dict, groups: dict[str, list[dict]], tolerance: float
) -> dict:
    """Handles within-group consistency orientation rules.

    Args:
        rule (dict): Rule dictionary.
        groups (dict[str, list[dict]]): Groups from assembly verifier.
        tolerance (float): Angular tolerance in degrees.

    Returns:
        dict: Rule analysis results.
    """
    if not groups:
        return {
            "compliant": True,
            "group_analyses": {},
            "reason": "No groups provided",
        }

    requirement = rule.get(
        "orientation_requirement", "parallel_or_perpendicular"
    )
    edge_type = rule.get("element_edge", "longer")

    group_analyses = check_within_group_orientation(
        groups, requirement, tolerance, edge_type
    )

    # Aggregate compliance: all clusters across all classes must be consistent
    all_compliant = all(
        cluster["orientation_consistent"]
        for class_clusters in group_analyses.values()
        for cluster in class_clusters
    )

    return {"compliant": all_compliant, "group_analyses": group_analyses}


def handle_element_to_elements_rule(
    rule: dict, elements: list[dict], tolerance: float
) -> dict:
    """Handles element-to-elements orientation rules.

    Each preference in `preference_order` specifies a `target_edge` and an
    `orientation_requirement`. The top-level `element_edge` field defines
    which edge of the source element is used across all preferences.

    The `match_requirement` field controls how target elements are evaluated:
    - `"all"` (default): the source element must satisfy the orientation
      requirement against *every* target element for a preference to be met.
    - `"any"`: the source element only needs to satisfy the requirement
      against *at least one* target element for a preference to be met.

    Args:
        rule (dict): Rule dictionary.
        elements (list[dict]): All layout elements.
        tolerance (float): Angular tolerance in degrees.

    Returns:
        dict: Rule analysis results.
    """
    # Get source element
    element_class = rule.get("element")
    if not element_class:
        return {
            "compliant": True,
            "element_results": {},
            "reason": "No element specified",
        }

    source_elements = get_elements_by_class(elements, element_class)

    # Get target elements
    target_classes = rule.get("target_elements", [])
    target_elements = []
    for target_class in target_classes:
        target_elements.extend(get_elements_by_class(elements, target_class))

    if not source_elements or not target_elements:
        return {
            "compliant": True,
            "element_results": {},
            "reason": "Source or target elements not found",
        }

    # Inject the top-level element_edge into every preference entry
    source_edge_type = rule.get("element_edge", "longer")
    raw_preferences = rule.get("preference_order", [])
    preference_order = [
        {**pref, "source_edge": source_edge_type} for pref in raw_preferences
    ]

    # "any": compliant if orientation holds for at least one target element.
    # "all" (default): compliant only if orientation holds for every target.
    match_requirement = rule.get("match_requirement", "all")

    # Check orientation
    element_results = check_elements_to_elements_orientation(
        source_elements,
        target_elements,
        preference_order,
        tolerance,
        match_requirement=match_requirement,
    )

    return {
        "compliant": all(r["compliant"] for r in element_results.values()),
        "element_results": element_results,
        "source_class": element_class,
        "target_classes": target_classes,
        "match_requirement": match_requirement,
    }


def process_orientation_rule(
    rule: dict, elements: list[dict], groups: dict[str, list[dict]]
) -> dict:
    """Processes a single orientation rule and returns analysis results.

    Args:
        rule (dict): Rule dictionary from rules.json.
        elements (list[dict]): All layout elements.
        groups (dict[str, list[dict]]): Groups from assembly verifier.

    Returns:
        dict: Analysis results for this rule.
    """
    rule_type = rule.get("type")
    tolerance = rule.get("tolerance_deg", 5.0)

    # Dispatch to appropriate handler based on rule type
    if rule_type == "within_group_consistency":
        return handle_within_group_consistency_rule(rule, groups, tolerance)

    elif rule_type == "element_to_elements":
        return handle_element_to_elements_rule(rule, elements, tolerance)

    else:
        return {
            "compliant": True,
            "reason": f"Unknown rule type: {rule_type}",
        }


def check_orientation_requirement(
    angle1: float, angle2: float, requirement: str, tolerance_deg: float = 5.0
) -> tuple[bool, str]:
    """Checks if two angles meet the specified orientation requirement.

    Args:
        angle1 (float): First angle in degrees.
        angle2 (float): Second angle in degrees.
        requirement (str): Required relationship:
            - 'parallel': Angles must be parallel
            - 'perpendicular': Angles must be perpendicular
            - 'parallel_or_perpendicular': Either parallel or perpendicular
        tolerance_deg (float): Angular tolerance in degrees.

    Returns:
        tuple[bool, str]: (is_compliant, actual_relationship)
    """
    if requirement == "parallel":
        is_compliant = are_angles_parallel(angle1, angle2, tolerance_deg)
        return is_compliant, "parallel" if is_compliant else "not_parallel"

    elif requirement == "perpendicular":
        is_compliant = are_angles_perpendicular(angle1, angle2, tolerance_deg)
        return (
            is_compliant,
            "perpendicular" if is_compliant else "not_perpendicular",
        )

    else:
        return are_angles_parallel_or_perpendicular(
            angle1, angle2, tolerance_deg
        )


def check_elements_to_elements_orientation(
    source_elements: list[dict],
    target_elements: list[dict],
    preference_order: list[dict],
    tolerance_deg: float = 5.0,
    match_requirement: str = "all",
) -> dict:
    """Checks orientation of source elements relative to target elements.

    Iterates through the preference order and returns the first preference
    that is satisfied. A preference is considered satisfied based on
    `match_requirement`:

    - `"all"` (default): the orientation requirement must hold against
      *every* target element.
    - `"any"`: the orientation requirement must hold against *at least
      one* target element.

    Args:
        source_elements (list[dict]): Elements to check.
        target_elements (list[dict]): Target elements to compare against.
        preference_order (list[dict]): Ordered list of preferences, each with:
            - `source_edge`: edge of the source element to use
              (`'longer'` or `'shorter'`).
            - `target_edge`: edge of each target element to compare against.
            - `orientation_requirement`: `'parallel'`,
              `'perpendicular'`, or `'parallel_or_perpendicular'`.
        tolerance_deg (float): Angular tolerance in degrees.
        match_requirement (str): `'all'` or `'any'` — how many targets
            must satisfy the requirement for a preference to be met.

    Returns:
        dict: Mapping from source element IDs to check results, each
        containing:
            - `compliant`: True if *any* preference was satisfied.
            - `preference_met`: True if the *first* (highest-priority)
              preference was the one satisfied; False if a lower-priority
              preference was met or no preference was met at all.
            - `violations`: list of violation dicts (empty when compliant).
            - `source_elem_class`: class name of the source element.
    """
    results: dict[int, dict[str, object]] = {}

    if not target_elements:
        return results

    for source_elem in source_elements:
        # Try each preference in order; stop at the first one that is met.
        preference_met = -1
        all_violations = []

        for pref_idx, preference in enumerate(preference_order):
            source_edge_type = preference.get("source_edge", "longer")
            target_edge_type = preference.get("target_edge", "longer")
            requirement = preference.get(
                "orientation_requirement", "parallel"
            )

            # Resolve the source edge angle from the actual polygon edges.
            if source_edge_type == "shorter":
                source_angle = get_shorter_edge_orientation(source_elem)
            else:
                source_angle = get_primary_orientation(source_elem)

            # Evaluate this preference against every target element.
            pref_violations = []
            any_match = False

            for target_elem in target_elements:
                # Resolve the target edge angle from the actual polygon edges.
                if target_edge_type == "shorter":
                    target_angle = get_shorter_edge_orientation(target_elem)
                else:
                    target_angle = get_primary_orientation(target_elem)

                is_compliant, relationship = check_orientation_requirement(
                    source_angle, target_angle, requirement, tolerance_deg
                )

                if is_compliant:
                    any_match = True
                else:
                    pref_violations.append(
                        {
                            "target_elem_id": target_elem.get("id"),
                            "target_elem_class": target_elem.get("class"),
                            "source_edge": source_edge_type,
                            "target_edge": target_edge_type,
                            "source_angle": round(source_angle, 2),
                            "target_angle": round(target_angle, 2),
                            "relationship": relationship,
                            "requirement": requirement,
                        }
                    )

            # Determine whether this preference is satisfied.
            if match_requirement == "any":
                pref_satisfied = any_match
            else:  # 'all'
                pref_satisfied = len(pref_violations) == 0

            if pref_satisfied:
                preference_met = pref_idx
                break
            else:
                all_violations.extend(pref_violations)

        results[source_elem["id"]] = {
            "compliant": preference_met >= 0,
            "preference_met": preference_met == 0,
            "violations": [] if preference_met >= 0 else all_violations,
            "source_elem_class": source_elem.get("class"),
        }

    return results


def check_within_group_orientation(
    groups: dict[str, list[dict]],
    requirement: str = "parallel_or_perpendicular",
    tolerance_deg: float = 5.0,
    edge_type: str = "longer",
) -> dict:
    """Checks orientation consistency within spatial groups.

    Args:
        groups (dict[str, list[dict]]): Groups from assembly verifier.
        requirement (str): Orientation requirement within groups.
        tolerance_deg (float): Angular tolerance in degrees.
        edge_type (str): Which edge to check.

    Returns:
        dict: Analysis results mapping class names to cluster analyses.
    """
    group_analysis = {}

    for class_name, clusters in groups.items():
        class_analysis = []

        for cluster_metadata in clusters:
            cluster_elements = cluster_metadata["elements"]

            if len(cluster_elements) < 2:
                class_analysis.append(
                    {
                        "cluster_index": cluster_metadata["cluster_index"],
                        "element_ids": cluster_metadata["element_ids"],
                        "orientation_consistent": True,
                        "violations": [],
                        "reason": "Single element cluster",
                    }
                )
                continue

            # Check pairwise orientation within cluster
            violations = []
            for i in range(len(cluster_elements)):
                for j in range(i + 1, len(cluster_elements)):
                    elem1 = cluster_elements[i]
                    elem2 = cluster_elements[j]

                    if edge_type == "shorter":
                        angle1 = get_shorter_edge_orientation(elem1)
                        angle2 = get_shorter_edge_orientation(elem2)
                    else:
                        angle1 = get_primary_orientation(elem1)
                        angle2 = get_primary_orientation(elem2)

                    is_compliant, relationship = (
                        check_orientation_requirement(
                            angle1, angle2, requirement, tolerance_deg
                        )
                    )

                    if not is_compliant:
                        violations.append(
                            {
                                "elem1_id": elem1.get("id"),
                                "elem1_angle": round(angle1, 2),
                                "elem2_id": elem2.get("id"),
                                "elem2_angle": round(angle2, 2),
                                "relationship": relationship,
                                "requirement": requirement,
                            }
                        )

            class_analysis.append(
                {
                    "cluster_index": cluster_metadata["cluster_index"],
                    "element_ids": cluster_metadata["element_ids"],
                    "orientation_consistent": len(violations) == 0,
                    "violations": violations,
                }
            )

        group_analysis[class_name] = class_analysis

    return group_analysis
