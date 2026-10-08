# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for deterministic (non-LLM) graph node logic.

Covers snapshot_node / fan_out_node (fully pure), the pure helper
functions inside generate.py / build_history.py, reduce_node (pure --
operates only on already-computed branch state dicts), and verify_node /
restore_node with the Verifier / plot_layout calls mocked out.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cando.agents.planners.models import (
    PlannerAction,
    PlannerResult,
    PlannerWorkerResult,
)
from cando.graph.nodes.build_history import (
    CATEGORIES,
    _build_deltas_without_historian,
    _extract_grounding_counts,
    _extract_planner_actions,
)
from cando.graph.nodes.fan_out import fan_out_node, snapshot_node
from cando.graph.nodes.generate import _derive_seed
from cando.graph.nodes.reduce import reduce_node

# ---------------------------------------------------------------------------
# snapshot_node / fan_out_node
# ---------------------------------------------------------------------------


def test_snapshot_node_captures_pre_fanout_state() -> None:
    layout = {"foo": 1}
    state = {"best_layout": layout, "best_score": 0.7, "current_round": 2}

    result = snapshot_node(state)

    assert result == {
        "pre_fanout_layout": {"foo": 1},
        "pre_fanout_score": 0.7,
        "current_round": 3,
    }
    assert result["pre_fanout_layout"] is not layout  # deep copy


def test_snapshot_node_defaults_when_state_is_empty() -> None:
    result = snapshot_node({})
    assert result == {
        "pre_fanout_layout": None,
        "pre_fanout_score": 0.0,
        "current_round": 1,
    }


def _fan_out_state(**overrides) -> dict:
    state = {
        "n_branches": 3,
        "branch_steps": 5,
        "local_patience": 2,
        "iteration": 4,
        "max_iterations": 20,
    }
    state.update(overrides)
    return state


def test_fan_out_node_dispatches_one_send_per_branch() -> None:
    sends = fan_out_node(_fan_out_state())

    assert len(sends) == 3
    for i, send in enumerate(sends):
        assert send.node == "branch_refinement_node"
        assert send.arg["branch_id"] == i
        assert send.arg["patience"] == 2
        assert send.arg["max_iterations"] == 10  # min(4+5+1, 20)
        assert send.arg["iterations_without_improvement"] == 0
        assert send.arg["should_restore"] is False
        assert send.arg["exit_reason"] is None
        assert send.arg["restored_from_iteration"] is None
        assert send.arg["branch_states"] == []
        assert send.arg["completed_branch_states"] == []


def test_fan_out_node_caps_max_iterations_at_global_budget() -> None:
    state = _fan_out_state(iteration=18, branch_steps=5, max_iterations=20)
    sends = fan_out_node(state)
    assert all(send.arg["max_iterations"] == 20 for send in sends)


