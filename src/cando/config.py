# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Configuration for CANDO models."""

import os

# LangChain model identifier used by every agent role unless overridden
# below. Read from CANDO_MODEL_ID so a single environment variable can
# point every role at a different provider/deployment (e.g. a local vLLM
# server, see slurm/run_ablation.slurm) without editing this file. Falls
# back to the model reported in the paper when unset.
DEFAULT_MODEL_ID = os.environ.get("CANDO_MODEL_ID", "azure_openai:gpt-5.2")


class ModelsConfig:
    """LangChain model identifier for each agent role.

    Every role defaults to `DEFAULT_MODEL_ID` (itself driven by the
    `CANDO_MODEL_ID` environment variable). Override individual roles
    here if a given agent should use a different model.
    """

    # Generators
    generator: str = DEFAULT_MODEL_ID
    generator_zero: str = DEFAULT_MODEL_ID

    # Historian
    historian: str = DEFAULT_MODEL_ID

    # Planners
    planner: str = DEFAULT_MODEL_ID
    flow_planner: str = DEFAULT_MODEL_ID
    hard_constraints_planner: str = DEFAULT_MODEL_ID
    proximity_planner: str = DEFAULT_MODEL_ID
    assembly_planner: str = DEFAULT_MODEL_ID
    orientation_planner: str = DEFAULT_MODEL_ID

    # Model-based verifier (no_tool_verifier ablation mode)
    model_verifier: str = DEFAULT_MODEL_ID
