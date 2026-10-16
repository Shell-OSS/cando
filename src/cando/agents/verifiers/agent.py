# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Verifier agent coordinating multiple worker verifiers."""

import concurrent.futures
from typing import Any

from .assembly.agent import AssemblyEvaluator
from .flow.agent import FlowEvaluator
from .hard_constraints.agent import HardConstraintsEvaluator
from .models import Feedback, VerifierResult, WorkerResult
from .orientation.agent import OrientationEvaluator
from .proximity.agent import ProximityEvaluator

ASSEMBLY_SOFT_WEIGHT = 0.3
ORIENTATION_SOFT_WEIGHT = 0.1
PROXIMITY_SOFT_WEIGHT = 0.6


class Verifier:
    def __init__(self):
        self._init_workers()

    def _init_workers(self):
        self.workers_registry: dict[str, Any] = {
            "hard_constraints": HardConstraintsEvaluator(),
            "assembly": AssemblyEvaluator(),
            "orientation": OrientationEvaluator(),
            "proximity": ProximityEvaluator(),
            "flow": FlowEvaluator(),
        }

    def evaluate_single(
        self,
        layout_data: dict,
        constraints: dict,
        weights: dict | None = None,
    ) -> VerifierResult:
        """
        Evaluates a single layout by supervising and aggregating the results
        from multiple worker verifiers.

        This method coordinates several specialized verifiers
        (hard_constraints, assembly, proximity, orientation, flow), collects
        their individual results in parallel, and aggregates the feedback into
        a single structured response.

        Args:
            layout_data (dict): The layout data dictionary to evaluate.
            constraints (dict): The constraints dictionary to validate
                against.
            weights (dict | None): Optional dictionary mapping worker names to
                their weights. Missing workers default to weight 1.0.

        Returns:
            VerifierResult: The aggregated evaluation result containing
                overall compliance, score, and feedback based on all worker
                verifiers.
        """
        # Run assembly first to get groups
        assembly_worker = self.workers_registry["assembly"]
        assembly_result: WorkerResult = assembly_worker.evaluate(
            layout_data, constraints
        )

        # Get groups from assembly evaluator
        groups = assembly_result.raw_data.get("groups", {})

        # Collect raw computational data from each worker
        raw_data = {"assembly": assembly_result.raw_data}

        # Validate remaining workers in parallel
        runs = {}
        with concurrent.futures.ThreadPoolExecutor() as executor:
            overall_results = {
                "assembly": assembly_result.model_dump(mode="json"),
            }

            for worker_name, worker in self.workers_registry.items():
                if worker_name == "assembly":
                    continue

                runs[worker_name] = executor.submit(
                    worker.evaluate,
                    layout_data,
                    constraints=constraints,
                    groups=groups,
                )

            for worker_name, worker_run in runs.items():
                worker_result: WorkerResult = worker_run.result()
                raw_data[worker_name] = worker_result.raw_data
                overall_results[worker_name] = worker_result.model_dump(
                    mode="json"
                )

        result = self._aggregate_results(overall_results)
        result.raw_data = raw_data
        return result

    def _aggregate_results(
        self,
        overall_results: dict,
    ) -> VerifierResult:
        """Deterministically aggregate worker results into a VerifierResult.

        Score formula:
            valid = hard_constraints.compliant AND flow.compliant
            soft_score = (
                ASSEMBLY_SOFT_WEIGHT * assembly['score']
                + ORIENTATION_SOFT_WEIGHT * orientation['score']
                + PROXIMITY_SOFT_WEIGHT * proximity['score']
            )
            final_score = valid * 0.5 + 0.5 * soft_score

        compliant is True only when ALL workers are compliant.
        valid is True when hard_constraints AND flow are both compliant.
        """
        hc = overall_results["hard_constraints"]
        flow = overall_results["flow"]
        assembly = overall_results["assembly"]
        orientation = overall_results["orientation"]
        proximity = overall_results["proximity"]

        valid = hc["compliant"] and flow["compliant"]
        compliant = all(
            v.get("compliant", False) for v in overall_results.values()
        )

        soft_score = (
            ASSEMBLY_SOFT_WEIGHT * assembly["score"]
            + ORIENTATION_SOFT_WEIGHT * orientation["score"]
            + PROXIMITY_SOFT_WEIGHT * proximity["score"]
        )
        final_score = (1.0 if valid else 0.0) * 0.5 + 0.5 * soft_score

        if compliant:
            final_score = 1.0
        else:
            final_score = max(0.0, min(0.99, final_score))

        def _worker_result(name: str) -> WorkerResult:
            d = overall_results[name]
            return WorkerResult(
                compliant=d["compliant"],
                score=d["score"],
                feedback=d["feedback"],
            )

        feedback = Feedback(
            hard_constraints=_worker_result("hard_constraints"),
            assembly=_worker_result("assembly"),
            orientation=_worker_result("orientation"),
            proximity=_worker_result("proximity"),
            flow=_worker_result("flow"),
        )

        return VerifierResult(
            valid=valid,
            compliant=compliant,
            score=final_score,
            feedback=feedback,
        )

    def evaluate(
        self,
        layouts: list[dict],
        constraints: dict,
        weights: dict | None = None,
    ) -> tuple[tuple[int, VerifierResult], list[tuple[int, VerifierResult]]]:
        """
        Evaluates multiple layouts in parallel and returns the best result
        along with all results.

        This method evaluates each layout independently using the
        evaluate_single method, runs all evaluations in parallel for
        efficiency, and identifies the best-scoring layout.

        Args:
            layouts (list[dict]): List of layout data dictionaries to
                evaluate.
            constraints (dict): The constraints dictionary to validate
                against.
            weights (dict | None): Unused; kept for backward compatibility.

        Returns:
            tuple: A tuple containing:
                - (best_index, best_result): The index (0-based) in the input
                  list and the VerifierResult of the best-scoring layout.
                - all_results: List of (index, result) tuples for all
                  evaluated layouts, ordered by input index.
        """
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_index = {
                executor.submit(
                    self.evaluate_single, layout, constraints, weights
                ): idx
                for idx, layout in enumerate(layouts)
            }

            all_results = []
            for future in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[future]
                result: VerifierResult = future.result()
                all_results.append((idx, result))

        # Sort by index to maintain input order
        all_results.sort(key=lambda x: x[0])

        # Find best result by score
        best_idx, best_result = max(all_results, key=lambda x: x[1].score)

        return (best_idx, best_result), all_results
