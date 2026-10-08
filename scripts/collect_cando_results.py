# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""
Collect CANDO ablation results from a run directory into a single JSONL file.

For each ``input<N>_rep<R>`` sub-folder produced by the ablation runner,
reads ``best_layout.json`` for the generated layout. The record's ``id`` is
the ``id`` of row number ``N`` (0-indexed) in the reference dataset jsonl
file (e.g. ``input00_rep00`` -> row 0, ``input01_rep00`` -> row 1, ...).
This writes one line per input record: ``{"id": <int>, "elements": [...]}``.
When multiple repetitions are present, it writes one JSONL file per
repetition, suffixing the requested output name with ``_repNN``.

Each output element only keeps the "id", "class" and "polygon" keys -
auxiliary keys such as "bbox" are dropped.

Usage:
    uv run python scripts/collect_cando_results.py \
        --input logs/my_ablation_run \
        --output results/no_planner_results.jsonl \
        --dataset /path/to/constraints.jsonl

This example produces ``results/no_planner_results_rep00.jsonl``,
``results/no_planner_results_rep01.jsonl``, and so on.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from tqdm import tqdm

# Regex that matches run folders produced by the ablation runner.
_FOLDER_RE = re.compile(r"^input(?P<input_idx>\d+)_rep(?P<rep>\d+)$")


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path) as fh:
        return json.load(fh)


def _load_dataset_ids(dataset_file: Path) -> list[int]:
    """Return the "id" of each row in *dataset_file*, in row order."""
    ids: list[int] = []
    with open(dataset_file) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            ids.append(json.loads(line)["id"])
    return ids


def _clean_elements(elements: list[dict]) -> list[dict]:
    """Keep only "id", "class" and "polygon" for each element."""
    return [
        {"id": el["id"], "class": el["class"], "polygon": el["polygon"]}
        for el in elements
    ]


def collect_results(
    input_dir: Path, output_file: Path, dataset_file: Path
) -> None:
    dataset_ids = _load_dataset_ids(dataset_file)

    # ------------------------------------------------------------------ #
    # Discover run folders and group by input_idx → repetition → run dir.
    # ------------------------------------------------------------------ #
    record_dirs: dict[int, dict[int, Path]] = defaultdict(dict)

    for d in sorted(input_dir.iterdir()):
        if not d.is_dir():
            continue
        m = _FOLDER_RE.match(d.name)
        if not m:
            continue
        input_idx = int(m.group("input_idx"))
        rep_idx = int(m.group("rep"))
        record_dirs[input_idx][rep_idx] = d

    if not record_dirs:
        print(f"No run directories found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    rep_indices = sorted(
        {rep_idx for run_dirs in record_dirs.values() for rep_idx in run_dirs}
    )
    output_suffix = output_file.suffix or ".jsonl"

    for rep_idx in rep_indices:
        results: list[dict] = []
        skipped: list[int] = []
        rep_output = output_file.with_name(
            f"{output_file.stem}_rep{rep_idx:02d}{output_suffix}"
        )

        for input_idx in tqdm(
            sorted(record_dirs),
            desc=f"Collecting repetition {rep_idx}",
            unit="record",
        ):
            run_dir = record_dirs[input_idx].get(rep_idx)
            if run_dir is None:
                skipped.append(input_idx)
                continue
            if input_idx >= len(dataset_ids):
                print(
                    f"  WARNING: input_idx={input_idx} is out of range for "
                    f"dataset {dataset_file} ({len(dataset_ids)} rows), "
                    "skipping.",
                    file=sys.stderr,
                )
                skipped.append(input_idx)
                continue
            record_id = dataset_ids[input_idx]

            best_layout = _load_json(run_dir / "best_layout.json")
            if best_layout is None or "elements" not in best_layout:
                print(
                    f"  WARNING: no best_layout.json/elements for "
                    f"input_idx={input_idx}, rep={rep_idx} "
                    f"(id={record_id}), skipping.",
                    file=sys.stderr,
                )
                skipped.append(input_idx)
                continue

            results.append(
                {
                    "id": record_id,
                    "elements": _clean_elements(best_layout["elements"]),
                }
            )

        rep_output.parent.mkdir(parents=True, exist_ok=True)
        with open(rep_output, "w") as fh:
            for entry in results:
                fh.write(json.dumps(entry) + "\n")

        print(f"Wrote {len(results)} entries → {rep_output}")
        if skipped:
            print(
                f"Skipped {len(skipped)} input indices for rep {rep_idx}: "
                f"{skipped}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect CANDO ablation results (best_layout.json per input) "
            "into a JSONL file."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help=(
            "Path to the run directory containing input<N>_rep<R> "
            "sub-folders."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSONL file path.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help=(
            "Path to the reference dataset jsonl file whose row order "
            "defines the id for each input<N>."
        ),
    )
    args = parser.parse_args()

    input_dir = args.input
    if not input_dir.is_absolute():
        input_dir = Path.cwd() / input_dir

    output_file = args.output
    if not output_file.is_absolute():
        output_file = Path.cwd() / output_file

    dataset_file = args.dataset
    if not dataset_file.is_absolute():
        dataset_file = Path.cwd() / dataset_file

    if not dataset_file.exists():
        print(
            f"ERROR: dataset file not found: {dataset_file}", file=sys.stderr
        )
        sys.exit(1)

    if not input_dir.exists():
        print(
            f"ERROR: input directory not found: {input_dir}", file=sys.stderr
        )
        sys.exit(1)

    collect_results(input_dir, output_file, dataset_file)


if __name__ == "__main__":
    main()
