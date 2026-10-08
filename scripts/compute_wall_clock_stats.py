# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Aggregate wall-clock / iteration statistics from CANDO ablation run
directories (logs/CANDO_ABLATION_<condition>_<jobid>/summary.json).

Each ablation run directory holds a single `summary.json` with a "runs"
list, one entry per (input_idx, rep_idx), and each entry's `metrics`
already has `duration_seconds` / `started_at` / `completed_at` /
`iterations` / `is_compliant` / `best_score` computed by the harness itself.

Usage (from the repository root):
    uv run python scripts/compute_wall_clock_stats.py \\
        --logs-root logs \\
        --out results/ablation_wallclock_stats.json \\
        --md-out results/ablation_wallclock_stats.md

Note: this prints a WARNING for any condition whose run directory has more
than one corresponding .out log under logs/logfiles/ (job requeue/resubmit),
since queueing time can otherwise get silently counted as compute time.
Always sanity-check duration_seconds against started_at/completed_at deltas
before quoting these numbers.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

# Folder name pattern: CANDO_ABLATION_{condition}_{jobid}
# e.g. CANDO_ABLATION_full_12345, CANDO_ABLATION_no_tool_verifier_12346
_DIR_RE = re.compile(r"^CANDO_ABLATION_(?P<condition>.+)_(?P<jobid>\d+)$")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def discover_run_dirs(logs_root: Path) -> list[Path]:
    return sorted(
        child
        for child in logs_root.iterdir()
        if child.is_dir() and _DIR_RE.match(child.name)
    )


def count_logfiles_for_condition(logs_root: Path, condition: str) -> int:
    """Count .out logfiles whose name contains the condition, to flag
    possible job requeues/resubmissions under a different job id."""
    logfiles_dir = logs_root / "logfiles"
    if not logfiles_dir.is_dir():
        return 0
    needle = f"cando_ablation_{condition}_".lower()
    return sum(
        1
        for f in logfiles_dir.iterdir()
        if f.is_file()
        and f.suffix == ".out"
        and f.name.lower().startswith(needle)
    )


def stats_for_condition(
    run_dir: Path, logs_root: Path
) -> dict[str, Any] | None:
    m = _DIR_RE.match(run_dir.name)
    if not m:
        return None
    condition = m.group("condition")

    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return None

    data = json.loads(summary_path.read_text(encoding="utf-8"))
    runs = data.get("runs", [])

    durations: list[float] = []
    iterations: list[float] = []
    best_scores: list[float] = []
    n_compliant = 0
    starts: list[datetime] = []
    ends: list[datetime] = []
    n_completed = 0
    n_failed = 0

    for run in runs:
        if run.get("status") != "completed":
            n_failed += 1
            continue
        n_completed += 1
        metrics = run.get("metrics", {})

        dur = metrics.get("duration_seconds")
        if isinstance(dur, (int, float)):
            durations.append(float(dur))

        it = metrics.get("iterations")
        if isinstance(it, (int, float)):
            iterations.append(float(it))

        score = metrics.get("best_score")
        if isinstance(score, (int, float)):
            best_scores.append(float(score))

        if metrics.get("is_compliant"):
            n_compliant += 1

        st = _parse_iso(metrics.get("started_at"))
        et = _parse_iso(metrics.get("completed_at"))
        if st:
            starts.append(st)
        if et:
            ends.append(et)

    if not durations:
        return None

    batch_wall_clock_seconds = None
    if starts and ends:
        batch_wall_clock_seconds = (max(ends) - min(starts)).total_seconds()

    n_logfiles = count_logfiles_for_condition(logs_root, condition)

    return {
        "run_dir": run_dir.name,
        "condition": condition,
        "total_runs": data.get("total_runs"),
        "n_completed": n_completed,
        "n_failed": n_failed,
        "n_logfiles_found": n_logfiles,
        "possible_requeue": n_logfiles > 1,
        "duration_seconds": {
            "mean": round(statistics.mean(durations), 2),
            "median": round(statistics.median(durations), 2),
            "std": round(statistics.pstdev(durations), 2)
            if len(durations) > 1
            else 0.0,
            "min": round(min(durations), 2),
            "max": round(max(durations), 2),
        },
        "iterations": {
            "mean": round(statistics.mean(iterations), 3)
            if iterations
            else None,
            "median": statistics.median(iterations) if iterations else None,
        },
        "best_score": {
            "mean": round(statistics.mean(best_scores), 4)
            if best_scores
            else None,
        },
        "compliance_rate": round(n_compliant / n_completed, 4)
        if n_completed
        else None,
        "batch_wall_clock_seconds": (
            round(batch_wall_clock_seconds, 1)
            if batch_wall_clock_seconds
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-root", type=Path, default=Path("logs"))
    parser.add_argument(
        "--out", type=Path, default=None, help="Optional JSON output path."
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=None,
        help="Optional Markdown table output path.",
    )
    args = parser.parse_args()

    run_dirs = discover_run_dirs(args.logs_root)
    if not run_dirs:
        raise SystemExit(
            "No matching CANDO_ABLATION_* directories found under "
            f"{args.logs_root}"
        )

    results = []
    for run_dir in run_dirs:
        r = stats_for_condition(run_dir, args.logs_root)
        if r is not None:
            results.append(r)
        else:
            print(f"[skip] {run_dir.name}: no usable summary.json data")

    results.sort(key=lambda r: r["condition"])

    for r in results:
        if r["possible_requeue"]:
            print(
                f"[WARNING] condition '{r['condition']}' has "
                f"{r['n_logfiles_found']} .out logfiles -> possible job "
                "requeue/resubmit. Sanity-check duration_seconds before "
                "quoting (see docstring)."
            )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=2))
        print(f"Wrote JSON -> {args.out}")

    lines = [
        "| Condition | N completed | Mean duration (s) | Median (s) | "
        "Std | Mean iters | Compliance rate | Requeue risk |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        d = r["duration_seconds"]
        it = r["iterations"]
        lines.append(
            f"| {r['condition']} | {r['n_completed']}/{r['total_runs']} | "
            f"{d['mean']} | {d['median']} | {d['std']} | "
            f"{it['mean'] if it['mean'] is not None else '-'} | "
            f"{r['compliance_rate']} | "
            f"{'YES' if r['possible_requeue'] else 'no'} |"
        )

    md = "\n".join(lines)
    print(md)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md + "\n")
        print(f"Wrote Markdown -> {args.md_out}")


if __name__ == "__main__":
    main()
