# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Graph runner script for CANDO pipeline.

Runs the compiled CANDO LangGraph for a configurable number of refinement
iterations. Accepts raw layout requirements, invokes build_graph(), and
saves all results to a timestamped log directory.
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # noqa: E402

from dotenv import load_dotenv

load_dotenv()

from cando.config import DEFAULT_MODEL_ID  # noqa: E402
from cando.experiments.ablations import (  # noqa: E402
    MODES,
    AblationConfig,
    resolve_ablation,
)
from cando.utils.generic import describe_exception_hint  # noqa: E402
from cando.utils.visualizer import plot_layout  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_jsonl_record(
    path: Path,
    sample_index: int | None = None,
    sample_id: int | None = None,
) -> dict[str, Any]:
    """Load one record from a JSONL dataset file.

    Args:
        path (Path): Path to the JSONL file.
        sample_index (int | None): Zero-based record index to load.
        sample_id (int | None): Dataset record id to load.

    Returns:
        dict[str, Any]: The selected dataset record.

    Raises:
        ValueError: If both selectors are provided, no records exist, or no
            matching record is found.
        json.JSONDecodeError: If any JSONL line is malformed.
    """
    if sample_index is not None and sample_id is not None:
        raise ValueError(
            "Provide only one of --sample-index or --sample-id for JSONL "
            "inputs."
        )

    records = _load_jsonl_records(path)

    if not records:
        raise ValueError(f"No records found in JSONL file: {path}")

    if sample_id is not None:
        for record in records:
            if record.get("id") == sample_id:
                return record
        raise ValueError(
            f"No JSONL record with id={sample_id} found in {path}."
        )

    selected_index = 0 if sample_index is None else sample_index
    if selected_index < 0 or selected_index >= len(records):
        raise ValueError(
            f"--sample-index must be in [0, {len(records) - 1}] for {path}."
        )
    return records[selected_index]


def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Load all JSON-object records from a JSONL dataset file."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                message = (
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                )
                raise ValueError(message) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"JSONL record on line {line_number} of {path} must be "
                    "a JSON object."
                )
            records.append(record)

    return records


