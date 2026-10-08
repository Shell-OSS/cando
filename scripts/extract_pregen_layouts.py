# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Build a run_generate_zero.py-style initial-layout cache from an existing
grid-search results directory.

scripts/run_eval.py's --pregen-layouts-dir / --pregen-layouts-root expect a
cache laid out as <root>/input{NN}/layout_{MM}/{layout.json,constraints.json}
(exactly what scripts/run_generate_zero.py produces). This script rebuilds
that structure from an older, out-of-repo grid-search run whose trial
folders match "*input{X}_rep{R}*" and contain
artifacts/initial_layout.json. No model calls are needed: constraints.json
is recomputed deterministically from the original input file via
GeneratorZero._build_constraints_dict.

IMPORTANT: X is the 0-based row position of a record in --input-file, NOT
that record's own "id" field (input files may carry an "id" that is
unrelated to row order -- see scripts/run_graph.py's --sample-id lookup).
--input-file must be the exact file (and row order) that was used to
produce --results-dir, since row X there is matched to trial folder
input{X}.

Usage:
    python scripts/extract_pregen_layouts.py <results_dir> \\
        --input-file <input_file> --output-dir <output_dir> [--copy-png]
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

RUN_ID_RE = re.compile(r"input(\d+)_rep(\d+)")


def _load_requirements(path: Path) -> list[dict[str, Any]]:
    """Load requirement records from a .json or .jsonl file, in row order."""
    if path.suffix == ".jsonl":
        records: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    with path.open(encoding="utf-8") as fh:
        parsed = json.load(fh)
    if isinstance(parsed, dict):
        return [parsed]
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild a run_generate_zero.py-style initial-layout cache "
            "from an existing grid-search results directory."
        )
    )
    parser.add_argument(
        "results_dir",
        type=str,
        help=(
            "Grid-search-style results directory containing "
            "*input{X}_rep{R}*/artifacts/initial_layout.json trial "
            "folders (e.g. my_hp_tuning_run_20250101)."
        ),
    )
    parser.add_argument(
        "--input-file",
        type=str,
        required=True,
        help=(
            "The .json/.jsonl input file used to produce results_dir. Row "
            "position X (0-based) must match each trial folder's "
            "input{X} -- NOT that record's own 'id' field."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help=(
            "Cache directory to write into, e.g. data/ablation_initials/full."
        ),
    )
    parser.add_argument(
        "--copy-png",
        action="store_true",
        help="Also copy initial_layout.png alongside layout.json.",
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    input_file = Path(args.input_file)
    output_dir = Path(args.output_dir)

    if not results_dir.exists():
        print(f"ERROR: {results_dir} does not exist.", file=sys.stderr)
        sys.exit(1)
    if not input_file.exists():
        print(f"ERROR: {input_file} does not exist.", file=sys.stderr)
        sys.exit(1)

    requirements = _load_requirements(input_file)

    # _build_constraints_dict is a pure @staticmethod -- no model call, no
    # env vars required, so GeneratorZero does not need to be instantiated.
    from cando.agents.generators.generator_zero.agent import GeneratorZero

    extracted = 0
    skipped: list[tuple[str, str]] = []

    for trial_dir in sorted(results_dir.iterdir()):
        if not trial_dir.is_dir():
            continue
        match = RUN_ID_RE.search(trial_dir.name)
        if not match:
            continue
        input_idx, rep_idx = int(match.group(1)), int(match.group(2))

        layout_path = trial_dir / "artifacts" / "initial_layout.json"
        if not layout_path.exists():
            skipped.append(
                (trial_dir.name, "no artifacts/initial_layout.json")
            )
            continue

        if input_idx >= len(requirements):
            skipped.append(
                (
                    trial_dir.name,
                    f"input_idx {input_idx} out of range for {input_file} "
                    f"({len(requirements)} records)",
                )
            )
            continue

        with open(layout_path, encoding="utf-8") as fh:
            layout = json.load(fh)

        constraints = GeneratorZero._build_constraints_dict(
            requirements[input_idx]
        )

        layout_dir = (
            output_dir / f"input{input_idx:02d}" / f"layout_{rep_idx:02d}"
        )
        layout_dir.mkdir(parents=True, exist_ok=True)

        with open(layout_dir / "layout.json", "w", encoding="utf-8") as fh:
            json.dump(layout, fh, indent=2)
        with open(
            layout_dir / "constraints.json", "w", encoding="utf-8"
        ) as fh:
            json.dump(constraints, fh, indent=2)

        if args.copy_png:
            png_path = trial_dir / "artifacts" / "initial_layout.png"
            if png_path.exists():
                shutil.copyfile(png_path, layout_dir / "layout.png")

        extracted += 1

    print(f"Extracted {extracted} initial layouts to {output_dir}")
    if skipped:
        print(f"Skipped {len(skipped)} trial(s):")
        for name, reason in skipped:
            print(f"  {name}: {reason}")


if __name__ == "__main__":
    main()
