# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for src/cando/agents/generators/generator/models.py."""

import pytest
from pydantic import ValidationError

from cando.agents.generators.generator.models import (
    ActionSequence,
    MoveAction,
    RotateAction,
    StackAction,
    StrategyConfig,
)


def test_move_action_defaults_type_field() -> None:
    action = MoveAction(element_id=3, dx=1.5, dy=-2.0)
    assert action.type == "move"
    assert action.element_id == 3


def test_rotate_action_defaults_type_field() -> None:
    action = RotateAction(element_id=2, degrees=90.0)
    assert action.type == "rotate"


def test_stack_action_location_ratio_bounds() -> None:
    with pytest.raises(ValidationError):
        StackAction(
            element_id=1,
            target_element_id=2,
            distance=0.5,
            location_ratio=1.5,
        )


def test_stack_action_valid_location_ratio() -> None:
    action = StackAction(
        element_id=1,
        target_element_id=2,
        side="top",
        distance=0.5,
        location_ratio=0.5,
    )
    assert action.type == "stack"
    assert action.location_ratio == pytest.approx(0.5)


def test_strategy_config_rejects_unknown_guidance_mode() -> None:
    with pytest.raises(ValidationError):
        StrategyConfig(key="custom", guidance_mode="everything")


def test_strategy_config_accepts_valid_guidance_mode() -> None:
    config = StrategyConfig(key="conservative", guidance_mode="all")
    assert config.guidance_mode == "all"


def test_action_sequence_accepts_mixed_action_types() -> None:
    sequence = ActionSequence(
        actions=[
            MoveAction(element_id=1, dx=1.0, dy=1.0),
            RotateAction(element_id=2, degrees=45.0),
        ],
        rationale="Reposition and rotate to improve clearance.",
    )
    assert len(sequence.actions) == 2
    assert sequence.priority == "medium"


def test_action_sequence_rejects_invalid_priority() -> None:
    with pytest.raises(ValidationError):
        ActionSequence(
            actions=[MoveAction(element_id=1, dx=1.0, dy=1.0)],
            rationale="x",
            priority="urgent",
        )
