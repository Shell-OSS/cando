# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Assembly planner worker for layout refinements based on assembly
rules.

AssemblyPlanner worker for planning layout refinements.
"""

import json
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from cando.agents.verifiers.models import WorkerResult
from cando.config import ModelsConfig
from cando.model_factory import resolve_agent_model
from cando.utils.generic import json_dumps, round_floats

from ..models import PlannerWorkerResult
from .prompt import ASSEMBLY_PLANNER_SYSTEM_PROMPT
from .tools import augment_layout_data_for_planning


class AssemblyPlanner:
    """
    Plans layout refinements for assembly formation violations.

    This planner addresses:
    - Fragmentation violations (scattered elements that should be clustered)
    - Formation violations (elements not forming required patterns)

    It uses computational tools to analyze spatial availability, then
    the LLM for higher-level planning decisions.
    """

    def __init__(
        self,
        rules: list[dict] | None = None,
        model: str = ModelsConfig.assembly_planner,
    ):
        """
        Initialize the assembly planner.

        Args:
            rules: List of assembly rule dictionaries. If None, loads from
                the assembly verifier's rules.json.
            model: Model identifier for chat model initialization.
        """
        # Load rules from assembly verifier if not provided
        if rules is None:
            base_dir = Path(__file__).resolve().parent.parent.parent
            rules_path = base_dir / "verifiers" / "assembly" / "rules.json"
            with open(rules_path) as rules_file:
                self.rules = json.load(rules_file)
        else:
            self.rules = rules

        # Initialize the assembly planner agent
        system_prompt = ASSEMBLY_PLANNER_SYSTEM_PROMPT.replace(
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
        """Plans layout refinements for assembly issues.

        Args:
            layout_data: Layout data with 'elements' list.
            feedback: Output from assembly verifier.

        Returns:
            PlannerWorkerResult: Planning result with actions to fix
                assembly issues.
        """
        # Augment layout data with fix computations
        spatial_analysis = augment_layout_data_for_planning(
            elements=layout_data.get("elements", []),
            rules=self.rules,
        )

        # Prepare enriched input for the agent
        enriched_input = {
            "layout_data": layout_data,
            "assembly_verifier_output": feedback.model_dump(mode="json"),
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
