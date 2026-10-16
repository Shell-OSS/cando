# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Flow planner worker for layout refinements based on flow rules.

FlowPlanner worker for planning layout refinements.
"""

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from cando.agents.verifiers.models import WorkerResult
from cando.config import ModelsConfig
from cando.model_factory import resolve_agent_model
from cando.utils.generic import json_dumps, round_floats

from ..models import PlannerWorkerResult
from .prompt import FLOW_PLANNER_SYSTEM_PROMPT
from .tools import calculate_element_proximity

# Max invalid routes fed into the prompt; keeps planner-worker prompts
# bounded regardless of how many invalid routes the verifier reports.
_MAX_INVALID_ROUTES_FOR_PLANNING = 10


class FlowPlanner:
    def __init__(
        self, rules: dict | None = None, model=ModelsConfig.flow_planner
    ):
        # Load rules if provided
        self.rules = rules if rules else {}

        # Initialize the flow planner agent
        system_prompt = FLOW_PLANNER_SYSTEM_PROMPT
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
        """Plans layout refinements for flow issues.

        Args:
            layout_data (dict): Layout data with 'elements' list.
            feedback (WorkerResult): Output from flow verifier.
            validation_data (dict | None): Optional validation data
                produced by the verifier's tools.
        Returns:
            PlannerWorkerResult: Planning result with actions to fix
                flow issues.
        """
        import copy

        layout_data = copy.deepcopy(layout_data)

        # Augment layout data with spatial availability analysis
        spatial_analysis = calculate_element_proximity(
            layout_elements=layout_data.get("elements", [])
        )

        if validation_data:
            invalid_routes = validation_data.get("invalid_routes", [])
            layout_data["invalid_routes"] = invalid_routes[
                :_MAX_INVALID_ROUTES_FOR_PLANNING
            ]

        # Prepare enriched input for the agent
        enriched_input = {
            "layout_data": layout_data,
            "flow_verifier_output": feedback.model_dump(mode="json"),
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
