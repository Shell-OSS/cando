# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for hard constraints verification."""

import math
from collections import Counter
from itertools import combinations
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment
from shapely.geometry import Polygon

from cando.constants.classes import INPUT_CLASSES

from .config import (
    ALLOWED_OVERLAPS,
    DIMENSION_TOLERANCE,
    OUT_OF_BOUNDARIES_TOLERANCE,
    OVERLAP_TOLERANCE,
    UNDERGROUND_CLASSES,
)
from .utils import corrections_for_outside_geom


def validate_constraints(parsed_constraints: dict, layout_json: dict) -> dict:
    """
    Validates that the generated layout matches the constraints in terms of
    element counts.

    Args:
        parsed_constraints (dict): Dictionary with 'elements' from parsed
            input constraints. Expects following structure:
            {
                "elements": [
                    {
                        "class": str,
                        "width": float,
                        "height": float,
                    },
                    ...
                ]

            }
        layout_json (dict): Dictionary with 'elements' from the generated
            layout. Expects following structure:
            {
                "elements": [
                    {
                        'id': int,
                        'class': string,
                        'polygon': list[[list[float, float]]],
                        'bbox': dict,
                        'rotation_deg': float
                    },
                    ...
                ]
            }

    Returns:
        dict: Dictionary with layout_json data plus 'count_validation' key.
    """
    # Get all constraint elements
    constraint_elements = parsed_constraints["elements"]

    # Get all generated elements
    generated_elements = layout_json["elements"]

    # Count elements by class
    expected_counts = Counter([elem["class"] for elem in constraint_elements])
    generated_counts = Counter([elem["class"] for elem in generated_elements])

    # Build class-to-element mapping for efficient lookup
    class_to_elements = {}
    for elem in constraint_elements:
        class_name = elem["class"]
        if class_name not in class_to_elements:
            class_to_elements[class_name] = elem

    # Find missing elements
    missing_elements = []
    for class_name, expected_count in expected_counts.items():
        generated_count = generated_counts.get(class_name, 0)
        if generated_count < expected_count:
            missing_count = expected_count - generated_count
            constraint_elem = class_to_elements[class_name]
            # Add missing element copies
            missing_elements.extend(
                [constraint_elem.copy() for _ in range(missing_count)]
            )

    # Check if counts match
    matches = len(constraint_elements) == len(generated_elements)

    validation = {
        "matches_constraints": matches,
        "missing_count": len(constraint_elements) - len(generated_elements),
        "missing_elements": missing_elements,
    }

    return {**layout_json, "count_validation": validation}


def _bbox_dims(element: dict[str, Any]) -> tuple[float, float]:
    """Returns the (width, height) of an element from its actual geometry.

    Uses the minimum rotated rectangle of the polygon to recover the true
    intrinsic dimensions regardless of the element's rotation.  Falls back
    to the stored `'bbox'` when no polygon is available.

    Args:
        element: Element dict with a `'polygon'` and optionally `'bbox'`.

    Returns:
        `(width, height)` in layout units.
    """
    coords = element.get("polygon", [])
    if coords and len(coords) >= 3:
        mrr = Polygon(coords).minimum_rotated_rectangle
        mrr_coords = list(mrr.exterior.coords)
        dx0 = mrr_coords[1][0] - mrr_coords[0][0]
        dy0 = mrr_coords[1][1] - mrr_coords[0][1]
        dx1 = mrr_coords[2][0] - mrr_coords[1][0]
        dy1 = mrr_coords[2][1] - mrr_coords[1][1]
        side0 = math.sqrt(dx0**2 + dy0**2)
        side1 = math.sqrt(dx1**2 + dy1**2)
        return (side0, side1)
    bbox = element.get("bbox")
    if bbox and all(k in bbox for k in ("min_x", "max_x", "min_y", "max_y")):
        return (
            abs(bbox["max_x"] - bbox["min_x"]),
            abs(bbox["max_y"] - bbox["min_y"]),
        )
    return (0.0, 0.0)


