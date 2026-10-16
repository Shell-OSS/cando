# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Node to call Generator and produce N diverse refined layout candidates.

Each candidate is generated from a different strategy so that verify_node
has a varied pool to evaluate, using the current layout and the Planner's
remediation plan.

When the no_planner ablation mode is active
(state["ablation"]["planner_mode"] == "direct"), a second, dedicated
Generator instance is used instead: it consumes structured Verifier
evidence directly (no PlannerResult) via
Generator.generate_from_verifier().
"""

import hashlib
import logging
from functools import lru_cache

from cando.agents.generators.generator.agent import Generator
from cando.agents.generators.generator.evidence import build_verifier_evidence
from cando.agents.generators.generator.prompt import GENERATOR_DIRECT_PROMPT
from cando.graph.state import CandoState
from cando.utils.history import format_history_for_prompt

logger = logging.getLogger(__name__)


@lru_cache
def _get_generator() -> Generator:
    return Generator()


@lru_cache
def _get_direct_generator() -> Generator:
    # Dedicated instance for the no_planner ablation: direct-generation
    # prompt (no Planner framing), tunable independently via its own
    # model_factory role.
    return Generator(
        system_prompt=GENERATOR_DIRECT_PROMPT,
        role="generator_direct",
    )


CATEGORIES = (
    "hard_constraints",
    "assembly",
    "proximity",
    "orientation",
    "flow",
)


def _derive_seed(*parts: object) -> int:
    """Derives a stable integer seed from arbitrary hashable-as-string parts.

    Args:
        *parts: Values combined (via str()) to derive the seed.

    Returns:
        int: A deterministic seed in [0, 2**32).
    """
    payload = ":".join(map(str, parts)).encode()
    return int(hashlib.sha256(payload).hexdigest()[:8], 16)


def generate_node(state: CandoState) -> dict:
    """Generates N diverse refined layout candidates.

    In `full` (and any mode with `planner_mode == "planner"`), calls
    `Generator.generate()` with the current layout and `PlannerResult`
    guidance. In `no_planner` (`planner_mode == "direct"`), calls
    `Generator.generate_from_verifier()` on a dedicated Generator instance
    with structured Verifier evidence instead.

    Candidate index `i` corresponds to `action_sequences[i]`. The winning
    candidate is determined by `verify_node` in the following node and stored
    as `winning_candidate_idx`.

    Args:
        state (CandoState): Current pipeline state.

    Returns:
        dict: Partial state update containing:
            - `candidates`       : list of N refined layout dicts
            - `action_sequences` : corresponding list of ActionSequence
            - `selected_strategies` : strategy keys used per candidate
    """
    experiment = state["ablation"]

    layout_data = state["current_layout"]
    if layout_data is None:
        raise ValueError(
            "state['current_layout'] is None: cannot generate candidates"
        )
    logger.debug(
        "generate_node: branch=%s iteration=%s planner_mode=%s",
        state.get("branch_id"),
        state.get("iteration"),
        experiment["planner_mode"],
    )
    n: int = state.get("n_candidates", 3)
    weights: dict | None = state.get("weights")
    layout_image: str | None = (
        state.get("layout_image")
        if experiment["visual_context_enabled"]
        else None
    )

    # Stable per-iteration seed for reproducible strategy selection/ordering
    # (does not make remote model sampling fully deterministic).
    base_seed = state.get("run_seed")
    strategy_seed = (
        _derive_seed(
            base_seed,
            state.get("current_round", 0),
            state.get("branch_id", -1),
            state.get("iteration", 0),
        )
        if base_seed is not None
        else None
    )

    if experiment["planner_mode"] == "planner":
        verifier_result = state.get("verification")
        plan = state["plan"]
        if plan is None:
            raise ValueError(
                "state['plan'] is None: cannot generate in planner mode"
            )

        (
            refined_layouts,
            action_sequences,
            selected_strategy_keys,
        ) = _get_generator().generate(
            layout_data=layout_data,
            guidance=plan,
            n=n,
            weights=weights,
            layout_image=layout_image,
            verifier_result=verifier_result,
            strategy_mode=experiment["strategy_mode"],
            seed=strategy_seed,
        )
    else:
        verification = state["verification"]
        if verification is None:
            raise ValueError(
                "state['verification'] is None: cannot build verifier "
                "evidence for the no_planner ablation mode"
            )
        evidence = build_verifier_evidence(verification, weights)

        failing_categories = sum(
            not getattr(verification.feedback, category).compliant
            for category in CATEGORIES
        )

        history_context = None
        if failing_categories >= 2:
            history_context = (
                format_history_for_prompt(
                    state.get("history", []),
                    window=state.get("history_window", 5),
                )
                or None
            )

        (
            refined_layouts,
            action_sequences,
            selected_strategy_keys,
        ) = _get_direct_generator().generate_from_verifier(
            layout_data=layout_data,
            verifier_evidence=evidence,
            history_context=history_context,
            n=n,
            weights=weights,
            layout_image=layout_image,
            strategy_mode=experiment["strategy_mode"],
            seed=strategy_seed,
        )

    return {
        "candidates": refined_layouts,
        "action_sequences": action_sequences,
        "selected_strategies": selected_strategy_keys,
    }
