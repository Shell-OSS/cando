# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""
Compute ablation-study insights from a CANDO ablation run directory.

For each ``input<N>_rep<R>`` sub-folder produced by the ablation runner, this
script reads:
  - ``result.json``                               -> run-level metrics
    (best_score, iterations, is_compliant, exit_reason, duration_seconds)
  - ``round_*/reduce_summary.json``                -> per-round fan-out info
    (number of branches explored, winning branch, whether the round improved
    the score)
  - ``round_*/branch_*/iteration_*/summary.json``  -> per-iteration details
    (score deltas, regressions, candidate/strategy counts, grounding-tool
    usage per category, per-category compliance scores)

It writes one JSON line per run to ``--output`` (JSONL) and prints an
aggregate summary (compliance rate, wall clock, average iterations to
comply, regression rate, grounding-tool usage, per-category final scores,
etc.) across all runs to stdout. These insights are meant to be compared
across ablation runs (e.g. "no_visual" vs "full") to measure the effect of
removing each architecture component.

Usage:
    uv run python scripts/collect_cando_insights.py \
        --input logs/my_ablation_run \
        --output results/no_planner_insights.jsonl \
        --dataset /path/to/constraints.jsonl
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from tqdm import tqdm

# Regex that matches run folders produced by the ablation runner.
_FOLDER_RE = re.compile(r"^input(?P<input_idx>\d+)_rep(?P<rep>\d+)$")

CATEGORIES = (
    "hard_constraints",
    "assembly",
    "proximity",
    "orientation",
    "flow",
)
MODALITIES = ("visual", "worker", "history", "layout")


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


def _empty_grounding_counts() -> dict[str, dict[str, int]]:
    return {cat: {mod: 0 for mod in MODALITIES} for cat in CATEGORIES}


def _analyze_run(run_dir: Path) -> dict[str, Any]:
    """Compute insights for a single ``input<N>_rep<R>`` run directory."""
    result = _load_json(run_dir / "result.json") or {}
    metrics = result.get("metrics", {})

    round_dirs = sorted(run_dir.glob("round_*"))

    total_branches = 0
    branches_per_round: list[int] = []
    rounds_improved = 0
    final_winning_branch: Path | None = None

    for round_dir in round_dirs:
        reduce_summary = _load_json(round_dir / "reduce_summary.json") or {}
        all_branches = reduce_summary.get("all_branches", [])
        branches_per_round.append(len(all_branches))
        total_branches += len(all_branches)
        if reduce_summary.get("improved"):
            rounds_improved += 1
        winning_branch_id = reduce_summary.get("winning_branch_id")
        if winning_branch_id is not None:
            final_winning_branch = (
                round_dir / f"branch_{winning_branch_id:02d}"
            )

    # Walk every iteration across all branches/rounds (not just the winning
    # one) to measure total search effort, e.g. tool-usage across ablations.
    total_iterations_explored = 0
    regression_count = 0
    candidate_counts: list[int] = []
    score_deltas: list[float] = []
    winning_strategy_counts: Counter = Counter()
    grounding_totals = _empty_grounding_counts()

    for round_dir in round_dirs:
        for branch_dir in sorted(round_dir.glob("branch_*")):
            for iter_dir in sorted(branch_dir.glob("iteration_*")):
                summary = _load_json(iter_dir / "summary.json")
                if summary is None:
                    continue
                total_iterations_explored += 1
                if summary.get("is_regression"):
                    regression_count += 1
                candidates = [
                    c
                    for c in (summary.get("candidate_scores") or [])
                    if c is not None
                ]
                candidate_counts.append(len(candidates))
                score_deltas.append(summary.get("score_delta", 0.0))
                winning_strategy = summary.get("winning_strategy")
                if winning_strategy:
                    winning_strategy_counts[winning_strategy] += 1
                for cat in CATEGORIES:
                    counts = summary.get("grounding_counts", {}).get(cat, {})
                    for mod in MODALITIES:
                        grounding_totals[cat][mod] += counts.get(mod, 0)

    # Final per-category scores: last iteration of the winning branch of
    # the last round, i.e. the trajectory that produced best_layout.json.
    final_category_scores: dict[str, float] = {}
    if final_winning_branch is not None:
        iter_dirs = sorted(final_winning_branch.glob("iteration_*"))
        if iter_dirs:
            last_summary = _load_json(iter_dirs[-1] / "summary.json") or {}
            for cat in CATEGORIES:
                cat_delta = last_summary.get("category_deltas", {}).get(cat)
                if cat_delta is not None:
                    final_category_scores[cat] = cat_delta.get("score_after")

    return {
        "run_id": run_dir.name,
        "input_idx": result.get("input_idx"),
        "rep_idx": result.get("rep_idx"),
        "mode": result.get("mode"),
        "best_score": metrics.get("best_score"),
        "is_compliant": metrics.get("is_compliant"),
        "exit_reason": metrics.get("exit_reason"),
        "iterations": metrics.get("iterations"),
        "duration_seconds": metrics.get("duration_seconds"),
        "seconds_per_iteration": (
            metrics.get("duration_seconds") / metrics.get("iterations")
            if metrics.get("duration_seconds") is not None
            and metrics.get("iterations")
            else None
        ),
        "started_at": metrics.get("started_at"),
        "completed_at": metrics.get("completed_at"),
        "num_rounds": len(round_dirs),
        "avg_branches_per_round": (
            sum(branches_per_round) / len(branches_per_round)
            if branches_per_round
            else 0.0
        ),
        "rounds_improved": rounds_improved,
        "total_branches_explored": total_branches,
        "total_iterations_explored": total_iterations_explored,
        "regression_count": regression_count,
        "regression_rate": (
            regression_count / total_iterations_explored
            if total_iterations_explored
            else 0.0
        ),
        "avg_candidates_per_iteration": (
            sum(candidate_counts) / len(candidate_counts)
            if candidate_counts
            else 0.0
        ),
        "avg_score_delta_per_iteration": (
            sum(score_deltas) / len(score_deltas) if score_deltas else 0.0
        ),
        "winning_strategy_counts": dict(winning_strategy_counts),
        "grounding_counts_total": grounding_totals,
        "final_category_scores": final_category_scores,
    }


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts)


