# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Node to call GeneratorZero and produce the initial layout from raw
user requirements.

Parses raw user requirements into the constraints dict (authoritative
parse) and seeds the candidates list with the single initial layout so
that verify_node can evaluate it on the very first pass.
"""

import logging
from functools import lru_cache

from cando.agents.generators.generator_zero.agent import GeneratorZero
from cando.graph.state import CandoState

logger = logging.getLogger(__name__)


@lru_cache
def _get_generator_zero() -> GeneratorZero:
    return GeneratorZero()


def generate_zero_node(state: CandoState) -> dict:
    """Produces the initial layout from raw user requirements.

    Calls `GeneratorZero.generate()` which internally:
    1. Normalizes `raw_requirements` (structured dict input) into the
       constraints dict.
    2. Retrieves similar exemplar layouts via k-NN over pre-computed feature
       vectors.
    3. Prompts an LLM with the exemplars as few-shot context and runs an
       internal bbox refinement loop to fix constraint violations.

    Args:
        state (CandoState): Current CandoState.

    Returns:
        dict: Partial state update containing:
            - `constraints`              : parsed constraints dict
            - `initial_layout`           : polygon-format layout dict
            - `generator_zero_history`   : internal refinement conversation
            - `candidates`               : `[initial_layout]`
    """
    logger.info("generate_zero_node: starting initial layout generation")
    layout, constraints_dict, history = _get_generator_zero().generate(
        state["raw_requirements"],
        use_icl=state["ablation"]["use_icl"],
    )

    return {
        "constraints": constraints_dict,
        "initial_layout": layout,
        "generator_zero_history": history,
        "candidates": [layout],
    }
