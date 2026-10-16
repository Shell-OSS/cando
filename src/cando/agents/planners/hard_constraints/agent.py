# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Hard constraints planner worker for layout refinements.

HardConstraintsPlanner worker for planning layout refinements.
"""

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from cando.agents.verifiers.models import WorkerResult
from cando.config import ModelsConfig
from cando.model_factory import resolve_agent_model
from cando.utils.generic import json_dumps, round_floats

from ..models import PlannerWorkerResult
from .prompt import HARD_CONSTRAINTS_PLANNER_SYSTEM_PROMPT
from .tools import augment_layout_data_for_planning


class HardConstraintsPlanner:
    """
    Plans layout refinements for hard constraint violations.

    This planner addresses:
    - Overlap violations (elements physically intersecting each other)
    - Boundary violations (elements outside the layout boundary)

    It uses computational tools to analyze spatial positions and provide
    spatial analysis information (e.g., overlaps, distances, free space),
    then the LLM for higher-level planning decisions.
    """

    def __init__(
        self,
        rules: dict | None = None,
        model: str = ModelsConfig.hard_constraints_planner,
    ):
        """
        Initialize the hard constraints planner.

        Args:
            rules: Hard constraint rules to load into the system prompt.
            model: Model identifier for chat model initialization.
        """
        # Load rules if provided
        self.rules = rules if rules else {}

        # Initialize the hard constraints planner agent
        system_prompt = HARD_CONSTRAINTS_PLANNER_SYSTEM_PROMPT.replace(
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
        """Plans layout refinements for hard constraint issues.

        Args:
            layout_data: Layout data with 'elements' list.
            feedback: Output from hard constraints verifier.
            validation_data: Optional raw structured data from the
                hard constraints verifier (overlaps, out_of_boundaries).

        Returns:
            PlannerWorkerResult: Planning result with actions to fix
                hard constraint violations.
        """
        # Augment layout data with spatial availability analysis
        spatial_analysis = augment_layout_data_for_planning(
            elements=layout_data.get("elements", []),
            validation_data=validation_data,
        )

        # Prepare enriched input for the agent
        enriched_input = {
            "layout_data": layout_data,
            "hard_constraints_verifier_output": feedback.model_dump(
                mode="json"
            ),
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
