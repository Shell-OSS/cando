# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for src/cando/experiments/ablations.py."""

import pytest

from cando.experiments import ablations
from cando.experiments.ablations import MODES, resolve_ablation


@pytest.mark.parametrize("mode", sorted(MODES))
def test_resolve_ablation_known_modes_roundtrip(mode: str) -> None:
    """Every declared mode resolves and echoes its own name back."""
    config = resolve_ablation(mode)
    assert config["mode"] == mode


def test_resolve_ablation_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported ablation mode"):
        resolve_ablation("not_a_real_mode")


def test_resolve_ablation_returns_a_copy_not_shared_state() -> None:
    """Mutating a resolved config must not corrupt the shared MODES dict."""
    config = resolve_ablation("full")
    config["historian_enabled"] = False

    assert ablations.MODES["full"]["historian_enabled"] is True


@pytest.mark.parametrize(
    ("mode", "field", "expected"),
    [
        ("no_historian", "historian_enabled", False),
        ("no_visual", "visual_context_enabled", False),
        ("no_strategies", "strategy_mode", "neutral"),
        ("no_planner", "planner_mode", "direct"),
        ("no_tool_verifier", "verifier_mode", "model"),
        ("no_code", "n_branches_override", 1),
        ("no_icl", "use_icl", False),
    ],
)
def test_resolve_ablation_overrides_only_its_own_field(
    mode: str, field: str, expected: object
) -> None:
    config = resolve_ablation(mode)
    assert config[field] == expected


def test_full_mode_matches_base_defaults() -> None:
    full = resolve_ablation("full")
    base = resolve_ablation("full")  # fresh copy for comparison
    base["mode"] = "full"
    assert full == base
    assert full["historian_enabled"] is True
    assert full["visual_context_enabled"] is True
    assert full["strategy_mode"] == "library"
    assert full["planner_mode"] == "planner"
    assert full["verifier_mode"] == "tool"
    assert full["use_icl"] is True
    assert full["n_branches_override"] is None
