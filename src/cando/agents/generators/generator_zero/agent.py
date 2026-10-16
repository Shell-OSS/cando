# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Generator Zero agent for initial layout generation and refinement."""

import json
import logging
import os
import re

from langchain.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.messages import BaseMessage

from cando.config import ModelsConfig
from cando.constants.classes import INPUT_CLASSES
from cando.model_factory import resolve_chat_model
from cando.utils.converters import (
    convert_to_bbox_layout,
    convert_to_polygons_layout,
)
from cando.utils.exemplar_retrievers import NearestNeighborsExemplarRetriever
from cando.utils.generic import json_dumps, round_floats
from cando.utils.geometry import extract_element_dimensions

from .prompt import (
    GENERATOR_ZERO_INITIAL_PROMPT,
    GENERATOR_ZERO_REFINEMENT_PROMPT,
)
from .tools import (
    adjust_shapes,
    apply_layout_modifications,
    validate_bbox_layout,
    validate_layout,
)

logger = logging.getLogger(__name__)


class GeneratorZero:
    """Generates the initial layout from raw user requirements.

    On construction, resolves the chat model for the "generator_zero"
    role and loads the k-NN exemplar retriever (parquet feature vectors +
    dataset annotations). Instantiation is therefore not free — callers
    should construct a single instance and reuse it (see
    `cando.graph.nodes.generate_zero._get_generator_zero`).

    Args:
        model (str): LangChain model identifier for the "generator_zero"
            role. Defaults to `ModelsConfig.generator_zero`.

    Raises:
        ValueError: If `FEATURE_VECTORS_PARQUET_PATH`,
            `DATASET_JSONL_PATH` or `SCALER_CONFIG_PATH` is not set in
            the environment.
    """

    def __init__(self, model: str = ModelsConfig.generator_zero):
        # Initialize the agent with validation tool and context schema
        self.model = resolve_chat_model(
            "generator_zero", model, max_retries=3
        )

        # Initialize exemplar retriever
        FEATURE_VECTORS_PATH = os.environ.get("FEATURE_VECTORS_PARQUET_PATH")
        DATASET_PATH = os.environ.get("DATASET_JSONL_PATH")
        SCALER_CONFIG_PATH = os.environ.get("SCALER_CONFIG_PATH")

        if not (FEATURE_VECTORS_PATH and DATASET_PATH and SCALER_CONFIG_PATH):
            raise ValueError(
                "FEATURE_VECTORS_PARQUET_PATH, DATASET_JSONL_PATH and "
                "SCALER_CONFIG_PATH environment variables must be set."
            )

        self.retriever = NearestNeighborsExemplarRetriever(
            FEATURE_VECTORS_PATH,
            DATASET_PATH,
            scaler_config_path=SCALER_CONFIG_PATH,
        )

    @staticmethod
    def _build_constraints_dict(constraints: dict) -> dict:
        """Normalizes structured input constraints to the agent format."""
        if not isinstance(constraints, dict):
            raise TypeError(
                "GeneratorZero expects constraints as a structured dict."
            )

        elements = []

        for elem in constraints.get("elements", []):
            class_name = elem.get("class", "").strip()
            if not class_name:
                continue

            polygon = elem.get("polygon") or elem.get("coordinates")

            if polygon and class_name in INPUT_CLASSES:
                elements.append(
                    {
                        "class": class_name,
                        "polygon": polygon,
                    }
                )
                continue

            width, height = extract_element_dimensions(elem)
            if width is not None and height is not None:
                elements.append(
                    {
                        "class": class_name,
                        "width": width,
                        "height": height,
                    }
                )
                continue

            elements.append({"class": class_name})

        return {"elements": elements}

    def _build_exemplar_pair(self, layout_data) -> str:
        """Builds an input-output exemplar pair from one layout's annotations.

        Args:
            layout_data: GeoDataFrame annotations for a single layout.

        Returns:
            str: Formatted string with input and output JSON.
        """
        input_elements = []
        output_elements = []
        element_id = 0

        for _, row in layout_data.iterrows():
            class_name = row["ann_class_id"]
            geom = row["geometry"]

            if geom.is_empty:
                continue

            # Extract polygon coordinates
            if hasattr(geom, "exterior"):
                coords = list(geom.exterior.coords)
                polygon = [[round(x, 2), round(y, 2)] for x, y in coords]

                # Build output element
                output_elements.append(
                    {
                        "id": element_id,
                        "class": class_name,
                        "polygon": polygon,
                    }
                )
                element_id += 1

                # Build input element
                # Layout Polygon, Entrance, Exit: polygon-based
                if class_name in INPUT_CLASSES:
                    input_elements.append(
                        {"class": class_name, "polygon": polygon}
                    )
                # Other elements: width/height from bounds
                else:
                    bounds = geom.bounds
                    width = round(bounds[2] - bounds[0], 2)
                    height = round(bounds[3] - bounds[1], 2)
                    input_elements.append(
                        {
                            "class": class_name,
                            "width": width,
                            "height": height,
                        }
                    )

        input_dict = {"elements": input_elements}
        output_dict = {"elements": output_elements}

        # Format as string
        result = "**Input:**\n"
        result += f"```json\n{json_dumps(round_floats(input_dict))}\n```\n"
        result += "\n**Output:**\n"
        result += f"```json\n{json_dumps(round_floats(output_dict))}\n```\n"

        return result

    def _build_context(self, constraints: dict) -> str:
        """Builds the context for the generator using dynamic exemplars.

        Args:
            constraints (dict): The layout input constraints.

        Returns:
            str: Formatted context string with input-output exemplar pairs.
        """
        # Retrieve dynamic exemplars based on constraints
        query_vector = self.retriever.build_query_vector(constraints)
        exemplars_df = self.retriever.retrieve(query_vector, top_k=8)

        # Format exemplars into context string
        context_parts = ["Here are some example layouts:\n"]

        key_column = (
            "layout_id" if "layout_id" in exemplars_df.columns else "image"
        )

        # Group by layout id and build exemplar pairs
        for idx, (_, layout_data) in enumerate(
            exemplars_df.groupby(key_column), 1
        ):
            context_parts.append(f"\n### Example {idx}\n")
            context_parts.append(self._build_exemplar_pair(layout_data))

        return "".join(context_parts)

    def _build_modifications_dict(self, response_content: str) -> dict:
        """Parses the LLM response to extract modifications dictionary.

        Args:
            response_content (str): The raw response content from the LLM.

        Returns:
            dict: Modifications dictionary mapping element IDs to changes,
                  or empty dict if parsing fails.
        """
        try:
            # Extract JSON from response (handle markdown code blocks)
            content = response_content.strip()
            if content.startswith("```"):
                # Remove markdown code blocks
                content = re.sub(r"```json\s*", "", content)
                content = re.sub(r"```\s*", "", content)
                content = content.strip()

            modifications = json.loads(content)
            return modifications
        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse modifications: %s\nResponse content: %s",
                e,
                response_content,
            )
            return {}

    def _generate_and_select_best_candidate(
        self, prompt: list, constraints_dict: dict, num_candidates: int = 10
    ) -> AIMessage | None:
        """Generates multiple layout candidates and selects the best one.

        Args:
            prompt (list): The prompt messages to send to the model.
            constraints_dict (dict): The constraints dictionary for
                validation.
            num_candidates (int): Number of candidates to generate.

        Returns:
            AIMessage | None: The response with the highest validation score.
        """
        try:
            responses = self.model.batch(
                [prompt] * num_candidates, return_exceptions=True
            )
        except Exception as e:
            logger.warning("Error during batch generation: %s", e)
            return None

        best_response = None
        best_score = -1.0

        for resp in responses:
            content = resp.content if isinstance(resp.content, str) else ""
            layout = adjust_shapes(content, constraints_dict)
            resp.content = json_dumps(round_floats(layout))
            validation = validate_layout(resp.content, constraints_dict)

            # Use validation score to select best candidate
            score = validation.get("score", 0.0)

            if score > best_score:
                best_score = score
                best_response = resp
                if score >= 1.0:
                    break

        return best_response

    def generate(
        self,
        constraints: dict,
        max_iterations: int = 10,
        use_icl: bool = True,
        num_candidates: int = 10,
    ) -> tuple[dict, dict, list]:
        """Generates an initial layout based on provided constraints and
        dynamic exemplars, then refines it using bbox-based modifications.

        Two-stage process:
        1. Initial Generation: Generate best of N polygon-based layouts
        2. Refinement: Convert to bbox format and iteratively refine using
           position/rotation modifications

        Args:
            constraints (dict): Structured layout input constraints.
            max_iterations (int): Maximum refinement iterations.
            use_icl (bool): Whether to include retrieved exemplar context
                (in-context learning) in the initial-generation prompt.
                False implements the no_icl ablation mode: the number of
                initial candidates, candidate selection, and internal
                refinement loop are otherwise unchanged.
            num_candidates (int): Number of initial candidates to generate
                and select the best of (see
                `_generate_and_select_best_candidate`).

        Returns:
            tuple: The generated layout (polygon format), constraints
                dictionary, and conversation history.

        Raises:
            ValueError: If the agent doesn't return valid JSON.
        """
        intermediate_layouts = []
        constraints_dict = self._build_constraints_dict(constraints)
        constraints_msg = f"""

Generate a layout that meets the following constraints:
{json_dumps(round_floats(constraints_dict))}
"""

        # Build context with exemplars (skipped entirely in no_icl mode)
        context = self._build_context(constraints_dict) if use_icl else ""

        # Build the user message with context and constraints
        user_message = f"{context}{constraints_msg}"

        # INITIAL GENERATION STAGE
        initial_prompt = [
            SystemMessage(content=GENERATOR_ZERO_INITIAL_PROMPT),
            HumanMessage(content=user_message),
        ]

        # Generate multiple candidates and select the best one
        response = self._generate_and_select_best_candidate(
            initial_prompt, constraints_dict, num_candidates
        )

        # Adjust shapes to match constraints
        try:
            if response is None:
                raise ValueError(
                    f"No valid response received from model after "
                    f"generating {num_candidates} candidate(s); check "
                    "logs above for the batch-generation error."
                )

            content = (
                response.content if isinstance(response.content, str) else ""
            )
            layout = adjust_shapes(content, constraints_dict)
            response.content = json_dumps(round_floats(layout))
        except Exception as e:
            raise ValueError(
                f"Failed to adjust shapes in initial generation: {str(e)}"
            ) from e

        intermediate_layouts.append(layout)

        # Validate initial layout
        validation = validate_layout(response.content, constraints_dict)

        # If initial layout is perfect, return it
        if validation["valid"]:
            return layout, constraints_dict, []

        # REFINEMENT STAGE
        # Convert to bbox layout
        bbox_layout = convert_to_bbox_layout(layout, INPUT_CLASSES)
        bbox_json = json_dumps(round_floats(bbox_layout))

        refinement_conversation: list[BaseMessage] = []

        for _ in range(max_iterations):
            # Validate current bbox layout
            validation = validate_bbox_layout(bbox_layout, constraints_dict)

            if validation["valid"]:
                break

            # Prepare refinement prompt
            refinement_msg = f"""
Below is the current layout in bbox format that you need to refine, along
with validation feedback indicating the issues.

Current layout state (bbox format):
{bbox_json}

Validation feedback:
{validation["feedback"]}

Please provide modifications to fix the violations. Output only the
modifications JSON in the format:
{{"<element_id>": {{"x": <new_x>, "y": <new_y>, "theta": <new_theta>}}}}
"""

            refinement_history: list[BaseMessage] = [
                SystemMessage(content=GENERATOR_ZERO_REFINEMENT_PROMPT)
            ]

            # Add recent conversation history if available
            if refinement_conversation:
                refinement_history.extend(refinement_conversation[-4:])

            # Add the current message
            refinement_history.append(HumanMessage(content=refinement_msg))

            # Get modifications from LLM
            response = self.model.invoke(refinement_history)
            refinement_conversation.append(
                HumanMessage(content=refinement_msg)
            )
            refinement_conversation.append(response)

            # Parse modifications
            if not isinstance(response.content, str):
                logger.warning(
                    "Unexpected response content type: %s",
                    type(response.content),
                )
                continue
            content = response.content
            modifications = self._build_modifications_dict(content)

            # Apply modifications
            try:
                bbox_layout = apply_layout_modifications(
                    bbox_layout, modifications
                )
                intermediate_layouts.append(
                    convert_to_polygons_layout(bbox_layout, INPUT_CLASSES)
                )
                bbox_json = json_dumps(round_floats(bbox_layout))
            except Exception as e:
                logger.warning(
                    "Failed to apply refinement modifications, stopping "
                    "refinement early: %s",
                    e,
                )
                break

        # Convert final bbox layout to polygon format
        final_layout = convert_to_polygons_layout(bbox_layout, INPUT_CLASSES)

        return (final_layout, constraints_dict, refinement_conversation)
