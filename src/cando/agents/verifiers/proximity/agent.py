# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Agent for proximity verification."""

import json
from pathlib import Path

from ..models import WorkerResult
from .tools import evaluate_all_rules

# Score deducted per proximity-rule violation.
VIOLATION_PENALTY = 0.1


class ProximityEvaluator:
    """
    Evaluates layouts for proximity compliance.

    This agent validates:
    - Edge stacking rules (elements aligned along edges)
    - Adjacency rules (elements nearby each other)
    - Distance rules (minimum distances maintained)

    It handles complex scenarios:
    - Capacity-aware stacking (limited edge space)
    - Priority hierarchies (disabled parking > normal parking)
    - Group satisfaction (element near ANY target in group)
    - Alternative placements

    All stacking rules are capacity-aware by default.
    """

    def __init__(self):
        # Load rules from JSON file
        base_dir = Path(__file__).resolve().parent
        rules_path = base_dir / "rules.json"
        with open(rules_path) as rules_file:
            self.rules = json.load(rules_file)

    def evaluate(
        self, layout_data: dict, groups: dict | None = None, *args, **kwargs
    ) -> WorkerResult:
        """Evaluates layout for proximity compliance.

        Args:
            layout_data (dict): Layout data with 'elements' list.
            groups (dict | None): Optional assembly groups from assembly
                verifier. Groups are used to calculate bounding boxes and
                evaluate rules at the group level.

        Returns:
            WorkerResult: Evaluation result with compliant, score, feedback.
        """
        elements = layout_data.get("elements", [])
        groups = groups if groups else {}

        # Evaluate all rules and get results organized by category
        validation_results = evaluate_all_rules(
            elements=elements, groups=groups, rules=self.rules
        )

        return self._format_result(validation_results)

    def _format_result(self, proximity_analysis: dict) -> WorkerResult:
        """Deterministically derive compliance, score and feedback
        from proximity analysis.

        All violations reduce the score and
        make the layout incompliant. Fixed penalty of 0.1 per violation.
        Score = max(0, 1.0 - 0.1 * N), capped at 0.99 when non-compliant.
        Compliance is True only when there are no violations.

        Args:
            proximity_analysis (dict): The analysis results for proximity
                rules, organized by category with lists of violations and
                details.
        """
        violations = []
        score = 1.0

        for category, violation_list in proximity_analysis.items():
            for v in violation_list:
                rule_id = v.get("rule_id", "?")
                desc = v.get("description", "")
                reason = v.get("violation", "")
                details = v.get("details", {})

                source_id = details.get("source_id", "")
                source_class = details.get("source_class", "")
                target_classes = details.get("target_classes", [])
                distance = details.get("distance")

                elem_info = ""
                if source_id:
                    elem_info += f"element {source_id} ({source_class})"
                if target_classes:
                    elem_info += f" ↔ {', '.join(target_classes)}"
                if distance is not None:
                    elem_info += f" distance={distance:.2f}"

                viol_str = (
                    f"[{category.upper()}] R{rule_id}: "
                    f"{desc}. {elem_info}. {reason}"
                )
                violations.append(viol_str)
                score -= VIOLATION_PENALTY

        compliant = not violations

        if not violations:
            score = 1.0
            feedback = "COMPLIANT: All proximity rules satisfied."
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
