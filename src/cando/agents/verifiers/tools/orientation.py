# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Orientation data tools for layout elements."""

from ..tools.geometric import (
    calculate_orientation_relation,
    get_element_center,
)


def sort_elements_along_axis(elements: list, axis: str) -> list:
    """
    Sort by projected coordinate along 'x' or 'y' axis.

    Args:
        elements (list[dict]): List of element dictionaries.
        axis (str): Either 'x' or 'y'.

    Returns:
        list: Sorted list of elements.
    """
    if axis not in ["x", "y"]:
        raise ValueError("axis must be 'x' or 'y'")

    centers = [(get_element_center(elem), elem) for elem in elements]
    sorted_elements = sorted(centers, key=lambda x: x[0][axis])

    return [elem for _, elem in sorted_elements]


def compute_adjacency_chain(elements: list, axis: str) -> list:
    """
    Return elements ordered by adjacency along a line.

    Args:
        elements (list[dict]): List of element dictionaries.
        axis (str): Either 'x' or 'y' - the axis along which to order.

    Returns:
        list: List of elements ordered by adjacency.
    """
    if len(elements) <= 1:
        return elements

    return sort_elements_along_axis(elements, axis)


def evaluate_orientation_preference(elements: list, preferred: str) -> dict:
    """
    Return whether group orientation matches preference.

    Args:
        elements (list[dict]): List of element dictionaries.
        preferred (str): Either 'longer_edges_adjacent' or
            'shorter_edges_adjacent'.

    Returns:
        dict: Dictionary with compliance information.
    """
    if len(elements) < 2:
        return {
            "compliant": True,
            "details": "< 2 elements, orientation preference not applicable",
        }

    violations = []

    for i in range(len(elements) - 1):
        relation = calculate_orientation_relation(
            elements[i], elements[i + 1]
        )

        if relation != preferred:
            violations.append(
                {
                    "elem1_id": elements[i].get("id"),
                    "elem2_id": elements[i + 1].get("id"),
                    "actual_orientation": relation,
                    "expected_orientation": preferred,
                }
            )

    return {
        "compliant": len(violations) == 0,
        "violations": violations,
        "details": (
            f"Checked {len(elements)} elements for '{preferred}' orientation"
        ),
    }
