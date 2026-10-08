# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Base class for exemplar retrievers."""

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from cando.constants.classes import INPUT_CLASSES
from cando.utils.exemplar_retrievers.boundary_sides import share_side


class BaseExemplarRetriever(ABC):
    """Abstract base class for exemplar retrievers.

    Provides common functionality for building query vectors from constraint
    dictionaries and loading feature data from parquet files.

    Attributes:
        features_df (pd.DataFrame): DataFrame containing all feature vectors.
        feature_columns (List[str]): List of feature column names.
    """

    def __init__(self) -> None:
        """Initializes the base retriever."""
        self.features_df: pd.DataFrame | None = None
        self.feature_columns: list[str] = []
        self.annotations_df: pd.DataFrame | None = None

    @abstractmethod
    def retrieve(self, query_vector: np.ndarray, top_k: int | None = None):
        """Retrieves top k nearest neighbors for the given query vector.

        Args:
            query_vector (np.ndarray): Feature vector to find neighbors for.
            top_k (int | None): Number of nearest neighbors to retrieve.
        Returns:
            GeoDataFrame or similar: Filtered annotations for k neighbors.
        """
        pass

    def _load_features(self, parquet_path: str) -> None:
        """Loads feature vectors from parquet file.

        Args:
            parquet_path (str): Path to the parquet file containing features.

        Raises:
            FileNotFoundError: If parquet file doesn't exist.
            ValueError: If required features are missing from the DataFrame.
        """
        parquet_path_obj = Path(parquet_path)
        if not parquet_path_obj.exists():
            raise FileNotFoundError(f"Parquet file not found: {parquet_path}")

        self.features_df = pd.read_parquet(parquet_path)

        missing_features = [
            col
            for col in self.feature_columns
            if col not in self.features_df.columns
        ]
        if missing_features:
            raise ValueError(
                f"Features missing from parquet file: {missing_features}"
            )

    def build_query_vector(self, constraints: dict) -> np.ndarray:
        """Builds a query feature vector from a constraints dictionary.

        Extracts features matching the feature columns from the constraints
        dictionary, including counts, averages, and layout geometry features.

        Args:
            constraints (dict): Constraints dictionary with 'elements' key
                containing a list of element dictionaries. Each element
                should have 'class' and either 'polygon' (for Layout Polygon/
                Entrance/Exit) or 'width' and 'height'.

        Returns:
            np.ndarray: Feature vector matching the feature columns
                (1, n_features).

        Raises:
            ValueError: If constraints format is invalid or Layout is
                missing.
        """
        features = self.compute_features(constraints, self.feature_columns)
        query_vector = np.array(
            [features[col] for col in self.feature_columns]
        )

        return query_vector.reshape(1, -1)

    @staticmethod
    def compute_features(
        constraints: dict, feature_columns: list[str]
    ) -> dict[str, float]:
        """Computes the feature dictionary for one constraints record.

        Single source of truth for both query-time features and the
        corpus features written by scripts/extract_exemplar_features.py,
        so the two cannot drift apart.

        Args:
            constraints (dict): Constraints dictionary (see
                `build_query_vector`).
            feature_columns (list[str]): Feature names to populate;
                features not computed here stay 0.0.

        Returns:
            dict[str, float]: Mapping from feature name to value.

        Raises:
            ValueError: If constraints format is invalid or Layout is
                missing.
        """
        if "elements" not in constraints:
            raise ValueError("Constraints must contain 'elements' key")

        elements = constraints["elements"]

        features = {col: 0.0 for col in feature_columns}

        # Separate elements by type
        layout_elem = None
        entrances = []
        exits = []
        typed_elements = []

        for elem in elements:
            class_name = elem.get("class", "")

            if class_name == INPUT_CLASSES[0]:
                layout_elem = elem
            elif class_name == INPUT_CLASSES[1]:
                entrances.append(elem)
            elif class_name == INPUT_CLASSES[2]:
                exits.append(elem)
            else:
                typed_elements.append(elem)

        # Validate layout exists
        if layout_elem is None or "polygon" not in layout_elem:
            raise ValueError(
                "Constraints must include Layout Polygon polygon"
            )

        layout_polygon = Polygon(layout_elem["polygon"])
        features["layout_area"] = layout_polygon.area
        features["layout_perimeter"] = layout_polygon.length

        # Calculate compactness (isoperimetric quotient)
        if features["layout_perimeter"] > 0:
            features["layout_compactness"] = (
                4
                * np.pi
                * features["layout_area"]
                / (features["layout_perimeter"] ** 2)
            )

        bounds = layout_polygon.bounds  # (minx, miny, maxx, maxy)
        features["layout_width"] = bounds[2] - bounds[0]
        features["layout_height"] = bounds[3] - bounds[1]

        if features["layout_height"] > 0:
            features["layout_aspect_ratio"] = (
                features["layout_width"] / features["layout_height"]
            )

        # Process entrances
        features["num_entrances"] = len(entrances)
        if entrances:
            entrance_widths = []
            entrance_heights = []
            for entrance in entrances:
                if "polygon" in entrance:
                    poly = Polygon(entrance["polygon"])
                    bounds = poly.bounds
                    entrance_widths.append(bounds[2] - bounds[0])
                    entrance_heights.append(bounds[3] - bounds[1])

            if entrance_widths:
                features["avg_entrance_width"] = np.mean(entrance_widths)
                features["avg_entrance_height"] = np.mean(entrance_heights)

        # Process exits
        features["num_exits"] = len(exits)
        if exits:
            exit_widths = []
            exit_heights = []
            for exit_elem in exits:
                if "polygon" in exit_elem:
                    poly = Polygon(exit_elem["polygon"])
                    bounds = poly.bounds
                    exit_widths.append(bounds[2] - bounds[0])
                    exit_heights.append(bounds[3] - bounds[1])

            if exit_widths:
                features["avg_exit_width"] = np.mean(exit_widths)
                features["avg_exit_height"] = np.mean(exit_heights)

        # Calculate entrance-exit spatial relationships
        if entrances and exits:
            BaseExemplarRetriever._calculate_entrance_exit_features(
                layout_polygon, entrances, exits, features
            )

        # Process typed elements (with width/height)
        element_groups: dict[str, list[dict]] = {}
        for elem in typed_elements:
            class_name = elem.get("class", "")
            if class_name:
                if class_name not in element_groups:
                    element_groups[class_name] = []
                element_groups[class_name].append(elem)

        # Calculate count and average features for each element type
        for class_name, group in element_groups.items():
            count_key = f"count_{class_name}"
            avg_width_key = f"avg_width_{class_name}"
            avg_height_key = f"avg_height_{class_name}"
            avg_area_key = f"avg_area_{class_name}"

            if count_key in features:
                features[count_key] = len(group)

            widths = [e["width"] for e in group if "width" in e]
            heights = [e["height"] for e in group if "height" in e]

            if widths and avg_width_key in features:
                features[avg_width_key] = np.mean(widths)
            if heights and avg_height_key in features:
                features[avg_height_key] = np.mean(heights)
            if widths and heights and avg_area_key in features:
                areas = [w * h for w, h in zip(widths, heights, strict=False)]
                features[avg_area_key] = np.mean(areas)

        features["total_elements"] = len(typed_elements)
        features["total_distinct_elements"] = len(element_groups)

        if features["layout_area"] > 0:
            total_element_area = sum(
                elem.get("width", 0) * elem.get("height", 0)
                for elem in typed_elements
            )
            features["coverage_ratio"] = (
                total_element_area / features["layout_area"]
            )

        return features

    @staticmethod
    def _calculate_entrance_exit_features(
        layout_polygon: Polygon,
        entrances: list,
        exits: list,
        features: dict,
    ) -> None:
        """Calculates spatial relationship features between entrances/exits.

        Args:
            layout_polygon (Polygon): Layout boundary polygon.
            entrances (list): List of entrance element dictionaries.
            exits (list): List of exit element dictionaries.
            features (dict): Feature dictionary to update with calculated
                values.
        """
        entrance_polys = [
            Polygon(e["polygon"]) for e in entrances if "polygon" in e
        ]
        exit_polys = [Polygon(e["polygon"]) for e in exits if "polygon" in e]
        entrance_centroids = [p.centroid for p in entrance_polys]
        exit_centroids = [p.centroid for p in exit_polys]

        if not entrance_centroids or not exit_centroids:
            return

        distances = []
        for ent_centroid in entrance_centroids:
            for exit_centroid in exit_centroids:
                distances.append(ent_centroid.distance(exit_centroid))

        if distances:
            features["min_entrance_exit_distance"] = min(distances)
            features["max_entrance_exit_distance"] = max(distances)
            features["avg_entrance_exit_distance"] = np.mean(distances)

        # Same side: an entrance and an exit touch a common boundary side
        if share_side(layout_polygon, entrance_polys, exit_polys):
            features["entrance_exit_same_side"] = 1.0

        for ent_c in entrance_centroids:
            for exit_c in exit_centroids:
                # Directional relationships
                if ent_c.y > exit_c.y:
                    features["entrance_north_of_exit"] = 1.0
                elif ent_c.y < exit_c.y:
                    features["entrance_south_of_exit"] = 1.0

                if ent_c.x > exit_c.x:
                    features["entrance_east_of_exit"] = 1.0
                elif ent_c.x < exit_c.x:
                    features["entrance_west_of_exit"] = 1.0
