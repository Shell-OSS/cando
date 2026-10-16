# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Fan-out node for the CANDO parallel branching refinement pipeline.

snapshot_node captures pre-fanout best layout / score into the global
state. fan_out_node then deep-copies the global state N times — once
per branch — overriding per-branch fields and dispatching each copy as
an independent LangGraph Send so that all N branches execute
concurrently before reduce_node runs.
"""

import copy
import logging

from langgraph.types import Send

from cando.graph.state import CandoState

logger = logging.getLogger(__name__)


def snapshot_node(state: CandoState) -> dict:
    """Capture pre-fanout best layout and score into the global state.

    Runs immediately before fan_out_node so that reduce_node can compare
    the winning branch score against the pre-fanout baseline.  Also
    increments the 1-based round counter used to organise live log output.

    Args:
        state (CandoState): Current global pipeline state.

    Returns:
        dict: Partial state update with pre_fanout_layout,
        pre_fanout_score, and current_round.
    """
    current_round = state.get("current_round", 0) + 1
    logger.debug(
        "snapshot_node: round=%s pre_fanout_score=%.4f",
        current_round,
        state.get("best_score", 0.0),
    )
    return {
        "pre_fanout_layout": copy.deepcopy(state.get("best_layout")),
        "pre_fanout_score": state.get("best_score", 0.0),
        "current_round": current_round,
    }


def fan_out_node(state: CandoState) -> list[Send]:
    """Dispatch N independent branch executions via the LangGraph Send API.

    Each branch receives a deep copy of the current global state with
    per-branch fields overridden for isolation:
    - `patience` is set to `local_patience` (within-branch restore
      threshold; -1 disables restores inside branches).
    - `max_iterations` is capped at `branch_steps` so branches exit
      after exactly B refinement steps.
    - `iterations_without_improvement` is zeroed so every branch starts
      with a clean patience clock.
    - Bookkeeping lists (`branch_states`, `completed_branch_states`)
      are cleared so they do not inherit stale data from previous rounds.

    This function is wired as the callable for `add_conditional_edges`.
    Because it returns a list of `Send` objects (not a routing string),
    LangGraph executes all N branch invocations concurrently and only
    advances to `reduce_node` once every branch has completed.

    Args:
        state (CandoState): Current global pipeline state.

    Returns:
        list[Send]: One Send per branch, each targeting
        `"branch_refinement_node"` with its own isolated state dict.
    """
    n_branches: int = state["n_branches"]
    branch_steps: int = state["branch_steps"]
    local_patience: int = state["local_patience"]

    logger.debug(
        "fan_out_node: round=%s dispatching %d branch(es)",
        state.get("current_round", 0),
        n_branches,
    )

    sends: list[Send] = []
    for branch_id in range(n_branches):
        branch_state = copy.deepcopy(dict(state))
        branch_state["branch_id"] = branch_id
        branch_state["patience"] = local_patience
        branch_state["max_iterations"] = min(
            state["iteration"] + branch_steps + 1,
            state["max_iterations"],
        )
        branch_state["iterations_without_improvement"] = 0
        branch_state["should_restore"] = False
        branch_state["exit_reason"] = None
        branch_state["restored_from_iteration"] = None
        branch_state["branch_states"] = []
        branch_state["completed_branch_states"] = []
        sends.append(Send("branch_refinement_node", branch_state))
    return sends
