# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Planner agent coordinating multiple worker planners."""

import concurrent.futures
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from cando.agents.verifiers.models import VerifierResult
from cando.config import ModelsConfig
from cando.model_factory import resolve_agent_model
from cando.utils.generic import json_dumps, round_floats

from .assembly.agent import AssemblyPlanner
from .flow.agent import FlowPlanner
from .hard_constraints.agent import HardConstraintsPlanner
from .models import PlannerResult, PlannerWorkerResult
from .orientation.agent import OrientationPlanner
from .prompt import SUPERVISOR_PLANNER_SYSTEM_PROMPT
from .proximity.agent import ProximityPlanner


class Planner:
    def __init__(
        self, model: str = ModelsConfig.planner, rules: dict | None = None
    ) -> None:
        # Load rules if provided
        self.rules = rules if rules else {}

        # Initialize the supervisor planner agent
        self.agent = create_agent(
            model=resolve_agent_model("planner_supervisor", model),
            system_prompt=SUPERVISOR_PLANNER_SYSTEM_PROMPT,
            response_format=ToolStrategy(PlannerResult),
        )
        self._init_workers()

    def _init_workers(self) -> None:
        self.workers_registry = {
            "hard_constraints": HardConstraintsPlanner(
                self.rules.get("hard_constraints", None)
            ),
            "assembly": AssemblyPlanner(self.rules.get("assembly", None)),
            "orientation": OrientationPlanner(
                self.rules.get("orientation", None)
            ),
            "proximity": ProximityPlanner(self.rules.get("proximity", None)),
            "flow": FlowPlanner(self.rules.get("flow", None)),
        }

    @staticmethod
    def _no_action_result() -> PlannerWorkerResult:
        return PlannerWorkerResult(
            actions=[],
        )

    @staticmethod
    def _to_planner_result(
        worker_results: dict[str, PlannerWorkerResult],
    ) -> PlannerResult:
        return PlannerResult(
            hard_constraints=worker_results["hard_constraints"],
            assembly=worker_results["assembly"],
            orientation=worker_results["orientation"],
            proximity=worker_results["proximity"],
            flow=worker_results["flow"],
        )

    def _run_worker(
        self,
        worker_name: str,
        worker,
        layout_data: dict,
        verification: VerifierResult,
    ) -> PlannerWorkerResult:
        return worker.plan(
            layout_data,
            getattr(verification.feedback, worker_name),
            verification.raw_data.get(worker_name, None),
        )

    def plan(
        self,
        layout_data: dict,
        verification: VerifierResult,
        layout_image: str | None = None,
        weights: dict | None = None,
        history_context: str | None = None,
        iteration: int | None = None,
    ) -> PlannerResult:
        """Supervises and aggregates the planning of layout refinements by
        multiple worker planners.

        Args:
            layout_data (dict): The layout data dictionary to plan on.
            verification (VerifierResult): The verification result to consider
                for planning.
            layout_image (str | None): Optional base64-encoded image of the
                layout for context.
            weights (dict | None): Optional weights for each worker planner.
            history_context (str | None): Optional formatted string
                containing the history of previous iterations (scores,
                planner suggestions, generator actions, and per-category
                score deltas).
            iteration (int | None): The current 0-based refinement iteration
                index. Used by the supervisor to apply iteration-aware anchor
                stability rules.

        Returns:
            PlannerResult: The aggregated planning result containing
                'actions'.
        """
        # Initialize weights for all workers (default to 1.0 if not provided)
        if weights is None:
            weights = {}
        worker_weights = {
            worker_name: weights.get(worker_name, 1.0)
            for worker_name in self.workers_registry.keys()
        }

        worker_results = {
            worker_name: self._no_action_result()
            for worker_name in self.workers_registry.keys()
        }
        active_workers = {
            worker_name: worker
            for worker_name, worker in self.workers_registry.items()
            if not getattr(verification.feedback, worker_name).compliant
        }

        if not active_workers:
            return self._to_planner_result(worker_results)

        if len(active_workers) == 1:
            worker_name, worker = next(iter(active_workers.items()))
            worker_results[worker_name] = self._run_worker(
                worker_name=worker_name,
                worker=worker,
                layout_data=layout_data,
                verification=verification,
            )
            return self._to_planner_result(worker_results)

        # Run planner workers in parallel
        runs = {}
        with concurrent.futures.ThreadPoolExecutor() as executor:
            for worker_name, worker in active_workers.items():
                runs[worker_name] = executor.submit(
                    self._run_worker,
                    worker_name,
                    worker,
                    layout_data,
                    verification,
                )

            for worker_name, worker_run in runs.items():
                worker_results[worker_name] = worker_run.result()

        overall_results = {
            worker_name: {
                **worker_result.model_dump(mode="json"),
                "weight": worker_weights[worker_name],
            }
            for worker_name, worker_result in worker_results.items()
        }

        # Build text body (always included)
        iteration_section = (
            f"# Current Iteration: {iteration}\n\n"
            if iteration is not None
            else ""
        )
        history_section = (
            f"# History Context:\n{history_context}\n\n"
            if history_context
            else ""
        )
        verification_json = json_dumps(
            round_floats(verification.model_dump(mode="json"))
        )
        layout_json = json_dumps(round_floats(layout_data))
        worker_plans_json = json_dumps(round_floats(overall_results))
        text_body = (
            f"{iteration_section}"
            f"{history_section}"
            f"# Verification:\n{verification_json}\n\n"
            f"# Layout Data:\n{layout_json}\n\n"
            f"# Worker Plans:\n{worker_plans_json}"
        )

        # Build message content based on whether image is provided
        if layout_image:
            message_content: list[dict[str, Any]] | str = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{layout_image}"
                    },
                },
                {"type": "text", "text": text_body},
            ]
        else:
            # No image provided - text only
            message_content = text_body

        response = self.agent.invoke(
            {"messages": [{"role": "user", "content": message_content}]}
        )

        return response["structured_response"]
