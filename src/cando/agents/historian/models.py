# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Data models for historian results.

Pydantic output model for the Historian agent's single LLM call. The
LLM fills in one-sentence notes per category; scores are computed
deterministically from VerifierResult objects and attached by the agent
before returning CategoryDelta dicts.
"""

from pydantic import BaseModel, Field


class HistorianResult(BaseModel):
    """Structured LLM output: one short note per verification category.

    The Historian agent uses this as the structured response format.
    Scores (score_before, score_after, delta) are filled in deterministically
    by the agent; only the natural-language notes come from the LLM.
    """

    hard_constraints: str = Field(
        description=(
            "Concise description of all hard-constraints changes "
            "between the previous and current iteration. Cover "
            "every violation that was resolved or introduced "
            "(overlaps, boundary violations, count mismatches). "
            "If this is the first iteration, describe the "
            "initial state instead. Be specific: name element "
            "IDs or counts where possible. Use as many "
            "sentences as needed, but no more."
        )
    )

    assembly: str = Field(
        description=(
            "Concise description of all assembly changes between "
            "the previous and current iteration. Cover "
            "every grouping improvement or regression "
            "(fragmentation, clustering, class separation). "
            "If this is the first iteration, describe "
            "the initial grouping state instead. "
            "Name specific element classes or IDs. "
            "Use as many sentences as needed, but no more."
        )
    )

    proximity: str = Field(
        description=(
            "Concise description of all proximity changes between "
            "the previous and current iteration. Cover "
            "every stacking, adjacency, or distance rule "
            "that was fixed or broken. "
            "If this is the first iteration, describe "
            "the initial proximity state instead. Mention "
            "relevant element pairs or distances. Use "
            "as many sentences as needed, but no more."
        )
    )

    orientation: str = Field(
        description=(
            "Concise description of all orientation changes between "
            "the previous and current iteration. Cover "
            "every rotation fix or regression. "
            "If this is the first iteration, describe "
            "the initial orientation state instead. "
            "Name specific elements with rotation issues "
            "where available. Use as many sentences as needed, "
            "but no more."
        )
    )

    flow: str = Field(
        description=(
            "Concise description of all flow changes between "
            "the previous and current iteration. Cover "
            "every routing improvement or regression "
            "(path length, blocked routes, entrance-to-exit "
            "connectivity). If this is the first iteration, "
            "describe the initial routing state instead. "
            "Use as many sentences as needed, but no more."
        )
    )
