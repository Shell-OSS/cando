# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Reduce node for the CANDO parallel branching refinement pipeline.

After all N branch sub-graphs have completed, reduce_node selects the
winning branch, merges its state into the global state, advances the
iteration counter by branch_steps, and checks all convergence / exit
conditions. route_after_reduce decides whether to loop back to fan_out
or terminate. No LLM calls are made.
"""

import copy
import logging

from cando.graph.state import CandoState
from cando.utils.live_logger import write_reduce_summary

logger = logging.getLogger(__name__)


def reduce_node(state: CandoState) -> dict:
    """Select the winning branch and merge its state into the global state.

    Algorithm
    ---------
    1. **Select winning branch**: the branch with the highest
       `best_score`. Ties are broken by lowest final `iteration`
       (fewest steps to reach that score).
    2. **Check for improvement**: compare `winning_score` against
       `pre_fanout_score`.
       - If improved  → adopt winning branch's `best_layout`; reset
         `iterations_since_best_changed` to 0.
       - If no improvement → restore `pre_fanout_layout` as the current
         layout; increment `iterations_since_best_changed` by 1.
       In both cases the winning branch's `history` replaces the global
       history and its per-node outputs are promoted to global state.
    3. **Advance global iteration** by `branch_steps`.
    4. **Check exit conditions** (compliant, max_iterations, plateau) and
       set `exit_reason` accordingly.
    5. **Reset** `completed_branch_states` to [] for the next round.

    Args:
        state (CandoState): Current global pipeline state, including
            `completed_branch_states` (list of N final branch state dicts)
            populated by the LangGraph reducer.

    Returns:
        dict: Partial state update merging the winning branch and
        advancing global counters.
    """
    all_collected: list[dict] = state["completed_branch_states"]

    logger.debug(
        "reduce_node: round=%s collected=%d branch state(s)",
        state.get("current_round", 0),
        len(all_collected),
    )

    # Separate healthy branches from any that raised an exception.
    failed = [b for b in all_collected if b.get("branch_failed")]
    branches = [b for b in all_collected if not b.get("branch_failed")]

    if failed:
        logger.warning(
            "Round %s: %d branch(es) failed, excluded from reduce: %s",
            state.get("current_round", 0),
            len(failed),
            [(b.get("branch_id"), b.get("branch_error")) for b in failed],
        )

    if not branches:
        # Every branch failed — abort the run rather than silently producing
        # a degenerate result.
        logger.error(
            "Round %s: all %d branch(es) failed. Aborting run.",
            state.get("current_round", 0),
            len(all_collected),
        )
        raise RuntimeError(
            f"Round {state.get('current_round', 0)}: "
            f"all {len(all_collected)} branch(es) failed. Cannot reduce. "
            "See logs for details."
        )

    branch_steps: int = state["branch_steps"]
    pre_fanout_score: float = state.get("pre_fanout_score", 0.0)
    pre_fanout_layout: dict | None = state.get("pre_fanout_layout")
    plateau_patience: int = state["plateau_patience"]
    max_iterations: int = state["max_iterations"]
    global_iteration: int = state.get("iteration", 0)

    # Select winning branch
    def branch_key(b: dict) -> tuple[float, int]:
        return (b.get("best_score", 0.0), -b.get("iteration", 0))

    winner = max(branches, key=branch_key)
    winning_score: float = winner.get("best_score", 0.0)

    iterations_since_best_changed: int = state.get(
        "iterations_since_best_changed", 0
    )
    improved = winning_score > pre_fanout_score

    if improved:
        current_layout = copy.deepcopy(winner.get("best_layout"))
        best_layout = copy.deepcopy(winner.get("best_layout"))
        best_score = winning_score
        iterations_since_best_changed = 0
    else:
        current_layout = copy.deepcopy(pre_fanout_layout)
        best_layout = copy.deepcopy(pre_fanout_layout)
        best_score = pre_fanout_score
        iterations_since_best_changed += 1

    # Advance global iteration by actual steps taken in the winning branch
    new_iteration = winner.get("iteration", global_iteration + branch_steps)

    any_compliant = any(b.get("exit_reason") == "compliant" for b in branches)
    exit_reason: str | None = None

    if any_compliant:
        compliant_branches = [
            b for b in branches if b.get("exit_reason") == "compliant"
        ]
        winner = max(compliant_branches, key=branch_key)
        best_layout = copy.deepcopy(winner.get("best_layout"))
        best_score = winner.get("best_score", 0.0)
        current_layout = copy.deepcopy(best_layout)
        exit_reason = "compliant"
    elif new_iteration >= max_iterations:
        exit_reason = "max_iterations"
    elif iterations_since_best_changed >= plateau_patience:
        exit_reason = "plateau"

    if exit_reason is not None:
        logger.info(
            "reduce_node: round=%s terminating exit_reason=%s "
            "best_score=%.4f iteration=%s",
            state.get("current_round", 0),
            exit_reason,
            best_score,
            new_iteration,
        )

    # Live logging: write round summary to disk before returning
    log_dir: str | None = state.get("log_dir")
    if log_dir:
        all_branch_info = [
            {
                "branch_id": b.get("branch_id"),
                "best_score": b.get("best_score", 0.0),
                "iteration": b.get("iteration", 0),
                "exit_reason": b.get("exit_reason"),
            }
            for b in all_collected
        ]
        reduce_log = {
            "round_num": state.get("current_round", 0),
            "improved": improved,
            "pre_fanout_score": pre_fanout_score,
            "winning_branch_id": winner.get("branch_id"),
            "winning_score": winning_score,
            "new_best_score": best_score,
            "global_iteration_after": new_iteration,
            "exit_reason": exit_reason,
            "iterations_since_best_changed": iterations_since_best_changed,
            "all_branches": all_branch_info,
        }
        write_reduce_summary(
            log_dir=log_dir,
            round_num=state.get("current_round", 0),
            summary=reduce_log,
        )

    return {
        # Layout / score
        "current_layout": current_layout,
        "best_layout": best_layout,
        "best_score": best_score,
        "best_layout_iteration": winner.get("best_layout_iteration", 0),
        # Counters
        "iteration": new_iteration,
        "iterations_since_best_changed": iterations_since_best_changed,
        "iterations_without_improvement": winner.get(
            "iterations_without_improvement", 0
        ),
        # Flags
        "is_compliant": exit_reason == "compliant",
        "exit_reason": exit_reason,
        "should_restore": False,
        "restored_from_iteration": None,
        # Per-node outputs from winning branch
        "verification": winner.get("verification"),
        "prev_verification": winner.get("prev_verification"),
        "plan": winner.get("plan"),
        "candidates": winner.get("candidates", []),
        "action_sequences": winner.get("action_sequences", []),
        "winning_candidate_idx": winner.get("winning_candidate_idx", 0),
        "selected_strategies": winner.get("selected_strategies", []),
        "candidate_scores": winner.get("candidate_scores", []),
        "layout_image": winner.get("layout_image"),
        # Promote winning branch history wholesale
        "history": winner.get("history", []),
        "completed_branch_states": None,
    }


def route_after_reduce(state: CandoState) -> str:
    """Route the graph after reduce_node.

    Returns:
        `"end"` if `exit_reason` is set (termination condition met),
        `"snapshot"` otherwise to begin the next fan-out round.
    """
    if state.get("exit_reason"):
        return "end"
    return "snapshot"