def load_requirements_batch(source: str) -> list[dict[str, Any]]:
    """Load multiple structured requirements from a JSON or JSONL file.

    Args:
        source (str): Path to a .json or .jsonl file.

    Returns:
        list[dict[str, Any]]: Requirement records ready for repeated graph
            evaluation.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: If the file type or decoded data shape is unsupported.
        json.JSONDecodeError: If the file contains invalid JSON.
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Requirements file not found: {path}")

    if path.suffix == ".jsonl":
        records = _load_jsonl_records(path)
        if not records:
            raise ValueError(f"No records found in JSONL file: {path}")
        return records

    if path.suffix != ".json":
        raise ValueError("Requirements file must be a .json or .jsonl file.")

    with path.open(encoding="utf-8") as fh:
        parsed = json.load(fh)

    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list) and all(
        isinstance(item, dict) for item in parsed
    ):
        return parsed

    raise ValueError(
        "Evaluation requirements file must decode to a JSON object or a "
        "list of JSON objects."
    )


def load_requirements(
    source: str,
    sample_index: int | None = None,
    sample_id: int | None = None,
) -> dict[str, Any]:
    """Load structured requirements from JSON, JSONL, or inline JSON.

    If *source* is a path to a ``.json`` file, the file is parsed and the
    resulting dict returned. If it is a ``.jsonl`` file, one record is
    selected by ``sample_index`` or ``sample_id``. Otherwise *source* is
    treated as an inline JSON object string.

    Args:
        source (str): File path (.json/.jsonl) or inline JSON requirements
            object.
        sample_index (int | None): Zero-based JSONL record index.
        sample_id (int | None): JSONL record id.

    Returns:
        dict[str, Any]: Structured requirements ready for graph.invoke().

    Raises:
        FileNotFoundError: If a file path is given but the file does not
            exist.
        json.JSONDecodeError: If a .json file or inline JSON cannot be parsed.
    """
    path = Path(source)
    if path.exists():
        if path.suffix == ".json":
            with path.open(encoding="utf-8") as fh:
                parsed = json.load(fh)
        elif path.suffix == ".jsonl":
            parsed = _load_jsonl_record(
                path,
                sample_index=sample_index,
                sample_id=sample_id,
            )
        else:
            raise ValueError(
                "Requirements file must be a .json or .jsonl file."
            )

        if not isinstance(parsed, dict):
            raise ValueError(
                "Requirements input must decode to a JSON object."
            )
        return parsed

    parsed = json.loads(source)
    if not isinstance(parsed, dict):
        raise ValueError("Requirements input must decode to a JSON object.")
    return parsed


def build_initial_state(
    raw_requirements: dict[str, Any],
    max_iterations: int,
    n_candidates: int,
    n_branches: int,
    branch_steps: int,
    local_patience: int,
    plateau_patience: int,
    history_window: int,
    weights: dict | None,
    ablation: AblationConfig,
    inference_profile: str,
    log_dir: str | None = None,
) -> dict:
    """Construct the initial CandoState dict for graph.invoke().

    All mutable working-state fields are set to their zero / empty defaults
    so that the graph starts a clean run.

    Args:
        raw_requirements (dict): Structured layout requirements.
        max_iterations (int): Hard upper bound on total refinement steps
            across all fan-out rounds.
        n_candidates (int): Number of candidates generated per iteration.
        n_branches (int): Number of parallel branches per fan-out round.
        branch_steps (int): Steps each branch runs per round.
        local_patience (int): Consecutive non-improving steps within a
            branch before restore is triggered. -1 disables restores.
        plateau_patience (int): Rounds without best-score change before
            plateau exit.
        history_window (int): Number of recent iteration summaries kept in
            LLM context.
        weights (dict | None): Optional per-category verifier weights. Keys:
            hard_constraints, assembly, proximity, orientation, flow.
        ablation (AblationConfig): Resolved AblationConfig for this run (see
            cando.experiments.ablations.resolve_ablation). Every graph node
            reads state["ablation"] directly with no default, so this key
            must always be present.
        inference_profile (str): One of "no_thinking", "fast", "max".
            Stored for logging only -- the active profile is actually
            applied via the CANDO_INFERENCE_PROFILE environment variable,
            which must be set before build_graph() is called.
        log_dir (str | None): Root directory for live log output. When set,
            nodes write per-iteration summaries and layout PNGs to disk
            during the run. None disables live logging.

    Returns:
        dict: Fully initialised CandoState-compatible dict.
    """
    return {
        # Inputs
        "raw_requirements": raw_requirements,
        "weights": weights,
        # Experiment / ablation configuration
        "ablation": ablation,
        "inference_profile": inference_profile,
        # Refinement config
        "max_iterations": max_iterations,
        "n_branches": n_branches,
        "branch_steps": branch_steps,
        "local_patience": local_patience,
        # fan_out_node overwrites this per branch, but the shared state now
        # defines it explicitly.
        "patience": local_patience,
        "plateau_patience": plateau_patience,
        "n_candidates": n_candidates,
        "history_window": history_window,
        "layout_image": None,
        # generate_zero_node outputs (filled during execution)
        "constraints": {},
        "initial_layout": None,
        "generator_zero_history": None,
        # Working state (iteration starts at -1 so that the first verify_node
        # pass increments it to 0, consistent with the 0-based docs)
        "current_layout": None,
        "iteration": -1,
        "iterations_without_improvement": 0,
        "iterations_since_best_changed": 0,
        # Fan-out / reduce working state
        "pre_fanout_layout": None,
        "pre_fanout_score": 0.0,
        "branch_states": [],
        "completed_branch_states": [],
        "current_round": 0,
        "branch_id": None,
        # Live logging
        "log_dir": log_dir,
        # Per-node outputs (None until first node runs)
        "verification": None,
        "prev_verification": None,
        "plan": None,
        "candidates": [],
        "action_sequences": [],
        "winning_candidate_idx": 0,
        "selected_strategies": [],
        "candidate_scores": [],
        # Best-result tracking
        "best_layout": None,
        "best_score": 0.0,
        "best_layout_iteration": 0,
        "restored_from_iteration": None,
        # Flags
        "is_compliant": False,
        "should_restore": False,
        "exit_reason": None,
        # Audit trail
        "history": [],
    }


def save_results(state: dict, output_dir: Path) -> None:
    """Persist the final graph state to *output_dir*.

    Saves:
    - ``best_layout.json``        — best layout seen across all iterations.
    - ``initial_layout.json``     — layout produced by GeneratorZero.
    - ``summary.json``            — run metadata (score, exit reason, etc.).
    - ``best_layout.png``         — visualisation of the best layout.
    - ``history/``                — per-iteration JSON snapshots.

    Args:
        state (dict): Final CandoState returned by graph.invoke().
        output_dir (Path): Directory where all artefacts are written.

    Returns:
        None
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Best layout ---
    if state.get("best_layout"):
        best_layout_file = output_dir / "best_layout.json"
        with open(best_layout_file, "w", encoding="utf-8") as fh:
            json.dump(state["best_layout"], fh, indent=2)
        print(f"  Saved best layout      → {best_layout_file}")

        viz_file = output_dir / "best_layout.png"
        try:
            plot_layout(
                state["best_layout"], show=False, save_path=str(viz_file)
            )
            print(f"  Saved best layout viz  → {viz_file}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Warning: could not render best layout: {exc}")

    # --- Initial layout ---
    if state.get("initial_layout"):
        initial_layout_file = output_dir / "initial_layout.json"
        with open(initial_layout_file, "w", encoding="utf-8") as fh:
            json.dump(state["initial_layout"], fh, indent=2)
        print(f"  Saved initial layout   → {initial_layout_file}")

        initial_viz = output_dir / "initial_layout.png"
        try:
            plot_layout(
                state["initial_layout"],
                show=False,
                save_path=str(initial_viz),
            )
            print(f"  Saved initial viz      → {initial_viz}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Warning: could not render initial layout: {exc}")

    # --- Per-iteration history snapshots ---
    history: list = state.get("history", [])
    if history:
        history_dir = output_dir / "history"
        history_dir.mkdir(exist_ok=True)
        for record in history:
            summary = record.get("summary", {})
            iter_idx = summary.get("iteration", 0)
            iter_file = history_dir / f"iteration_{iter_idx:02d}.json"
            # Serialise summary only (layout_snapshot may contain non-JSON
            # types such as VerifierResult objects if they slipped through)
            with open(iter_file, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, default=str)
        print(f"  Saved {len(history)} iteration record(s) → {history_dir}/")

    # --- Run summary ---
    summary = {
        "timestamp": datetime.now().isoformat(),
        "exit_reason": state.get("exit_reason"),
        "total_iterations": state.get("iteration", 0),
        "best_score": state.get("best_score", 0.0),
        "best_layout_iteration": state.get("best_layout_iteration", 0),
        "is_compliant": state.get("is_compliant", False),
        "ablation": state.get("ablation"),
        "inference_profile": state.get("inference_profile"),
        "model_id": DEFAULT_MODEL_ID,
        "dataset_jsonl_path": os.environ.get("DATASET_JSONL_PATH"),
        "feature_vectors_parquet_path": os.environ.get(
            "FEATURE_VECTORS_PARQUET_PATH"
        ),
        "max_iterations": state.get("max_iterations"),
        "n_candidates": state.get("n_candidates"),
        "n_branches": state.get("n_branches"),
        "branch_steps": state.get("branch_steps"),
        "local_patience": state.get("local_patience"),
        "plateau_patience": state.get("plateau_patience"),
    }
    summary_file = output_dir / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"  Saved run summary      → {summary_file}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(args: argparse.Namespace) -> None:
    """Entry point: build the graph, invoke it, and save results.

    Args:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        None
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or f"logs/graph_run_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("CANDO Graph Runner")
    print("=" * 70)
    print(f"  mode             : {args.mode}")
    print(f"  inference_profile: {args.inference_profile}")
    print(f"  model_id         : {DEFAULT_MODEL_ID}")
    print(f"  max_iterations   : {args.max_iterations}")
    print(f"  n_branches       : {args.n_branches}")
    print(f"  branch_steps     : {args.branch_steps}")
    print(f"  n_candidates     : {args.n_candidates}")
    print(f"  local_patience   : {args.local_patience}")
    print(f"  plateau_patience : {args.plateau_patience}")
    print(f"  history_window   : {args.history_window}")
    if args.sample_id is not None:
        print(f"  sample_id        : {args.sample_id}")
    elif args.sample_index is not None:
        print(f"  sample_index     : {args.sample_index}")
    print(f"  output_dir       : {output_dir}")
    print("=" * 70)

    print("\nLoading requirements...")
    raw_requirements = load_requirements(
        args.requirements,
        sample_index=args.sample_index,
        sample_id=args.sample_id,
    )
    print(f"  Requirements (dict): {list(raw_requirements.keys())}")
    if "id" in raw_requirements:
        print(f"  Selected sample id : {raw_requirements['id']}")

    weights = None
    if args.weights:
        try:
            weights = json.loads(args.weights)
            print(f"  Weights: {weights}")
        except json.JSONDecodeError as exc:
            print(f"Error: --weights must be a valid JSON object. {exc}")
            sys.exit(1)

    # Set the inference profile before the graph is invoked: agents are
    # constructed lazily on first use inside each node, so this must be
    # set before build_graph()'s nodes run.
    os.environ["CANDO_INFERENCE_PROFILE"] = args.inference_profile
    ablation = resolve_ablation(args.mode)

    # Build the graph (compiled once and reused for this invocation)
    print("\nBuilding graph...")
    from cando.graph.graph import build_graph

    graph = build_graph()
    print("  Graph compiled successfully.")

    initial_state = build_initial_state(
        raw_requirements=raw_requirements,
        max_iterations=args.max_iterations,
        n_candidates=args.n_candidates,
        n_branches=args.n_branches,
        branch_steps=args.branch_steps,
        local_patience=args.local_patience,
        plateau_patience=args.plateau_patience,
        history_window=args.history_window,
        weights=weights,
        ablation=ablation,
        inference_profile=args.inference_profile,
        log_dir=str(output_dir),
    )

    print(f"\nRunning graph (max {args.max_iterations} iteration(s))...")
    print("-" * 70)
    try:
        final_state = graph.invoke(initial_state)
    except Exception as exc:
        print("-" * 70)
        error_log = output_dir / "error.log"
        with open(error_log, "w", encoding="utf-8") as fh:
            fh.write(traceback.format_exc())
        print(f"\nGraph execution FAILED: {exc}")
        hint = describe_exception_hint(exc)
        if hint:
            print(f"Hint: {hint}")
        print(f"Full traceback saved to: {error_log}")
        raise
    print("-" * 70)

    print("\nGraph execution complete.")
    print(f"  Exit reason      : {final_state.get('exit_reason')}")
    print(f"  Iterations run   : {final_state.get('iteration', 0)}")
    print(f"  Best score       : {final_state.get('best_score', 0.0):.4f}")
    print(f"  Compliant        : {final_state.get('is_compliant', False)}")
    print(
        f"  Best at iter     : {final_state.get('best_layout_iteration', 0)}"
    )

    print("\nSaving results...")
    save_results(final_state, output_dir)

    print(f"\nAll results saved to: {output_dir}")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Run the CANDO LangGraph pipeline for a configurable number "
            "of refinement iterations."
        )
    )
    parser.add_argument(
        "requirements",
        type=str,
        help=(
            "Structured layout requirements as either: "
            "(1) a path to a .json file containing a constraints dict, "
            "(2) a path to a .jsonl file containing dataset samples, "
            "or (3) an inline JSON object string."
        ),
    )
    parser.add_argument(
        "--sample-index",
        type=int,
        default=None,
        help=(
            "Zero-based record index to load when requirements points to a "
            ".jsonl dataset file. Defaults to 0 when omitted."
        ),
    )
    parser.add_argument(
        "--sample-id",
        type=int,
        default=None,
        help=(
            "Dataset record id to load when requirements points to a .jsonl "
            "dataset file. Mutually exclusive with --sample-index."
        ),
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="Maximum number of refinement iterations (default: 20).",
    )
    parser.add_argument(
        "--n-candidates",
        type=int,
        default=6,
        help=(
            "Number of candidate layouts generated per iteration "
            "(default: 6)."
        ),
    )
    parser.add_argument(
        "--n-branches",
        type=int,
        default=4,
        help="Number of parallel branches per fan-out round (default: 4).",
    )
    parser.add_argument(
        "--branch-steps",
        type=int,
        default=10,
        help=(
            "Number of refinement steps each branch runs per round"
            "(default: 10)."
        ),
    )
    parser.add_argument(
        "--local-patience",
        type=int,
        default=-1,
        help=(
            "Consecutive steps within a branch without improvement before "
            "restore is triggered. -1 disables within-branch restores "
            "(default: -1)."
        ),
    )
    parser.add_argument(
        "--plateau-patience",
        type=int,
        default=21,
        help=(
            "Iterations without best-score change before plateau exit "
            "(default: 21)."
        ),
    )
    parser.add_argument(
        "--history-window",
        type=int,
        default=6,
        help=(
            "Number of recent iteration summaries kept in LLM context "
            "(default: 6)."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=sorted(MODES),
        default="full",
        help="Ablation mode to run (default: full).",
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
        "--weights",
        type=str,
        default=None,
        help=(
            "Optional JSON object of per-category verifier weights, e.g. "
            '\'{"hard_constraints": 100.0, "assembly": 8.0, '
            '"proximity": 8.0, "orientation": 6.0, "flow": 4.0}\'. '
            "Missing keys default to 1.0 inside each agent."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help=(
            "Directory where results are saved "
            "(default: logs/graph_run_TIMESTAMP)."
        ),
    )

    parsed_args = parser.parse_args()
    main(parsed_args)
