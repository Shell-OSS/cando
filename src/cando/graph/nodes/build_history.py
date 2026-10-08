# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Node to append one IterationRecord to state["history"] after each
refinement iteration.

Calls the Historian agent for the LLM-generated CategoryDelta notes.
"""

import copy
import logging
from functools import lru_cache

from cando.agents.historian.agent import Historian
from cando.agents.planners.models import PlannerResult
from cando.graph.state import CandoState, IterationRecord, IterationSummary
from cando.utils.live_logger import write_iteration_log

logger = logging.getLogger(__name__)


@lru_cache
def _get_historian() -> Historian:
    return Historian()


CATEGORIES = (
    "hard_constraints",
    "assembly",
    "proximity",
    "orientation",
    "flow",
)

GROUNDING_SOURCES = ("visual", "worker", "history", "layout")


def _build_deltas_without_historian(
    prev_verification,
    verification,
) -> dict:
    """Deterministic CategoryDelta computation used when the Historian is
    disabled (no_historian ablation mode). Preserves the score history and
    category-delta structure but omits the LLM-generated note.

    Args:
        prev_verification: The previous iteration's VerifierResult, or None.
        verification: The current iteration's VerifierResult.

    Returns:
        dict: Mapping of category name to CategoryDelta with an empty note.
    """
    result = {}

    for category in CATEGORIES:
        before = (
            getattr(
                prev_verification.feedback,
                category,
            ).score
            if prev_verification is not None
            else 0.0
        )
        after = getattr(
            verification.feedback,
            category,
        ).score

        result[category] = {
            "score_before": before,
            "score_after": after,
            "delta": round(after - before, 2),
            "note": "",
        }

    return result


def _extract_grounding_counts(
    plan: PlannerResult | None,
) -> dict[str, dict[str, int]]:
    """Counts grounding source frequencies per category from a PlannerResult.

    Args:
        plan (PlannerResult | None): PlannerResult from plan_node, or None.

    Returns:
        dict[str, dict[str, int]]: Mapping of category, to grounding source,
            and to count. All five categories and all four grounding sources
            are always present; missing ones default to 0.
    """
    counts: dict[str, dict[str, int]] = {
        cat: {src: 0 for src in GROUNDING_SOURCES} for cat in CATEGORIES
    }
    if plan is None:
        return counts
    for cat in CATEGORIES:
        for action in getattr(plan, cat).actions:
            counts[cat][action.grounding] += 1
    return counts


def _extract_planner_actions(
    plan: PlannerResult | None,
) -> dict[str, list[str]]:
    """Extracts high-level action strings from a PlannerResult.

    Only the top-level action descriptions are kept (no sub-steps) to
    keep the history summary compact.

    Args:
        plan (PlannerResult | None): PlannerResult from plan_node, or None
            if unavailable.

    Returns:
        dict[str, list[str]]: Mapping of category name to list of action
            description strings.
    """
    if plan is None:
        return {cat: [] for cat in CATEGORIES}

    return {
        cat: [a.action for a in getattr(plan, cat).actions]
        for cat in CATEGORIES
    }


def build_history_node(state: CandoState) -> dict:
    """Appends one IterationRecord to state["history"].

    Responsibilities:
    - Calls Historian.generate() for per-category CategoryDelta notes.
    - Assembles IterationSummary deterministically from state fields.
    - Deep-copies current_layout as the layout_snapshot for programmatic
      restore.

    Args:
        state (CandoState): Current CandoState.

    Returns:
        dict: Partial state update containing the updated "history" list.
    """
    prev_verification = state["prev_verification"]
    verification = state["verification"]
    if verification is None:
        raise RuntimeError(
            "build_history_node called before verification was set "
            f"(round={state.get('current_round')}, "
            f"branch_id={state.get('branch_id')})"
        )

    logger.debug(
        "build_history_node: branch=%s iteration=%s",
        state.get("branch_id"),
        state.get("iteration"),
    )

    # Generate per-category notes and compute score deltas.
    if state["ablation"]["historian_enabled"]:
        category_deltas = _get_historian().generate(
            prev_verification=prev_verification, verification=verification
        )
    else:
        category_deltas = _build_deltas_without_historian(
            prev_verification,
            verification,
        )
    score_before = (
        prev_verification.score if prev_verification is not None else 0.0
    )
    score_after = verification.score

    # Format the winning ActionSequence as compact strings for history.
    winning_candidate_idx: int = state.get("winning_candidate_idx", 0)
    action_sequences = state.get("action_sequences") or []
    winning_sequence: list[str]
    if action_sequences and winning_candidate_idx < len(action_sequences):
        winning_seq = action_sequences[winning_candidate_idx]
        winning_sequence = [str(action) for action in winning_seq.actions]
    else:
        winning_sequence = []

    restored_from_iteration: int | None = state.get("restored_from_iteration")

    # Experiment-tracking fields
    selected_strategies: list[str] = state.get("selected_strategies") or []
    candidate_scores: list[float | None] = state.get("candidate_scores") or []
    winning_strategy: str | None = (
        selected_strategies[winning_candidate_idx]
        if (
            selected_strategies
            and winning_candidate_idx < len(selected_strategies)
        )
        else None
    )
    grounding_counts = _extract_grounding_counts(state.get("plan"))

    summary: IterationSummary = {
        "iteration": state["iteration"],
        "score_before": score_before,
        "score_after": score_after,
        "score_delta": round(score_after - score_before, 6),
        "is_regression": score_after < score_before,
        "restored_from_iteration": restored_from_iteration,
        "planner_actions": _extract_planner_actions(state.get("plan")),
        "winning_sequence": winning_sequence,
        "category_deltas": category_deltas,
        "selected_strategies": selected_strategies,
        "winning_strategy": winning_strategy,
        "candidate_scores": candidate_scores,
        "grounding_counts": grounding_counts,
    }

    current_layout = state["current_layout"]
    if current_layout is None:
        raise RuntimeError(
            "build_history_node called before current_layout was set "
            f"(round={state.get('current_round')}, "
            f"branch_id={state.get('branch_id')})"
        )

    record: IterationRecord = {
        "summary": summary,
        "layout_snapshot": copy.deepcopy(current_layout),
    }

    # Live logging: write per-iteration summary and layout PNG to disk
    log_dir: str | None = state.get("log_dir")
    if log_dir:
        write_iteration_log(
            log_dir=log_dir,
            round_num=state.get("current_round", 0),
            branch_id=state.get("branch_id") or 0,
            iteration=state["iteration"],
            summary=summary,
            layout_image_b64=state.get("layout_image"),
        )

    return {"history": state["history"] + [record]}
