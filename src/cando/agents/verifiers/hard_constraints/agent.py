# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Hard constraints verification agent."""

import concurrent.futures
import logging
from collections import Counter

from shapely.geometry import Polygon as _Polygon

from ..models import WorkerResult
from .tools import (
    detect_out_of_boundaries,
    detect_overlaps,
    validate_constraints,
    validate_dimensions,
)

logger = logging.getLogger(__name__)

# Max individual violations rendered per category in the feedback string;
# caps [DIMENSIONS]/[OVERLAPS]/[BOUNDARIES] independently of the outer
# category cap applied to violation_parts below.
_MAX_VIOLATIONS_IN_FEEDBACK = 10

# Geometry-violation penalty: penalty_i = BASE + AREA_SCALE * area_ratio,
# capping each violation's contribution to (BASE, BASE + AREA_SCALE].
_BASE_GEOMETRY_PENALTY = 0.1
_AREA_SCALED_GEOMETRY_PENALTY = 0.1


class HardConstraintsEvaluator:
    def evaluate(
        self, layout_data: dict, constraints: dict, *args, **kwargs
    ) -> WorkerResult:
        """
        Evaluates the layout data for hard constraints compliance.

        Args:
            layout_data (dict): The layout data dictionary to evaluate.
            constraints (dict): The constraints dictionary to validate
                against.

        Returns:
            WorkerResult: The evaluation result containing 'compliant',
                'score', and 'feedback'.
        """
        # Validate in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_counts = executor.submit(
                validate_constraints, constraints, layout_data
            )
            future_overlaps = executor.submit(detect_overlaps, layout_data)
            future_boundaries = executor.submit(
                detect_out_of_boundaries, layout_data
            )
            future_dimensions = executor.submit(
                validate_dimensions, constraints, layout_data
            )

            validated_layout = future_counts.result()
            overlaps_result = future_overlaps.result()
            boundaries_result = future_boundaries.result()
            dimensions_result = future_dimensions.result()

        # Merge the results
        validated_layout["overlaps"] = overlaps_result.get("overlaps", [])
        validated_layout["out_of_boundaries"] = boundaries_result.get(
            "out_of_boundaries", []
        )
        validated_layout["dimension_violations"] = dimensions_result.get(
            "dimension_violations", []
        )

        result = self._format_result(validated_layout)

        result.raw_data = {
            "overlaps": overlaps_result.get("overlaps", []),
            "out_of_boundaries": boundaries_result.get(
                "out_of_boundaries", []
            ),
            "dimension_violations": dimensions_result.get(
                "dimension_violations", []
            ),
            "count_validation": validated_layout.get("count_validation", {}),
        }

        return result

    def _format_result(self, validated_layout: dict) -> WorkerResult:
        """Deterministically derive compliance, score and feedback
        from tool outputs.

        Score formula (when no missing elements or dimension violations):
            - 0 violations → score = 1.0
            - Each overlap/boundary violation contributes:
                penalty_i = 0.1 + 0.1 * (violating_area / element_area)
              Range per violation: (0.1, 0.2].
              score = max(0, min(0.99, 1.0 - sum(penalties)))

        If any element is missing or any dimension violation is present,
        score = 0.0 regardless of other violations.
        Compliance is True only when there are no violations of any kind.

        Args:
            validated_layout (dict): Layout data enriched with counts
                validation, overlaps, out-of-boundaries, and dimension
                analysis.
        """
        count = validated_layout.get("count_validation", {})
        overlaps = validated_layout.get("overlaps", [])
        boundaries = validated_layout.get("out_of_boundaries", [])
        dim_violations = validated_layout.get("dimension_violations", [])

        violation_parts = []
        hard_zero = False

        # Count violations
        if not count.get("matches_constraints", True):
            missing = count.get("missing_count", 0)
            missing_elems = count.get("missing_elements", [])
            class_counts = Counter(e["class"] for e in missing_elems)
            missing_desc = ", ".join(
                f"{cls} x{cnt}" if cnt > 1 else cls
                for cls, cnt in class_counts.items()
            )
            violation_parts.append(
                f"[COUNTS]: {missing} missing element(s): {missing_desc}"
            )
            hard_zero = True

        # Dimension violations — treated as hard zero like missing elements
        if dim_violations:
            _shown_dims = dim_violations[:_MAX_VIOLATIONS_IN_FEEDBACK]
            parts = [
                f"element {d['element_id']} ({d['element_class']}) "
                f"generated={d['gen_w']:.3f}x{d['gen_h']:.3f} "
                f"expected={d['con_w']:.3f}x{d['con_h']:.3f}"
                for d in _shown_dims
            ]
            _omitted = len(dim_violations) - len(_shown_dims)
            if _omitted:
                parts.append(
                    f"... and {_omitted} more dimension violation(s) omitted"
                )
            violation_parts.append(f"[DIMENSIONS]: {' | '.join(parts)}")
            hard_zero = True

        # Overlap violations
        if overlaps:
            _shown_overlaps = overlaps[:_MAX_VIOLATIONS_IN_FEEDBACK]
            parts = [
                f"element {o['element_a_id']} ({o['element_a_class']}) ↔ "
                f"element {o['element_b_id']} ({o['element_b_class']}) "
                f"type={o['type']} area={o['area']:.4f} "
                f"x_correction={o['x_correction']:.2f} "
                f"y_correction={o['y_correction']:.2f}"
                for o in _shown_overlaps
            ]
            _omitted = len(overlaps) - len(_shown_overlaps)
            if _omitted:
                parts.append(f"... and {_omitted} more overlap(s) omitted")
            violation_parts.append(f"[OVERLAPS]: {' | '.join(parts)}")

        # Boundary violations
        if boundaries:
            _shown_boundaries = boundaries[:_MAX_VIOLATIONS_IN_FEEDBACK]
            parts = [
                f"element {b['element_id']} ({b['element_class']}) "
                f"{b['type']} outside layout "
                f"(x_correction={b['x_correction']:.2f}, "
                f"y_correction={b['y_correction']:.2f}, "
                f"outside_area={b['outside_area']:.4f})"
                for b in _shown_boundaries
            ]
            _omitted = len(boundaries) - len(_shown_boundaries)
            if _omitted:
                parts.append(
                    f"... and {_omitted} more boundary violation(s) omitted"
                )
            violation_parts.append(f"[BOUNDARIES]: {' | '.join(parts)}")

        compliant = not violation_parts

        if compliant:
            score = 1.0
            feedback = "COMPLIANT: All hard constraints satisfied."
        elif hard_zero:
            score = 0.0
            _shown = violation_parts[:_MAX_VIOLATIONS_IN_FEEDBACK]
            _tail = len(violation_parts) - len(_shown)
            feedback = "VIOLATIONS:\n" + "\n".join(f"- {v}" for v in _shown)
            if _tail:
                feedback += f"\n... and {_tail} more violation(s) omitted."
        else:
            # Geometry violations only: area-normalised penalty.
            elem_areas: dict[int, float] = {}
            for elem in validated_layout.get("elements", []):
                try:
                    elem_areas[elem["id"]] = _Polygon(elem["polygon"]).area
                except Exception as exc:
                    logger.debug(
                        "Falling back to unit area for element %s: "
                        "invalid polygon (%s)",
                        elem.get("id"),
                        exc,
                    )
                    elem_areas[elem["id"]] = 1.0

            # Penalty is computed over ALL violations (not just the ones
            # shown in feedback above) so capping display doesn't skew score.
            penalties = []
            for o in overlaps:
                area_a = elem_areas.get(o["element_a_id"], 1.0)
                area_b = elem_areas.get(o["element_b_id"], 1.0)
                denom = min(area_a, area_b) or 1.0
                penalties.append(
                    _BASE_GEOMETRY_PENALTY
                    + _AREA_SCALED_GEOMETRY_PENALTY
                    * o.get("area", 0.0)
                    / denom
                )
            for b in boundaries:
                denom = elem_areas.get(b["element_id"], 1.0) or 1.0
                penalties.append(
                    _BASE_GEOMETRY_PENALTY
                    + _AREA_SCALED_GEOMETRY_PENALTY
                    * b.get("outside_area", 0.0)
                    / denom
                )

            score = max(0.0, min(0.99, 1.0 - sum(penalties)))
            _shown = violation_parts[:_MAX_VIOLATIONS_IN_FEEDBACK]
            _tail = len(violation_parts) - len(_shown)
            feedback = "VIOLATIONS:\n" + "\n".join(f"- {v}" for v in _shown)
            if _tail:
                feedback += f"\n... and {_tail} more violation(s) omitted."

        return WorkerResult(
            compliant=compliant, score=score, feedback=feedback
        )
