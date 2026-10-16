# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Node to evaluate candidate layouts via the Verifier, update best-result
tracking, and determine whether to exit, restore, or continue.

Evaluates all candidate layouts in parallel via the Verifier agent,
computes patience and plateau counters, and determines whether the graph
should exit or continue to the next stage. When the no_tool_verifier
ablation mode is active, uses the model-based ModelVerifier instead of
the deterministic, tool-based Verifier.
"""

import logging
from functools import lru_cache
from typing import cast

from cando.agents.verifiers.agent import Verifier
from cando.agents.verifiers.model_verifier import ModelVerifier
from cando.graph.state import CandoState
from cando.utils.visualizer import plot_layout

logger = logging.getLogger(__name__)


@lru_cache
def _get_tool_verifier() -> Verifier:
    return Verifier()


@lru_cache
def _get_model_verifier() -> ModelVerifier:
    return ModelVerifier()


def verify_node(state: CandoState) -> dict:
    """Evaluates all candidates and update best-result / patience tracking.

    Steps:
    1. Increment iteration counter.
    2. Clear `restored_from_iteration` (only meaningful in the iteration
       immediately after a restore; must not bleed into subsequent ones).
    3. Build the active candidate set by filtering `state["candidates"]`
       to those whose corresponding `ActionSequence` in
       `state["action_sequences"]` contains at least one action. Unchanged
       layouts are kept in state for logging but excluded from evaluation
       to avoid wasting LLM calls. Falls back to all candidates when every
       sequence is empty.
    4. Evaluate the filtered candidates in parallel via
       `Verifier.evaluate()`.
    5. Translate the winning filtered-pool index back to the original index
       in the full `candidates` list and store as `winning_candidate_idx`.
    6. Update `best_layout` / `best_score` if the new winner improves on
       the global best.
    7. Advance the patience counter (`iterations_without_improvement`);
       reset to 0 on improvement.
    8. Advance the plateau counter (`iterations_since_best_changed`);
       reset to 0 on improvement. This counter is intentionally NOT reset
       by restore_node — repeated restores cannot defer a plateau exit.
    9. Set `should_restore` when patience is exhausted.
    10. Set `exit_reason` (and thus trigger terminal routing) when:
       - The best candidate is compliant (`"compliant"`), or
       - `iteration >= max_iterations` (`"max_iterations"`), or
       - `iterations_since_best_changed >= plateau_patience` (`"plateau"`).

    Args:
        state (CandoState): Current pipeline state. Consumes:
            `candidates`, `action_sequences`, `constraints`, `weights`,
            `iteration`, `best_score`, `best_layout`, `best_layout_iteration`,
            `iterations_without_improvement`,
            `iterations_since_best_changed`, `patience`,
            `plateau_patience`, `max_iterations`, `verification`.

    Returns:
        dict: Partial state update with all relevant fields.
    """
    iteration: int = state["iteration"] + 1
    candidates: list[dict] = state["candidates"]
    action_sequences = state.get("action_sequences") or []
    constraints: dict = state["constraints"]
    weights: dict | None = state.get("weights")

    logger.debug(
        "verify_node: branch=%s iteration=%s evaluating %d candidate(s)",
        state.get("branch_id"),
        iteration,
        len(candidates),
    )

    # Only evaluate candidates whose action sequence is non-empty.
    active_indices: list[int] = [
        i for i, seq in enumerate(action_sequences) if seq.actions
    ]
    if not active_indices:
        active_indices = list(range(len(candidates)))

    candidates_to_evaluate = [candidates[i] for i in active_indices]

    verifier = (
        _get_tool_verifier()
        if state["ablation"]["verifier_mode"] == "tool"
        else _get_model_verifier()
    )

    (best_filtered_idx, best_result), all_results = verifier.evaluate(
        candidates_to_evaluate, constraints, weights
    )

    best_idx = active_indices[best_filtered_idx]

    # Build a score list aligned with the full candidates list.
    candidate_scores: list[float | None] = [None] * len(candidates)
    for filtered_idx, result in all_results:
        original_idx = active_indices[filtered_idx]
        candidate_scores[original_idx] = result.score

    prev_verification = state.get("verification")
    best_score: float = state.get("best_score", 0.0)
    best_layout: dict | None = state.get("best_layout")
    best_layout_iteration: int = state.get("best_layout_iteration", 0)
    iterations_without_improvement: int = state.get(
        "iterations_without_improvement", 0
    )
    iterations_since_best_changed: int = state.get(
        "iterations_since_best_changed", 0
    )
    patience: int = state["patience"]
    plateau_patience: int = state["plateau_patience"]
    max_iterations: int = state["max_iterations"]

    improved = best_result.score > best_score

    if improved:
        best_score = best_result.score
        best_layout = candidates[best_idx]
        best_layout_iteration = iteration
        iterations_without_improvement = 0
        iterations_since_best_changed = 0
    else:
        iterations_without_improvement += 1
        iterations_since_best_changed += 1

    should_restore: bool = (
        iterations_without_improvement >= patience and patience != -1
    )

    exit_reason: str | None = None
    if best_result.score >= 1.0:
        exit_reason = "compliant"
    elif iteration >= max_iterations:
        exit_reason = "max_iterations"
    elif iterations_since_best_changed >= plateau_patience:
        exit_reason = "plateau"

    layout_image: str | None = cast(
        "str | None",
        plot_layout(candidates[best_idx], show=False, return_base64=True),
    )

    return {
        "iteration": iteration,
        # Clear restore marker
        "restored_from_iteration": None,
        "layout_image": layout_image,
        "prev_verification": prev_verification,
        "verification": best_result,
        "winning_candidate_idx": best_idx,
        "candidate_scores": candidate_scores,
        "current_layout": candidates[best_idx],
        "best_layout": best_layout,
        "best_score": best_score,
        "best_layout_iteration": best_layout_iteration,
        "is_compliant": best_result.compliant,
        "iterations_without_improvement": iterations_without_improvement,
        "iterations_since_best_changed": iterations_since_best_changed,
        "should_restore": should_restore,
        "exit_reason": exit_reason,
    }


def route_after_verify(state: CandoState) -> str:
    """Decides where to go after verify_node.

    Routing priority:
    - If `exit_reason` is set, then `"end"` (terminal).
    - If `should_restore` is True, then `"restore"` (patience exhausted).
    - Otherwise `"build_history"` (normal loop continuation).

    Args:
        state (CandoState): Current pipeline state.

    Returns:
        str: One of `"end"`, `"restore"`, or `"build_history"`.
    """
    if state.get("exit_reason") is not None:
        return "end"
    if state.get("should_restore"):
        return "restore"
    return "build_history"
