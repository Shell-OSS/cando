# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Generator agent coordinating layout modifications."""

import concurrent.futures
import logging
import random
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from cando.agents.planners.models import PlannerResult
from cando.agents.verifiers.models import VerifierResult
from cando.config import ModelsConfig
from cando.model_factory import resolve_agent_model
from cando.utils.generic import json_dumps, round_floats

from .configs import NEUTRAL_STRATEGY, STRATEGY_CONFIGS
from .models import ActionSequence, StrategyConfig
from .prompt import (
    DIRECT_STRATEGY_PROMPT_EXTENSIONS,
    GENERATOR_BASE_PROMPT,
    NEUTRAL_STRATEGY_PROMPT,
    STRATEGY_PROMPT_EXTENSIONS,
)
from .tools import execute_sequence

logger = logging.getLogger(__name__)


class Generator:
    """Generator agent for creating and executing layout refinement sequences.

    The Generator analyzes planner output, resolves conflicts, and generates
    multiple diverse action sequences that can be executed in parallel to
    produce refined layouts.
    """

    def __init__(
        self,
        model: str = ModelsConfig.generator,
        system_prompt: str = GENERATOR_BASE_PROMPT,
        role: str = "generator",
    ):
        """Initializes the Generator agent.

        Args:
            model (str): The model identifier to use for the agent.
                Defaults to ModelsConfig.generator.
            system_prompt (str): The system prompt for the agent. Defaults to
                GENERATOR_BASE_PROMPT.
            role (str): Role key used by cando.model_factory to resolve the
                "fast" inference profile's max-token/thinking settings.
                Defaults to "generator". The no_planner ablation mode
                instantiates a second Generator with role="generator_direct".
        """
        self.role = role
        self.agent = create_agent(
            model=resolve_agent_model(role, model),
            system_prompt=system_prompt,
            response_format=ToolStrategy(ActionSequence),
        )

    def generate(
        self,
        layout_data: dict[str, Any],
        guidance: PlannerResult | str,
        n: int = 3,
        weights: dict[str, float] | None = None,
        layout_image: str | None = None,
        verifier_result: VerifierResult | None = None,
        strategy_mode: str = "library",
        seed: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[ActionSequence], list[str]]:
        """Generates multiple refined layouts based on the planner's output.

        This method:
        1. Analyzes the planner's refinement plan across all categories.
        2. Generates N diverse action sequences using batch LLM calls with
           different strategies.
        3. Executes all sequences in parallel.
        4. Returns N refined layouts.

        Args:
            layout_data (dict): The current layout data dictionary with
                elements, dimensions, and metadata.
            guidance (PlannerResult | str): The PlannerResult or guidance
                string containing refinement plans for all 5 categories
                (hard_constraints, assembly, proximity, orientation, flow).
            n (int): Number of diverse action sequences to generate (and thus
                number of refined layouts to return). Default is 3.
            weights (dict[str, float] | None): Optional dictionary mapping
                category names to their weights (importance). Missing
                categories default to 1.0.
            layout_image (str | None): Optional base64-encoded image of the
                layout for visual context (may help with spatial reasoning).
            verifier_result (VerifierResult | None): Optional verifier output.
                When provided, it is injected into autonomous-strategy
                messages (guidance_mode="none") so the agent knows which
                categories are currently failing.
            strategy_mode (str): "library" samples from STRATEGY_CONFIGS as
                before; "neutral" (no_strategies ablation mode) uses the same
                neutral strategy for every candidate. Defaults to "library".
            seed (int | None): Optional seed for repository-side strategy
                selection/ordering reproducibility. None uses the shared
                random module state.

        Returns:
            tuple[list[dict], list[ActionSequence], list[str]]: A tuple
            containing:
            - a list of N refined layout dictionaries,
            - a list of the corresponding ActionSequence objects,
            - a list of strategy keys (`StrategyConfig.key`) in the same
              order as the layouts and sequences, indicating which generation
              strategy produced each candidate.

        Raises:
            ValueError: If n < 1.
        """
        if n < 1:
            raise ValueError(f"n must be at least 1, got {n}")

        rng = random if seed is None else random.Random(seed)

        # Initialize weights for all categories
        if weights is None:
            weights = {}

        if isinstance(guidance, PlannerResult):
            raw_weights = {
                "hard_constraints": weights.get("hard_constraints", 0.2),
                "assembly": weights.get("assembly", 0.2),
                "proximity": weights.get("proximity", 0.2),
                "orientation": weights.get("orientation", 0.2),
                "flow": weights.get("flow", 0.2),
            }

            # Normalize weights so they sum to 1
            total_weight = sum(raw_weights.values())
            category_weights = {
                category: w / total_weight
                for category, w in raw_weights.items()
            }

            # Prepare planner result with weights
            guidance_input: dict[str, Any] | str = {
                "hard_constraints": {
                    **guidance.hard_constraints.model_dump(mode="json"),
                    "weight": category_weights["hard_constraints"],
                },
                "assembly": {
                    **guidance.assembly.model_dump(mode="json"),
                    "weight": category_weights["assembly"],
                },
                "proximity": {
                    **guidance.proximity.model_dump(mode="json"),
                    "weight": category_weights["proximity"],
                },
                "orientation": {
                    **guidance.orientation.model_dump(mode="json"),
                    "weight": category_weights["orientation"],
                },
                "flow": {
                    **guidance.flow.model_dump(mode="json"),
                    "weight": category_weights["flow"],
                },
            }
        else:
            # If guidance is already a string, use it directly
            guidance_input = guidance

        # Select n strategies — "library" samples STRATEGY_CONFIGS (with
        # conservative always included at least once); "neutral" (the
        # no_strategies ablation mode) uses the same neutral strategy for
        # every candidate.
        selected_configs = self._select_strategies(n, strategy_mode, rng)
        selected_strategy_keys: list[str] = [c.key for c in selected_configs]

        # Prepare messages for batch generation
        layout_json = json_dumps(round_floats(layout_data))
        batch_inputs = []
        for config in selected_configs:
            # Filter guidance according to the strategy's guidance_mode
            filtered_guidance = self._filter_guidance(
                guidance_input, config.guidance_mode
            )

            # Build the text body of the user message
            if config.key == "neutral":
                strategy_extension = NEUTRAL_STRATEGY_PROMPT
            else:
                strategy_extension = STRATEGY_PROMPT_EXTENSIONS.get(
                    config.key, f"Strategy: {config.key}"
                )

            if filtered_guidance is not None:
                if isinstance(guidance, str):
                    text_body = (
                        f"# Layout Data:\n{layout_json}\n\n"
                        f"# Guidance:\n{filtered_guidance}."
                    )
                else:
                    guidance_json = json_dumps(
                        round_floats(filtered_guidance)
                    )
                    text_body = (
                        f"# Layout Data:\n{layout_json}\n\n"
                        f"# Guidance:\n{guidance_json}\n\n"
                        f"{strategy_extension}\n\n"
                        "Generate a single action sequence following the "
                        "strategy above."
                    )
            else:
                verifier_section = (
                    f"# Verifier Feedback:\n"
                    f"{self._format_verifier_result(verifier_result)}\n\n"
                    if verifier_result is not None
                    else ""
                )
                text_body = (
                    f"# Layout Data:\n{layout_json}\n\n"
                    f"{verifier_section}"
                    f"{strategy_extension}\n\n"
                    "No planner guidance is provided. Reason autonomously "
                    "from the layout data and generate a single action "
                    "sequence following the strategy above."
                )

            # Build message content based on whether image is provided
            if layout_image:
                message_content: list[dict[str, Any]] | str = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{layout_image}"
                        },
                    },
                    {
                        "type": "text",
                        "text": text_body,
                    },
                ]
            else:
                message_content = text_body

            batch_inputs.append(
                {"messages": [{"role": "user", "content": message_content}]}
            )

        # Generate N action sequences and execute them in parallel.
        refined_layouts, action_sequences = self._batch_generate_and_execute(
            batch_inputs, layout_data, n
        )

        return refined_layouts, action_sequences, selected_strategy_keys

    def generate_from_verifier(
        self,
        layout_data: dict[str, Any],
        verifier_evidence: dict,
        history_context: str | None,
        n: int = 3,
        weights: dict[str, float] | None = None,
        layout_image: str | None = None,
        strategy_mode: str = "library",
        seed: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[ActionSequence], list[str]]:
        """Generates N diverse refined layouts directly from verifier
        evidence, without an intermediate Planner (no_planner ablation mode).

        Mirrors `generate()`'s candidate count, batch generation, action
        execution, strategy filtering, and layout-image behavior, but always
        builds the user message from verifier evidence (never a
        PlannerResult) using an explicit direct-prompt builder so the
        strategy section is always present.

        Args:
            layout_data (dict): The current layout data dictionary.
            verifier_evidence (dict): Evidence dict built by
                `cando.agents.generators.generator.evidence.
                build_verifier_evidence`.
            history_context (str | None): Optional formatted history string.
            n (int): Number of diverse action sequences to generate.
            weights (dict[str, float] | None): Optional per-category
                weights; unused here since `verifier_evidence` already
                carries a "weight" per category (kept for interface
                symmetry with `generate()`).
            layout_image (str | None): Optional base64-encoded layout image.
            strategy_mode (str): "library" or "neutral" (no_strategies).
            seed (int | None): Optional seed for repository-side strategy
                selection/ordering reproducibility.

        Returns:
            tuple[list[dict], list[ActionSequence], list[str]]: Same shape
            as `generate()`'s return value.

        Raises:
            ValueError: If n < 1.
        """
        if n < 1:
            raise ValueError(f"n must be at least 1, got {n}")

        rng = random if seed is None else random.Random(seed)

        selected_configs = self._select_strategies(n, strategy_mode, rng)
        selected_strategy_keys: list[str] = [c.key for c in selected_configs]

        layout_json = json_dumps(round_floats(layout_data))
        batch_inputs = []
        for config in selected_configs:
            # "none" means no Planner guidance is available (as in the full
            # system's autonomous strategies); the direct Generator should
            # still reason from the complete verifier evidence rather than
            # receiving nothing. Only a specific category name narrows the
            # evidence down to that category.
            if config.guidance_mode == "none":
                filtered_evidence: dict | str | None = verifier_evidence
            else:
                filtered_evidence = self._filter_guidance(
                    verifier_evidence, config.guidance_mode
                )

            if config.key == "neutral":
                strategy_extension = NEUTRAL_STRATEGY_PROMPT
            else:
                strategy_extension = DIRECT_STRATEGY_PROMPT_EXTENSIONS.get(
                    config.key, f"Strategy: {config.key}"
                )

            history_section = (
                f"# Iteration History:\n{history_context}\n\n"
                if history_context
                else "# Iteration History:\nNone.\n\n"
            )

            evidence_json = json_dumps(round_floats(filtered_evidence))
            text_body = (
                f"# Layout Data:\n{layout_json}\n\n"
                f"# Verifier Evidence:\n{evidence_json}\n\n"
                f"{history_section}"
                f"{strategy_extension}\n\n"
                "Generate a single action sequence following the strategy "
                "above."
            )

            if layout_image:
                message_content: list[dict[str, Any]] | str = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{layout_image}"
                        },
                    },
                    {
                        "type": "text",
                        "text": text_body,
                    },
                ]
            else:
                message_content = text_body

            batch_inputs.append(
                {"messages": [{"role": "user", "content": message_content}]}
            )

        refined_layouts, action_sequences = self._batch_generate_and_execute(
            batch_inputs, layout_data, n
        )

        return refined_layouts, action_sequences, selected_strategy_keys

    def _batch_generate_and_execute(
        self,
        batch_inputs: list[dict],
        layout_data: dict[str, Any],
        n: int,
    ) -> tuple[list[dict[str, Any]], list[ActionSequence]]:
        """Runs a batch of ActionSequence generations and executes each one.

        Shared by `generate()` and `generate_from_verifier()`.

        Args:
            batch_inputs (list[dict]): Per-candidate agent.batch() inputs.
            layout_data (dict): The current layout data (execution baseline).
            n (int): Number of candidates (used for the failure fallback).

        Returns:
            tuple[list[dict], list[ActionSequence]]: Refined layouts and
                their corresponding ActionSequence objects, in order.
        """
        # Generate N action sequences in parallel using batch
        try:
            responses = self.agent.batch(cast(list, batch_inputs))
            action_sequences = [
                response["structured_response"] for response in responses
            ]
        except Exception as e:
            logger.warning("Batch generation failed: %s", e)
            # Fallback to empty sequences
            action_sequences = [
                ActionSequence(
                    actions=[],
                    rationale=f"Generation failed: {e}",
                    priority="low",
                )
                for _ in range(n)
            ]

        # Execute all sequences in parallel, preserving order
        refined_layouts: list[dict[str, Any]] = [{} for _ in range(n)]
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Submit all execution tasks with indices
            future_to_index = {
                executor.submit(execute_sequence, layout_data, sequence): idx
                for idx, sequence in enumerate(action_sequences)
            }

            # Collect results and place them in correct order
            for future in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    refined_layout: dict[str, Any] = future.result()
                    refined_layouts[idx] = refined_layout
                except Exception as e:
                    logger.warning(
                        "Failed to execute sequence %d '%s...': %s",
                        idx,
                        action_sequences[idx].rationale[:50],
                        e,
                    )
                    # On failure, use the original layout unchanged
                    refined_layouts[idx] = layout_data

        return refined_layouts, action_sequences

    @staticmethod
    def _select_strategies(
        n: int,
        strategy_mode: str,
        rng: Any,
    ) -> list[StrategyConfig]:
        """Selects n StrategyConfig objects for one generation round.

        Args:
            n (int): Number of strategies to select.
            strategy_mode (str): "library" samples from STRATEGY_CONFIGS
                (conservative always included at least once); "neutral"
                (no_strategies ablation mode) returns the same neutral
                strategy n times.
            rng: Either the `random` module or a `random.Random` instance.
                Accepts both so callers can opt into deterministic,
                per-iteration seeding via `random.Random(seed)`.

        Returns:
            list[StrategyConfig]: n strategy configs, in generation order.
        """
        if strategy_mode == "neutral":
            return [NEUTRAL_STRATEGY] * n

        conservative_config = next(
            c for c in STRATEGY_CONFIGS if c.key == "conservative"
        )

        num_configs = len(STRATEGY_CONFIGS)
        full_passes = n // num_configs
        remainder = n % num_configs

        selected_configs = list(STRATEGY_CONFIGS) * full_passes
        if remainder > 0:
            selected_configs += rng.sample(STRATEGY_CONFIGS, k=remainder)

        # Ensure conservative strategy is included at least once
        if n < num_configs and not any(
            c.key == "conservative" for c in selected_configs
        ):
            selected_configs[0] = conservative_config

        rng.shuffle(selected_configs)
        return selected_configs

    def generate_single(
        self,
        layout_data: dict,
        planner_result: PlannerResult,
        weights: dict[str, float] | None = None,
        layout_image: str | None = None,
        verifier_result: VerifierResult | None = None,
    ) -> tuple[dict, ActionSequence]:
        """Generates a single refined layout.

        Args:
            layout_data (dict): The current layout data dictionary.
            planner_result (PlannerResult): The PlannerResult containing
                refinement plans.
            weights (dict[str, float] | None): Optional category weights.
            layout_image (str | None): Optional base64-encoded layout image.
            verifier_result (VerifierResult | None): Optional verifier output
                passed to autonomous strategies.

        Returns:
            tuple[dict, ActionSequence]: A single refined layout dictionary
            and its corresponding action sequence.
        """
        layouts, action_sequences, _ = self.generate(
            layout_data=layout_data,
            guidance=planner_result,
            n=1,
            weights=weights,
            layout_image=layout_image,
            verifier_result=verifier_result,
        )
        return layouts[0], action_sequences[0]

    @staticmethod
    def _filter_guidance(
        guidance_input: dict | str | None, mode: str
    ) -> dict | str | None:
        """Filters the prepared guidance dict according to a strategy mode.

        Args:
            guidance_input (dict | str | None): The full guidance dict (built
                from PlannerResult) or a raw string, or None.
            mode (str): One of "all", "hard_constraints", "assembly",
                "proximity", "orientation", "flow", or "none".

        Returns:
            dict | str | None: The filtered guidance or None.
        """
        if mode == "none" or guidance_input is None:
            return None

        # String guidance is always passed as-is (no sub-category filtering)
        if isinstance(guidance_input, str) or mode == "all":
            return guidance_input

        # Return only the requested category if it exists in the dict
        if mode in guidance_input:
            return {mode: guidance_input[mode]}

        # Requested category not present — fall back to full guidance
        return guidance_input

    @staticmethod
    def _format_verifier_result(verifier_result: VerifierResult) -> str:
        """Formats a VerifierResult into a concise per-category summary.

        Args:
            verifier_result (VerifierResult): The VerifierResult to format.

        Returns:
            str: A human-readable summary of scores and feedback per category.
        """
        fb = verifier_result.feedback
        lines = [
            f"Overall score: {verifier_result.score:.3f} | "
            f"Compliant: {verifier_result.compliant}",
        ]
        for category in (
            "hard_constraints",
            "assembly",
            "proximity",
            "orientation",
            "flow",
        ):
            worker = getattr(fb, category)
            lines.append(f"\n### {category}\n{worker.to_string()}")
        return "\n".join(lines)
