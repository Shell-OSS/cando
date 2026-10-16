# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Extract per-layout feature vectors and fit a scaler for exemplar retrieval.

Computes a fixed-schema feature vector per layout in a training
constraints .jsonl file (per-class element counts/dimensions, layout
shape metrics, entrance/exit spatial relationships -- see
BOX_GEOM_CLASSES in src/cando/constants/classes.py), then writes:
    - <output-dir>/feature_vectors.parquet: raw feature vectors
    - --scaler-output (defaults to scaler_config.json inside
      <output-dir>): fitted scaler parameters
      (mean/scale/var/feature_columns)

Features are computed with BaseExemplarRetriever.compute_features, the
same code GeneratorZero uses to featurize the query constraints at
inference time, so corpus and query features are identical by
construction.

feature_vectors.parquet and the scaler config are the two artifacts
GeneratorZero's NearestNeighborsExemplarRetriever loads at runtime via
the FEATURE_VECTORS_PARQUET_PATH / SCALER_CONFIG_PATH environment
variables (see src/cando/agents/generators/generator_zero/agent.py):
the retriever applies the fitted scaler itself to both the raw corpus
and each query vector.

Usage:
    uv run python scripts/extract_exemplar_features.py \\
        data/train_constraints.jsonl --output-dir data/feature_vectors
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

from cando.constants.classes import BOX_GEOM_CLASSES
from cando.utils.exemplar_retrievers.base import BaseExemplarRetriever


def load_constraint_records(constraints_path: Path) -> list[dict]:
    """Loads the constraints JSONL into a list of records."""
    records = []
    with open(constraints_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    return records


def get_feature_columns() -> list[str]:
    """Returns the ordered feature schema.

    Returns:
        list[str]: Feature names in the order stored in the scaler config.
    """
    columns = [
        "total_elements",
        "total_distinct_elements",
        "num_entrances",
        "num_exits",
    ]

    for class_name in BOX_GEOM_CLASSES:
        columns += [
            f"count_{class_name}",
            f"avg_width_{class_name}",
            f"avg_height_{class_name}",
            f"avg_area_{class_name}",
        ]

    columns += [
        "avg_entrance_width",
        "avg_entrance_height",
        "avg_exit_width",
        "avg_exit_height",
        "layout_area",
        "layout_perimeter",
        "layout_compactness",
        "layout_width",
        "layout_height",
        "layout_aspect_ratio",
        "entrance_exit_same_side",
        "entrance_north_of_exit",
        "entrance_south_of_exit",
        "entrance_east_of_exit",
        "entrance_west_of_exit",
        "min_entrance_exit_distance",
        "max_entrance_exit_distance",
        "avg_entrance_exit_distance",
        "coverage_ratio",
    ]

    return columns


def compute_feature_vectors_for_all_layouts(
    records: list[dict],
) -> pd.DataFrame:
    """Computes feature vectors for all layouts in the dataset.

    Args:
        records (list[dict]): Constraints JSONL records, each with an 'id'
            and 'elements'.

    Returns:
        DataFrame: One row per layout, with a leading 'layout_id' column.

    Raises:
        ValueError: If a record cannot be featurized (e.g. no Layout
            Polygon).
    """
    print(f"Processing {len(records)} layouts...")

    columns = get_feature_columns()
    rows = []
    for record in tqdm(records, desc="Computing features"):
        layout_id = str(record["id"])
        try:
            features = BaseExemplarRetriever.compute_features(record, columns)
        except ValueError as exc:
            raise ValueError(f"Layout {layout_id}: {exc}") from exc

        rows.append(
            {"layout_id": layout_id, **{c: features[c] for c in columns}}
        )

    return pd.DataFrame(rows, columns=["layout_id", *columns])


def save_scaler_config(
    feature_vectors: pd.DataFrame, scaler_path: Path
) -> list[str]:
    """Fits a scaler to raw feature vectors and saves its parameters.

    Args:
        feature_vectors (DataFrame): DataFrame with 'layout_id' column and
            feature columns.
        scaler_path (Path): Path to save the fitted scaler parameters.

    Returns:
        list[str]: Feature column names in scaler order.
    """
    # Separate layout ids from features
    feature_columns = [
        col for col in feature_vectors.columns if col != "layout_id"
    ]
    feature_values = feature_vectors[feature_columns].values

    # Fit using StandardScaler
    scaler = StandardScaler()
    scaler.fit(feature_values)

    if scaler.mean_ is None or scaler.scale_ is None or scaler.var_ is None:
        raise ValueError("StandardScaler parameters were not fitted.")

    # Save scaler
    scaler_data = {
        "feature_columns": feature_columns,
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "var": scaler.var_.tolist(),
    }
    with open(scaler_path, "w") as f:
        json.dump(scaler_data, f, indent=2)

    return feature_columns


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract feature vectors and fit a scaler from layout "
            "constraints."
        )
    )
    parser.add_argument(
        "constraints_path",
        type=str,
        help=(
            "Path to the training constraints .jsonl file to extract "
            "exemplar feature vectors from (same schema as the test "
            "constraints file)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help=("Directory to write feature_vectors.parquet into."),
    )
    parser.add_argument(
        "--scaler-output",
        type=str,
        default=None,
        help=(
            "Output path for the fitted scaler configuration JSON file. "
            "Defaults to scaler_config.json inside --output-dir."
        ),
    )

    args = parser.parse_args()

    constraints_path = Path(args.constraints_path)
    output_dir = Path(args.output_dir)
    scaler_output = (
        Path(args.scaler_output)
        if args.scaler_output
        else output_dir / "scaler_config.json"
    )

    if not constraints_path.exists():
        print(f"ERROR: {constraints_path} does not exist.", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    scaler_output.parent.mkdir(parents=True, exist_ok=True)

    # Load data
    print(f"Loading data from {constraints_path}...")
    records = load_constraint_records(constraints_path)

    # Compute feature vectors
    print("Computing feature vectors for all layouts...")
    feature_vectors = compute_feature_vectors_for_all_layouts(records)
    print(f"Computed features for {len(feature_vectors)} layouts")

    # Fit and save scaler
    print(f"Fitting scaler and saving parameters to {scaler_output}...")
    feature_columns = save_scaler_config(
        feature_vectors, scaler_path=scaler_output
    )
    print(f"Number of features: {len(feature_columns)}")

    # Save raw features
    output_path = output_dir / "feature_vectors.parquet"
    print(f"Saving feature vectors to {output_path}...")
    feature_vectors.to_parquet(output_path)

    print("Feature extraction complete!")


if __name__ == "__main__":
    main()
