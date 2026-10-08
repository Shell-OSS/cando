# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Autonomous self-refinement agent for layout generation.

SelfRefinementAgent combines GeneratorZero, Verifier and an LLM into a
single autonomous loop that iteratively improves layouts. Two modes are
supported:
  - ``action_sequence``: the LLM issues Generator tool actions.
  - ``complete_layout``: the LLM rewrites the full layout JSON.
"""

import copy
import json
import logging
import math
import re
import warnings
from collections.abc import Callable
from typing import Any, Literal, cast

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from shapely.geometry import Polygon as ShapelyPolygon

from cando.agents.generators.generator.models import ActionSequence
from cando.agents.generators.generator.tools import execute_sequence
from cando.agents.generators.generator_zero.agent import GeneratorZero
from cando.agents.verifiers.agent import Verifier
from cando.agents.verifiers.models import VerifierResult
from cando.config import ModelsConfig
from cando.constants.classes import INPUT_CLASSES
from cando.utils.generic import json_dumps, round_floats
from cando.utils.visualizer import plot_layout

from .models import IterationRecord, SelfRefinementResult
from .prompt import (
    COMPLETE_LAYOUT_SYSTEM_PROMPT,
    SELF_REFINEMENT_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


def _strip_images(conversation: list) -> list:
    """Remove image_url parts from all messages in a conversation history.

    Only the current iteration's message should carry the layout image.
    Stripping images from retained turns avoids re-sending ~15-20 K tokens
    per historical turn on models that already processed them.
    Handles both plain-dict messages (action_sequence mode) and LangChain
    HumanMessage objects (complete_layout mode).
    """
    result: list[Any] = []
    for msg in conversation:
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = [
                    p for p in content if p.get("type") != "image_url"
                ]
                if len(text_parts) == 1:
                    msg = {**msg, "content": text_parts[0]["text"]}
                elif text_parts:
                    msg = {**msg, "content": text_parts}
            result.append(msg)
        elif isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                text_parts = [
                    p
                    for p in content
                    if not (
                        isinstance(p, dict) and p.get("type") == "image_url"
                    )
                ]
                new_content: str | list = (
                    text_parts[0]["text"]
                    if len(text_parts) == 1
                    and isinstance(text_parts[0], dict)
                    else text_parts
                )
                result.append(HumanMessage(content=new_content))
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result


class SelfRefinementAgent:
    """Autonomous layout self-refinement agent.

    Combines GeneratorZero (initial placement), a deterministic Verifier
    (feedback), and an LLM decision-maker into a closed feedback loop.

    Two modes are available (controlled by the ``mode`` parameter):

    ``action_sequence`` (default)
        The LLM issues targeted actions from the Generator tool stack
        (move, rotate, stack, align, cluster, …). Actions are applied
        deterministically by ``execute_sequence``.

    ``complete_layout``
        The LLM rewrites the entire layout JSON from scratch, constrained
        to preserve element IDs, classes, and bounding-box dimensions.
        If the proposed layout has structural violations it is discarded
        and the current layout is carried forward.
    """

    def __init__(
        self,
        max_iterations: int = 10,
        model: str = ModelsConfig.generator,
        mode: Literal[
            "action_sequence", "complete_layout"
        ] = "action_sequence",
        system_prompt: str | None = None,
        context_turns: int = 1,
        send_image: bool = True,
        callbacks: list | None = None,
    ):
        """Initialises the SelfRefinementAgent.

        Args:
            max_iterations: Hard upper bound on refinement iterations.
                The loop exits early when the layout becomes fully compliant.
            model: LangChain model identifier (e.g.
                ``"azure_openai:gpt-5.2"``).
            mode: Refinement strategy.
                ``"action_sequence"`` uses Generator tools (default);
                ``"complete_layout"`` rewrites the entire layout JSON.
            system_prompt: Override the default system prompt for the chosen
                mode. Pass ``None`` to use the built-in default.
            context_turns: Number of previous (user, assistant) exchange pairs
                to retain in the LLM context at each iteration. ``1`` keeps
                only the most recent exchange so token count stays bounded
                regardless of ``max_iterations``. ``0`` is fully stateless.
                Negative values disable trimming (full history — only safe
                for small ``max_iterations``).
            send_image: If ``True`` (default), renders a base64-encoded PNG of
                the layout and includes it in the current iteration message.
                Images from previous turns are always stripped regardless of
                this flag. Pass ``False`` (``--no-image`` in the CLI) to
                disable images entirely for text-only models such as Qwen3.
            callbacks: Optional list of LangChain callback handlers to attach
                to every LLM invocation (e.g. ``AzureAIOpenTelemetryTracer``).
        """
        self.max_iterations = max_iterations
        self.mode = mode
        self._context_turns = context_turns
        self._send_image = send_image
        self._callbacks: list = callbacks or []

        if system_prompt is None:
            system_prompt = (
                SELF_REFINEMENT_SYSTEM_PROMPT
                if mode == "action_sequence"
                else COMPLETE_LAYOUT_SYSTEM_PROMPT
            )
        self._system_prompt = system_prompt

        self.generator_zero = GeneratorZero(model=model)
        self.verifier = Verifier()

        self._agent: Any = None
        self._llm: Any = None

        if mode == "action_sequence":
            # ToolStrategy uses tool-calling, avoiding OpenAI's 'oneOf'
            # restriction in strict response_format mode.
            self._agent = create_agent(
                model=model,
                system_prompt=system_prompt,
                response_format=ToolStrategy(ActionSequence),
            )
        else:
            # complete_layout: plain chat model, no structured output schema.
            # The LLM is asked to return raw JSON which we parse ourselves.
            self._llm = init_chat_model(model=model, max_retries=3)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        requirements: dict,
        weights: dict | None = None,
        on_iteration: Callable[[IterationRecord], None] | None = None,
    ) -> SelfRefinementResult:
        """Runs the autonomous self-refinement loop.

        1. Calls GeneratorZero to produce an initial layout.
        2. Evaluates the initial layout with the Verifier.
        3. Iterates up to ``max_iterations``, asking the LLM to improve the
           layout via action sequences based on verifier feedback.
        4. Returns the best layout found (highest Verifier score).

        Args:
            requirements (dict): Raw user requirements in the dataset-style
                schema.
            weights (dict | None): Optional per-category weights for the
                Verifier. Keys: hard_constraints, assembly, proximity,
                orientation, flow.
            on_iteration: Optional callback invoked immediately after each
                iteration completes (before the next LLM call). Receives the
                completed ``IterationRecord``. Use this for incremental
                logging without waiting for the full run to finish.

        Returns:
            SelfRefinementResult: Contains the best layout, score, compliance
                status, iteration count, and per-iteration history.
        """
        # ---- Step 1: initial layout ----------------------------------- #
        layout, constraints, _gz_history = self.generator_zero.generate(
            requirements, num_candidates=5
        )

        # ---- Step 2: initial verification ----------------------------- #
        verifier_result = self.verifier.evaluate_single(
            layout, constraints, weights
        )

        best_layout = copy.deepcopy(layout)
        best_score = verifier_result.score
        history: list[IterationRecord] = []

        if self.mode == "action_sequence":
            return self._run_action_sequence(
                layout,
                constraints,
                weights,
                verifier_result,
                best_layout,
                best_score,
                history,
                on_iteration,
            )
        else:
            return self._run_complete_layout(
                layout,
                constraints,
                weights,
                verifier_result,
                best_layout,
                best_score,
                history,
                on_iteration,
            )

    # ------------------------------------------------------------------
    # Mode-specific loops
    # ------------------------------------------------------------------

    def _run_action_sequence(
        self,
        layout: dict,
        constraints: dict,
        weights: dict | None,
        verifier_result: VerifierResult,
        best_layout: dict,
        best_score: float,
        history: list[IterationRecord],
        on_iteration: Callable[[IterationRecord], None] | None,
    ) -> SelfRefinementResult:
        """Inner loop for action_sequence mode."""
        # Multi-turn conversation history (user + assistant dicts).
        # The system prompt is injected by create_agent automatically.
        conversation: list[Any] = []

        for iteration in range(self.max_iterations):
            if verifier_result.compliant:
                break

            # Trim to the last context_turns (user, assistant) pairs to keep
            # token count bounded across many iterations.
            if self._context_turns >= 0:
                keep = 2 * self._context_turns
                if len(conversation) > keep:
                    conversation = conversation[-keep:] if keep > 0 else []
                conversation = _strip_images(conversation)

            score_before = verifier_result.score
            user_content = self._build_iteration_message(
                layout, verifier_result, iteration
            )
            conversation.append({"role": "user", "content": user_content})

            if self._agent is None:
                raise ValueError(
                    f"self._agent is None but mode={self.mode!r} requires "
                    "the action_sequence agent; construct with "
                    "mode='action_sequence' to use this method."
                )
            result = self._agent.invoke(
                {"messages": conversation},
                config={"callbacks": self._callbacks}
                if self._callbacks
                else {},
            )
            seq: ActionSequence = result["structured_response"]

            conversation.append(
                {"role": "assistant", "content": seq.model_dump_json()}
            )

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", category=RuntimeWarning, module="shapely"
                )
                new_layout = execute_sequence(layout, seq)
                verifier_result = self.verifier.evaluate_single(
                    new_layout, constraints, weights
                )
            layout = new_layout

            if verifier_result.score > best_score:
                best_score = verifier_result.score
                best_layout = copy.deepcopy(layout)

            record = IterationRecord(
                iteration=iteration,
                response_type="action_sequence",
                reasoning=seq.rationale,
                score_before=score_before,
                score_after=verifier_result.score,
                compliant_after=verifier_result.compliant,
                verifier_scores=self._verifier_scores_dict(verifier_result),
                layout_snapshot=copy.deepcopy(layout),
            )
            history.append(record)
            if on_iteration is not None:
                on_iteration(record)

        return SelfRefinementResult(
            layout=best_layout,
            constraints=constraints,
            score=best_score,
            compliant=verifier_result.compliant,
            iterations_run=len(history),
            history=history,
        )

    def _run_complete_layout(
        self,
        layout: dict,
        constraints: dict,
        weights: dict | None,
        verifier_result: VerifierResult,
        best_layout: dict,
        best_score: float,
        history: list[IterationRecord],
        on_iteration: Callable[[IterationRecord], None] | None,
    ) -> SelfRefinementResult:
        """Inner loop for complete_layout mode.

        The LLM returns raw JSON. We:
        1. Parse the JSON (on failure → feed error back, ask to fix).
        2. Validate element counts and dimensions against the constraints
           (on violation → feed error back, ask to fix).
        3. Apply the new layout and verify.
        """
        conversation: list[SystemMessage | HumanMessage | AIMessage] = [
            SystemMessage(content=self._system_prompt)
        ]

        for iteration in range(self.max_iterations):
            if verifier_result.compliant:
                break

            # Keep the system message + last context_turns (human, AI) pairs
            # so the token count stays bounded across many iterations.
            if self._context_turns >= 0:
                keep = 2 * self._context_turns
                system_msg = conversation[0]
                recent = conversation[1:]
                conversation = [system_msg] + (
                    recent[-keep:] if keep > 0 else []
                )
                conversation = _strip_images(conversation)

            score_before = verifier_result.score
            conversation.append(
                HumanMessage(
                    content=self._build_iteration_message(
                        layout, verifier_result, iteration
                    )
                )
            )

            if self._llm is None:
                raise ValueError(
                    f"self._llm is None but mode={self.mode!r} requires "
                    "the complete_layout LLM; construct with "
                    "mode='complete_layout' to use this method."
                )
            response = self._llm.invoke(
                conversation,
                config={"callbacks": self._callbacks}
                if self._callbacks
                else {},
            )
            raw_text: str = (
                response.content if isinstance(response.content, str) else ""
            )
            conversation.append(AIMessage(content=raw_text))

            # ---- 1. Parse JSON --------------------------------------- #
            parse_error = None
            parsed: dict | None = None
            try:
                parsed = self._extract_json(raw_text)
            except Exception as exc:
                parse_error = str(exc)

            if parse_error or parsed is None:
                err_msg = parse_error or "No JSON object found in response."
                conversation.append(
                    HumanMessage(
                        content=(
                            "Your response could not be parsed as valid "
                            "JSON.\n\n"
                            f"Error: {err_msg}\n\n"
                            "Please return **only** a valid JSON object "
                            "with an `elements` list and a `reasoning` "
                            "string. Do not include any text outside the "
                            "JSON."
                        )
                    )
                )
                record = IterationRecord(
                    iteration=iteration,
                    response_type="complete_layout_parse_error",
                    reasoning=raw_text[:500],
                    score_before=score_before,
                    score_after=score_before,
                    compliant_after=verifier_result.compliant,
                    verifier_scores=self._verifier_scores_dict(
                        verifier_result
                    ),
                    layout_snapshot={},
                )
                history.append(record)
                if on_iteration is not None:
                    on_iteration(record)
                continue

            # ---- 2. Validate structure + constraints ----------------- #
            violations = self._validate_parsed_layout(parsed, layout)
            if violations:
                violation_text = "\n".join(f"- {v}" for v in violations)
                conversation.append(
                    HumanMessage(
                        content=(
                            "Your proposed layout has the following "
                            "violations and cannot be applied:\n\n"
                            f"{violation_text}\n\n"
                            "Please return a corrected layout JSON that "
                            "fixes all listed violations."
                        )
                    )
                )
                record = IterationRecord(
                    iteration=iteration,
                    response_type="complete_layout_invalid",
                    reasoning=parsed.get("reasoning", raw_text[:300]),
                    score_before=score_before,
                    score_after=score_before,
                    compliant_after=verifier_result.compliant,
                    verifier_scores=self._verifier_scores_dict(
                        verifier_result
                    ),
                    layout_snapshot={},
                )
                history.append(record)
                if on_iteration is not None:
                    on_iteration(record)
                continue

            # ---- 3. Apply and verify --------------------------------- #
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", category=RuntimeWarning, module="shapely"
                )
                new_layout = self._apply_parsed_layout(
                    parsed["elements"], layout
                )
                verifier_result = self.verifier.evaluate_single(
                    new_layout, constraints, weights
                )
            layout = new_layout

            if verifier_result.score > best_score:
                best_score = verifier_result.score
                best_layout = copy.deepcopy(layout)

            record = IterationRecord(
                iteration=iteration,
                response_type="complete_layout",
                reasoning=parsed.get("reasoning", ""),
                score_before=score_before,
                score_after=verifier_result.score,
                compliant_after=verifier_result.compliant,
                verifier_scores=self._verifier_scores_dict(verifier_result),
                layout_snapshot=copy.deepcopy(layout),
            )
            history.append(record)
            if on_iteration is not None:
                on_iteration(record)

        return SelfRefinementResult(
            layout=best_layout,
            constraints=constraints,
            score=best_score,
            compliant=verifier_result.compliant,
            iterations_run=len(history),
            history=history,
        )

    # ------------------------------------------------------------------
    # Message builder
    # ------------------------------------------------------------------

    def _build_iteration_message(
        self,
        layout: dict,
        verifier_result: VerifierResult,
        iteration: int,
    ) -> list | str:
        """Builds the user message content for a refinement iteration.

        Returns a multimodal list when a layout image is available,
        otherwise a plain string.
        """

        if self.mode == "action_sequence":
            closing = (
                "Return an action_sequence to improve compliance, "
                "prioritising hard_constraints violations first."
            )
        else:
            closing = (
                "Return a complete replacement layout (all elements with "
                "updated polygons) that resolves the violations above. "
                "Preserve every element ID, class, and bounding-box "
                "dimensions. Fixed elements (Layout Polygon, Entrance, "
                "Exit) must keep their exact polygon coordinates "
                "unchanged."
            )

        scores_only = self._verifier_scores_dict(verifier_result)

        text = (
            f"# Iteration {iteration + 1}\n\n"
            "## Layout Data\n"
            f"```json\n{json_dumps(round_floats(layout))}\n```\n\n"
            "## Verification Report\n"
            f"{json_dumps(round_floats(scores_only))}\n\n" + closing
        )

        layout_image = (
            self._render_layout(layout) if self._send_image else None
        )
        if layout_image is None:
            return text

        return [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{layout_image}"},
            },
        ]

    # ------------------------------------------------------------------
    # complete_layout helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _verifier_scores_dict(verifier_result: VerifierResult) -> dict:
        """Returns a scores-only dict from a VerifierResult (no feedback)."""
        fb = verifier_result.feedback
        return {
            "score": verifier_result.score,
            "valid": verifier_result.valid,
            "compliant": verifier_result.compliant,
            "categories": {
                "hard_constraints": {
                    "compliant": fb.hard_constraints.compliant,
                    "score": fb.hard_constraints.score,
                },
                "assembly": {
                    "compliant": fb.assembly.compliant,
                    "score": fb.assembly.score,
                },
                "proximity": {
                    "compliant": fb.proximity.compliant,
                    "score": fb.proximity.score,
                },
                "orientation": {
                    "compliant": fb.orientation.compliant,
                    "score": fb.orientation.score,
                },
                "flow": {
                    "compliant": fb.flow.compliant,
                    "score": fb.flow.score,
                },
            },
        }

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extracts the first JSON object from the LLM response text.

        Handles responses wrapped in markdown code fences.
        Raises ValueError if no valid JSON object is found.
        """
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            return json.loads(fenced.group(1))
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(
                f"No JSON object found in response: {text[:200]!r}"
            )
        return json.loads(text[start : end + 1])

    def _validate_parsed_layout(
        self,
        parsed: dict,
        reference_layout: dict,
    ) -> list[str]:
        """Returns a list of human-readable violation strings (empty = valid).

        Checks only what the Verifier cannot safely handle on a malformed
        layout: structural validity, ID integrity, fixed-element polygon
        integrity, and minimum polygon point counts.

        Element counts and dimensions are deliberately omitted — the
        Verifier's hard_constraints check already validates those and will
        feed the violations back through the normal refinement loop.
        """
        violations: list[str] = []

        if not isinstance(parsed.get("elements"), list):
            violations.append(
                'Response must contain an "elements" key with a list value.'
            )
            return violations

        ref_by_id: dict[int, dict] = {
            e["id"]: e for e in reference_layout.get("elements", [])
        }
        ref_ids = set(ref_by_id)
        proposed_elements: list[dict] = parsed["elements"]
        proposed_ids: set[int] = set()

        for elem in proposed_elements:
            eid = elem.get("id")
            if not isinstance(eid, int):
                violations.append(
                    "Element has a missing or non-integer 'id': "
                    f"{str(elem)[:100]}"
                )
                continue
            proposed_ids.add(eid)

            if eid not in ref_by_id:
                violations.append(
                    f"Element id={eid} does not exist in the current layout."
                )
                continue

            polygon = elem.get("polygon", [])
            ref = ref_by_id[eid]
            elem_class = ref.get("class", "")

            if elem_class in INPUT_CLASSES:
                # Fixed elements must keep their polygon exactly — changing
                # them (esp. Layout Polygon) would corrupt boundary checking.
                if not self._polygons_equal(ref.get("polygon", []), polygon):
                    violations.append(
                        f"Fixed element id={eid} ('{elem_class}') polygon "
                        "was modified — it must remain unchanged."
                    )
                continue

            if not isinstance(polygon, list) or len(polygon) < 3:
                violations.append(
                    f"Element id={eid} polygon must have at least 3 points."
                )

        for mid in ref_ids - proposed_ids:
            cls = ref_by_id[mid].get("class", "?")
            violations.append(
                f"Element id={mid} ('{cls}') is missing from the "
                "proposed layout."
            )
        for eid in proposed_ids - ref_ids:
            violations.append(
                f"Element id={eid} does not exist in the current layout."
            )

        return violations

    def _apply_parsed_layout(
        self, elements: list[dict], reference_layout: dict
    ) -> dict:
        """Merges validated element polygons into the reference layout."""
        proposed_by_id = {e["id"]: e["polygon"] for e in elements}
        new_elements: list[dict] = []
        for ref_elem in reference_layout.get("elements", []):
            new_elem = copy.deepcopy(ref_elem)
            eid = ref_elem["id"]
            if eid in proposed_by_id:
                poly = proposed_by_id[eid]
                new_elem["polygon"] = poly
                new_elem["bbox"] = self._compute_bbox(poly)
                new_elem["rotation_deg"] = self._compute_rotation_deg(poly)
            new_elements.append(new_elem)
        result = {
            k: v for k, v in reference_layout.items() if k != "elements"
        }
        result["elements"] = new_elements
        return result

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _polygons_equal(
        a: list[list[float]], b: list[list[float]], tol: float = 1e-3
    ) -> bool:
        if len(a) != len(b):
            return False
        return all(
            abs(ax - bx) <= tol and abs(ay - by) <= tol
            for (ax, ay), (bx, by) in zip(a, b, strict=True)
        )

    @staticmethod
    def _polygon_min_rect_dims(
        polygon: list[list[float]],
    ) -> tuple[float, float]:
        """Returns (shorter_side, longer_side) of the min bounding rect."""
        try:
            poly = ShapelyPolygon(polygon)
            if not poly.is_valid:
                poly = cast(ShapelyPolygon, poly.buffer(0))
            rect = cast(ShapelyPolygon, poly.minimum_rotated_rectangle)
            coords = list(rect.exterior.coords)[:-1]
            if len(coords) < 2:
                return 0.0, 0.0
            e0 = math.hypot(
                coords[1][0] - coords[0][0], coords[1][1] - coords[0][1]
            )
            e1 = math.hypot(
                coords[2][0] - coords[1][0], coords[2][1] - coords[1][1]
            )
            return min(e0, e1), max(e0, e1)
        except Exception as exc:
            logger.debug(
                "Failed to compute min/max rect dims, defaulting to "
                "(0.0, 0.0): %s",
                exc,
            )
            return 0.0, 0.0

    @staticmethod
    def _compute_bbox(polygon: list[list[float]]) -> dict:
        xs = [pt[0] for pt in polygon]
        ys = [pt[1] for pt in polygon]
        return {
            "min_x": min(xs),
            "min_y": min(ys),
            "max_x": max(xs),
            "max_y": max(ys),
        }

    @staticmethod
    def _compute_rotation_deg(polygon: list[list[float]]) -> float:
        try:
            poly = ShapelyPolygon(polygon)
            if not poly.is_valid:
                poly = cast(ShapelyPolygon, poly.buffer(0))
            rect = cast(ShapelyPolygon, poly.minimum_rotated_rectangle)
            coords = list(rect.exterior.coords)[:-1]
            if len(coords) < 2:
                return 0.0
            lengths = [
                math.hypot(
                    coords[(i + 1) % len(coords)][0] - coords[i][0],
                    coords[(i + 1) % len(coords)][1] - coords[i][1],
                )
                for i in range(len(coords))
            ]
            idx = lengths.index(max(lengths))
            dx = coords[(idx + 1) % len(coords)][0] - coords[idx][0]
            dy = coords[(idx + 1) % len(coords)][1] - coords[idx][1]
            return round(math.degrees(math.atan2(dy, dx)) % 180.0, 2)
        except Exception as exc:
            logger.debug(
                "Failed to compute rotation, defaulting to 0.0: %s", exc
            )
            return 0.0

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _render_layout(layout: dict) -> str | None:
        """Renders a layout to a base64-encoded PNG string.

        Returns None on failure so callers can proceed without the image.
        """
        try:
            result = plot_layout(layout, show=False, return_base64=True)
            return result if isinstance(result, str) else None
        except Exception as exc:
            logger.debug("Failed to render layout preview image: %s", exc)
            return None
