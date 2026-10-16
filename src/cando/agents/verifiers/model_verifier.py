# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Model-based Verifier for the no_tool_verifier ablation mode.

Replaces in-loop deterministic verification with one model-based verifier
that judges compliance directly from the candidate layout JSON,
constraints, and a rendered image -- without any pre-computed overlaps,
distances, groups, or pathfinding results. Matches the interface of
cando.agents.verifiers.agent.Verifier.evaluate() so verify_node can select
between them transparently.
"""

import concurrent.futures
from typing import Any

from pydantic import BaseModel, Field

from cando.config import ModelsConfig
from cando.model_factory import resolve_chat_model
from cando.utils.generic import json_dumps, round_floats
from cando.utils.visualizer import plot_layout

from .model_prompt import MODEL_VERIFIER_SYSTEM_PROMPT
from .models import Feedback, VerifierResult, WorkerResult

CATEGORIES = (
    "hard_constraints",
    "assembly",
    "orientation",
    "proximity",
    "flow",
)


class ModelCategoryResult(BaseModel):
    compliant: bool
    score: float = Field(ge=0.0, le=1.0)
    feedback: str


class ModelVerifierOutput(BaseModel):
    hard_constraints: ModelCategoryResult
    assembly: ModelCategoryResult
    orientation: ModelCategoryResult
    proximity: ModelCategoryResult
    flow: ModelCategoryResult


class ModelVerifier:
    """Model-based replacement for the deterministic, tool-based Verifier.

    Used only in-loop by the no_tool_verifier ablation mode. Final saved
    layouts are still scored by the deterministic evaluation pipeline under
    src/cando/evaluation/ so that scores stay comparable across modes.
    """

    def __init__(self, model: str = ModelsConfig.model_verifier):
        self.model = resolve_chat_model(
            "model_verifier", model
        ).with_structured_output(ModelVerifierOutput)

    def evaluate_single(
        self,
        layout_data: dict,
        constraints: dict,
        weights: dict | None = None,
    ) -> VerifierResult:
        """Evaluates a single candidate layout via one structured model call.

        Args:
            layout_data (dict): The candidate layout to evaluate.
            constraints (dict): The constraints dictionary to validate
                against.
            weights (dict | None): Unused; kept for interface parity with
                Verifier.evaluate_single().

        Returns:
            VerifierResult: The aggregated evaluation result.
        """
        image_b64 = plot_layout(layout_data, show=False, return_base64=True)

        text_body = (
            f"# Candidate Layout:\n"
            f"{json_dumps(round_floats(layout_data))}\n\n"
            f"# Constraints:\n{json_dumps(round_floats(constraints))}\n\n"
            "Evaluate this candidate and return the structured "
            "ModelVerifierOutput."
        )

        if image_b64:
            content: str | list[dict[str, Any]] = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_b64}"
                    },
                },
                {"type": "text", "text": text_body},
            ]
        else:
            content = text_body

        output = ModelVerifierOutput.model_validate(
            self.model.invoke(
                [
                    {
                        "role": "system",
                        "content": MODEL_VERIFIER_SYSTEM_PROMPT,
                    },
                    {"role": "user", "content": content},
                ]
            )
        )

        return self._to_verifier_result(output)

    @staticmethod
    def _to_verifier_result(output: ModelVerifierOutput) -> VerifierResult:
        valid = output.hard_constraints.compliant and output.flow.compliant
        compliant = all(
            getattr(output, category).compliant for category in CATEGORIES
        )

        soft_score = (
            0.3 * output.assembly.score
            + 0.1 * output.orientation.score
            + 0.6 * output.proximity.score
        )
        final_score = 0.5 * float(valid) + 0.5 * soft_score

        if compliant:
            final_score = 1.0
        else:
            final_score = max(0.0, min(0.99, final_score))

        feedback = Feedback(
            hard_constraints=WorkerResult(
                compliant=output.hard_constraints.compliant,
                score=output.hard_constraints.score,
                feedback=output.hard_constraints.feedback,
                raw_data={},
            ),
            assembly=WorkerResult(
                compliant=output.assembly.compliant,
                score=output.assembly.score,
                feedback=output.assembly.feedback,
                raw_data={},
            ),
            orientation=WorkerResult(
                compliant=output.orientation.compliant,
                score=output.orientation.score,
                feedback=output.orientation.feedback,
                raw_data={},
            ),
            proximity=WorkerResult(
                compliant=output.proximity.compliant,
                score=output.proximity.score,
                feedback=output.proximity.feedback,
                raw_data={},
            ),
            flow=WorkerResult(
                compliant=output.flow.compliant,
                score=output.flow.score,
                feedback=output.flow.feedback,
                raw_data={},
            ),
        )

        return VerifierResult(
            valid=valid,
            compliant=compliant,
            score=final_score,
            feedback=feedback,
            raw_data={},
        )

    def evaluate(
        self,
        layouts: list[dict],
        constraints: dict,
        weights: dict | None = None,
    ) -> tuple[tuple[int, VerifierResult], list[tuple[int, VerifierResult]]]:
        """Evaluates multiple candidate layouts in parallel.

        Matches the interface of Verifier.evaluate(): one model call per
        candidate, batched via a thread pool.

        Args:
            layouts (list[dict]): Candidate layouts to evaluate.
            constraints (dict): The constraints dictionary to validate
                against.
            weights (dict | None): Unused; kept for interface parity.

        Returns:
            tuple: ((best_index, best_result), all_results) -- identical
                shape to Verifier.evaluate().
        """
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_index = {
                executor.submit(
                    self.evaluate_single, layout, constraints, weights
                ): idx
                for idx, layout in enumerate(layouts)
            }

            all_results = []
            for future in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[future]
                result: VerifierResult = future.result()
                all_results.append((idx, result))

        all_results.sort(key=lambda x: x[0])
        best_idx, best_result = max(all_results, key=lambda x: x[1].score)

        return (best_idx, best_result), all_results