def _dims_match(
    gen_w: float,
    gen_h: float,
    con_w: float,
    con_h: float,
    tolerance: float,
) -> bool:
    """Returns True when generated dimensions match constraint dimensions.

    Both the natural and the rotated (90°) orientations are tested, since
    the generator may produce a valid element in either orientation.

    Args:
        gen_w: Generated element width.
        gen_h: Generated element height.
        con_w: Constraint width.
        con_h: Constraint height.
        tolerance: Maximum allowed per-side deviation.

    Returns:
        `True` if at least one orientation satisfies the tolerance.
    """
    natural = (
        abs(gen_w - con_w) <= tolerance and abs(gen_h - con_h) <= tolerance
    )
    rotated = (
        abs(gen_w - con_h) <= tolerance and abs(gen_h - con_w) <= tolerance
    )
    return natural or rotated


def validate_dimensions(
    parsed_constraints: dict[str, Any],
    layout_json: dict[str, Any],
    tolerance: float = DIMENSION_TOLERANCE,
) -> dict[str, Any]:
    """Validates that generated element dimensions match constraint
    dimensions.

    Elements in :data:`~cando.constants.classes.INPUT_CLASSES` (Layout
    Polygon, Entrance, Exit) carry polygon constraints rather than
    width/height specifications and are skipped.

    For each remaining class, a minimum-cost bipartite matching is used to
    pair constraint elements with generated elements.  A pair is
    **compatible** when the generated bounding-box dimensions agree with the
    constraint width/height within *tolerance*, in either the natural or the
    90°-rotated orientation.  Pairs with incompatible dimensions are recorded
    as violations.

    Args:
        parsed_constraints: Dict with an `'elements'` list.  Each entry
            that carries `'width'` and `'height'` fields defines the
            expected dimensions for one element of that class.
        layout_json: Dict with an `'elements'` list produced by the
            generator.
        tolerance: Maximum allowed per-side deviation in layout units.

    Returns:
        Dict with a `'dimension_violations'` key containing a list of
        violation dicts::

            {
                "element_id":    int,
                "element_class": str,
                "gen_w":         float,  # generated width
                "gen_h":         float,  # generated height
                "con_w":         float,  # constraint width
                "con_h":         float,  # constraint height
            }
    """
    _SKIP = set(INPUT_CLASSES)

    # --- Collect constraint elements that carry dimension specs -----------
    # Group by class; each entry is (con_w, con_h).
    from collections import defaultdict

    con_by_class: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for elem in parsed_constraints.get("elements", []):
        if elem["class"] in _SKIP:
            continue
        w, h = elem.get("width"), elem.get("height")
        if w is None or h is None:
            continue
        con_by_class[elem["class"]].append((float(w), float(h)))

    # --- Collect generated elements grouped by class ----------------------
    gen_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for elem in layout_json.get("elements", []):
        if elem["class"] in _SKIP:
            continue
        gen_by_class[elem["class"]].append(elem)

    violations: list[dict[str, Any]] = []

    for cls, con_dims in con_by_class.items():
        gen_elems = gen_by_class.get(cls, [])

        n_con = len(con_dims)
        n_gen = len(gen_elems)

        if n_gen == 0:
            # Missing elements are handled by validate_constraints.
            continue

        # Build cost matrix: rows = constraint slots, cols = generated elems.
        # cost = 0 if compatible, 1 if not.
        cost = np.ones((n_con, n_gen), dtype=float)
        for i, (cw, ch) in enumerate(con_dims):
            for j, gen_elem in enumerate(gen_elems):
                gw, gh = _bbox_dims(gen_elem)
                if _dims_match(gw, gh, cw, ch, tolerance):
                    cost[i, j] = 0.0

        row_ind, col_ind = linear_sum_assignment(cost)

        for i, j in zip(row_ind, col_ind, strict=True):
            if cost[i, j] > 0.0:  # incompatible pair
                cw, ch = con_dims[i]
                gen_elem = gen_elems[j]
                gw, gh = _bbox_dims(gen_elem)
                violations.append(
                    {
                        "element_id": gen_elem["id"],
                        "element_class": cls,
                        "gen_w": round(gw, 4),
                        "gen_h": round(gh, 4),
                        "con_w": cw,
                        "con_h": ch,
                    }
                )

    return {"dimension_violations": violations}


