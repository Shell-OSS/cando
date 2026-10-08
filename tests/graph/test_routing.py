# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for conditional routing functions across the CANDO graph.

These are pure functions of `CandoState` with no LLM calls, so they can
be exercised directly with minimal hand-built state dicts.
"""

from cando.experiments.ablations import resolve_ablation
from cando.graph.graph import route_after_history
from cando.graph.nodes.reduce import route_after_reduce
from cando.graph.nodes.verify import route_after_verify


class TestRouteAfterVerify:
    def test_exit_reason_set_routes_to_end(self) -> None:
        assert route_after_verify({"exit_reason": "compliant"}) == "end"

    def test_should_restore_routes_to_restore(self) -> None:
        state = {"exit_reason": None, "should_restore": True}
        assert route_after_verify(state) == "restore"

    def test_default_routes_to_build_history(self) -> None:
        state = {"exit_reason": None, "should_restore": False}
        assert route_after_verify(state) == "build_history"

    def test_exit_reason_takes_priority_over_should_restore(self) -> None:
        state = {"exit_reason": "max_iterations", "should_restore": True}
        assert route_after_verify(state) == "end"


class TestRouteAfterReduce:
    def test_exit_reason_set_routes_to_end(self) -> None:
        assert route_after_reduce({"exit_reason": "plateau"}) == "end"

    def test_no_exit_reason_routes_to_snapshot(self) -> None:
        assert route_after_reduce({"exit_reason": None}) == "snapshot"

    def test_missing_exit_reason_key_routes_to_snapshot(self) -> None:
        assert route_after_reduce({}) == "snapshot"


class TestRouteAfterHistory:
    def test_exit_reason_set_routes_to_end(self) -> None:
        state = {
            "exit_reason": "compliant",
            "ablation": resolve_ablation("full"),
        }
        assert route_after_history(state) == "end"

    def test_full_mode_routes_to_plan(self) -> None:
        state = {"exit_reason": None, "ablation": resolve_ablation("full")}
        assert route_after_history(state) == "plan"

    def test_no_planner_mode_routes_to_generate(self) -> None:
        state = {
            "exit_reason": None,
            "ablation": resolve_ablation("no_planner"),
        }
        assert route_after_history(state) == "generate"