def test_fan_out_node_deep_copies_per_branch_state() -> None:
    state = _fan_out_state(n_branches=2)
    state["candidates"] = [{"nested": [1, 2, 3]}]

    sends = fan_out_node(state)
    sends[0].arg["candidates"][0]["nested"].append(4)

    assert state["candidates"][0]["nested"] == [1, 2, 3]
    assert sends[1].arg["candidates"][0]["nested"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# _derive_seed
# ---------------------------------------------------------------------------


def test_derive_seed_is_deterministic() -> None:
    assert _derive_seed("a", 1, "b") == _derive_seed("a", 1, "b")


def test_derive_seed_differs_for_different_inputs() -> None:
    assert _derive_seed("a", 1) != _derive_seed("a", 2)


def test_derive_seed_within_32_bit_range() -> None:
    seed = _derive_seed("iteration", 42, "branch", 3)
    assert 0 <= seed < 2**32


# ---------------------------------------------------------------------------
# build_history helpers
# ---------------------------------------------------------------------------


def _worker_result(score: float) -> SimpleNamespace:
    return SimpleNamespace(score=score)


def _verification(scores: dict) -> SimpleNamespace:
    return SimpleNamespace(
        feedback=SimpleNamespace(
            **{cat: _worker_result(scores[cat]) for cat in CATEGORIES}
        )
    )


def test_build_deltas_without_historian_first_iteration_has_zero_before() -> (
    None
):
    verification = _verification({cat: 0.5 for cat in CATEGORIES})

    deltas = _build_deltas_without_historian(None, verification)

    for cat in CATEGORIES:
        assert deltas[cat]["score_before"] == pytest.approx(0.0)
        assert deltas[cat]["score_after"] == pytest.approx(0.5)
        assert deltas[cat]["delta"] == pytest.approx(0.5)
        assert deltas[cat]["note"] == ""


def test_build_deltas_without_historian_computes_deltas() -> None:
    prev = _verification({cat: 0.4 for cat in CATEGORIES})
    current = dict.fromkeys(CATEGORIES, 0.4)
    current["assembly"] = 0.7
    current_verification = _verification(current)

    deltas = _build_deltas_without_historian(prev, current_verification)

    assert deltas["assembly"]["score_before"] == pytest.approx(0.4)
    assert deltas["assembly"]["score_after"] == pytest.approx(0.7)
    assert deltas["assembly"]["delta"] == pytest.approx(0.3)
    assert deltas["flow"]["delta"] == pytest.approx(0.0)


def test_extract_grounding_counts_none_plan_returns_all_zero() -> None:
    counts = _extract_grounding_counts(None)
    assert set(counts) == set(CATEGORIES)
    for cat in CATEGORIES:
        assert counts[cat] == {
            "visual": 0,
            "worker": 0,
            "history": 0,
            "layout": 0,
        }


def _make_planner_result(**category_actions) -> PlannerResult:
    kwargs = {}
    for cat in CATEGORIES:
        actions = category_actions.get(cat, [])
        kwargs[cat] = PlannerWorkerResult(
            actions=[
                PlannerAction(action=a, steps=[], grounding=g)
                for a, g in actions
            ]
        )
    return PlannerResult(**kwargs)


def test_extract_grounding_counts_tallies_by_category_and_source() -> None:
    plan = _make_planner_result(
        assembly=[("cluster A/B", "worker"), ("cluster C/D", "worker")],
        hard_constraints=[("move E away from wall", "visual")],
    )

    counts = _extract_grounding_counts(plan)

    assert counts["assembly"]["worker"] == 2
    assert counts["hard_constraints"]["visual"] == 1
    assert counts["hard_constraints"]["worker"] == 0
    assert counts["proximity"] == {
        "visual": 0,
        "worker": 0,
        "history": 0,
        "layout": 0,
    }


def test_extract_planner_actions_none_plan_returns_empty_lists() -> None:
    assert _extract_planner_actions(None) == {cat: [] for cat in CATEGORIES}


def test_extract_planner_actions_extracts_top_level_actions_only() -> None:
    plan = _make_planner_result(
        assembly=[("cluster A/B", "worker")],
    )
    actions = _extract_planner_actions(plan)
    assert actions["assembly"] == ["cluster A/B"]
    assert actions["flow"] == []


# ---------------------------------------------------------------------------
# reduce_node (pure: operates only on already-computed branch state dicts)
# ---------------------------------------------------------------------------


def _branch(
    branch_id: int,
    best_score: float,
    iteration: int,
    exit_reason: str | None = None,
    **overrides,
) -> dict:
    branch = {
        "branch_id": branch_id,
        "best_score": best_score,
        "best_layout": {"branch": branch_id},
        "iteration": iteration,
        "exit_reason": exit_reason,
        "branch_failed": False,
    }
    branch.update(overrides)
    return branch


def _reduce_state(completed_branch_states, **overrides) -> dict:
    state = {
        "completed_branch_states": completed_branch_states,
        "branch_steps": 5,
        "pre_fanout_score": 0.0,
        "pre_fanout_layout": {"pre": "fanout"},
        "plateau_patience": 6,
        "max_iterations": 20,
        "iteration": 0,
        "iterations_since_best_changed": 0,
        "current_round": 1,
        "log_dir": None,
    }
    state.update(overrides)
    return state


def test_reduce_node_selects_highest_scoring_branch() -> None:
    branches = [
        _branch(0, best_score=0.4, iteration=5),
        _branch(1, best_score=0.9, iteration=5),
        _branch(2, best_score=0.6, iteration=5),
    ]
    result = reduce_node(_reduce_state(branches))
    assert result["best_score"] == pytest.approx(0.9)
    assert result["best_layout"] == {"branch": 1}


def test_reduce_node_breaks_ties_by_fewest_iterations() -> None:
    branches = [
        _branch(0, best_score=0.8, iteration=5),
        _branch(1, best_score=0.8, iteration=2),
    ]
    result = reduce_node(_reduce_state(branches))
    assert result["best_layout"] == {"branch": 1}


def test_reduce_node_restores_pre_fanout_layout_when_no_improvement() -> None:
    branches = [_branch(0, best_score=0.2, iteration=5)]
    result = reduce_node(
        _reduce_state(
            branches,
            pre_fanout_score=0.5,
            pre_fanout_layout={"pre": "fanout"},
        )
    )
    assert result["best_score"] == pytest.approx(0.5)
    assert result["best_layout"] == {"pre": "fanout"}
    assert result["current_layout"] == {"pre": "fanout"}


def test_reduce_node_prefers_compliant_branch_even_if_not_highest_score() -> (
    None
):
    branches = [
        _branch(0, best_score=0.95, iteration=5),
        _branch(1, best_score=0.7, iteration=5, exit_reason="compliant"),
    ]
    result = reduce_node(_reduce_state(branches))
    assert result["exit_reason"] == "compliant"
    assert result["best_layout"] == {"branch": 1}
    assert result["is_compliant"] is True


def test_reduce_node_sets_max_iterations_exit_reason() -> None:
    branches = [_branch(0, best_score=0.9, iteration=20)]
    result = reduce_node(_reduce_state(branches, max_iterations=20))
    assert result["exit_reason"] == "max_iterations"


def test_reduce_node_sets_plateau_exit_reason() -> None:
    branches = [_branch(0, best_score=0.1, iteration=5)]
    result = reduce_node(
        _reduce_state(
            branches,
            pre_fanout_score=0.5,
            plateau_patience=1,
            iterations_since_best_changed=0,
        )
    )
    assert result["exit_reason"] == "plateau"


def test_reduce_node_excludes_failed_branches() -> None:
    branches = [
        _branch(0, best_score=0.9, iteration=5, branch_failed=True),
        _branch(1, best_score=0.3, iteration=5),
    ]
    result = reduce_node(_reduce_state(branches))
    assert result["best_layout"] == {"branch": 1}


def test_reduce_node_raises_when_all_branches_failed() -> None:
    branches = [_branch(0, best_score=0.9, iteration=5, branch_failed=True)]
    with pytest.raises(RuntimeError, match="all 1 branch"):
        reduce_node(_reduce_state(branches))


def test_reduce_node_resets_completed_branch_states_to_none() -> None:
    branches = [_branch(0, best_score=0.5, iteration=5)]
    result = reduce_node(_reduce_state(branches))
    assert result["completed_branch_states"] is None


# ---------------------------------------------------------------------------
# verify_node / restore_node (Verifier + plot_layout mocked)
# ---------------------------------------------------------------------------


def _verify_state(**overrides) -> dict:
    state = {
        "iteration": 0,
        "candidates": [{"id": "layout-0"}],
        "action_sequences": [],
        "constraints": {},
        "weights": None,
        "patience": 2,
        "plateau_patience": 6,
        "max_iterations": 10,
        "ablation": {"verifier_mode": "tool"},
    }
    state.update(overrides)
    return state


def test_verify_node_marks_compliant_exit_reason() -> None:
    from cando.graph.nodes import verify as verify_module

    mock_result = SimpleNamespace(score=1.0, compliant=True)
    mock_verifier = SimpleNamespace(
        evaluate=lambda *a, **k: ((0, mock_result), [(0, mock_result)])
    )

    with (
        patch.object(
            verify_module, "_get_tool_verifier", return_value=mock_verifier
        ),
        patch.object(verify_module, "plot_layout", return_value=None),
    ):
        result = verify_module.verify_node(_verify_state())

    assert result["exit_reason"] == "compliant"
    assert result["is_compliant"] is True
    assert result["best_score"] == pytest.approx(1.0)


def test_verify_node_triggers_restore_after_patience_exhausted() -> None:
    from cando.graph.nodes import verify as verify_module

    mock_result = SimpleNamespace(score=0.1, compliant=False)
    mock_verifier = SimpleNamespace(
        evaluate=lambda *a, **k: ((0, mock_result), [(0, mock_result)])
    )

    state = _verify_state(
        best_score=0.5,
        iterations_without_improvement=1,
        patience=2,
    )

    with (
        patch.object(
            verify_module, "_get_tool_verifier", return_value=mock_verifier
        ),
        patch.object(verify_module, "plot_layout", return_value=None),
    ):
        result = verify_module.verify_node(state)

    assert result["should_restore"] is True
    assert result["iterations_without_improvement"] == 2
    # A non-improving, non-compliant candidate must not clear best_score.
    assert result["best_score"] == pytest.approx(0.5)


def test_restore_node_resets_current_layout_and_patience() -> None:
    from cando.graph.nodes import restore as restore_module

    state = {
        "best_layout": {"restored": True},
        "best_layout_iteration": 3,
    }
    with patch.object(restore_module, "plot_layout", return_value=None):
        result = restore_module.restore_node(state)

    assert result["current_layout"] == {"restored": True}
    assert result["current_layout"] is not state["best_layout"]
    assert result["iterations_without_improvement"] == 0
    assert result["should_restore"] is False
    assert result["restored_from_iteration"] == 3
