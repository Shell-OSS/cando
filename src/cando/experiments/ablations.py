# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Centralized ablation-mode configuration for the CANDO component ablations.

Defines the AblationConfig structure and the resolved configuration for
each supported ablation mode. Every graph node consumes one resolved
AblationConfig rather than independently interpreting a mode string.
"""

from typing import Literal

from typing_extensions import TypedDict

AblationMode = Literal[
    "full",
    "no_historian",
    "no_visual",
    "no_strategies",
    "no_planner",
    "no_tool_verifier",
    "no_code",
    "no_icl",
]


class AblationConfig(TypedDict):
    mode: AblationMode
    historian_enabled: bool
    visual_context_enabled: bool
    strategy_mode: Literal["library", "neutral"]
    planner_mode: Literal["planner", "direct"]
    verifier_mode: Literal["tool", "model"]
    use_icl: bool
    n_branches_override: int | None


BASE: AblationConfig = {
    "mode": "full",
    "historian_enabled": True,
    "visual_context_enabled": True,
    "strategy_mode": "library",
    "planner_mode": "planner",
    "verifier_mode": "tool",
    "use_icl": True,
    "n_branches_override": None,
}


MODES: dict[str, AblationConfig] = {
    "full": {
        **BASE,
        "mode": "full",
    },
    "no_historian": {
        **BASE,
        "mode": "no_historian",
        "historian_enabled": False,
    },
    "no_visual": {
        **BASE,
        "mode": "no_visual",
        "visual_context_enabled": False,
    },
    "no_strategies": {
        **BASE,
        "mode": "no_strategies",
        "strategy_mode": "neutral",
    },
    "no_planner": {
        **BASE,
        "mode": "no_planner",
        "planner_mode": "direct",
    },
    "no_tool_verifier": {
        **BASE,
        "mode": "no_tool_verifier",
        "verifier_mode": "model",
    },
    "no_code": {
        **BASE,
        "mode": "no_code",
        "n_branches_override": 1,
    },
    "no_icl": {
        **BASE,
        "mode": "no_icl",
        "use_icl": False,
    },
}


def resolve_ablation(mode: str) -> AblationConfig:
    """Resolves an ablation mode name to its `AblationConfig`.

    Args:
        mode (str): One of the `AblationMode` literal values (e.g.
            `"full"`, `"no_planner"`).

    Returns:
        AblationConfig: A new copy of the resolved configuration
        (safe for the caller to store per-run without mutating `MODES`).

    Raises:
        ValueError: If `mode` is not a key in `MODES`.
    """
    try:
        return MODES[mode].copy()
    except KeyError as exc:
        raise ValueError(f"Unsupported ablation mode: {mode}") from exc
