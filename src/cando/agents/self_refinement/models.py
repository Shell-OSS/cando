# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Data models for the SelfRefinementAgent.

Pydantic models for self-refinement agent responses.
"""

from pydantic import BaseModel, Field


class IterationRecord(BaseModel):
    """Record for a single self-refinement iteration."""

    iteration: int
    response_type: str
    reasoning: str
    score_before: float
    score_after: float
    compliant_after: bool
    verifier_scores: dict
    layout_snapshot: dict = Field(
        default_factory=dict,
        description=(
            "Deep copy of the layout at the end of this iteration "
            "(after applying the LLM response and verifying)."
        ),
    )


class SelfRefinementResult(BaseModel):
    """Final result returned by SelfRefinementAgent.run()."""

    layout: dict = Field(description="Best layout found (polygon format)")
    constraints: dict = Field(description="Parsed constraints dict")
    score: float = Field(description="Verifier score of the best layout")
    compliant: bool = Field(
        description="True if the best layout is fully compliant"
    )
    iterations_run: int = Field(
        description="Number of refinement iterations executed"
    )
    history: list[IterationRecord] = Field(
        default_factory=list,
        description="Per-iteration records",
    )
