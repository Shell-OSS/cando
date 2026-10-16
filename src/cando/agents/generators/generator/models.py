# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Data models for the Generator agent.

Pydantic models for generator actions and results.
"""

from typing import Literal

from pydantic import BaseModel, Field


class StrategyConfig(BaseModel):
    """Configuration for a single generation strategy."""

    key: str
    guidance_mode: Literal[
        "all",
        "hard_constraints",
        "assembly",
        "proximity",
        "orientation",
        "flow",
        "none",
    ]


class MoveAction(BaseModel):
    """Action to translate an element by a (dx, dy) offset."""

    type: Literal["move"] = "move"
    element_id: int = Field(description="ID of the element to move")
    dx: float = Field(
        description=(
            "Translation along the x-axis in meters "
            "(positive = right, negative = left)"
        )
    )
    dy: float = Field(
        description=(
            "Translation along the y-axis in meters "
            "(positive = up, negative = down)"
        )
    )


class RotateAction(BaseModel):
    """Action to rotate an element by a specified angle."""

    type: Literal["rotate"] = "rotate"
    element_id: int = Field(description="ID of the element to rotate")
    degrees: float = Field(
        description=(
            "Rotation angle in degrees (clockwise from current orientation)"
        )
    )


class StackAction(BaseModel):
    """Action to stack an element against a target side or exact edge."""

    type: Literal["stack"] = "stack"
    element_id: int = Field(description="ID of the element to position")
    target_element_id: int = Field(
        description="ID of the element to stack against"
    )
    side: Literal["top", "bottom", "left", "right"] | None = Field(
        default=None,
        description=(
            "Optional coarse side label of the target element to stack "
            "against. Use target_edge_index when the planner specifies an "
            "exact polygon edge."
        ),
    )
    target_edge_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional exact target polygon edge index. When provided, the "
            "stack action uses that concrete edge instead of selecting by "
            "side."
        ),
    )
    source_edge: Literal["longer", "shorter", "any"] = Field(
        default="any",
        description=(
            "Which edge of the source element to align with target: "
            "'longer' uses the longer edge, 'shorter' uses the shorter edge, "
            "'any' lets the system decide based on the target edge"
        ),
    )
    distance: float = Field(
        description="Target distance between elements in meters", ge=0.0
    )
    location: Literal["center", "available_space", "exact_location"] = Field(
        default="center",
        description=(
            "Positioning mode along the target edge: center, first "
            "available space, or explicit ratio"
        ),
    )
    location_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Edge ratio (0-1) when location is exact_location; 0 = start of "
            "edge, 1 = end of edge"
        ),
    )


class StackGroupAction(BaseModel):
    """Action to stack multiple elements as one group along a target edge."""

    type: Literal["stack_group"] = "stack_group"
    element_ids: list[int] = Field(
        description="Ordered list of element IDs to stack as a group"
    )
    target_element_id: int = Field(
        description="ID of the element to stack the group against"
    )
    side: Literal["top", "bottom", "left", "right"] | None = Field(
        default=None,
        description=(
            "Optional coarse side label of the target element to stack "
            "against. Use target_edge_index when the planner specifies an "
            "exact polygon edge."
        ),
    )
    target_edge_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional exact target polygon edge index. When provided, the "
            "stack_group action uses that concrete edge instead of selecting "
            "by side."
        ),
    )
    source_edge: Literal["longer", "shorter", "any"] = Field(
        default="any",
        description=(
            "Which edge of the source elements to align with target: "
            "'longer' uses the longer edge, 'shorter' uses the shorter edge, "
            "'any' lets the system decide based on the target edge"
        ),
    )
    distance: float = Field(
        description="Target distance between elements in meters", ge=0.0
    )
    spacing: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Gap in meters between the closest two points of each pair "
            "of consecutive elements in the group."
        ),
    )
    location: Literal["center", "available_space", "exact_location"] = Field(
        default="center",
        description=("Positioning mode for the group along the target edge"),
    )
    location_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Edge ratio (0-1) when location is exact_location; applied to "
            "group center"
        ),
    )


class AlignAction(BaseModel):
    """Action to rotate an element so its edges become parallel to
    target's."""

    type: Literal["align"] = "align"
    element_id: int = Field(
        description="ID of the element to rotate (source)"
    )
    target_element_id: int = Field(
        description="ID of the element to align with (target)"
    )
    edge: Literal["shorter", "longer"] = Field(
        description=("Which edge type of the source element to align")
    )
    target_edge: Literal["shorter", "longer"] | None = Field(
        default=None,
        description=(
            "Optional edge type of the target element to align to. "
            "If None, the target's same edge type as `edge` is used"
        ),
    )


