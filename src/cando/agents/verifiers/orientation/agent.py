# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Agent for orientation verification.

Orientation verification agent.
"""

import json
from pathlib import Path

from ..models import WorkerResult
from .tools import analyze_orientation_patterns

# Angle-aware penalty formula (see _format_result docstring):
# penalty_per_violation = BASE_VIOLATION_PENALTY
#     + (angle_diff / MAX_ANGLE_DIFF_DEGREES) * ANGLE_SCALED_PENALTY
BASE_VIOLATION_PENALTY = 0.1
ANGLE_SCALED_PENALTY = 0.1
MAX_ANGLE_DIFF_DEGREES = 90.0


class OrientationEvaluator:
    def __init__(self):

        # Load rules from JSON file
        base_dir = Path(__file__).resolve().parent
        rules_path = base_dir / "rules.json"
        with open(rules_path) as rules_file:
            self.rules = json.load(rules_file)

    def evaluate(
        self, layout_data: dict, groups: dict | None = None, *args, **kwargs
    ) -> WorkerResult:
        """
        Evaluates the layout data for orientation compliance using
        rule-driven preprocessing.

        Args:
            layout_data (dict): The layout data dictionary to evaluate.
            groups (dict | None): Optional groups dictionary from assembly
                verifier.

        Returns:
            WorkerResult: The evaluation result containing 'compliant',
                'score', and 'feedback'.
        """
        elements = layout_data["elements"]

        if groups is None:
            groups = {}

        orientation_analysis = analyze_orientation_patterns(
            self.rules, elements, groups
        )

        result = self._format_result(orientation_analysis)
        result.raw_data = {"orientation_analysis": orientation_analysis}
        return result

    def _format_result(self, orientation_analysis: dict) -> WorkerResult:
        """Deterministically derive compliance, score and feedback
        from orientation analysis.

        All violations reduce the score and make the layout incompliant.
        Penalties are angle-aware:
            penalty_per_violation = 0.1 (fixed) + (angle_diff / 90) * 0.1

        Score = max(0, 1.0 - total_penalty), capped at 0.99 when
        non-compliant.
        Compliance is True only when there are no violations.

        Args:
            orientation_analysis (dict): The analysis results for orientation
                rules, organized by rule keys with details on compliant and
                non-compliant elements, groups, and patterns.
        """
        violations = []
        total_penalty = 0.0

        for _, data in orientation_analysis.items():
            analysis = data.get("analysis", {})
            rule = data.get("rule", {})
            rule_type = rule.get("type", "")

            if analysis.get("compliant", True):
                continue

            if rule_type == "within_group_consistency":
                group_analyses = analysis.get("group_analyses", {})
                for class_name, clusters in group_analyses.items():
                    for cluster in clusters:
                        if cluster.get("orientation_consistent", True):
                            continue
                        cluster_idx = cluster.get("cluster_index", "?")
                        raw_violations = cluster.get("violations", [])
                        viol_parts = [
                            self._describe_within_group_violation(v)
                            for v in raw_violations
                            if isinstance(v, dict)
                        ]
                        violations.append(
                            f"[WITHIN_GROUP]: {class_name} cluster "
                            f"{cluster_idx} has inconsistent orientations: "
                            + "; ".join(viol_parts)
                        )
                        # Angle-aware penalty: avg diff across violations
                        angle_diffs = [
                            v.get("angle_diff", 45.0)
                            for v in raw_violations
                            if isinstance(v, dict)
                        ]
                        avg_diff = (
                            sum(angle_diffs) / len(angle_diffs)
                            if angle_diffs
                            else 45.0
                        )
                        total_penalty += (
                            BASE_VIOLATION_PENALTY
                            + (avg_diff / MAX_ANGLE_DIFF_DEGREES)
                            * ANGLE_SCALED_PENALTY
                        )

            elif rule_type == "element_to_elements":
                element_results = analysis.get("element_results", {})
                source_class = analysis.get(
                    "source_class", rule.get("element", "")
                )
                for elem_key, elem_result in element_results.items():
                    if elem_result.get("compliant", True):
                        continue
                    raw_violations = elem_result.get("violations", [])
                    viol_parts = [
                        self._describe_element_to_element_violation(
                            v, source_class, elem_key
                        )
                        for v in raw_violations
                        if isinstance(v, dict)
                    ]
                    violations.append(
                        "[ORIENTATION]: " + "; ".join(viol_parts)
                    )
                    # Angle-aware penalty: avg angular diff per element
                    angle_diffs = []
                    for v in raw_violations:
                        if not isinstance(v, dict):
                            continue
                        sa = v.get("source_angle")
                        ta = v.get("target_angle")
                        if sa is not None and ta is not None:
                            raw_diff = abs(sa - ta) % 180
                            angle_diffs.append(min(raw_diff, 180 - raw_diff))
                    avg_diff = (
                        sum(angle_diffs) / len(angle_diffs)
                        if angle_diffs
                        else 45.0
                    )
                    total_penalty += (
                        BASE_VIOLATION_PENALTY
                        + (avg_diff / MAX_ANGLE_DIFF_DEGREES)
                        * ANGLE_SCALED_PENALTY
                    )

        compliant = not violations

        if not violations:
            score = 1.0
            feedback = "COMPLIANT: All orientation rules satisfied."
        else:
            score = max(0.0, min(0.99, 1.0 - total_penalty))
            shown = violations[:10]
            tail = len(violations) - len(shown)
            feedback = "VIOLATIONS:\n" + "\n".join(f"- {v}" for v in shown)
            if tail:
                feedback += f"\n... and {tail} more violation(s) omitted."

        return WorkerResult(
            compliant=compliant, score=score, feedback=feedback
        )

    @staticmethod
    def _describe_element_to_element_violation(
        v: dict,
        source_class: str,
        elem_key: str | int,
    ) -> str:
        """Return a human-readable description of an element-to-elements
        orientation violation.

        Args:
            v (dict): The violation details dictionary containing keys like
                'source_edge', 'target_edge', 'requirement', 'relationship',
                'target_elem_id', 'target_elem_class', 'source_angle', and
                'target_angle'.
            source_class (str): The class of the source element.
            elem_key (str | int): The ID of the source element.
        """
        source_edge = v.get("source_edge", "?").replace("_", " ")
        target_edge = v.get("target_edge", "?").replace("_", " ")
        requirement = v.get("requirement", "?")
        relationship = v.get("relationship", "?").replace("_", " ")
        target_id = v.get("target_elem_id", "?")
        target_class = v.get("target_elem_class", "?")
        source_angle = v.get("source_angle")
        target_angle = v.get("target_angle")

        angle_info = ""
        if source_angle is not None and target_angle is not None:
            raw_diff = abs(source_angle - target_angle) % 180
            angle_diff = min(raw_diff, 180 - raw_diff)
            angle_info = (
                f" (source={source_angle:.1f}°, "
                f"target={target_angle:.1f}°, diff={angle_diff:.1f}°)"
            )

        return (
            f"{source_class} element (id: {elem_key}): "
            f"{source_edge} must be {requirement} to "
            f"{target_class}'s (id: {target_id}) {target_edge}, "
            f"but found {relationship}{angle_info}"
        )

    @staticmethod
    def _describe_within_group_violation(v: dict) -> str:
        """Return a human-readable description of a within-group consistency
        violation.

        Args:
            v (dict): The violation details dictionary containing keys like
                'elem1_id', 'elem2_id', 'angle_diff', and 'requirement'.
        """
        id_a = v.get("elem1_id", "?")
        id_b = v.get("elem2_id", "?")
        angle_diff = v.get("angle_diff")
        requirement = v.get("requirement", "consistent orientation")

        diff_str = (
            f" (angle diff={angle_diff:.1f}°)"
            if angle_diff is not None
            else ""
        )
        return f"elements {id_a} and {id_b} are not {requirement}{diff_str}"