def detect_overlaps(layout_json: dict) -> dict:
    """
    Detects overlaps between all elements and add overlaps information.

    Args:
        layout_json (dict): Dictionary with 'elements' from the generated
            layout. Expects following structure:
            {
                "elements": [
                    {
                        'id': int,
                        'class': string,
                        'polygon': list[[list[float, float]]],
                        'bbox': dict,
                        'rotation_deg': float
                    },
                    ...
                ]
            }

    Returns:
        dict: Dictionary with 'elements' and 'overlaps' keys. Where 'overlaps'
            is a list of dictionaries with the following structure:
            {
                "element_a_id": id_a,
                "element_a_class": class_a,
                "element_b_id": id_b,
                "element_b_class": class_b,
                "type": overlap_type ("intersect" or "containment"),
                "area": overlap_area,
                "x_correction": signed x-axis distance to resolve overlap,
                "y_correction": signed y-axis distance to resolve overlap,
            }
    """
    elements = layout_json["elements"]
    overlaps = []

    # Filter out layout elements
    non_layout_elements = [
        elem for elem in elements if elem["class"] != INPUT_CLASSES[0]
    ]

    # Check all pairs of elements
    for elemA, elemB in combinations(non_layout_elements, 2):
        # Check if this pair is allowed to overlap
        classes_pair = (elemA["class"], elemB["class"])
        if (
            classes_pair in ALLOWED_OVERLAPS
            or (classes_pair[1], classes_pair[0]) in ALLOWED_OVERLAPS
        ):
            continue

        if elemA["class"] != elemB["class"] and (
            elemA["class"] in UNDERGROUND_CLASSES
            or elemB["class"] in UNDERGROUND_CLASSES
        ):
            # One of the elements is underground, allow overlap with others
            continue

        # Create polygons
        poly_a = Polygon(elemA["polygon"])
        poly_b = Polygon(elemB["polygon"])

        # Check if polygons overlap (intersect but don't just touch)
        if not (poly_a.intersects(poly_b) and not poly_a.touches(poly_b)):
            continue

        # Ensure element A is always the smaller element (the one to move)
        if poly_a.area > poly_b.area:
            elemA, elemB = elemB, elemA
            poly_a, poly_b = poly_b, poly_a

        # Calculate overlap details
        intersection = poly_a.intersection(poly_b)
        overlap_area = round(intersection.area, 2)

        # Determine overlap type
        if poly_a.within(poly_b) or poly_b.within(poly_a):
            overlap_type = "containment"
        else:
            overlap_type = "intersect"

        # Get intersection polygon coordinates and dimensions
        if intersection.geom_type == "Polygon":
            overlap_polygon = [
                [round(x, 2), round(y, 2)]
                for x, y in list(intersection.exterior.coords)[:-1]
            ]
            # Calculate bounding box dimensions of the overlap polygon
            # Used for tolerance check: if either dimension is negligible,
            # the overlap is a thin sliver and can be ignored
            minx, miny, maxx, maxy = intersection.bounds
            overlap_width = round(maxx - minx, 2)
            overlap_height = round(maxy - miny, 2)
        else:
            overlap_polygon = None
            overlap_width = None
            overlap_height = None

        if overlap_width is None or overlap_height is None:
            # We couldn't calculate dimensions
            continue

        if (
            overlap_width <= OVERLAP_TOLERANCE
            or overlap_height <= OVERLAP_TOLERANCE
        ):
            # Negligible overlap, skip
            continue

        # Compute minimum signed corrections
        bounds_a = poly_a.bounds  # (minx, miny, maxx, maxy)
        bounds_b = poly_b.bounds

        left_escape = bounds_b[2] - bounds_a[0]  # push elemA left
        right_escape = bounds_a[2] - bounds_b[0]  # push elemA right
        x_correction = round(
            -left_escape if left_escape <= right_escape else right_escape, 2
        )

        down_escape = bounds_b[3] - bounds_a[1]  # push elemA down
        up_escape = bounds_a[3] - bounds_b[1]  # push elemA up
        y_correction = round(
            -down_escape if down_escape <= up_escape else up_escape, 2
        )

        # Create overlap entry
        overlap_entry = {
            "element_a_id": elemA["id"],
            "element_a_class": elemA["class"],
            "element_b_id": elemB["id"],
            "element_b_class": elemB["class"],
            "type": overlap_type,
            "area": overlap_area,
            "x_correction": x_correction,
            "y_correction": y_correction,
        }

        if overlap_polygon is not None:
            overlap_entry["overlap_polygon"] = overlap_polygon

        overlaps.append(overlap_entry)

    return {"elements": elements, "overlaps": overlaps}