class ClusterAction(BaseModel):
    """Action to arrange multiple elements in a cluster formation."""

    type: Literal["cluster"] = "cluster"
    element_ids: list[int] = Field(
        description="List of element IDs to cluster together"
    )
    formation: Literal["grid", "line"] = Field(
        description="Clustering formation type"
    )
    edge: Literal["shorter", "longer"] = Field(
        description=(
            "The neighboring edge type used to orient the cluster. "
            "For 'line': each element's selected edge faces the next "
            "element; elements are first aligned by this edge type and "
            "then arranged along the direction perpendicular to it. "
            "For 'grid': the selected edge faces neighbors in the row "
            "direction; rows are separated perpendicular to this edge "
            "and columns run along it."
        )
    )
    spacing: float = Field(
        description=(
            "Gap in meters between the closest two points of each pair "
            "of consecutive / neighboring elements."
        ),
        ge=0.0,
    )
    rows: int | None = Field(
        default=None,
        description=(
            "Number of rows for grid formation (first dimension). "
            "Required when formation='grid'. rows * cols must be >= "
            "len(element_ids)."
        ),
        ge=1,
    )
    cols: int | None = Field(
        default=None,
        description=(
            "Number of columns for grid formation (second dimension). "
            "Required when formation='grid'. rows * cols must be >= "
            "len(element_ids)."
        ),
        ge=1,
    )


class AlignGroupAction(BaseModel):
    """Action to align all elements in a group to parallel orientations."""

    type: Literal["align_group"] = "align_group"
    element_ids: list[int] = Field(
        description="List of element IDs in the group to align"
    )
    edge: Literal["shorter", "longer"] = Field(
        description=(
            "Which edge type of the source elements to align - each "
            "element is rotated so its selected edges become parallel to "
            "the target's (or to the group median) reference edges"
        )
    )
    target_element_id: int | None = Field(
        default=None,
        description=(
            "Optional ID of a target element to align all elements to. "
            "If None, elements are aligned with each other using a "
            "median reference angle computed from the group"
        ),
    )
    target_edge: Literal["shorter", "longer"] | None = Field(
        default=None,
        description=(
            "Optional edge type of the target element to align to. "
            "If None, the target's same edge type as `edge` is used. "
            "Only relevant when target_element_id is provided"
        ),
    )


class MoveGroupAction(BaseModel):
    """Action to translate all elements in a group by the same offset."""

    type: Literal["move_group"] = "move_group"
    element_ids: list[int] = Field(
        description="List of element IDs to move together as a group"
    )
    dx: float = Field(
        description=(
            "Translation along the x-axis in meters "
            "(positive = right, negative = left)"
        )
    )
    dy: float = Field(
        description=(
            "Translation along the y-axis in meters "
            "(positive = up, negative = down)"
        )
    )


class RotateGroupAction(BaseModel):
    """Action to rotate all elements in a group by the same angle."""

    type: Literal["rotate_group"] = "rotate_group"
    element_ids: list[int] = Field(
        description="List of element IDs to rotate together as a group"
    )
    degrees: float = Field(
        description=(
            "Rotation angle in degrees applied to every element "
            "(clockwise from current orientation)"
        )
    )
    pivot: Literal["group_centroid", "individual"] = Field(
        default="individual",
        description=(
            "Pivot point for rotation: "
            "'group_centroid' rotates all elements around the shared "
            "centroid of the group (preserves relative positions), "
            "'individual' rotates each element around its own centroid "
            "(preserves individual positions, changes only orientation)"
        ),
    )


Action = (
    MoveAction
    | RotateAction
    | StackAction
    | StackGroupAction
    | AlignAction
    | ClusterAction
    | AlignGroupAction
    | MoveGroupAction
    | RotateGroupAction
)


class ActionSequence(BaseModel):
    """A sequence of actions to be executed together."""

    actions: list[Action] = Field(
        description="Ordered list of actions to execute"
    )
    rationale: str = Field(
        description=(
            "Brief explanation of the strategy and expected improvements"
        )
    )
    priority: Literal["high", "medium", "low"] = Field(
        default="medium", description="Priority level of this sequence"
    )
