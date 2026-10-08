# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Nearest Neighbors exemplar retriever for initial layout generation."""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from ..base import BaseExemplarRetriever
from .constants import WEIGHTS


class NearestNeighborsExemplarRetriever(BaseExemplarRetriever):
    """Nearest Neighbors based exemplar retriever for finding similar
    layouts.

    This class loads feature vectors from a parquet file and uses
    scikit-learn's StandardScaler to normalize features before performing
    NN search. The scaler configuration is loaded from a JSON file containing
    mean, scale, and variance values computed during training.

    Attributes:
        scaler (StandardScaler): Fitted StandardScaler for feature
            normalization.
        features_df (pd.DataFrame): DataFrame containing all feature
            vectors.
        nn_model (NearestNeighbors): Fitted NearestNeighbors model for
            retrieval.
        feature_columns (List[str]): List of feature column names.
        feature_weights (np.ndarray): Weights for each feature in distance
            calculation.
    """

    def __init__(
        self,
        parquet_path: str,
        dataset_path: str,
        scaler_config_path: str,
        n_neighbors: int = 8,
        metric: str = "minkowski",
        feature_weights: np.ndarray | None = None,
        auto_weight: bool = True,
    ) -> None:
        """Initializes the KNN exemplar retriever.

        Args:
            parquet_path (str): Path to the parquet file containing feature
                vectors.
            dataset_path (str): Path to the dataset annotations file.
            scaler_config_path (str): Path to the scaler configuration
                JSON file.
            n_neighbors (int): Default number of neighbors for the KNN model.
            metric (str): Distance metric for KNN. Default is 'minkowski'
                which supports weighting.
            feature_weights (np.ndarray | None): Custom weights for each
                feature. If None and auto_weight=True, weights are computed
                automatically based on feature priorities.
            auto_weight (bool): If True, automatically compute feature weights
                based on feature name patterns (element counts, entrance/exit
                positions, layout metrics).

        Raises:
            FileNotFoundError: If parquet or scaler config file doesn't
                exist.
            ValueError: If scaler configuration is invalid or features
                are missing.
        """
        super().__init__()

        self._load_scaler_config(scaler_config_path)

        self._load_features(parquet_path)

        self._load_annotations(dataset_path)

        if feature_weights is not None:
            self.feature_weights = np.asarray(feature_weights)
            if len(self.feature_weights) != len(self.feature_columns):
                raise ValueError(
                    f"feature_weights length "
                    f"({len(self.feature_weights)}) "
                    f"must match number of features "
                    f"({len(self.feature_columns)})"
                )
        elif auto_weight:
            self.feature_weights = self._compute_feature_weights()
        else:
            self.feature_weights = np.ones(len(self.feature_columns))

        # Apply weights to scaled features
        self.weighted_features = self.scaled_features * np.sqrt(
            self.feature_weights
        )

        self.nn_model = NearestNeighbors(
            n_neighbors=n_neighbors, metric=metric, algorithm="auto", p=2
        )
        self.nn_model.fit(self.weighted_features)

    def _load_scaler_config(self, config_path: str) -> None:
        """Loads StandardScaler configuration from JSON file.

        The JSON file should contain 'mean', 'scale', 'var', and
        'feature_columns'.

        Args:
            config_path (str): Path to the scaler configuration JSON file.

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config format is invalid
        """
        config_path_obj = Path(config_path)
        if not config_path_obj.exists():
            raise FileNotFoundError(
                f"Scaler config file not found: {config_path}"
            )

        with open(config_path) as f:
            config = json.load(f)

        required_keys = ["mean", "scale", "var", "feature_columns"]
        missing_keys = [key for key in required_keys if key not in config]
        if missing_keys:
            raise ValueError(
                f"Scaler config missing required keys: {missing_keys}"
            )

        self.scaler = StandardScaler()
        self.scaler.mean_ = np.array(config["mean"])
        self.scaler.scale_ = np.array(config["scale"])
        self.scaler.var_ = np.array(config["var"])
        self.scaler.n_features_in_ = len(config["feature_columns"])
        self.feature_columns = config["feature_columns"]

    def _load_features(self, parquet_path: str) -> None:
        """Loads feature vectors from parquet file and scales them.

        Args:
            parquet_path (str): Path to the parquet file containing features.

        Raises:
            FileNotFoundError: If parquet file doesn't exist.
            ValueError: If required features are missing from the DataFrame.
        """
        super()._load_features(parquet_path)

        if self.features_df is None:
            raise ValueError(
                f"Failed to load features from parquet file: {parquet_path}"
            )

        feature_matrix = self.features_df[self.feature_columns].values
        self.scaled_features = self.scaler.transform(feature_matrix)

    def _load_annotations(self, dataset_path: str) -> None:
        """Loads annotations from the configured dataset source.

        Args:
            dataset_path (str): Path to dataset JSONL or parquet.

        Raises:
            FileNotFoundError: If the dataset file doesn't exist.
        """
        dataset_path_obj = Path(dataset_path)
        if not dataset_path_obj.exists():
            raise FileNotFoundError(f"Dataset file not found: {dataset_path}")

        rows = []
        with dataset_path_obj.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                layout_id = str(record["id"])
                for elem in record.get("elements", []):
                    coords = elem.get("coordinates") or elem.get("polygon")
                    if not coords or len(coords) < 3:
                        continue
                    rows.append(
                        {
                            "layout_id": layout_id,
                            "ann_class_id": elem["class"],
                            "geometry": Polygon(coords),
                        }
                    )

        self.annotations_df = gpd.GeoDataFrame(rows, geometry="geometry")

    def _compute_feature_weights(self) -> np.ndarray:
        """Computes feature weights based on feature priorities defined in
        `constants.py`.

        Priority categories:
        - Element counts per class
        - Entrance/exit positions and distances
        - Other features (averages for width/height/area)
        - Layout metrics (area, perimeter, etc.)

        Returns:
            np.ndarray: Array of weights for each feature.
        """
        weights = np.ones(len(self.feature_columns))

        for idx, feature_name in enumerate(self.feature_columns):
            # Element counts per class
            if feature_name.startswith("count_") or feature_name in [
                "num_entrances",
                "num_exits",
            ]:
                weights[idx] = WEIGHTS.ELEMENT_COUNTS_PER_CLASS
            elif feature_name in [
                "total_elements",
                "total_distinct_elements",
            ]:
                weights[idx] = WEIGHTS.TOTAL_ELEMENT_COUNTS

            # Entrance/exit positions and distances
            elif any(
                substring in feature_name
                for substring in ["entrance_", "exit_", "_entrance", "_exit"]
            ):
                weights[idx] = WEIGHTS.ENTRANCE_AND_EXIT

            # Layout metrics
            elif feature_name.startswith("layout_") or feature_name in [
                "coverage_ratio"
            ]:
                weights[idx] = WEIGHTS.LAYOUT

            # All other features
            else:
                weights[idx] = WEIGHTS.OTHER_FEATURES

        return weights

    def retrieve(self, query_vector: np.ndarray, top_k: int | None = None):
        """Retrieves top k nearest neighbors for the given query vector.

        The query vector is normalized using the loaded scaler before
        performing the nearest neighbor search. Returns filtered annotations
        for the top k nearest neighbor layouts.

        Args:
            query_vector (np.ndarray): Feature vector to find neighbors for.
                Should have the same dimensionality as the feature columns
                (n_features,).
            top_k (int | None): Number of nearest neighbors to retrieve. If
                None, uses the default n_neighbors set during initialization.

        Returns:
            GeoDataFrame: Filtered annotations containing only the rows for
                the top_k nearest neighbor layouts.

        Raises:
            ValueError: If query_vector has incorrect dimensionality.
        """
        query_vector = np.asarray(query_vector)
        if query_vector.ndim == 1:
            query_vector = query_vector.reshape(1, -1)

        if query_vector.shape[1] != len(self.feature_columns):
            raise ValueError(
                f"Query vector has {query_vector.shape[1]} features, "
                f"but {len(self.feature_columns)} expected"
            )

        if self.features_df is None or self.annotations_df is None:
            raise ValueError(
                "Features and annotations must be loaded before retrieval"
            )

        scaled_query = self.scaler.transform(query_vector)

        weighted_query = scaled_query * np.sqrt(self.feature_weights)

        if top_k is None:
            _, indices = self.nn_model.kneighbors(weighted_query)
        else:
            _, indices = self.nn_model.kneighbors(
                weighted_query, n_neighbors=top_k
            )

        key_column = (
            "layout_id"
            if "layout_id" in self.features_df.columns
            else "image"
        )

        neighbor_indices = indices[0]
        neighbor_layout_ids = [
            self.features_df.iloc[idx][key_column] for idx in neighbor_indices
        ]

        filtered_annotations = self.annotations_df[
            self.annotations_df[key_column].isin(neighbor_layout_ids)
        ]

        return filtered_annotations
