# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Proximity planner worker for layout refinements based on proximity
rules.

ProximityPlanner worker for planning layout refinements.
"""

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from cando.agents.verifiers.models import WorkerResult
from cando.config import ModelsConfig
from cando.model_factory import resolve_agent_model
from cando.utils.generic import json_dumps, round_floats

from ..models import PlannerWorkerResult
from .prompt import PROXIMITY_PLANNER_SYSTEM_PROMPT
from .tools import augment_layout_data_for_planning


class ProximityPlanner:
    def __init__(
        self, rules: dict | None = None, model=ModelsConfig.proximity_planner
    ):
        # Load rules if provided
        self.rules = rules if rules else {}

        # Initialize the proximity planner agent
        system_prompt = PROXIMITY_PLANNER_SYSTEM_PROMPT.replace(
            "{rules_json}",
            json_dumps(round_floats(self.rules)),
        )
        self.agent = create_agent(
            model=resolve_agent_model("planner_worker", model),
            system_prompt=system_prompt,
            response_format=ToolStrategy(PlannerWorkerResult),
        )

    def plan(
        self, layout_data: dict, feedback: WorkerResult, *args, **kwargs
    ) -> PlannerWorkerResult:
        """Plans layout refinements for proximity issues.

        Args:
            layout_data (dict): Layout data with 'elements' list.
            feedback (WorkerResult): Output from proximity verifier.

        Returns:
            PlannerWorkerResult: Planning result with actions to fix
                proximity issues.
        """
        # Augment layout data with spatial availability analysis
        spatial_analysis = augment_layout_data_for_planning(
            elements=layout_data.get("elements", []),
            rules=self.rules.get("rules", []),
        )

        # Prepare enriched input for the agent
        enriched_input = {
            "layout_data": layout_data,
            "proximity_verifier_output": feedback.model_dump(mode="json"),
            "spatial_analysis": spatial_analysis,
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
