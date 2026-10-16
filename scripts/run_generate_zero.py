# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""GeneratorZero batch runner.

Runs GeneratorZero for each eval input in a constraints .jsonl file,
generating N initial layouts per input with a thread pool. Results
(layout.json, constraints.json, layout.png) are saved to a timestamped
directory under logs/, alongside a top-level summary.json.

Usage:
    python scripts/run_generate_zero.py REQUIREMENTS [--n-layouts N] \
        [--max-iterations N] [--input-indices IDX [IDX ...]] \
        [--output-dir DIR] [--mode {full,no_icl}] \
        [--inference-profile {no_thinking,fast,max}] [--n-workers N]
"""

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_N_LAYOUTS = 1
DEFAULT_MAX_ITERATIONS = 10  # GeneratorZero internal refinement iterations
N_WORKERS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_inputs(inputs_file: Path) -> list[dict]:
    if not inputs_file.exists():
        print(f"ERROR: {inputs_file} not found.", file=sys.stderr)
        sys.exit(1)
    inputs = []
    with open(inputs_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                inputs.append(json.loads(line))
    return inputs


def _write_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _save_layout_png(layout: dict, path: Path) -> None:
    """Render layout to a PNG file, silently skipping on error."""
    try:
        from cando.utils.visualizer import plot_layout

        result = plot_layout(layout, show=False, return_figure=True)
        fig, _ = result
        fig.savefig(path, dpi=150, bbox_inches="tight")
        import matplotlib.pyplot as plt

        plt.close(fig)
    except Exception as exc:
        print(f"  [WARN] Could not render PNG for {path.name}: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate N initial layouts per eval input using GeneratorZero."
        )
    )
    parser.add_argument(
        "requirements",
        type=str,
        help=(
            "Path to the input constraints .jsonl file. Each line must be "
            "one structured constraints sample passed into GeneratorZero."
        ),
    )
    parser.add_argument(
        "--n-layouts",
        type=int,
        default=DEFAULT_N_LAYOUTS,
        help=(
            "Number of initial layouts to generate per input "
            f"(default: {DEFAULT_N_LAYOUTS})."
        ),
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=(
            f"Max internal refinement iterations inside GeneratorZero "
            f"(default: {DEFAULT_MAX_ITERATIONS})."
        ),
    )
    parser.add_argument(
        "--input-indices",
        type=int,
        nargs="+",
        default=None,
        metavar="IDX",
        help="Subset of input indices to process (0-based). Defaults to all.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help=(
            "Root output directory. Defaults to "
            "logs/generate_zero_<YYYYMMDD_HHMMSS>/. Existing complete "
            "layout entries are preserved."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["full", "no_icl"],
        default="full",
        help=(
            "'full' includes retrieved exemplar context (ICL); 'no_icl' "
            "omits it (default: full)."
        ),
    )
    parser.add_argument(
        "--inference-profile",
        choices=["no_thinking", "fast", "max"],
        default="max",
        help=(
            "Model inference profile: 'no_thinking' disables thinking for "
            "every role; 'fast' caps both thinking and response tokens "
            "per role; 'max' leaves thinking and token limits uncapped "
            "(default: max)."
        ),
    )
    parser.add_argument(
        "--n-workers",
        type=int,
        default=N_WORKERS,
        help=(
            "Number of layout generations to run concurrently per input "
            "using a thread pool (default: 10)."
        ),
    )
    args = parser.parse_args()

    # Set the inference profile before importing GeneratorZero, since it
    # instantiates its chat model at construction time.
    os.environ["CANDO_INFERENCE_PROFILE"] = args.inference_profile

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = (
        Path(args.output_dir)
        if args.output_dir
        else Path("logs") / f"generate_zero_{timestamp}"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    inputs = _load_inputs(Path(args.requirements))
    indices = (
        args.input_indices
        if args.input_indices is not None
        else list(range(len(inputs)))
    )

    summary: dict = {
        "output_dir": str(output_root),
        "n_layouts_per_input": args.n_layouts,
        "max_iterations": args.max_iterations,
        "mode": args.mode,
        "use_icl": args.mode != "no_icl",
        "inference_profile": args.inference_profile,
        "inputs_processed": indices,
        "results": [],
    }

    print_lock = threading.Lock()
    input_results: dict[int, dict] = {}
    generator = None

    def _generate_one(
        input_idx: int, layout_idx: int
    ) -> tuple[int, int, dict]:
        constraints = inputs[input_idx]
        input_dir = output_root / f"input{input_idx:02d}"
        with print_lock:
            print(
                f"  [input {input_idx:02d}] Generating layout "
                f"{layout_idx + 1}/{args.n_layouts}…",
                flush=True,
            )
        try:
            assert generator is not None
            layout, constraints_dict, history = generator.generate(
                constraints,
                max_iterations=args.max_iterations,
                use_icl=args.mode != "no_icl",
            )

            layout_dir = input_dir / f"layout_{layout_idx:02d}"
            layout_dir.mkdir(parents=True, exist_ok=True)

            _write_json(layout_dir / "layout.json", layout)
            _write_json(layout_dir / "constraints.json", constraints_dict)
            _save_layout_png(layout, layout_dir / "layout.png")

            with print_lock:
                print(
                    f"  [input {input_idx:02d}] layout {layout_idx + 1}: done"
                )
            return (
                input_idx,
                layout_idx,
                {
                    "layout_idx": layout_idx,
                    "status": "completed",
                    "path": str(layout_dir.relative_to(output_root)),
                },
            )

        except Exception as exc:
            with print_lock:
                print(
                    f"  [input {input_idx:02d}] layout {layout_idx + 1}: "
                    f"FAILED: {exc}"
                )
            return (
                input_idx,
                layout_idx,
                {
                    "layout_idx": layout_idx,
                    "status": "failed",
                    "error": str(exc),
                },
            )

    # Flatten (input_idx, layout_idx) tasks across all inputs up front, so
    # concurrency is shared globally rather than capped per input.
    tasks = []
    for input_idx in indices:
        if input_idx >= len(inputs):
            print(f"[WARN] input index {input_idx} out of range, skipping.")
            continue
        (output_root / f"input{input_idx:02d}").mkdir(
            parents=True, exist_ok=True
        )
        input_results[input_idx] = {"input_idx": input_idx, "layouts": {}}
        for layout_idx in range(args.n_layouts):
            layout_dir = (
                output_root
                / f"input{input_idx:02d}"
                / f"layout_{layout_idx:02d}"
            )
            if (layout_dir / "layout.json").is_file() and (
                layout_dir / "constraints.json"
            ).is_file():
                input_results[input_idx]["layouts"][layout_idx] = {
                    "layout_idx": layout_idx,
                    "status": "existing",
                    "path": str(layout_dir.relative_to(output_root)),
                }
                continue
            tasks.append((input_idx, layout_idx))

    if tasks:
        # Instantiate once after discovering missing cache entries; this
        # avoids loading the model during a no-op cache completion run.
        print("Initialising GeneratorZero…")
        from cando.agents.generators.generator_zero.agent import GeneratorZero

        generator = GeneratorZero()

    n_workers = max(1, args.n_workers)
    if n_workers == 1:
        for input_idx, layout_idx in tasks:
            _, _, result = _generate_one(input_idx, layout_idx)
            input_results[input_idx]["layouts"][layout_idx] = result
    else:
        print(
            f"Running with {n_workers} concurrent workers across "
            f"{len(tasks)} tasks…"
        )
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {
                executor.submit(_generate_one, input_idx, layout_idx): (
                    input_idx,
                    layout_idx,
                )
                for input_idx, layout_idx in tasks
            }
            for future in as_completed(futures):
                input_idx, layout_idx, result = future.result()
                input_results[input_idx]["layouts"][layout_idx] = result

    # Deterministic ordering: inputs by index, layouts by layout_idx.
    for input_idx in sorted(input_results):
        input_result = input_results[input_idx]
        input_result["layouts"] = [
            input_result["layouts"][layout_idx]
            for layout_idx in sorted(input_result["layouts"])
        ]
        summary["results"].append(input_result)

    _write_json(output_root / "summary.json", summary)

    completed = sum(
        1
        for r in summary["results"]
        for layout in r["layouts"]
        if layout["status"] == "completed"
    )
    existing = sum(
        1
        for r in summary["results"]
        for layout in r["layouts"]
        if layout["status"] == "existing"
    )
    print(
        f"\nDone. Generated {completed} layout(s); preserved "
        f"{existing} complete existing layout(s)."
    )
    print(f"Output: {output_root}")


if __name__ == "__main__":
    main()
