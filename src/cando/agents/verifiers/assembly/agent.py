# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Agent for assembly verification."""

import json
from pathlib import Path

from ..models import WorkerResult
from .tools import (
    analyze_cluster_patterns,
    cluster_elements_by_class,
    detect_fragmentation,
)

# Score deducted per fragmentation or formation-pattern violation.
VIOLATION_PENALTY = 0.1


class AssemblyEvaluator:
    def __init__(self):

        # Load rules from JSON file
        base_dir = Path(__file__).resolve().parent
        rules_path = base_dir / "rules.json"
        with open(rules_path) as rules_file:
            self.rules = json.load(rules_file)

        # Extract distance thresholds and average group sizes from rules
        self.distance_thresholds = {}
        self.average_group_sizes = {}
        self.default_threshold = 10.0

        for rule in self.rules:
            if rule.get("type") == "assembly_constraint":
                element_class = rule.get("element")
                conditions = rule.get("conditions", {})

                threshold = conditions.get(
                    "distance_threshold", self.default_threshold
                )
                avg_size = conditions.get("average_group_size")

                if element_class:
                    self.distance_thresholds[element_class] = threshold
                    if avg_size is not None:
                        self.average_group_sizes[element_class] = avg_size

    def evaluate(self, layout_data: dict, *args, **kwargs) -> WorkerResult:
        """
        Evaluates the layout data for assembly compliance.

        Args:
            layout_data (dict): The layout data dictionary to evaluate.

        Returns:
            WorkerResult: The evaluation result containing 'compliant',
                'score', and 'feedback'.
        """
        # Cluster elements by class using distance thresholds from rules
        groups = cluster_elements_by_class(
            elements=layout_data["elements"],
            distance_threshold=self.distance_thresholds,
            default_threshold=self.default_threshold,
        )

        # Compute fragmentation analysis for classes with average_group_size
        fragmentation_analysis = {}
        for class_name, avg_size in self.average_group_sizes.items():
            if class_name in groups:
                analysis = detect_fragmentation(
                    class_name=class_name,
                    clusters=groups[class_name],
                    average_group_size=avg_size,
                )
                fragmentation_analysis[class_name] = analysis

        # Preprocess pattern detection for all clusters
        pattern_analysis = analyze_cluster_patterns(groups)

        enriched_layout_data = {
            **layout_data,
            "groups": groups,
            "fragmentation_analysis": fragmentation_analysis,
            "pattern_analysis": pattern_analysis,
        }

        result = self._format_result(enriched_layout_data)
        result.raw_data = {"groups": groups}
        return result

    def _format_result(self, enriched_layout_data: dict) -> WorkerResult:
        """Deterministically derive compliance, score and feedback
        from tool outputs.

        Args:
            enriched_layout_data (dict): Layout data enriched with groups,
                fragmentation analysis, and pattern analysis.
        """
        groups = enriched_layout_data.get("groups", {})
        fragmentation_analysis = enriched_layout_data.get(
            "fragmentation_analysis", {}
        )
        pattern_analysis = enriched_layout_data.get("pattern_analysis", {})

        violations = []
        score = 1.0

        for rule in self.rules:
            if rule.get("type") != "assembly_constraint":
                continue

            class_name = rule.get("element")
            if class_name not in groups:
                continue

            conditions = rule.get("conditions", {})
            arrangement = conditions.get("arrangement", {})
            required_pattern = arrangement.get("pattern")
            rules_for_counts = arrangement.get("rules_for_counts", {})

            # Check fragmentation for this class
            frag = fragmentation_analysis.get(class_name, {})
            if frag.get("is_fragmented", False):
                violations.append(
                    f"[FRAGMENTATION]: {class_name}: "
                    f"{frag['total_elements']} elements in "
                    f"{frag['total_clusters']} clusters "
                    f"({frag['singleton_clusters']} singletons). "
                    f"Expected avg={frag['expected_average_size']}, "
                    f"actual={frag['average_cluster_size']:.1f}."
                )
                score -= VIOLATION_PENALTY

            # Check formation pattern per cluster
            class_clusters = groups.get(class_name, [])
            class_patterns = pattern_analysis.get(class_name, [])

            for cluster_analysis in class_patterns:
                cluster_idx = cluster_analysis.get("cluster_index", 0)
                cluster = next(
                    (
                        c
                        for c in class_clusters
                        if c.get("cluster_index") == cluster_idx
                    ),
                    None,
                )
                if cluster is None:
                    continue

                count = cluster.get("cluster_size", 0)
                element_ids = cluster.get("element_ids", [])

                # Determine required formation for this cluster
                if rules_for_counts:
                    required = rules_for_counts.get(
                        "odd" if count % 2 == 1 else "even", required_pattern
                    )
                else:
                    required = required_pattern

                violation = self._check_formation(
                    class_name,
                    element_ids,
                    required,
                    cluster_analysis.get("single_line", {}),
                    cluster_analysis.get("rectangular", {}),
                    cluster_analysis.get("irregular", {}),
                    count=count,
                )
                if violation:
                    violations.append(violation)
                    score -= VIOLATION_PENALTY

        compliant = not violations
        if compliant:
            score = 1.0
            feedback = "COMPLIANT: All assembly rules satisfied."
        else:
            score = max(0.0, min(0.99, score))
            shown = violations[:10]
            tail = len(violations) - len(shown)
            feedback = "VIOLATIONS:\n" + "\n".join(f"- {v}" for v in shown)
            if tail:
                feedback += f"\n... and {tail} more violation(s) omitted."

        return WorkerResult(
            compliant=compliant, score=score, feedback=feedback
        )

    @staticmethod
    def _check_formation(
        class_name: str,
        element_ids: list,
        required: str | None,
        single_line: dict,
        rectangular: dict,
        irregular: dict,
        count: int = 0,
    ) -> str | None:
        """Return a violation string if the cluster doesn't meet the
        required formation, else None.

        Args:
            class_name (str): The class of the elements in the cluster.
            element_ids (list): List of element IDs in the cluster.
            required (str | None): The required formation pattern, e.g.,
                'single_line', 'rectangular_blocks', etc.
            single_line (dict): Analysis results for single-line pattern.
            rectangular (dict): Analysis results for rectangular pattern.
            irregular (dict): Analysis results for irregular pattern.
            count (int): Number of elements in the cluster.
        """
        if not required:
            return None

        is_single = single_line.get("is_single_line", False)
        is_rect = rectangular.get("is_rectangular", False)

        if required == "single_line":
            if not is_single:
                detected = AssemblyEvaluator._describe_pattern(
                    single_line, rectangular, irregular
                )
                return (
                    f"[FORMATION]: {class_name} elements {element_ids} "
                    f"do not form the required single-line arrangement. "
                    f"Detected: {detected}"
                )
        elif required == "rectangular_blocks":
            # 2 elements can't form a rectangular grid; single line accepted
            if count == 2 and is_single:
                return None
            elif count == 2:
                detected = AssemblyEvaluator._describe_pattern(
                    single_line, rectangular, irregular
                )
                return (
                    f"[FORMATION]: {class_name} elements {element_ids} "
                    f"do not form the required single-line arrangement."
                )
            elif not is_rect:
                detected = AssemblyEvaluator._describe_pattern(
                    single_line, rectangular, irregular
                )
                return (
                    f"[FORMATION]: {class_name} elements {element_ids} "
                    f"do not form the required rectangular arrangement. "
                    f"Detected: {detected}"
                )
        elif required == "rectangular_or_single_line":
            if not is_single and not is_rect:
                detected = AssemblyEvaluator._describe_pattern(
                    single_line, rectangular, irregular
                )
                return (
                    f"[FORMATION]: {class_name} elements {element_ids} "
                    f"do not form the required single-line or rectangular "
                    f"arrangement. Detected: {detected}"
                )
        return None

    @staticmethod
    def _describe_pattern(
        single_line: dict,
        rectangular: dict,
        irregular: dict,
    ) -> str:
        """Return a human-readable description of the detected
        cluster pattern.

        Args:
            single_line (dict): Analysis results for single-line pattern.
            rectangular (dict): Analysis results for rectangular pattern.
            irregular (dict): Analysis results for irregular pattern.
        """
        if single_line.get("is_single_line"):
            return (
                f"single-line {single_line.get('pattern', '')} "
                f"orientation={single_line.get('orientation', '')}"
            )
        if rectangular.get("is_rectangular"):
            return f"rectangular {rectangular.get('pattern', '')}"
        rows = irregular.get("rows", "?")
        cols = irregular.get("cols", "?")
        desc = irregular.get("description", "")
        return f"irregular ~{rows}x{cols}. {desc}"
