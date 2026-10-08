# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Evaluation runner for CANDO pipeline.

Runs the CANDO graph for each evaluation input in a .jsonl constraints file,
repeating each input N times. After all repetitions for an input are
complete, computes per-input mean metrics (best_score, is_compliant,
iterations). Writes live artefacts (branch logs, layout PNGs) to disk via
the same live-logging infrastructure as the graph runner. Supports resume
via --resume <dir>.
"""

import argparse
import hashlib
import json
import os
import random
import signal
import sys
import time
import traceback
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib as _mpl

_mpl.use("Agg")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from cando.config import DEFAULT_MODEL_ID  # noqa: E402
from cando.experiments.ablations import MODES, resolve_ablation  # noqa: E402
from cando.utils.generic import describe_exception_hint  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREGEN_LAYOUTS_DIR = Path("data/evaluation/inputs")
N_REPETITIONS = 1
MAX_RETRIES = 3
N_WORKERS = 12  # parallel repetitions across inputs

# Default graph hyperparameters (paper configuration)
MAX_ITERATIONS = 20
N_BRANCHES = 4
BRANCH_STEPS = 10
N_CANDIDATES = 6
LOCAL_PATIENCE = -1
PLATEAU_PATIENCE = 21
HISTORY_WINDOW = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


def canonical_json_hash(value: dict) -> str:
    """Compute a stable SHA-256 hash of a JSON-serializable value.

    Uses canonical JSON (sorted keys, compact separators) so that
    semantically identical dicts always hash to the same digest.

    Args:
        value: The JSON-serializable value to hash.

    Returns:
        str: Hex-encoded SHA-256 digest.
    """
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


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
    ablation: dict,
    inference_profile: str,
    run_seed: int | None,
    log_dir: str | None = None,
) -> dict:
    """Construct the initial CandoState dict for graph.invoke()."""
    return {
        # Inputs
        "raw_requirements": raw_requirements,
        "weights": weights,
        # Experiment / ablation configuration
        "ablation": ablation,
        "inference_profile": inference_profile,
        "run_seed": run_seed,
        # Refinement config
        "max_iterations": max_iterations,
        "n_branches": n_branches,
        "branch_steps": branch_steps,
        "local_patience": local_patience,
        "patience": local_patience,
        "plateau_patience": plateau_patience,
        "n_candidates": n_candidates,
        "history_window": history_window,
        "layout_image": None,
        # generate_zero_node outputs (filled during execution)
        "constraints": {},
        "initial_layout": None,
        "generator_zero_history": None,
        # Working state
        "current_layout": None,
        "iteration": -1,
        "iterations_without_improvement": 0,
        "iterations_since_best_changed": 0,
        "pre_fanout_layout": None,
        "pre_fanout_score": 0.0,
        "branch_states": [],
        "completed_branch_states": [],
        "current_round": 0,
        "branch_id": None,
        "log_dir": log_dir,
        # Per-node outputs
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


def _run_id(input_idx: int, rep_idx: int) -> str:
    return f"input{input_idx:02d}_rep{rep_idx:02d}"


def _load_inputs(inputs_file: Path) -> list[dict]:
    try:
        return load_requirements_batch(str(inputs_file))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def _iter_pregen_layouts(
    pregen_dir: Path,
) -> Iterator[tuple[int, int, dict, dict]]:
    """Yield (input_idx, layout_idx, layout, constraints).

    Directory structure expected:
        <pregen_dir>/input{NN}/layout_{MM}/layout.json
        <pregen_dir>/input{NN}/layout_{MM}/constraints.json
    """
    for input_dir in sorted(pregen_dir.glob("input*")):
        try:
            input_idx = int(input_dir.name.lstrip("input"))
        except ValueError:
            continue
        for layout_dir in sorted(input_dir.glob("layout_*")):
            try:
                layout_idx = int(layout_dir.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            layout_file = layout_dir / "layout.json"
            constraints_file = layout_dir / "constraints.json"
            if not layout_file.exists() or not constraints_file.exists():
                continue
            with open(layout_file, encoding="utf-8") as fh:
                layout = json.load(fh)
            with open(constraints_file, encoding="utf-8") as fh:
                constraints = json.load(fh)
            yield input_idx, layout_idx, layout, constraints


def _load_pregen_layouts(
    pregen_dir: Path, n_inputs: int, n_layouts: int
) -> dict[int, list[tuple[dict, dict]]]:
    """Return mapping of input_idx -> [(layout, constraints), ...] for the
    first *n_layouts* entries found per input.

    Args:
        pregen_dir: Root directory containing input{NN}/layout_{MM}/ folders.
        n_inputs: Number of inputs expected (0-based indices).
        n_layouts: Maximum number of pre-generated layouts to load per input.

    Returns:
        Dict mapping input_idx to a list of (layout, constraints) tuples.
    """
    result: dict[int, list[tuple[dict, dict]]] = {
        i: [] for i in range(n_inputs)
    }
    for (
        input_idx,
        _layout_idx,
        layout,
        constraints,
    ) in _iter_pregen_layouts(pregen_dir):
        if input_idx not in result:
            continue
        if len(result[input_idx]) < n_layouts:
            result[input_idx].append((layout, constraints))
    return result


def _load_or_init_summary(
    results_dir: Path,
    all_runs: list[dict],
    resume_dir: Path | None,
) -> tuple[dict, Path]:
    """Return (summary_dict, resolved_results_dir).

    If *resume_dir* is given, loads its summary.json to continue where it
    left off.  Otherwise initialises a fresh summary in *results_dir*.
    """
    if resume_dir is not None:
        summary_path = resume_dir / "summary.json"
        if not summary_path.exists():
            print(
                f"ERROR: No summary.json in {resume_dir}",
                file=sys.stderr,
            )
            sys.exit(1)
        with open(summary_path, encoding="utf-8") as fh:
            summary = json.load(fh)
        completed = summary.get("completed", 0)
        total = summary.get("total_runs", len(all_runs))
        print(f"Resuming from {resume_dir} ({completed}/{total} done).")
        return summary, resume_dir

    results_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "results_dir": str(results_dir),
        "total_runs": len(all_runs),
        "completed": 0,
        "failed": 0,
        "runs": all_runs,
        "input_means": {},
    }
    _write_summary(results_dir / "summary.json", summary)
    return summary, results_dir


def _write_summary(path: Path, summary: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)


def _update_summary(
    summary: dict,
    summary_path: Path,
    run_id: str,
    status: str,
    metrics: dict | None = None,
) -> None:
    """Mutate summary in-place and persist."""
    for run in summary["runs"]:
        if run["run_id"] == run_id:
            run["status"] = status
            if metrics is not None:
                run["metrics"] = metrics
            break
    if status == "completed":
        summary["completed"] = summary.get("completed", 0) + 1
    elif status == "failed":
        summary["failed"] = summary.get("failed", 0) + 1
    _write_summary(summary_path, summary)


def _compute_input_means(
    runs: list[dict],
    input_idx: int,
    n_reps: int,
) -> dict:
    """Compute mean metrics over all successful repetitions for one input.

    Args:
        runs: Full list of run dicts from summary.
        input_idx: Index of the eval input to aggregate.
        n_reps: Expected number of repetitions.

    Returns:
        dict with mean_best_score, mean_iterations, compliant_count,
        compliant_rate, n_successful.
    """
    rep_runs = [
        r
        for r in runs
        if r.get("input_idx") == input_idx and r.get("status") == "completed"
    ]
    n = len(rep_runs)
    if n == 0:
        return {
            "n_successful": 0,
            "n_expected": n_reps,
            "mean_best_score": None,
            "mean_iterations": None,
            "compliant_count": 0,
            "compliant_rate": None,
            "mean_duration_seconds": None,
        }

    scores = [r["metrics"]["best_score"] for r in rep_runs]
    iters = [r["metrics"]["iterations"] for r in rep_runs]
    compliant = [r["metrics"]["is_compliant"] for r in rep_runs]
    durations = [
        r["metrics"]["duration_seconds"]
        for r in rep_runs
        if r["metrics"].get("duration_seconds") is not None
    ]

    return {
        "n_successful": n,
        "n_expected": n_reps,
        "mean_best_score": round(sum(scores) / n, 6),
        "mean_iterations": round(sum(iters) / n, 2),
        "compliant_count": sum(compliant),
        "compliant_rate": round(sum(compliant) / n, 4),
        "scores_per_rep": scores,
        "mean_duration_seconds": (
            round(sum(durations) / len(durations), 3) if durations else None
        ),
        "durations_per_rep_seconds": durations,
    }


def _serialize_final_state(state: dict) -> dict:
    """Convert a final CandoState dict to a JSON-serializable dict.

    Pydantic model fields (verification, prev_verification, plan,
    action_sequences) are converted via .model_dump(). Bulky or
    non-serializable fields (completed_branch_states, branch_states,
    layout_image, generator_zero_history) are omitted.

    Args:
        state: The raw dict returned by graph.invoke().

    Returns:
        A fully JSON-serializable dict preserving all useful fields,
        including the full history list for score-progression analysis.
    """
    from pydantic import BaseModel

    OMIT = {
        "completed_branch_states",
        "branch_states",
        "layout_image",
        "generator_zero_history",  # LangChain message objects
    }

    out: dict = {}
    for key, val in state.items():
        if key in OMIT:
            continue
        if isinstance(val, BaseModel):
            out[key] = val.model_dump()
        elif isinstance(val, list) and val and isinstance(val[0], BaseModel):
            out[key] = [
                v.model_dump() if isinstance(v, BaseModel) else v for v in val
            ]
        else:
            out[key] = val
    return out


# ---------------------------------------------------------------------------
# Worker (runs in a subprocess)
# ---------------------------------------------------------------------------


def _run_trial(
    run_plan: dict,
    inputs: list[dict],
    results_dir: Path,
    graph_cfg: dict,
    max_retries: int,
    pregen_layout: tuple[dict, dict] | None = None,
) -> tuple[str, str, dict | None, str | None]:
    """Execute one (input, repetition) trial.

    Each call runs in its own subprocess so there is no shared state.

    Args:
        run_plan: Dict with run_id, input_idx, rep_idx, status.
        inputs: Full list of evaluated input dicts.
        results_dir: Root directory for all trial outputs.
        graph_cfg: Graph hyperparameter dict fed to build_initial_state.
        max_retries: Retry budget on transient failure.
        pregen_layout: Optional (layout, constraints) pair. When provided
            the trial skips generate_zero and starts refinement from this
            pre-generated initial layout.

    Returns:
        (run_id, status, metrics_or_None, error_msg_or_None)
    """
    # Each subprocess must bootstrap env independently.
    from dotenv import load_dotenv

    load_dotenv()

    import matplotlib

    matplotlib.use("Agg")

    # Set the inference profile before the graph is invoked: agents are
    # constructed lazily on first use inside each node, so this must be
    # set before build_graph()'s nodes run, not before this import.
    os.environ["CANDO_INFERENCE_PROFILE"] = graph_cfg["inference_profile"]

    from cando.graph.graph import build_graph, build_graph_from_layout

    run_id = run_plan["run_id"]
    input_idx = run_plan["input_idx"]
    rep_idx = run_plan["rep_idx"]
    run_seed: int | None = run_plan.get("run_seed")
    req = inputs[input_idx]

    trial_dir = results_dir / run_id
    trial_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{run_id}] START", flush=True)
    trial_started_at = datetime.now().isoformat()
    trial_start_time = time.time()

    # Repository-side reproducibility (strategy selection / ordering).
    # Does not make remote/model sampling fully deterministic.
    if run_seed is not None:
        random.seed(run_seed)

    last_error: Exception | None = None
    last_traceback: str = ""
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(
                    f"[{run_id}] Retry {attempt}/{max_retries}",
                    flush=True,
                )

            if pregen_layout is not None:
                # Bypass generate_zero: seed state from pre-generated layout.
                layout, constraints = pregen_layout
                graph = build_graph_from_layout()
                initial_state = build_initial_state(
                    raw_requirements=req,
                    log_dir=str(trial_dir),
                    run_seed=run_seed,
                    **graph_cfg,
                )
                initial_state["constraints"] = constraints
                initial_state["initial_layout"] = layout
                initial_state["current_layout"] = layout
                initial_state["candidates"] = [layout]
                initial_layout_for_hash = layout
            else:
                graph = build_graph()
                initial_state = build_initial_state(
                    raw_requirements=req,
                    log_dir=str(trial_dir),
                    run_seed=run_seed,
                    **graph_cfg,
                )
                initial_layout_for_hash = None

            final_state = graph.invoke(
                initial_state,
                config={"recursion_limit": 300},
            )

            if initial_layout_for_hash is None:
                initial_layout_for_hash = final_state.get("initial_layout")

            best_score: float = final_state.get("best_score", 0.0)
            # iteration is 0-based; add 1 for a human-readable count.
            iterations: int = final_state.get("iteration", -1) + 1
            is_compliant: bool = final_state.get("is_compliant", False)
            exit_reason: str | None = final_state.get("exit_reason")
            duration_seconds = round(time.time() - trial_start_time, 3)
            trial_completed_at = datetime.now().isoformat()

            metrics = {
                "best_score": best_score,
                "iterations": iterations,
                "is_compliant": is_compliant,
                "exit_reason": exit_reason,
                "duration_seconds": duration_seconds,
                "started_at": trial_started_at,
                "completed_at": trial_completed_at,
            }

            result = {
                "run_id": run_id,
                "input_idx": input_idx,
                "rep_idx": rep_idx,
                "metrics": metrics,
                "mode": graph_cfg["ablation"]["mode"],
                "run_seed": run_seed,
                "inference_profile": graph_cfg["inference_profile"],
                "initial_layout_sha256": (
                    canonical_json_hash(initial_layout_for_hash)
                    if initial_layout_for_hash
                    else None
                ),
            }
            with open(trial_dir / "result.json", "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)

            # Persist full final state (history, scores, all fields)
            try:
                serialized = _serialize_final_state(final_state)
                with open(
                    trial_dir / "final_state.json", "w", encoding="utf-8"
                ) as fh:
                    json.dump(serialized, fh, indent=2)
            except Exception as state_exc:  # noqa: BLE001
                print(
                    f"[{run_id}] State save warning: {state_exc}",
                    file=sys.stderr,
                )

            if final_state.get("best_layout"):
                with open(
                    trial_dir / "best_layout.json", "w", encoding="utf-8"
                ) as fh:
                    json.dump(final_state["best_layout"], fh, indent=2)

                try:
                    from cando.utils.visualizer import plot_layout

                    plot_layout(
                        final_state["best_layout"],
                        show=False,
                        save_path=str(trial_dir / "best_layout.png"),
                    )
                except Exception as viz_exc:  # noqa: BLE001
                    print(
                        f"[{run_id}] Viz warning: {viz_exc}",
                        file=sys.stderr,
                    )

            print(
                f"[{run_id}] DONE  score={best_score:.4f}"
                f"  iters={iterations}"
                f"  compliant={is_compliant}"
                f"  duration={duration_seconds:.1f}s",
                flush=True,
            )
            return run_id, "completed", metrics, None

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            last_traceback = traceback.format_exc()
            print(
                f"[{run_id}] Attempt {attempt} failed: {exc}",
                flush=True,
            )
            hint = describe_exception_hint(exc)
            if hint:
                print(f"[{run_id}] Hint: {hint}", flush=True)

    duration_seconds = round(time.time() - trial_start_time, 3)
    with open(
        results_dir / f"error_{run_id}.log", "w", encoding="utf-8"
    ) as fh:
        fh.write(last_traceback)
    print(
        f"[{run_id}] FAILED after {max_retries} attempts "
        f"(duration={duration_seconds:.1f}s).",
        flush=True,
    )
    return run_id, "failed", None, str(last_error)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point: run evaluation across all inputs and repetitions."""
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the CANDO graph on each input in a constraints "
            "JSONL dataset, repeating each run N times and "
            "reporting per-input mean metrics."
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
        "--n-reps",
        type=int,
        default=N_REPETITIONS,
        help=f"Repetitions per input (default: {N_REPETITIONS}).",
    )
    parser.add_argument(
        "--n-workers",
        type=int,
        default=N_WORKERS,
        help=f"Parallel worker processes (default: {N_WORKERS}).",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=MAX_ITERATIONS,
        help=(
            f"Max refinement iterations per run (default: {MAX_ITERATIONS})."
        ),
    )
    parser.add_argument(
        "--n-branches",
        type=int,
        default=N_BRANCHES,
        help=f"Branches per fan-out round (default: {N_BRANCHES}).",
    )
    parser.add_argument(
        "--branch-steps",
        type=int,
        default=BRANCH_STEPS,
        help=f"Steps per branch per round (default: {BRANCH_STEPS}).",
    )
    parser.add_argument(
        "--n-candidates",
        type=int,
        default=N_CANDIDATES,
        help=f"Candidates per iteration (default: {N_CANDIDATES}).",
    )
    parser.add_argument(
        "--local-patience",
        type=int,
        default=LOCAL_PATIENCE,
        help=(
            f"Within-branch patience before restore (default: "
            f"{LOCAL_PATIENCE}, -1 disables)."
        ),
    )
    parser.add_argument(
        "--plateau-patience",
        type=int,
        default=PLATEAU_PATIENCE,
        help=f"Rounds without improvement before plateau exit "
        f"(default: {PLATEAU_PATIENCE}).",
    )
    parser.add_argument(
        "--history-window",
        type=int,
        default=HISTORY_WINDOW,
        help=f"LLM history context window (default: {HISTORY_WINDOW}).",
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
        help="Root output directory (default: logs/eval_<timestamp>).",
    )
    parser.add_argument(
        "--resume",
        metavar="DIR",
        default=None,
        help="Resume an interrupted eval run from its summary.json.",
    )
    parser.add_argument(
        "--pregen-layouts-dir",
        type=str,
        default=None,
        metavar="DIR",
        help=(
            "Optional directory containing pre-generated initial layouts "
            "(input{NN}/layout_{MM}/layout.json + constraints.json). "
            f"Example: {PREGEN_LAYOUTS_DIR}. When omitted, run the full "
            "graph starting from GeneratorZero. Takes precedence over "
            "--pregen-layouts-root."
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
        "--seed",
        type=int,
        default=None,
        help=(
            "Optional base seed for repository-side randomness (strategy "
            "selection/ordering). Does not make remote model sampling "
            "fully deterministic."
        ),
    )
    parser.add_argument(
        "--pregen-layouts-root",
        type=str,
        default=None,
        help=(
            "Root containing full/ and no_icl/ initial-layout directories. "
            "All modes except no_icl use full/. Ignored when "
            "--pregen-layouts-dir is set."
        ),
    )
    parser.add_argument(
        "--require-pregen",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Fail instead of falling back to GeneratorZero when a required "
            "cached initial layout is missing."
        ),
    )
    args = parser.parse_args()

    ablation = resolve_ablation(args.mode)
    resolved_n_branches = (
        ablation["n_branches_override"]
        if ablation["n_branches_override"] is not None
        else args.n_branches
    )

    resume_dir = Path(args.resume) if args.resume else None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("logs") / f"eval_{timestamp}"
    )

    if args.pregen_layouts_dir:
        pregen_dir = Path(args.pregen_layouts_dir)
    elif args.pregen_layouts_root:
        initialization_name = "no_icl" if args.mode == "no_icl" else "full"
        pregen_dir = Path(args.pregen_layouts_root) / initialization_name
    else:
        pregen_dir = None
    inputs_file = Path(args.requirements)

    # Auto-resume: if the resolved output-dir already contains a summary.json
    # treat it as a resume target (no need to pass --resume explicitly).
    if resume_dir is None and (results_dir / "summary.json").exists():
        print(f"Found existing summary.json in {results_dir} — resuming.")
        resume_dir = results_dir

    inputs = _load_inputs(inputs_file)
    n_inputs = len(inputs)
    n_reps = args.n_reps

    weights = None
    if args.weights:
        try:
            weights = json.loads(args.weights)
            print(f"Using weights: {weights}")
        except json.JSONDecodeError as exc:
            print(
                f"ERROR: --weights must be a valid JSON object. {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    pregen_layouts: dict[int, list[tuple[dict, dict]]] = {}
    if pregen_dir is not None and pregen_dir.exists():
        pregen_layouts = _load_pregen_layouts(pregen_dir, n_inputs, n_reps)
        total_loaded = sum(len(v) for v in pregen_layouts.values())
        print(
            f"Loaded {total_loaded} pre-generated layouts from {pregen_dir}"
        )
    elif pregen_dir is not None:
        print(
            f"[WARN] Pre-generated layouts dir not found: {pregen_dir}. "
            "Falling back to generate_zero.",
            file=sys.stderr,
        )

    if args.require_pregen:
        missing = [
            (i_idx, r_idx)
            for i_idx in range(n_inputs)
            for r_idx in range(n_reps)
            if r_idx >= len(pregen_layouts.get(i_idx, []))
        ]
        if missing:
            print(
                f"ERROR: Missing required pre-generated layouts: {missing}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Build full run list (all inputs × all repetitions)
    all_runs = [
        {
            "run_id": _run_id(i_idx, r_idx),
            "status": "pending",
            "input_idx": i_idx,
            "rep_idx": r_idx,
            "metrics": None,
            "run_seed": (
                args.seed + i_idx * 10_000 + r_idx * 100
                if args.seed is not None
                else None
            ),
        }
        for i_idx in range(n_inputs)
        for r_idx in range(n_reps)
    ]

    summary, results_dir = _load_or_init_summary(
        results_dir, all_runs, resume_dir
    )
    summary_path = results_dir / "summary.json"

    # Graph hyperparameters forwarded to build_initial_state
    graph_cfg = {
        "max_iterations": args.max_iterations,
        "n_branches": resolved_n_branches,
        "branch_steps": args.branch_steps,
        "n_candidates": args.n_candidates,
        "local_patience": args.local_patience,
        "plateau_patience": args.plateau_patience,
        "history_window": args.history_window,
        "weights": weights,
        "ablation": ablation,
        "inference_profile": args.inference_profile,
    }

    if not resume_dir:
        summary["mode"] = args.mode
        summary["ablation"] = ablation
        summary["inference_profile"] = args.inference_profile
        summary["base_seed"] = args.seed
        summary["model_id"] = DEFAULT_MODEL_ID
        summary["dataset_jsonl_path"] = os.environ.get("DATASET_JSONL_PATH")
        summary["feature_vectors_parquet_path"] = os.environ.get(
            "FEATURE_VECTORS_PARQUET_PATH"
        )
        summary["graph_config"] = graph_cfg
        _write_summary(summary_path, summary)

    run_wall_clock_start = time.time()
    pending = [r for r in summary["runs"] if r["status"] == "pending"]

    print("=" * 70)
    print("CANDO Evaluation Runner")
    print("=" * 70)
    print(f"  Inputs         : {n_inputs}")
    print(f"  requirements   : {inputs_file}")
    print(f"  Repetitions    : {n_reps}")
    print(f"  Total runs     : {summary['total_runs']}")
    print(f"  Pending        : {len(pending)}")
    print(f"  Workers        : {args.n_workers}")
    print(f"  mode           : {args.mode}")
    print(f"  inference_profile: {args.inference_profile}")
    print(f"  model_id       : {DEFAULT_MODEL_ID}")
    print(f"  seed           : {args.seed}")
    print(f"  max_iterations : {args.max_iterations}")
    print(f"  n_branches     : {resolved_n_branches}")
    print(f"  branch_steps   : {args.branch_steps}")
    print(f"  n_candidates   : {args.n_candidates}")
    print(f"  local_patience : {args.local_patience}")
    print(f"  plateau_patience: {args.plateau_patience}")
    print(f"  history_window : {args.history_window}")
    print(f"  weights        : {weights}")
    print(
        f"  pregen_layouts : {pregen_dir or 'disabled (using generate_zero)'}"
    )
    print(f"  output_dir     : {results_dir}")
    print("=" * 70)

    if not pending:
        print("No pending runs — all already completed.")
    else:
        executor = ProcessPoolExecutor(max_workers=args.n_workers)
        submitted_futures: dict = {}

        def _kill_workers(sig=None, frame=None) -> None:
            """Terminate all worker processes on Ctrl+C / SIGTERM."""
            print(
                "\nStopping — terminating worker processes...",
                flush=True,
            )
            for f in submitted_futures:
                f.cancel()
            if hasattr(executor, "_processes"):
                for pid in list(executor._processes.keys()):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
            executor.shutdown(wait=False, cancel_futures=True)
            sys.exit(1)

        signal.signal(signal.SIGINT, _kill_workers)
        signal.signal(signal.SIGTERM, _kill_workers)

        try:
            submitted_futures = {
                executor.submit(
                    _run_trial,
                    run_plan,
                    inputs,
                    results_dir,
                    graph_cfg,
                    MAX_RETRIES,
                    # Pass the pre-generated layout for this input/rep pair
                    # when available; otherwise fall back to generate_zero.
                    next(
                        iter(
                            pregen_layouts.get(
                                run_plan["input_idx"],
                                [],
                            )[run_plan["rep_idx"] : run_plan["rep_idx"] + 1]
                        ),
                        None,
                    ),
                ): run_plan["run_id"]
                for run_plan in pending
            }
            for future in as_completed(submitted_futures):
                queued_id = submitted_futures[future]
                try:
                    result_id, status, metrics, err = future.result()
                    _update_summary(
                        summary, summary_path, result_id, status, metrics
                    )
                    if status == "completed":
                        # Recompute means for that input after every rep
                        i_idx = next(
                            r["input_idx"]
                            for r in summary["runs"]
                            if r["run_id"] == result_id
                        )
                        summary["input_means"][str(i_idx)] = (
                            _compute_input_means(
                                summary["runs"], i_idx, n_reps
                            )
                        )
                        _write_summary(summary_path, summary)
                except Exception as exc:
                    print(
                        f"Unexpected error in {queued_id}: {exc}",
                        flush=True,
                    )
                    _update_summary(
                        summary, summary_path, queued_id, "failed"
                    )
        finally:
            executor.shutdown(wait=False)

    # Final per-input means (ensure all inputs are covered)
    for i_idx in range(n_inputs):
        summary["input_means"][str(i_idx)] = _compute_input_means(
            summary["runs"], i_idx, n_reps
        )
    # Wall-clock time for this invocation only (resumed runs only measure
    # the time spent on this call, not prior sessions).
    run_duration_seconds = round(time.time() - run_wall_clock_start, 3)
    summary["last_run_duration_seconds"] = run_duration_seconds
    summary["last_run_completed_at"] = datetime.now().isoformat()
    _write_summary(summary_path, summary)

    print("\n" + "=" * 70)
    print("Evaluation Results")
    print("=" * 70)
    header = (
        f"{'Input':>6}  {'Reps OK':>7}  "
        f"{'Mean Score':>10}  {'Mean Iters':>10}  "
        f"{'Compliant':>9}  {'Rate':>6}  {'Mean Dur(s)':>11}"
    )
    print(header)
    print("-" * len(header))
    for i_idx in range(n_inputs):
        m = summary["input_means"].get(str(i_idx), {})
        n_ok = m.get("n_successful", 0)
        score = (
            f"{m['mean_best_score']:.4f}"
            if m.get("mean_best_score") is not None
            else "N/A"
        )
        iters = (
            f"{m['mean_iterations']:.1f}"
            if m.get("mean_iterations") is not None
            else "N/A"
        )
        c_cnt = m.get("compliant_count", 0)
        rate = (
            f"{m['compliant_rate']:.2f}"
            if m.get("compliant_rate") is not None
            else "N/A"
        )
        dur = (
            f"{m['mean_duration_seconds']:.1f}"
            if m.get("mean_duration_seconds") is not None
            else "N/A"
        )
        print(
            f"{i_idx:>6}  {n_ok:>7}  "
            f"{score:>10}  {iters:>10}  "
            f"{c_cnt:>9}  {rate:>6}  {dur:>11}"
        )

    print("-" * len(header))
    print(f"\nThis invocation took: {run_duration_seconds:.1f}s")
    print(f"\nAll artefacts saved to: {results_dir}")
    print("Done.")


if __name__ == "__main__":
    main()
