# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Node to call the Planner agent and produce a high-level remediation
plan.

The plan is based on the current layout and the latest VerifierResult.
History context is formatted from state["history"] and passed to the
Planner for context-aware planning.
"""

import logging
from functools import lru_cache

from cando.agents.planners.agent import Planner
from cando.graph.state import CandoState
from cando.utils.history import format_history_for_prompt

logger = logging.getLogger(__name__)


@lru_cache
def _get_planner() -> Planner:
    return Planner()


def plan_node(state: CandoState) -> dict:
    """Produces a high-level remediation plan for the current layout.

    Calls `Planner.plan()` with the current layout, latest
    `VerifierResult`, optional layout image, optional category weights,
    and a formatted history context string built from the most recent
    `history_window` entries of `state["history"]`.

    Args:
        state (CandoState): Current pipeline state.

    Returns:
        dict: Partial state update containing `plan`.
    """
    layout_data = state["current_layout"]
    if layout_data is None:
        raise ValueError("state['current_layout'] is None: cannot plan")
    verification = state["verification"]
    if verification is None:
        raise ValueError(
            "state['verification'] is None: cannot plan without a "
            "verification result"
        )
    layout_image: str | None = (
        state.get("layout_image")
        if state["ablation"]["visual_context_enabled"]
        else None
    )
    weights: dict | None = state.get("weights")
    history: list = state.get("history", [])
    history_window: int = state.get("history_window", 5)

    history_context: str | None = (
        format_history_for_prompt(history, window=history_window) or None
    )
    iteration: int = state.get("iteration", 0)

    logger.debug(
        "plan_node: branch=%s iteration=%s",
        state.get("branch_id"),
        iteration,
    )

    plan = _get_planner().plan(
        layout_data=layout_data,
        verification=verification,
        layout_image=layout_image,
        weights=weights,
        history_context=history_context,
        iteration=iteration,
    )

    return {"plan": plan}
