# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Data models for planner results."""

from typing import Literal

from pydantic import BaseModel, Field

ActionGrounding = Literal["visual", "worker", "history", "layout"]
""" Primary information source that most influenced a planner action.

- "visual"   : the layout image (spatial analysis, side selection, occupancy).
- "worker"   : a worker planner suggestion (adopted or modified).
- "history"  : past-iteration outcomes (avoiding regressions,
               preserving gains).
- "layout"   : layout_data (element geometry, coordinates,
               spatial relationships).
"""


class PlannerAction(BaseModel):
    """Planner suggested action for layout refinement."""

    action: str = Field(
        description="High-level action suggested by the worker."
    )

    steps: list[str] = Field(
        description="List of steps to be taken for layout refinement."
    )

    grounding: ActionGrounding = Field(
        description=(
            "Primary information source that most influenced this action."
            " One of: 'visual' (layout image spatial analysis), 'worker'"
            " (worker planner suggestion), 'history' (past-iteration"
            " outcomes), 'layout' (layout_data element geometry and"
            " spatial relationships)."
        )
    )


class PlannerWorkerResult(BaseModel):
    """Planner worker result of suggested actions for layout refinement."""

    actions: list[PlannerAction] = Field(
        description="Actions suggested by the worker for layout refinement."
    )


class PlannerResult(BaseModel):
    """Result of overall layout refinement planning."""

    assembly: PlannerWorkerResult = Field(
        description=("Plan for assembly-related issues refinement.")
    )

    hard_constraints: PlannerWorkerResult = Field(
        description=("Plan for hard-constraints-related issues refinement.")
    )

    orientation: PlannerWorkerResult = Field(
        description=("Plan for orientation-related issues refinement.")
    )

    proximity: PlannerWorkerResult = Field(
        description=("Plan for proximity-related issues refinement.")
    )

    flow: PlannerWorkerResult = Field(
        description=("Plan for flow-related issues refinement.")
    )
