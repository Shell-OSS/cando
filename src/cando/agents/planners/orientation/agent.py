# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Orientation planner worker for layout refinements based on
orientation rules.

OrientationPlanner worker for planning layout refinements.
"""

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from cando.agents.verifiers.models import WorkerResult
from cando.config import ModelsConfig
from cando.model_factory import resolve_agent_model
from cando.utils.generic import json_dumps, round_floats

from ..models import PlannerWorkerResult
from .prompt import ORIENTATION_PLANNER_SYSTEM_PROMPT


class OrientationPlanner:
    def __init__(
        self,
        rules: list[dict] | None = None,
        model=ModelsConfig.orientation_planner,
    ):
        # Load rules if provided
        self.rules = rules if rules else []

        # Initialize the orientation planner agent
        system_prompt = ORIENTATION_PLANNER_SYSTEM_PROMPT.replace(
            "{rules_json}",
            json_dumps(round_floats(self.rules)),
        )
        self.agent = create_agent(
            model=resolve_agent_model("planner_worker", model),
            system_prompt=system_prompt,
            response_format=ToolStrategy(PlannerWorkerResult),
        )

    def plan(
        self,
        layout_data: dict,
        feedback: WorkerResult,
        validation_data: dict | None = None,
        *args,
        **kwargs,
    ) -> PlannerWorkerResult:
        """Plans layout refinements for orientation issues.

        Args:
            layout_data (dict): Layout data with 'elements' list.
            feedback (WorkerResult): Output from orientation verifier.
            validation_data (dict | None): Optional validation data
                produced by the verifier's tools (contains
                orientation_analysis).

        Returns:
            PlannerWorkerResult: Planning result with actions to fix
                orientation issues.
        """
        import copy

        layout_data = copy.deepcopy(layout_data)

        # Get orientation analysis from validation data
        if validation_data:
            orientation_analysis = validation_data.get(
                "orientation_analysis", {}
            )
            layout_data["orientation_analysis"] = orientation_analysis

        # Prepare enriched input for the agent
        enriched_input = {
            "layout_data": layout_data,
            "orientation_verifier_output": feedback.model_dump(mode="json"),
        }

        # Invoke agent
        response = self.agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": json_dumps(round_floats(enriched_input)),
                    }
                ]
            }
        )

        return response["structured_response"]
