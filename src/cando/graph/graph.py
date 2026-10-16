# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""CANDO LangGraph pipeline construction.

Assembles and compiles the CANDO StateGraph from all node functions and
routing logic. The pipeline uses a fan-out / reduce strategy: after
generate_zero produces the initial layout, each round snapshot_node
captures the pre-fanout state, fan_out_node dispatches N independent
branch sub-graphs in parallel (each running
verify→build_history→plan→generate for branch_steps steps), and
reduce_node selects the winning branch and merges it back. Call
build_graph() once at application startup and reuse the returned
CompiledStateGraph for all graph.invoke() calls.
"""

import logging
import traceback

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from cando.graph.nodes import (
    build_history_node,
    fan_out_node,
    generate_node,
    generate_zero_node,
    plan_node,
    reduce_node,
    restore_node,
    route_after_reduce,
    route_after_verify,
    snapshot_node,
    verify_node,
)
from cando.graph.state import CandoState

logger = logging.getLogger(__name__)


def route_after_history(state: CandoState) -> str:
    """Decides where to go after build_history_node.

    Routing priority:
    - If `exit_reason` is set (final iteration recording path), then
      `"end"`.
    - If the resolved ablation config disables the Planner
      (`planner_mode == "direct"`, i.e. the no_planner ablation mode), then
      `"generate"` — skipping the Planner entirely.
    - Otherwise `"plan"` (normal `full` behavior).

    Args:
        state (CandoState): Current pipeline state.

    Returns:
        str: One of `"end"`, `"plan"`, or `"generate"`.
    """
    if state.get("exit_reason") is not None:
        return "end"
    if state["ablation"]["planner_mode"] == "planner":
        return "plan"
    return "generate"


def build_branch_graph() -> CompiledStateGraph:
    """Compile the branch sub-graph used by every parallel branch.

    The sub-graph performs a sequential refinement loop:
    verify -> (restore ->) build_history -> plan -> generate -> verify.
    It exits when verify_node sets exit_reason (compliant or
    max_iterations, which equals branch_steps for each branch).

    This compiled graph is invoked synchronously inside
    branch_refinement_node and reused across all branches and rounds.

    Returns:
        CompiledStateGraph: The compiled branch sub-graph.
    """
    b = StateGraph(CandoState)

    b.add_node("verify", verify_node)
    b.add_node("restore", restore_node)
    b.add_node("build_history", build_history_node)
    b.add_node("plan", plan_node)
    b.add_node("generate", generate_node)

    b.set_entry_point("verify")
    b.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            # Route 'end' through build_history so the final iteration is
            # always recorded in history before the branch terminates.
            "end": "build_history",
            "restore": "restore",
            "build_history": "build_history",
        },
    )
    b.add_edge("restore", "build_history")
    # Branches per route_after_history: plan (full), generate (no_planner),
    # or end (final iteration already recorded).
    b.add_conditional_edges(
        "build_history",
        route_after_history,
        {
            "end": END,
            "plan": "plan",
            "generate": "generate",
        },
    )
    b.add_edge("plan", "generate")
    b.add_edge("generate", "verify")

    return b.compile()


# Module-level singleton — compiled once, reused for every branch invocation.
_branch_graph: CompiledStateGraph = build_branch_graph()


def branch_refinement_node(state: CandoState) -> dict:
    """Entry-point node invoked by each Send from fan_out_node.

    Runs the full branch sub-graph synchronously on the isolated branch
    state. LangGraph executes all N invocations concurrently; each carries
    its own deep-copied state so there is no shared mutable data.

    Returns:
        dict: A single-key update
        `{"completed_branch_states": [final_state]}`
        that is appended to the global list by the LangGraph reducer
        (`operator.add`) defined on `CandoState.completed_branch_states`.
    """
    try:
        final_state = _branch_graph.invoke(state)
        return {"completed_branch_states": [dict(final_state)]}
    except Exception as exc:
        branch_id = state.get("branch_id")
        logger.error(
            "Branch %s failed — excluding from reduce.\n%s",
            branch_id,
            traceback.format_exc(),
        )
        # Return a sentinel so reduce_node can log and skip this branch.
        # The global state is not modified; reduce works on healthy branches.
        sentinel: dict = {
            "branch_id": branch_id,
            "best_score": 0.0,
            "iteration": state.get("iteration", 0),
            "exit_reason": "failed",
            "branch_failed": True,
            "branch_error": str(exc),
        }
        return {"completed_branch_states": [sentinel]}


def build_graph() -> CompiledStateGraph:
    """Assemble and compile the top-level CANDO StateGraph.

    Top-level flow::

        generate_zero → snapshot → fan_out → (Send x N) → reduce
                            ↑                                 │
                            └──────────── (loop) ─────────────┘
                                                              │
                                                            END

    Returns:
        CompiledStateGraph: The compiled LangGraph ready to invoke.

    Examples:

        >>> graph = build_graph()
        >>> result = graph.invoke({
                "raw_requirements": "...",
                "weights": None,
                "max_iterations": 20,
                "n_branches": 4,
                "branch_steps": 10,
                "local_patience": -1,
                "plateau_patience": 21,
                "n_candidates": 6,
                "history_window": 6,
                "layout_image": None,
                "initial_layout": None,
                "current_layout": None,
                "iteration": -1,
                "iterations_without_improvement": 0,
                "iterations_since_best_changed": 0,
                "pre_fanout_layout": None,
                "pre_fanout_score": 0.0,
                "branch_states": [],
                "completed_branch_states": [],
                "best_layout": None,
                "best_score": 0.0,
                "best_layout_iteration": 0,
                "restored_from_iteration": None,
                "is_compliant": False,
                "should_restore": False,
                "exit_reason": None,
                "verification": None,
                "prev_verification": None,
                "plan": None,
                "candidates": [],
                "action_sequences": [],
                "winning_candidate_idx": 0,
                "constraints": {},
                "generator_zero_history": None,
                "history": [],
            })
    """
    g = StateGraph(CandoState)

    g.add_node("generate_zero", generate_zero_node)
    g.add_node("snapshot", snapshot_node)
    g.add_node("branch_refinement_node", branch_refinement_node)
    g.add_node("reduce", reduce_node)

    g.set_entry_point("generate_zero")

    g.add_edge("generate_zero", "snapshot")

    # Dispatches N Send objects to branch_refinement_node.
    g.add_conditional_edges(
        "snapshot",
        fan_out_node,
        ["branch_refinement_node"],
    )

    g.add_edge("branch_refinement_node", "reduce")

    g.add_conditional_edges(
        "reduce",
        route_after_reduce,
        {
            "end": END,
            "snapshot": "snapshot",
        },
    )

    return g.compile()


def build_graph_from_layout() -> CompiledStateGraph:
    """Build the CANDO graph starting from the refinement phase.

    Identical to :func:`build_graph` but skips `generate_zero_node`.
    The entry point is `snapshot`, so the caller must pre-populate
    `constraints`, `initial_layout`, `current_layout`, and
    `candidates` in the initial state before calling `graph.invoke()`.

    Use this variant when an initial layout has already been produced
    (e.g. loaded from disk) and only the fan-out / reduce refinement loop
    should run.

    Returns:
        CompiledStateGraph: Ready-to-invoke compiled graph.
    """
    g = StateGraph(CandoState)

    g.add_node("snapshot", snapshot_node)
    g.add_node("branch_refinement_node", branch_refinement_node)
    g.add_node("reduce", reduce_node)

    g.set_entry_point("snapshot")

    g.add_conditional_edges(
        "snapshot",
        fan_out_node,
        ["branch_refinement_node"],
    )

    g.add_edge("branch_refinement_node", "reduce")

    g.add_conditional_edges(
        "reduce",
        route_after_reduce,
        {
            "end": END,
            "snapshot": "snapshot",
        },
    )

    return g.compile()
