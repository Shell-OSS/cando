# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Historian agent for generating per-category change notes.

Stateless agent used for history management.
"""

from langchain.messages import HumanMessage, SystemMessage

from cando.agents.verifiers.models import VerifierResult
from cando.config import ModelsConfig
from cando.graph.state import CategoryDelta
from cando.model_factory import resolve_chat_model

from .models import HistorianResult
from .prompt import HISTORIAN_SYSTEM_PROMPT

CATEGORIES = (
    "hard_constraints",
    "assembly",
    "proximity",
    "orientation",
    "flow",
)


class Historian:
    """Generates CategoryDelta objects for all five verification categories.

    Makes a single LLM call that produces one-sentence spatial-change notes
    for each category. Scores (score_before, score_after, delta) are
    derived deterministically from the supplied VerifierResult objects and
    never delegated to the LLM.

    This agent is stateless: all configuration is set in __init__ and nothing
    is mutated afterwards. It is safe to instantiate once as a module-level
    singleton and call concurrently from multiple graph.invoke() runs.
    """

    def __init__(self, model: str = ModelsConfig.historian):
        self.model = resolve_chat_model(
            "historian", model
        ).with_structured_output(HistorianResult)

    def generate(
        self,
        prev_verification: VerifierResult | None,
        verification: VerifierResult,
    ) -> dict[str, CategoryDelta]:
        """Generate CategoryDelta objects for all five categories.

        Makes a single LLM call to produce one-sentence notes, then combines
        them with deterministically-computed scores.

        Args:
            prev_verification (VerifierResult | None): The verification result
                from the previous iteration. None on the first iteration.
            verification (VerifierResult): The verification result for the
                current (winning) candidate.

        Returns:
            dict[str, CategoryDelta]: Mapping of category name to
                CategoryDelta. Keys: hard_constraints, assembly, proximity,
                orientation, flow.
        """
        user_message = self._build_message(prev_verification, verification)

        response = self.model.invoke(
            [
                SystemMessage(content=HISTORIAN_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ]
        )
        if not isinstance(response, HistorianResult):
            raise ValueError(
                f"Unexpected model output type: {type(response)}. "
                "Expected HistorianResult."
            )

        return self._assemble_deltas(
            prev_verification, verification, response
        )

    def _build_message(
        self,
        prev_verification: VerifierResult | None,
        verification: VerifierResult,
    ) -> str:
        """Build the user message for the single LLM call.

        Args:
            prev_verification (VerifierResult | None): Previous result or
                None.
            verification (VerifierResult): Current result.

        Returns:
            str: Formatted user message.
        """
        lines = []

        if prev_verification is None:
            lines.append(
                "# Previous Iteration\n"
                "This is the first iteration — "
                "no previous verification exists.\n"
                "Describe the initial state for each category.\n"
            )
        else:
            lines.append(
                "# Previous Iteration — Category Scores and Feedback\n"
            )
            for cat in CATEGORIES:
                worker = getattr(prev_verification.feedback, cat)
                lines.append(
                    f"## {cat}\n"
                    f"Score: {worker.score:.4f}\n"
                    f"Compliant: {worker.compliant}\n"
                    f"Feedback: {worker.feedback}\n"
                )

        lines.append("# Current Iteration — Category Scores and Feedback\n")
        for cat in CATEGORIES:
            worker = getattr(verification.feedback, cat)
            lines.append(
                f"## {cat}\n"
                f"Score: {worker.score:.4f}\n"
                f"Compliant: {worker.compliant}\n"
                f"Feedback: {worker.feedback}\n"
            )

        return "\n".join(lines)

    def _assemble_deltas(
        self,
        prev_verification: VerifierResult | None,
        verification: VerifierResult,
        notes: HistorianResult | None,
    ) -> dict[str, CategoryDelta]:
        """Combine deterministic scores with LLM-generated notes.

        Args:
            prev_verification (VerifierResult | None): Previous result or
                None.
            verification (VerifierResult): Current result.
            notes (HistorianResult): LLM-generated notes for each category.

        Returns:
            dict[str, CategoryDelta]: One CategoryDelta per category.
        """
        deltas: dict[str, CategoryDelta] = {}

        for cat in CATEGORIES:
            score_before = (
                getattr(prev_verification.feedback, cat).score
                if prev_verification is not None
                else 0.0
            )
            score_after = getattr(verification.feedback, cat).score
            note = getattr(notes, cat)

            deltas[cat] = CategoryDelta(
                score_before=score_before,
                score_after=score_after,
                delta=round(score_after - score_before, 2),
                note=note,
            )

        return deltas
