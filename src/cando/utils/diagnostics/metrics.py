# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Aggregate metrics extracted from a completed CANDO run.

Post-run analysis utilities for runs.
"""

from __future__ import annotations

from typing import Any

from cando.graph.state import CandoState, CategoryDelta

_CATEGORIES = (
    "hard_constraints",
    "assembly",
    "proximity",
    "orientation",
    "flow",
)

_GROUNDING_SOURCES = ("visual", "worker", "history", "layout")


def compute_metrics(final_state: CandoState) -> dict[str, Any]:
    """Compute aggregate metrics from a finished CANDO run.

    Args:
        final_state (CandoState): The final state returned by
            `graph.invoke()`.

    Returns:
        dict[str, Any]: A flat-ish dictionary of metrics grouped into the
        following top-level keys:

        **run_summary** - scalar overview of the run::

            {
                "total_iterations"       : int,
                "exit_reason"            : str | None,
                "initial_score"          : float,
                "best_score"             : float,
                "total_score_improvement": float,
                "is_compliant"           : bool,
            }

        **convergence** - iteration-level improvement statistics::

            {
                "n_improvements"             : int,
                "n_regressions"              : int,
                "first_improvement_iteration": int | None,
                "improvement_rate"           : float,
            }

        **restore_analysis** - patience-triggered restore statistics::

            {
                "restore_count"            : int,
                "restore_iterations"       : list[int],
                "restore_outcome_deltas"   : list[float],
                "avg_restore_outcome_delta": float | None,
            }

        **strategy_analysis** - generator strategy effectiveness::

            {
                "strategy_selection_counts": dict[str, int],
                "strategy_win_counts"      : dict[str, int],
                "strategy_win_rate"        : dict[str, float],
                "strategy_avg_score"       : dict[str, float],
                "strategy_details_per_iter": list[dict],
            }

        **grounding_analysis** - planner grounding source usage::

            {
                "grounding_counts_by_category" : dict[str, dict[str, int]],
                "grounding_counts_total"       : dict[str, int],
                "grounding_source_fractions"   : dict[str, float],
                "grounding_counts_per_iteration": list[dict],
            }

        **planning_focus** - how much attention each category received::

            {
                "category_action_counts": dict[str, int],
            }

        **score_trajectory** - per-iteration overall scores::

            [
                {
                    "iteration"              : int,
                    "score_before"           : float,
                    "score_after"            : float,
                    "score_delta"            : float,
                    "is_regression"          : bool,
                    "restored_from_iteration": int | None,
                },
                ...
            ]

        **category_score_trajectory** - per-category score per iteration::

            {
                "<category>": [
                    {
                        "iteration"   : int,
                        "score_before": float,
                        "score_after" : float,
                        "delta"       : float,
                    },
                    ...
                ],
                ...
            }

        **candidate_diversity** - score spread across candidates per
        iteration::

            {
                "per_iteration": [
                    {
                        "iteration"     : int,
                        "score_variance": float,
                        "score_std"     : float,
                        "score_range"   : float,
                        "n_evaluated"   : int,
                    },
                    ...
                ],
                "avg_score_variance": float,
            }

    Examples:

        >>> final_state = graph.invoke({...})
        >>> metrics = compute_metrics(final_state)
        >>> import json
        >>> print(json.dumps(metrics["run_summary"], indent=2))
        {
          "total_iterations": 12,
          "exit_reason": "compliant",
          "initial_score": 0.43,
          "best_score": 1.0,
          "total_score_improvement": 0.57,
          "is_compliant": true
        }
    """
    history = final_state.get("history") or []
    total_iterations: int = final_state.get("iteration", len(history))
    exit_reason: str | None = final_state.get("exit_reason")
    best_score: float = final_state.get("best_score", 0.0)
    is_compliant: bool = final_state.get("is_compliant", False)

    # Initial score of the very first iteration
    initial_score: float = (
        history[0]["summary"]["score_after"] if history else best_score
    )

    # Score trajectory
    score_trajectory = [
        {
            "iteration": r["summary"]["iteration"],
            "score_before": r["summary"]["score_before"],
            "score_after": r["summary"]["score_after"],
            "score_delta": r["summary"]["score_delta"],
            "is_regression": r["summary"]["is_regression"],
            "restored_from_iteration": (
                r["summary"]["restored_from_iteration"]
            ),
        }
        for r in history
    ]

    # Per-category score trajectory
    category_score_trajectory: dict[str, list[dict]] = {
        cat: [] for cat in _CATEGORIES
    }
    for r in history:
        deltas: dict[str, CategoryDelta] = (
            r["summary"].get("category_deltas") or {}
        )
        for cat in _CATEGORIES:
            entry: CategoryDelta | dict[str, float] = deltas.get(cat) or {}
            category_score_trajectory[cat].append(
                {
                    "iteration": r["summary"]["iteration"],
                    "score_before": entry.get("score_before", 0.0),
                    "score_after": entry.get("score_after", 0.0),
                    "delta": entry.get("delta", 0.0),
                }
            )

    # Convergence analysis
    score_deltas = [r["summary"]["score_delta"] for r in history]
    n_improvements = sum(1 for d in score_deltas if d > 0)
    n_regressions = sum(1 for d in score_deltas if d < 0)
    first_improvement_iteration: int | None = next(
        (
            r["summary"]["iteration"]
            for r in history
            if r["summary"]["score_delta"] > 0
        ),
        None,
    )
    improvement_rate = (
        n_improvements / total_iterations if total_iterations > 0 else 0.0
    )

    # Restore analysis
    restore_records = [
        r
        for r in history
        if r["summary"]["restored_from_iteration"] is not None
    ]
    restore_count = len(restore_records)
    restore_iterations = [r["summary"]["iteration"] for r in restore_records]
    restore_outcome_deltas = [
        r["summary"]["score_delta"] for r in restore_records
    ]
    avg_restore_outcome_delta: float | None = (
        sum(restore_outcome_deltas) / len(restore_outcome_deltas)
        if restore_outcome_deltas
        else None
    )

    # Strategy analysis
    strategy_selection_counts: dict[str, int] = {}
    strategy_win_counts: dict[str, int] = {}
    strategy_scores: dict[str, list[float]] = {}
    strategy_details_per_iter: list[dict] = []

    for r in history:
        summary = r["summary"]
        strategies: list[str] = summary.get("selected_strategies") or []
        candidate_scores: list = summary.get("candidate_scores") or []
        winning_strategy: str | None = summary.get("winning_strategy")

        for i, strat in enumerate(strategies):
            strategy_selection_counts[strat] = (
                strategy_selection_counts.get(strat, 0) + 1
            )
            if i < len(candidate_scores) and candidate_scores[i] is not None:
                strategy_scores.setdefault(strat, []).append(
                    candidate_scores[i]
                )

        if winning_strategy:
            strategy_win_counts[winning_strategy] = (
                strategy_win_counts.get(winning_strategy, 0) + 1
            )

        strategy_details_per_iter.append(
            {
                "iteration": summary["iteration"],
                "selected_strategies": strategies,
                "winning_strategy": winning_strategy,
                "candidate_scores": candidate_scores,
            }
        )

    strategy_win_rate: dict[str, float] = {
        strat: strategy_win_counts.get(strat, 0) / count
        for strat, count in strategy_selection_counts.items()
        if count > 0
    }
    strategy_avg_score: dict[str, float] = {
        strat: sum(scores) / len(scores)
        for strat, scores in strategy_scores.items()
        if scores
    }

    # Grounding source analysis
    grounding_counts_by_category: dict[str, dict[str, int]] = {
        cat: {src: 0 for src in _GROUNDING_SOURCES} for cat in _CATEGORIES
    }
    grounding_counts_per_iteration: list[dict] = []

    for r in history:
        iter_grounding = r["summary"].get("grounding_counts") or {}
        snapshot: dict[str, dict[str, int]] = {}
        for cat in _CATEGORIES:
            cat_counts = iter_grounding.get(cat) or {}
            snapshot[cat] = {
                src: cat_counts.get(src, 0) for src in _GROUNDING_SOURCES
            }
            for src in _GROUNDING_SOURCES:
                grounding_counts_by_category[cat][src] += snapshot[cat][src]
        grounding_counts_per_iteration.append(
            {
                "iteration": r["summary"]["iteration"],
                "counts": snapshot,
            }
        )

    grounding_counts_total: dict[str, int] = {
        src: 0 for src in _GROUNDING_SOURCES
    }
    for cat_counts in grounding_counts_by_category.values():
        for src, count in cat_counts.items():
            grounding_counts_total[src] += count

    total_actions_planned = sum(grounding_counts_total.values())
    grounding_source_fractions: dict[str, float] = {
        src: (
            count / total_actions_planned
            if total_actions_planned > 0
            else 0.0
        )
        for src, count in grounding_counts_total.items()
    }

    # Planning focus
    category_action_counts: dict[str, int] = {cat: 0 for cat in _CATEGORIES}
    for r in history:
        for cat, actions in (
            r["summary"].get("planner_actions") or {}
        ).items():
            if cat in category_action_counts:
                category_action_counts[cat] += len(actions)

    # Candidate diversity
    diversity_per_iter: list[dict] = []
    for r in history:
        scores = [
            s
            for s in (r["summary"].get("candidate_scores") or [])
            if s is not None
        ]
        n_eval = len(scores)
        if n_eval >= 2:
            mean = sum(scores) / n_eval
            variance = sum((s - mean) ** 2 for s in scores) / n_eval
            std = variance**0.5
            score_range = max(scores) - min(scores)
        else:
            variance = std = score_range = 0.0
        diversity_per_iter.append(
            {
                "iteration": r["summary"]["iteration"],
                "score_variance": round(variance, 8),
                "score_std": round(std, 8),
                "score_range": round(score_range, 6),
                "n_evaluated": n_eval,
            }
        )

    avg_score_variance = (
        sum(d["score_variance"] for d in diversity_per_iter)
        / len(diversity_per_iter)
        if diversity_per_iter
        else 0.0
    )

    # Assemble final output
    return {
        "run_summary": {
            "total_iterations": total_iterations,
            "exit_reason": exit_reason,
            "initial_score": round(initial_score, 6),
            "best_score": round(best_score, 6),
            "total_score_improvement": round(best_score - initial_score, 6),
            "is_compliant": is_compliant,
        },
        "convergence": {
            "n_improvements": n_improvements,
            "n_regressions": n_regressions,
            "first_improvement_iteration": first_improvement_iteration,
            "improvement_rate": round(improvement_rate, 4),
        },
        "restore_analysis": {
            "restore_count": restore_count,
            "restore_iterations": restore_iterations,
            "restore_outcome_deltas": restore_outcome_deltas,
            "avg_restore_outcome_delta": (
                round(avg_restore_outcome_delta, 6)
                if avg_restore_outcome_delta is not None
                else None
            ),
        },
        "strategy_analysis": {
            "strategy_selection_counts": strategy_selection_counts,
            "strategy_win_counts": strategy_win_counts,
            "strategy_win_rate": {
                k: round(v, 4) for k, v in strategy_win_rate.items()
            },
            "strategy_avg_score": {
                k: round(v, 6) for k, v in strategy_avg_score.items()
            },
            "strategy_details_per_iter": strategy_details_per_iter,
        },
        "grounding_analysis": {
            "grounding_counts_by_category": grounding_counts_by_category,
            "grounding_counts_total": grounding_counts_total,
            "grounding_source_fractions": {
                k: round(v, 4) for k, v in grounding_source_fractions.items()
            },
            "grounding_counts_per_iteration": (
                grounding_counts_per_iteration
            ),
        },
        "planning_focus": {
            "category_action_counts": category_action_counts,
        },
        "score_trajectory": score_trajectory,
        "category_score_trajectory": category_score_trajectory,
        "candidate_diversity": {
            "per_iteration": diversity_per_iter,
            "avg_score_variance": round(avg_score_variance, 8),
        },
    }
