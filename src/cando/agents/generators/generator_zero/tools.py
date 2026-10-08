# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tools for GeneratorZero agent.

Tools for initial layout generation and refinement.
"""

import json
import math
from typing import Any

from shapely.affinity import rotate, translate
from shapely.geometry import Polygon

from cando.agents.verifiers.hard_constraints.tools import (
    detect_out_of_boundaries,
    detect_overlaps,
)
from cando.constants.classes import BOX_GEOM_CLASSES, INPUT_CLASSES
from cando.utils.converters import convert_to_polygons_layout
from cando.utils.generic import json_dumps, round_floats

from .constants import DIMENSION_TOLERANCE, UNDERGROUND_CLASSES


def adjust_shapes(layout_json: str, constraints_dict: dict) -> dict:
    """Adjusts element dimensions to match input constraints using shapely.

    For each element class (except fixed Layout/Entrance/Exit), performs
    bipartite matching between current and expected dimensions using
    minimum rotated rectangles. Each element is matched exactly once
    to an expected dimension.

    Algorithm:
    1. Convert polygons to minimum rotated bounding boxes using shapely
    2. Extract width, height, and rotation angle from bounding boxes
    3. Perform bipartite matching between layout and constraint elements
    4. Scale bounding boxes to match constraint dimensions (keep rotation)
    5. Convert bounding boxes back to polygons

    Args:
        layout_json (str): The generated layout as a JSON string with
            'elements' key.
        constraints_dict (dict): The input constraints containing expected
            dimensions.

    Returns:
        dict: The adjusted layout dictionary with updated polygons.
    """
    # Parse JSON string
    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON format - {str(e)}"}

    if "elements" not in layout:
        return {"error": "Invalid layout format - missing 'elements' key"}

    elements = layout["elements"]
    input_elements = constraints_dict["elements"]

    # Extract expected dimensions from constraints, grouped by class
    expected_by_class: dict[str, list[dict[str, float]]] = {}
    for inp_elem in input_elements:
        elem_class = inp_elem.get("class", "")
        if elem_class and elem_class not in INPUT_CLASSES:
            width = inp_elem.get("width", 0)
            height = inp_elem.get("height", 0)

            if width > 0 and height > 0:
                if elem_class not in expected_by_class:
                    expected_by_class[elem_class] = []
                expected_by_class[elem_class].append(
                    {"width": width, "height": height}
                )

    # Group layout elements by class with their current dimensions
    elements_by_class: dict[str, list[dict[str, Any]]] = {}
    for element in elements:
        elem_class = element.get("class", "")

        # Skip fixed elements
        if elem_class in INPUT_CLASSES:
            continue

        polygon_coords = element.get("polygon", [])
        if not polygon_coords or len(polygon_coords) < 3:
            continue

        # Convert to shapely Polygon
        try:
            poly = Polygon(polygon_coords)
            if not poly.is_valid:
                poly = poly.buffer(0)  # Fix invalid polygons
        except Exception:
            continue

        # Get minimum rotated rectangle
        min_rect = poly.minimum_rotated_rectangle

        # Extract coordinates of the bounding box
        rect_coords = list(min_rect.exterior.coords)

        # Calculate dimensions and rotation
        # The minimum_rotated_rectangle returns 5 points (4 corners + close)
        if len(rect_coords) >= 4:
            # Calculate edge vectors
            edge1 = (
                rect_coords[1][0] - rect_coords[0][0],
                rect_coords[1][1] - rect_coords[0][1],
            )
            edge2 = (
                rect_coords[2][0] - rect_coords[1][0],
                rect_coords[2][1] - rect_coords[1][1],
            )

            # Calculate edge lengths
            len1 = (edge1[0] ** 2 + edge1[1] ** 2) ** 0.5
            len2 = (edge2[0] ** 2 + edge2[1] ** 2) ** 0.5

            # Width is the longer edge, height is the shorter
            if len1 >= len2:
                current_width = len1
                current_height = len2
                # Rotation angle of the longer edge
                rotation_rad = math.atan2(edge1[1], edge1[0])
            else:
                current_width = len2
                current_height = len1
                # Rotation angle of the longer edge
                rotation_rad = math.atan2(edge2[1], edge2[0])

            # Normalize rotation to [-pi/2, pi/2]
            while rotation_rad > math.pi / 2:
                rotation_rad -= math.pi
            while rotation_rad < -math.pi / 2:
                rotation_rad += math.pi

            # Convert to degrees
            rotation_deg = math.degrees(rotation_rad)

            if elem_class not in elements_by_class:
                elements_by_class[elem_class] = []

            elements_by_class[elem_class].append(
                {
                    "element": element,
                    "polygon": poly,
                    "min_rect": min_rect,
                    "current_width": round(current_width, 2),
                    "current_height": round(current_height, 2),
                    "rotation_rad": rotation_rad,
                    "rotation_deg": rotation_deg,
                    "centroid": (poly.centroid.x, poly.centroid.y),
                }
            )

    # For each class, perform bipartite matching
    for elem_class in elements_by_class:
        if elem_class not in expected_by_class:
            continue

        current_elements = elements_by_class[elem_class]
        expected_dims = expected_by_class[elem_class]

        # Verify counts match
        if len(current_elements) != len(expected_dims):
            return {
                "error": (
                    f"Mismatch in element count for class '{elem_class}': "
                    f"{len(current_elements)} in layout vs "
                    f"{len(expected_dims)} in constraints"
                )
            }

        # Perform greedy bipartite matching
        n = len(current_elements)
        matched_expected = [False] * n
        matching = []

        # For each current element, find best unmatched expected dimension
        for curr_idx, curr_data in enumerate(current_elements):
            curr_w = curr_data["current_width"]
            curr_h = curr_data["current_height"]

            best_exp_idx = None
            best_distance = float("inf")
            best_orientation = "normal"  # 'normal' or 'rotated'

            for exp_idx, exp_dim in enumerate(expected_dims):
                if matched_expected[exp_idx]:
                    continue

                exp_w = exp_dim["width"]
                exp_h = exp_dim["height"]

                # Try normal orientation: current(w,h) -> expected(w,h)
                distance_normal = (
                    (curr_w - exp_w) ** 2 + (curr_h - exp_h) ** 2
                ) ** 0.5

                # Try rotated orientation: current(w,h) -> expected(h,w)
                distance_rotated = (
                    (curr_w - exp_h) ** 2 + (curr_h - exp_w) ** 2
                ) ** 0.5

                # Choose the orientation with smaller distance
                if distance_normal < distance_rotated:
                    distance = distance_normal
                    orientation = "normal"
                else:
                    distance = distance_rotated
                    orientation = "rotated"

                if distance < best_distance:
                    best_distance = distance
                    best_exp_idx = exp_idx
                    best_orientation = orientation

            if best_exp_idx is not None:
                matched_expected[best_exp_idx] = True
                matching.append(
                    (curr_idx, best_exp_idx, best_distance, best_orientation)
                )

        # Apply adjustments based on matching
        for curr_idx, exp_idx, _, orientation in matching:
            curr_data = current_elements[curr_idx]
            expected_dim = expected_dims[exp_idx]

            element = curr_data["element"]
            current_width = curr_data["current_width"]
            current_height = curr_data["current_height"]
            rotation_rad = curr_data["rotation_rad"]
            centroid = curr_data["centroid"]

            # Apply orientation: if rotated, swap expected dimensions
            if orientation == "rotated":
                expected_width = expected_dim["height"]
                expected_height = expected_dim["width"]
            else:
                expected_width = expected_dim["width"]
                expected_height = expected_dim["height"]

            # Check if adjustment is needed
            width_diff = abs(current_width - expected_width)
            height_diff = abs(current_height - expected_height)

            if (
                width_diff <= DIMENSION_TOLERANCE
                and height_diff <= DIMENSION_TOLERANCE
            ):
                # Already within tolerance
                continue

            # Create a new axis-aligned bounding box with target dimensions
            half_w = expected_width / 2
            half_h = expected_height / 2

            # Create rectangle centered at origin
            new_box = Polygon(
                [
                    [-half_w, -half_h],
                    [half_w, -half_h],
                    [half_w, half_h],
                    [-half_w, half_h],
                ]
            )

            # Rotate the box by the original rotation angle
            rotated_box = rotate(
                new_box, rotation_rad, origin=(0, 0), use_radians=True
            )

            # Translate to original centroid
            final_box = translate(
                rotated_box, xoff=centroid[0], yoff=centroid[1]
            )

            # Convert back to coordinate list
            new_coords = list(final_box.exterior.coords)
            # Round to 2 decimal places and convert to list format
            new_polygon = [
                [round(x, 2), round(y, 2)]
                for x, y in new_coords[:-1]  # Exclude last (closing) point
            ]

            # Update element polygon
            element["polygon"] = new_polygon

    return layout


def _check_boundaries(
    elements: list, errors: list, violations: dict
) -> float:
    """Check that all elements are within layout boundaries.

    Uses detect_out_of_boundaries from hard_constraints.

    Args:
        elements: List of all elements.
        errors: List to append error messages to.
        violations: Dict to append structured violation info to.

    Returns:
        float: Total area outside boundaries in square meters.
    """
    layout_json = {"elements": elements}
    result = detect_out_of_boundaries(layout_json)

    out_of_boundaries = result.get("out_of_boundaries", [])
    total_outside_area = 0.0

    for entry in out_of_boundaries:
        elem_id = entry.get("element_id", "?")
        elem_class = entry.get("element_class", "")
        boundary_type = entry.get("type", "")
        outside_area = entry.get("outside_area", 0)

        # Accumulate total outside area
        total_outside_area += outside_area

        # Add structured violation
        if str(elem_id) not in violations:
            violations[str(elem_id)] = []

        violations[str(elem_id)].append(
            {
                "type": "out_of_boundaries",
                "class": elem_class,
                "boundary_type": boundary_type,
                "outside_area": round(outside_area, 2),
            }
        )

        # Keep text errors for backward compatibility
        if boundary_type == "full":
            errors.append(
                f"Element {elem_id} ('{elem_class}'): "
                f"Completely outside layout boundaries"
            )
        elif boundary_type == "partial":
            errors.append(
                f"Element {elem_id} ('{elem_class}'): "
                f"Partially outside layout boundaries "
                f"(outside area: {outside_area} sq m)"
            )

    return total_outside_area


def _check_overlaps(elements: list, errors: list, violations: dict) -> float:
    """Check for overlaps between elements (except underground elements).

    Uses detect_overlaps from hard_constraints, then filters out
    allowed overlaps (underground elements).

    Args:
        elements: List of all elements.
        errors: List to append error messages to.
        violations: Dict to append structured violation info to.

    Returns:
        float: Total overlap area in square meters (excluding underground).
    """
    layout_json = {"elements": elements}
    result = detect_overlaps(layout_json)

    overlaps = result.get("overlaps", [])
    total_overlap_area = 0.0

    for overlap in overlaps:
        elem_a_id = overlap.get("element_a_id", "?")
        elem_a_class = overlap.get("element_a_class", "")
        elem_b_id = overlap.get("element_b_id", "?")
        elem_b_class = overlap.get("element_b_class", "")
        overlap_area = overlap.get("area", 0)

        # Skip if either element is underground (can overlap)
        if (
            elem_a_class in UNDERGROUND_CLASSES
            or elem_b_class in UNDERGROUND_CLASSES
        ):
            continue

        # Accumulate total overlap area
        total_overlap_area += overlap_area

        # Add structured violation for both elements
        for elem_id, elem_class, other_id, other_class in [
            (elem_a_id, elem_a_class, elem_b_id, elem_b_class),
            (elem_b_id, elem_b_class, elem_a_id, elem_a_class),
        ]:
            if str(elem_id) not in violations:
                violations[str(elem_id)] = []

            violations[str(elem_id)].append(
                {
                    "type": "overlap",
                    "class": elem_class,
                    "overlaps_with_id": other_id,
                    "overlaps_with_class": other_class,
                    "overlap_area": round(overlap_area, 2),
                }
            )

        # Keep text errors for backward compatibility
        errors.append(
            f"Overlap detected between Element {elem_a_id} "
            f"('{elem_a_class}') and Element {elem_b_id} "
            f"('{elem_b_class}'): overlap area = {overlap_area} sq m"
        )

    return total_overlap_area


def validate_layout(layout_json: str, constraints_dict: dict) -> dict:
    """Validates the generated layout against input constraints.

    Performs comprehensive validation including:
    - JSON schema and structure validation
    - Element count validation (all constraint elements present)
    - Fixed elements validation (Layout/Entrance/Exit match exactly)
    - Boundary validation (all elements within layout)
    - Overlap validation (no overlaps except underground elements)

    Args:
        layout_json (str): The generated layout as a JSON string with
            'elements' key.
        constraints_dict (dict): The input constraints containing expected
            elements and their dimensions.

    Returns:
        dict: A dictionary with three keys:
            - 'valid' (bool): True if layout passes all validations,
                False otherwise.
            - 'feedback' (str): JSON string with structured violations if
                invalid, or success message if valid.
            - 'score' (float): Weighted score from 0 to 1 based on violations.
    """
    errors: list[str] = []
    # Structured violations by element ID
    violations: dict[str, list[dict]] = {}

    # Parse JSON string
    try:
        layout = json.loads(layout_json)
    except json.JSONDecodeError as e:
        return {
            "valid": False,
            "feedback": f"Error: Invalid JSON format - {str(e)}",
            "score": 0.0,
        }

    # Check top-level structure
    if "elements" not in layout:
        return {
            "valid": False,
            "feedback": "Error: Layout must contain 'elements' key",
            "score": 0.0,
        }

    elements = layout["elements"]

    if not isinstance(elements, list):
        return {
            "valid": False,
            "feedback": "Error: 'elements' must be a list",
            "score": 0.0,
        }

    if len(elements) == 0:
        return {
            "valid": False,
            "feedback": "Error: Layout must contain at least one element",
            "score": 0.0,
        }

    input_elements = constraints_dict["elements"]
    layout_class = INPUT_CLASSES[0]

    # Validate output elements and extract data in a single pass
    all_valid_classes = INPUT_CLASSES + BOX_GEOM_CLASSES
    element_ids: set[int] = set()
    output_class_counts: dict[str, int] = {}
    # Fixed elements (Layout, Entrance, Exit)
    output_fixed: dict[str, list[list]] = {}
    layout_count = 0

    for idx, element in enumerate(elements):
        # Check element structure
        if not isinstance(element, dict):
            errors.append(f"Element {idx}: Must be a dictionary")
            continue

        # Check required fields
        if "id" not in element:
            errors.append(f"Element {idx}: Missing 'id' field")
        else:
            # Check for duplicate IDs
            elem_id = element["id"]
            if elem_id in element_ids:
                errors.append(f"Element {idx}: Duplicate id '{elem_id}'")
            element_ids.add(elem_id)

            # Check ID is integer
            if not isinstance(elem_id, int):
                errors.append(
                    f"Element {idx}: 'id' must be an integer, "
                    f"got {type(elem_id).__name__}"
                )

        if "class" not in element:
            errors.append(f"Element {idx}: Missing 'class' field")
        else:
            elem_class = element["class"]
            # Count elements by class
            output_class_counts[elem_class] = (
                output_class_counts.get(elem_class, 0) + 1
            )

            # Check class is valid
            if elem_class not in all_valid_classes:
                errors.append(
                    f"Element {idx}: Invalid class '{elem_class}'. "
                    f"Must be one of: {', '.join(all_valid_classes)}"
                )

            # Track Layout elements
            if elem_class == layout_class:
                layout_count += 1

        if "polygon" not in element:
            errors.append(f"Element {idx}: Missing 'polygon' field")
        else:
            polygon = element["polygon"]
            # Check polygon structure
            if not isinstance(polygon, list):
                errors.append(f"Element {idx}: 'polygon' must be a list")
            elif len(polygon) < 3:
                errors.append(
                    f"Element {idx}: 'polygon' must have at least 3 points"
                )
            else:
                # Check each point
                for point_idx, point in enumerate(polygon):
                    if not isinstance(point, list):
                        errors.append(
                            f"Element {idx}, point {point_idx}: "
                            f"Must be a list"
                        )
                    elif len(point) != 2:
                        errors.append(
                            f"Element {idx}, point {point_idx}: "
                            f"Must have exactly 2 coordinates [x, y]"
                        )
                    else:
                        # Check coordinates are numbers
                        for coord_idx, coord in enumerate(point):
                            if not isinstance(coord, (int, float)):
                                errors.append(
                                    f"Element {idx}, point {point_idx}, "
                                    f"coordinate {coord_idx}: "
                                    f"Must be a number"
                                )

            # Extract fixed elements (Layout, Entrance, Exit)
            elem_class = element.get("class", "")
            if elem_class in INPUT_CLASSES:
                # Only check for multiple Layout elements
                if elem_class == layout_class:
                    if elem_class in output_fixed:
                        errors.append(
                            f"Multiple '{elem_class}' elements found "
                            f"(should be only one)"
                        )
                    else:
                        output_fixed[elem_class] = [polygon]
                else:
                    # Allow multiple Entrance/Exit elements
                    if elem_class not in output_fixed:
                        output_fixed[elem_class] = []
                    output_fixed[elem_class].append(polygon)

    # Check for required Layout element
    if layout_count == 0:
        errors.append(f"Missing required '{layout_class}' element")
    elif layout_count > 1:
        errors.append(
            f"Multiple '{layout_class}' elements found (should be only one)"
        )

    # Process input elements: extract fixed elements and count classes
    input_fixed: dict[str, list[list]] = {}  # Fixed elements from input
    input_class_counts: dict[str, int] = {}

    for inp_elem in input_elements:
        elem_class = inp_elem.get("class", "")
        if not elem_class:
            continue

        # Count all input elements by class
        input_class_counts[elem_class] = (
            input_class_counts.get(elem_class, 0) + 1
        )

        # Extract fixed elements (Layout, Entrance, Exit)
        if elem_class in INPUT_CLASSES and "polygon" in inp_elem:
            if elem_class not in input_fixed:
                input_fixed[elem_class] = []
            input_fixed[elem_class].append(inp_elem["polygon"])

    # Check that all input fixed elements are present in output
    for fixed_class in input_fixed:
        if fixed_class not in output_fixed:
            errors.append(
                f"Missing required '{fixed_class}' element from input"
            )
        else:
            # Check polygon lists match exactly (same polygons, same count)
            input_polys = sorted([str(p) for p in input_fixed[fixed_class]])
            output_polys = sorted([str(p) for p in output_fixed[fixed_class]])

            if input_polys != output_polys:
                errors.append(
                    f"'{fixed_class}' polygon(s) do not match input. "
                    f"Layout, Entrance, and Exit polygons must be "
                    f"copied exactly from input constraints. "
                    f"Expected {len(input_fixed[fixed_class])} element(s), "
                    f"found {len(output_fixed[fixed_class])}."
                )

    # Validate element counts match between input and output
    for elem_class, expected_count in input_class_counts.items():
        actual_count = output_class_counts.get(elem_class, 0)
        if actual_count != expected_count:
            errors.append(
                f"Class '{elem_class}': Expected {expected_count} "
                f"element(s), but found {actual_count} in output"
            )

    # Check no extra classes in output
    for elem_class in output_class_counts:
        if elem_class not in input_class_counts:
            errors.append(
                f"Class '{elem_class}': Found in output but not in input. "
                f"Output should only contain elements specified in input."
            )

    # Track if critical errors exist (before boundary/overlap checks)
    critical_errors_exist = len(errors) > 0

    # Check boundaries and overlaps (get total violation areas)
    total_outside_area = _check_boundaries(elements, errors, violations)
    total_overlap_area = _check_overlaps(elements, errors, violations)

    # Calculate score
    if critical_errors_exist:
        score = 0.0
    else:
        # Score based on total violation area: 1/(1 + total_violations)
        total_violation_area = total_outside_area + total_overlap_area
        score = 1.0 / (1.0 + total_violation_area)

    if errors:
        # Build structured feedback JSON
        feedback_dict = {
            "status": "invalid",
            "violations": violations,
            "summary": {
                "total_errors": len(errors),
                "elements_with_violations": len(violations),
                "total_outside_area": round(total_outside_area, 2),
                "total_overlap_area": round(total_overlap_area, 2),
            },
        }

        return {
            "valid": False,
            "feedback": json_dumps(round_floats(feedback_dict)),
            "score": score,
        }
    else:
        return {
            "valid": True,
            "feedback": json_dumps({"status": "valid"}),
            "score": 1.0,
        }


def apply_layout_modifications(
    bbox_layout: dict, modifications: dict
) -> dict:
    """Applies position and rotation modifications to bbox layout.

    Takes a bbox layout and a modifications dictionary, and applies the
    changes while keeping the bbox format.

    Args:
        bbox_layout (dict): Layout in bbox format.
        modifications (dict): Dictionary mapping element IDs to modifications:
            {<element_id>: {"x": <new_x>, "y": <new_y>, "theta": <new_theta>}}

    Returns:
        dict: Updated layout in bbox format.
    """
    elements = bbox_layout.get("elements", [])
    updated_elements = []

    for element in elements:
        elem_id = element.get("id")
        elem_class = element.get("class", "")

        # Keep fixed elements unchanged (already in polygon format)
        if elem_class in INPUT_CLASSES:
            updated_elements.append(element.copy())
            continue

        # For bbox elements, apply modifications if present
        if str(elem_id) in modifications:
            mod = modifications[str(elem_id)]
            x = mod.get("x", element.get("x"))
            y = mod.get("y", element.get("y"))
            theta = mod.get("theta", element.get("theta", 0))
        else:
            x = element.get("x")
            y = element.get("y")
            theta = element.get("theta", 0)

        width = element.get("width")
        height = element.get("height")

        # Keep as bbox format
        updated_elements.append(
            {
                "id": elem_id,
                "class": elem_class,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "theta": theta,
            }
        )

    return {"elements": updated_elements}


def validate_bbox_layout(bbox_layout: dict, constraints_dict: dict) -> dict:
    """Validates a bbox layout by converting to polygon format first.

    Args:
        bbox_layout (dict): Layout in bbox format.
        constraints_dict (dict): Input constraints.

    Returns:
        dict: Validation result with 'valid', 'feedback', and 'score'.
    """
    # Convert bbox layout to polygon format
    polygon_layout = convert_to_polygons_layout(bbox_layout, INPUT_CLASSES)

    # Use the existing validate_layout function
    layout_json = json_dumps(round_floats(polygon_layout))
    return validate_layout(layout_json, constraints_dict)