def check_single_overlap(elemA: dict, elemB: dict) -> bool:
    """
    Returns True if polygons overlap.

    Args:
        elemA (dict): First element dictionary.
        elemB (dict): Second element dictionary.

    Returns:
        bool: True if the elements overlap.
    """
    polyA = Polygon(elemA.get("polygon", []))
    polyB = Polygon(elemB.get("polygon", []))

    if not polyA.is_valid or not polyB.is_valid:
        return False

    return polyA.intersects(polyB) and not polyA.touches(polyB)


def detect_out_of_boundaries(layout_json: dict) -> dict:
    """
    Detects elements that are outside or partially outside the layout
    boundary (Layout Polygon).

    Args:
        layout_json (dict): Dictionary with 'elements' from the generated
            layout. Expects following structure:
            {
                "elements": [
                    {
                        'id': int,
                        'class': string,
                        'polygon': list[[list[float, float]]],
                        'bbox': dict,
                        'rotation_deg': float
                    },
                    ...
                ]
            }

    Returns:
        dict: Dictionary with 'elements' and 'out_of_boundaries' keys.
            Each violation entry includes signed 'x_correction' and
            'y_correction' indicating the minimum distance and direction to
            move the element along each axis to be fully inside the layout
            boundary.
    """
    elements = layout_json["elements"]
    out_of_boundaries = []

    # Find layout and create element data in single pass
    layout_polygon = None
    element_data = []

    for elem in elements:
        if elem["class"] == INPUT_CLASSES[0]:
            layout_polygon = Polygon(elem["polygon"])
        else:
            element_data.append(
                (
                    elem["id"],
                    elem["class"],
                    Polygon(elem["polygon"]),
                    elem["polygon"],
                )
            )

    if layout_polygon is None:
        # No layout found, return original data with empty out_of_boundaries
        return {**layout_json, "out_of_boundaries": []}

    # Check each element against layout boundary
    for elem_id, elem_class, elem_polygon, original_coords in element_data:
        # Check if element is completely within layout
        if layout_polygon.contains(elem_polygon):
            # Element is fully inside, skip
            continue

        # Check if element is completely outside
        if not layout_polygon.intersects(elem_polygon):
            x_corr, y_corr = corrections_for_outside_geom(
                elem_polygon, layout_polygon
            )

            out_entry = {
                "element_id": elem_id,
                "element_class": elem_class,
                "type": "full",
                "outside_area": round(elem_polygon.area, 2),
                "outside_polygon": original_coords,
                "x_correction": round(x_corr, 2),
                "y_correction": round(y_corr, 2),
            }
            out_of_boundaries.append(out_entry)
        else:
            # Partially outside
            outside_part = elem_polygon.difference(layout_polygon)

            if outside_part.is_empty:
                continue

            # Get outside polygon coordinates
            if outside_part.geom_type == "Polygon":
                outside_polygon = [
                    [round(x, 2), round(y, 2)]
                    for x, y in list(outside_part.exterior.coords)[:-1]
                ]
            else:
                outside_polygon = None

            if outside_polygon is None:
                continue

            # Calculate bounding box dimensions of the outside polygon
            out_minx, out_miny, out_maxx, out_maxy = outside_part.bounds
            outside_width = round(out_maxx - out_minx, 2)
            outside_height = round(out_maxy - out_miny, 2)

            if (
                outside_width <= OUT_OF_BOUNDARIES_TOLERANCE
                or outside_height <= OUT_OF_BOUNDARIES_TOLERANCE
            ):
                # Negligible outside extent, skip
                continue

            x_correction, y_correction = corrections_for_outside_geom(
                outside_part, layout_polygon
            )

            out_entry = {
                "element_id": elem_id,
                "element_class": elem_class,
                "type": "partial",
                "outside_area": round(outside_part.area, 2),
                "outside_polygon": outside_polygon,
                "x_correction": round(x_correction, 2),
                "y_correction": round(y_correction, 2),
            }
            out_of_boundaries.append(out_entry)

    return {**layout_json, "out_of_boundaries": out_of_boundaries}