def _build_aggregate_summary_lines(runs: list[dict[str, Any]]) -> list[str]:
    n = len(runs)
    if n == 0:
        return ["No runs to summarize."]

    compliant = [r for r in runs if r["is_compliant"]]
    durations = [
        r["duration_seconds"]
        for r in runs
        if r["duration_seconds"] is not None
    ]
    per_iter_durations = [
        r["seconds_per_iteration"]
        for r in runs
        if r["seconds_per_iteration"] is not None
    ]
    best_scores = [
        r["best_score"] for r in runs if r["best_score"] is not None
    ]
    iterations_all = [
        r["iterations"] for r in runs if r["iterations"] is not None
    ]
    iterations_compliant = [
        r["iterations"] for r in compliant if r["iterations"] is not None
    ]

    # Makespan, not sum of durations: runs execute concurrently, so wall
    # clock for the batch is max(completed_at) - min(started_at).
    starts = [
        t for t in (_parse_ts(r["started_at"]) for r in runs) if t is not None
    ]
    ends = [
        t
        for t in (_parse_ts(r["completed_at"]) for r in runs)
        if t is not None
    ]
    makespan_seconds = (
        (max(ends) - min(starts)).total_seconds() if starts and ends else None
    )

    exit_reason_counts = Counter(r["exit_reason"] for r in runs)

    grounding_totals = _empty_grounding_counts()
    for r in runs:
        for cat in CATEGORIES:
            for mod in MODALITIES:
                grounding_totals[cat][mod] += r["grounding_counts_total"][
                    cat
                ][mod]

    final_category_means = {}
    for cat in CATEGORIES:
        vals = [
            r["final_category_scores"][cat]
            for r in runs
            if cat in r["final_category_scores"]
        ]
        final_category_means[cat] = _mean(vals)

    strategy_counts: Counter = Counter()
    for r in runs:
        strategy_counts.update(r["winning_strategy_counts"])

    total_iters_explored = sum(r["total_iterations_explored"] for r in runs)
    total_regressions = sum(r["regression_count"] for r in runs)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"Aggregate insights over {n} runs")
    lines.append("=" * 70)
    lines.append(
        f"Compliance rate:             {len(compliant)}/{n} "
        f"({100 * len(compliant) / n:.1f}%)"
    )
    lines.append(f"Mean best_score:             {_mean(best_scores):.4f}")
    lines.append(f"Exit reasons:                {dict(exit_reason_counts)}")
    lines.append("")
    lines.append("Wall clock (seconds):")
    lines.append(f"  mean per-run duration:      {_mean(durations):.1f}")
    lines.append(f"  median per-run duration:    {_median(durations):.1f}")
    lines.append(
        f"  sum of per-run durations:   {sum(durations):.1f}  "
        "(NOT elapsed time - runs overlap; useful only as total "
        "compute-seconds consumed)"
    )
    if makespan_seconds is not None:
        lines.append(
            f"  batch makespan (elapsed):   {makespan_seconds:.1f}  "
            "(max completed_at - min started_at)"
        )
    lines.append(
        f"  mean seconds/iteration:     {_mean(per_iter_durations):.1f}  "
        "(normalizes for iteration count)"
    )
    lines.append("")
    lines.append("Iterations to convergence:")
    lines.append(f"  mean (all runs):            {_mean(iterations_all):.2f}")
    lines.append(
        f"  mean (compliant runs only): {_mean(iterations_compliant):.2f}"
    )
    lines.append("")
    lines.append("Search effort:")
    lines.append(
        "  mean branches/round:        "
        f"{_mean([r['avg_branches_per_round'] for r in runs]):.2f}"
    )
    lines.append(
        "  mean iterations explored:   "
        f"{_mean([r['total_iterations_explored'] for r in runs]):.2f}"
    )
    if total_iters_explored:
        lines.append(
            f"  regression rate:            {total_regressions}/"
            f"{total_iters_explored} "
            f"({100 * total_regressions / total_iters_explored:.1f}%)"
        )
    else:
        lines.append("  regression rate:            n/a")
    lines.append(
        "  mean candidates/iteration:  "
        f"{_mean([r['avg_candidates_per_iteration'] for r in runs]):.2f}"
    )
    lines.append("")
    lines.append("Winning strategy distribution:")
    for strategy, count in strategy_counts.most_common():
        lines.append(f"  {strategy:<25s} {count}")
    lines.append("")
    lines.append(
        "Grounding modality usage (summed over all iterations/branches):"
    )
    for cat in CATEGORIES:
        lines.append(f"  {cat:<20s} {grounding_totals[cat]}")
    lines.append("")
    lines.append(
        "Mean final per-category score (winning trajectory, last iteration):"
    )
    for cat in CATEGORIES:
        lines.append(f"  {cat:<20s} {final_category_means[cat]:.4f}")
    lines.append("=" * 70)
    return lines


