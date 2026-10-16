# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Flow (vehicle-routing) verification agent.

FlowEvaluator checks that every predefined routing scenario (e.g. car
entrance -> service area -> exit) has a feasible path through the layout.
Score is the fraction of feasible routes; compliance requires zero
infeasible routes.
"""

from ..models import WorkerResult
from .constants import (
    ALL_SCENARIOS,
    AVERAGE_VEHICLE_SIZES,
    NO_BACKTRACKING_CLASSES,
    UNDERGROUND_CLASSES,
)
from .tools import find_routes


class FlowEvaluator:
    @staticmethod
    def _normalize_waypoint_label(label: str) -> str:
        """Convert internal service area IDs back to original element IDs.

        Car service areas are split into two graph nodes with IDs of the form
        `original_id * 100 + service_position_index` (e.g. element 3 → nodes
        300 and 301).  This helper converts those back so route strings read
        `Car Service Area_3` instead of `Car Service Area_300`.
        """
        last_underscore = label.rfind("_")
        if last_underscore == -1:
            return label
        class_name = label[:last_underscore]
        try:
            internal_id = int(label[last_underscore + 1 :])
        except ValueError:
            return label
        if class_name == "Car Service Area" and internal_id >= 100:
            internal_id = internal_id // 100
        return f"{class_name}_{internal_id}"

    def evaluate(self, layout_data: dict, *args, **kwargs) -> WorkerResult:
        """
        Evaluates the layout data for flow compliance.

        Args:
            layout_data (dict): The layout data dictionary to evaluate.

        Returns:
            WorkerResult: The evaluation result containing 'compliant',
                'score', and 'feedback'.
        """
        scenarios = self._build_scenarios(layout_data["elements"])
        report = self._build_flow_report(layout_data, scenarios)

        result = self._format_result(report)
        result.raw_data = {"invalid_routes": report["invalid_routes"]}
        return result

    def _format_result(self, report: dict) -> WorkerResult:
        """Deterministically derive compliance, score and feedback
        from flow report.

        Score = number of feasible routes / total routes evaluated.
        Compliance = True only when there are no invalid routes.

        Args:
            report (dict): The flow report containing 'valid_routes' and
                'invalid_routes' lists.
        """
        invalid_routes = report.get("invalid_routes", [])
        valid_routes = report.get("valid_routes", [])

        total = len(valid_routes) + len(invalid_routes)
        compliant = len(invalid_routes) == 0

        if total == 0:
            return WorkerResult(
                compliant=True,
                score=1.0,
                feedback="COMPLIANT: No routes to evaluate for this layout.",
            )

        score = round(len(valid_routes) / total, 4)

        if compliant:
            feedback = self._format_compliant(valid_routes, score)
        else:
            feedback = self._format_violations(
                invalid_routes, valid_routes, score
            )

        return WorkerResult(
            compliant=compliant, score=score, feedback=feedback
        )

    @staticmethod
    def _format_compliant(valid_routes: list, score: float) -> str:
        """Build a compact per-vehicle-type summary for compliant layouts.

        Args:
            valid_routes (list): List of valid route dictionaries with keys
                like 'vehicle_type', 'length_meters', 'number_of_turns', and
                'number_of_sharp_turns'.
            score (float): The computed quality score for the layout.
        """
        from collections import defaultdict

        by_vehicle: dict = defaultdict(list)
        for r in valid_routes:
            by_vehicle[r["vehicle_type"]].append(r)

        total = sum(len(v) for v in by_vehicle.values())
        lines = [
            f"COMPLIANT: All {total} routes reachable. "
            f"Quality score={score:.4f}."
        ]

        for vehicle_type, routes in sorted(by_vehicle.items()):
            lengths = [r["length_meters"] for r in routes]
            turns = [r["number_of_turns"] for r in routes]
            sharp = [r["number_of_sharp_turns"] for r in routes]
            avg_len = sum(lengths) / len(lengths)
            max_len = max(lengths)
            lines.append(
                f"\n{vehicle_type} ({len(routes)} routes):\n"
                f"  avg_length={avg_len:.1f}m  max_length={max_len:.1f}m  "
                f"total_turns={sum(turns)}  total_sharp_turns={sum(sharp)}"
            )

        return "\n".join(lines)

    @staticmethod
    def _format_violations(
        invalid_routes: list,
        valid_routes: list | None = None,
        score: float = 0.0,
        head: int = 3,
    ) -> str:
        """Deduplicate violations by unreachable element and group
        affected routes.

        Args:
            invalid_routes (list): List of invalid route dictionaries with
                keys like 'vehicle_type', 'scenario', and 'reason'.
            valid_routes (list | None): List of valid routes (for the summary
                fraction line).
            score (float): Fraction of feasible routes (valid / total).
            head (int): Number of affected routes to show per violation before
                collapsing into a summary line.
        """
        from collections import defaultdict

        valid_count = len(valid_routes) if valid_routes else 0
        total = len(invalid_routes) + valid_count

        groups: dict = defaultdict(list)
        for r in invalid_routes:
            groups[r["reason"]].append(r["scenario"])

        lines = [
            f"FLOW VIOLATIONS: {len(invalid_routes)}/{total} route(s) "
            f"blocked (score={score:.4f}).\n"
        ]
        group_items = list(groups.items())
        shown_groups = group_items[:10]
        extra_groups = len(group_items) - len(shown_groups)
        for reason, scenarios in shown_groups:
            lines.append(f"- {reason}")
            n = len(scenarios)
            if n == 1:
                lines.append(f"  Affected route: {scenarios[0]}")
            else:
                lines.append(f"  Affected routes ({n}):")
                for s in scenarios[:head]:
                    lines.append(f"    · {s}")
                if n > head:
                    lines.append(f"    · ... and {n - head} more")
        if extra_groups:
            lines.append(f"... and {extra_groups} more element(s) omitted.")

        return "\n".join(lines)

    def _build_flow_report(self, layout_data: dict, scenarios: dict) -> dict:
        """Builds a detailed flow report with valid and invalid routes.

        Args:
            layout_data (dict): The layout data dictionary.
            scenarios (dict): The flow scenarios to evaluate.

        Returns:
            dict: Copy of layout with 'valid_routes' and 'invalid_routes'
                keys.
        """
        import copy

        report = copy.deepcopy(layout_data)

        valid_routes_report = []
        invalid_routes_report = []

        for vehicle_type, route_seqs in scenarios.items():
            _, valid_routes, invalid_routes = find_routes(
                layout=layout_data,
                routes=route_seqs,
                average_vehicle_size=AVERAGE_VEHICLE_SIZES[vehicle_type],
                ignored_classes=UNDERGROUND_CLASSES,
                no_backtracking=NO_BACKTRACKING_CLASSES,
            )

            for route_data in valid_routes:
                route = route_data.get("route", [])
                paths = route_data.get("paths", [])

                for path_entry in paths:
                    length = path_entry.get("length", 0)
                    num_turns = path_entry.get("num_turns", 0)
                    num_sharp_turns = path_entry.get("num_sharp_turns", 0)
                    coordinates = path_entry.get("coordinates", [])
                    waypoints = path_entry.get("waypoints", {})

                    scenario = [
                        self._normalize_waypoint_label(wp.removeprefix("wp_"))
                        for wp in waypoints.values()
                    ]

                    valid_routes_report.append(
                        {
                            "vehicle_type": vehicle_type,
                            "scenario": " -> ".join(scenario),
                            "path_coordinates": coordinates,
                            "length_meters": round(length, 2),
                            "number_of_turns": num_turns,
                            "number_of_sharp_turns": num_sharp_turns,
                        }
                    )

            for invalid_entry in invalid_routes:
                route = invalid_entry.get("route", [])
                reason = invalid_entry.get("reason", "Unknown reason")

                invalid_routes_report.append(
                    {
                        "vehicle_type": vehicle_type,
                        "scenario": " -> ".join(route),
                        "reason": reason,
                    }
                )

        report["valid_routes"] = valid_routes_report
        report["invalid_routes"] = invalid_routes_report

        return report

    def _build_scenarios(self, layout: dict) -> dict:
        allowed = set([x["class"] for x in layout])

        filtered_scenarios = {}

        for vehicle_type, possible_scenarios in ALL_SCENARIOS.items():
            filtered_scenarios[vehicle_type] = [
                seq
                for seq in possible_scenarios
                if all(x in allowed for x in seq)
            ]

        return filtered_scenarios