def _print_aggregate_summary(
    runs: list[dict[str, Any]], summary_file: Path | None = None
) -> None:
    lines = _build_aggregate_summary_lines(runs)
    text = "\n".join(lines)
    print(text)
    if summary_file is not None:
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(text + "\n")
        print(f"Wrote aggregate summary -> {summary_file}")


def compute_insights(
    input_dir: Path,
    output_file: Path,
    dataset_file: Path | None,
    summary_file: Path | None,
) -> None:
    dataset_ids = (
        _load_dataset_ids(dataset_file) if dataset_file is not None else None
    )

    run_dirs = []
    for d in sorted(input_dir.iterdir()):
        if not d.is_dir():
            continue
        if _FOLDER_RE.match(d.name):
            run_dirs.append(d)

    if not run_dirs:
        print(f"No run directories found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    run_dirs.sort(
        key=lambda d: tuple(int(g) for g in _FOLDER_RE.match(d.name).groups())
    )

    runs: list[dict[str, Any]] = []
    for run_dir in tqdm(run_dirs, desc="Computing insights", unit="run"):
        insight = _analyze_run(run_dir)
        if dataset_ids is not None and insight["input_idx"] is not None:
            input_idx = insight["input_idx"]
            if input_idx < len(dataset_ids):
                insight["id"] = dataset_ids[input_idx]
            else:
                print(
                    f"  WARNING: input_idx={input_idx} is out of range "
                    f"for dataset {dataset_file} ({len(dataset_ids)} rows), "
                    "leaving 'id' unset.",
                    file=sys.stderr,
                )
        runs.append(insight)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as fh:
        for entry in runs:
            fh.write(json.dumps(entry) + "\n")
    print(f"Wrote {len(runs)} entries -> {output_file}")

    _print_aggregate_summary(runs, summary_file)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compute ablation-study insights (wall clock, compliance rate, "
            "iterations to comply, regression rate, grounding-tool usage, "
            "per-category scores, etc.) from a CANDO ablation run directory."
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
        help="Output JSONL file path (one insights record per run).",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help=(
            "Optional path to the reference dataset jsonl file whose row "
            "order defines the 'id' for each input<N> (adds an 'id' field "
            "to each output record)."
        ),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help=(
            "Output text file path for the aggregate summary (defaults to "
            "--output with a .txt suffix, e.g. no_visual_insights.jsonl -> "
            "no_visual_insights.txt)."
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
    if dataset_file is not None:
        if not dataset_file.is_absolute():
            dataset_file = Path.cwd() / dataset_file
        if not dataset_file.exists():
            print(
                f"ERROR: dataset file not found: {dataset_file}",
                file=sys.stderr,
            )
            sys.exit(1)

    if not input_dir.exists():
        print(
            f"ERROR: input directory not found: {input_dir}", file=sys.stderr
        )
        sys.exit(1)

    summary_file = args.summary
    if summary_file is None:
        summary_file = output_file.with_suffix(".txt")
    elif not summary_file.is_absolute():
        summary_file = Path.cwd() / summary_file

    compute_insights(input_dir, output_file, dataset_file, summary_file)


if __name__ == "__main__":
    main()
