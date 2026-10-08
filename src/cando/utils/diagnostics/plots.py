# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Matplotlib visualisations for CANDO metrics.

Saves a suite of PNG plots from a metrics dictionary.
"""

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def save_plots(
    metrics: dict,
    out_dir: Path | str,
) -> None:
    """Saves all metric plots to *out_dir*.

    Creates the directory if it does not exist. Each plot is saved as a
    150-DPI PNG. Errors in individual plots are caught and logged so that
    one failure does not prevent the remaining plots from being saved.

    Args:
        metrics (dict): Output of `compute_metrics(final_state)`.
        out_dir (Path | str): Directory where the PNGs will be written.
            Created automatically if absent.
    """
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    plot_fns = (
        plot_score_trajectory,
        plot_category_trajectories,
        plot_strategy_analysis,
        plot_grounding_analysis,
        plot_candidate_diversity,
        plot_planning_focus,
    )
    for fn in plot_fns:
        try:
            fn(metrics, out_dir, plt)
        except Exception as exc:
            logger.warning("%s failed: %s", fn.__name__, exc)


# Individual plot functions
def plot_score_trajectory(
    metrics: dict,
    out_dir: Path,
    plt,
) -> None:
    """Plots overall compliance score before/after per iteration.

    Restore events are marked with vertical dotted lines.

    Args:
        metrics (dict): Output of `compute_metrics(final_state)`.
        out_dir (Path): Directory to save `score_trajectory.png`.
        plt: `matplotlib.pyplot` module passed in by the caller.
    """
    traj = metrics["score_trajectory"]
    if not traj:
        return

    iterations = [r["iteration"] for r in traj]
    score_after = [r["score_after"] for r in traj]
    score_before = [r["score_before"] for r in traj]
    restore_iters = [
        r["iteration"]
        for r in traj
        if r["restored_from_iteration"] is not None
    ]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(
        iterations,
        score_before,
        "o--",
        color="#888888",
        alpha=0.6,
        label="Score before",
        linewidth=1,
    )
    ax.plot(
        iterations,
        score_after,
        "o-",
        color="#2171b5",
        label="Score after",
        linewidth=2,
    )

    added_restore_label = False
    for ri in restore_iters:
        ax.axvline(
            ri,
            color="#d62728",
            linestyle=":",
            alpha=0.7,
            linewidth=1.5,
            label="Restore" if not added_restore_label else None,
        )
        added_restore_label = True

    rs = metrics["run_summary"]
    ax.set_title(
        f"Score Trajectory  |  exit: {rs['exit_reason']}"
        f"  |  best: {rs['best_score']:.4f}"
    )
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Compliance Score")
    ax.set_ylim(-0.02, 1.05)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "score_trajectory.png", dpi=150)
    plt.close(fig)


def plot_category_trajectories(
    metrics: dict,
    out_dir: Path,
    plt,
) -> None:
    """Plots a 2x3 grid of per-category score trajectories.

    Scatter dots are coloured green on improvement and red on regression.

    Args:
        metrics (dict): Output of `compute_metrics(final_state)`.
        out_dir (Path): Directory to save `category_trajectories.png`.
        plt: `matplotlib.pyplot` module passed in by the caller.
    """
    cat_traj = metrics["category_score_trajectory"]
    categories = list(cat_traj.keys())
    if not categories or not cat_traj[categories[0]]:
        return

    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    axes_flat = axes.flatten()

    for i, cat in enumerate(categories):
        ax = axes_flat[i]
        records = cat_traj[cat]
        iters = [r["iteration"] for r in records]
        after = [r["score_after"] for r in records]
        delta_colors = [
            "#d62728" if r["delta"] < 0 else "#2ca02c" for r in records
        ]
        ax.plot(iters, after, "-", linewidth=2, color="#2171b5")
        ax.scatter(iters, after, c=delta_colors, zorder=5, s=40)
        ax.set_title(cat.replace("_", " ").title(), fontsize=10)
        ax.set_ylim(-0.02, 1.05)
        ax.set_xlabel("Iteration", fontsize=8)
        ax.set_ylabel("Score", fontsize=8)
        ax.grid(True, alpha=0.3)

    for j in range(len(categories), len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle("Per-Category Score Trajectories", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_dir / "category_trajectories.png", dpi=150)
    plt.close(fig)


def plot_strategy_analysis(
    metrics: dict,
    out_dir: Path,
    plt,
) -> None:
    """Plots a three-panel strategy effectiveness chart.

    Panels: selection vs win counts, win rate, average compliance score.

    Args:
        metrics (dict): Output of `compute_metrics(final_state)`.
        out_dir (Path): Directory to save `strategy_analysis.png`.
        plt: `matplotlib.pyplot` module passed in by the caller.
    """
    sa = metrics["strategy_analysis"]
    sel = sa["strategy_selection_counts"]
    if not sel:
        return

    strategies = sorted(sel.keys())
    x = np.arange(len(strategies))
    sel_counts = [sel.get(s, 0) for s in strategies]
    win_counts = [sa["strategy_win_counts"].get(s, 0) for s in strategies]
    win_rates = [sa["strategy_win_rate"].get(s, 0.0) for s in strategies]
    avg_scores = [sa["strategy_avg_score"].get(s, 0.0) for s in strategies]
    labels = [s.replace("_", "\n") for s in strategies]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    ax = axes[0]
    w = 0.35
    ax.bar(
        x - w / 2,
        sel_counts,
        w,
        label="Selected",
        color="#4292c6",
    )
    ax.bar(
        x + w / 2,
        win_counts,
        w,
        label="Won",
        color="#08519c",
    )
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_title("Selection vs Wins")
    ax.set_ylabel("Count")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")

    ax = axes[1]
    ax.bar(x, win_rates, color="#2ca02c")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_title("Win Rate")
    ax.set_ylabel("Win Rate")
    ax.set_ylim(0, 1.05)
    ax.grid(True, alpha=0.3, axis="y")

    ax = axes[2]
    ax.bar(x, avg_scores, color="#ff7f0e")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_title("Average Compliance Score")
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.05)
    ax.grid(True, alpha=0.3, axis="y")

    fig.suptitle("Generator Strategy Analysis", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_dir / "strategy_analysis.png", dpi=150)
    plt.close(fig)


def plot_grounding_analysis(
    metrics: dict,
    out_dir: Path,
    plt,
) -> None:
    """Plots a stacked bar per category and overall pie for grounding sources.

    Args:
        metrics (dict): Output of `compute_metrics(final_state)`.
        out_dir (Path): Directory to save `grounding_analysis.png`.
        plt: `matplotlib.pyplot` module passed in by the caller.
    """
    ga = metrics["grounding_analysis"]
    by_cat = ga["grounding_counts_by_category"]
    if not by_cat:
        return

    categories = list(by_cat.keys())
    sources = ["visual", "worker", "history", "layout"]
    colors = {
        "visual": "#6baed6",
        "worker": "#74c476",
        "history": "#fd8d3c",
        "layout": "#9e9ac8",
    }

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    x = np.arange(len(categories))
    bottoms = np.zeros(len(categories))
    for src in sources:
        vals = np.array(
            [by_cat[cat].get(src, 0) for cat in categories],
            dtype=float,
        )
        ax.bar(
            x,
            vals,
            bottom=bottoms,
            label=src,
            color=colors[src],
            alpha=0.85,
        )
        bottoms += vals
    ax.set_xticks(x)
    ax.set_xticklabels(
        [c.replace("_", " ").title() for c in categories],
        rotation=30,
        ha="right",
        fontsize=8,
    )
    ax.set_title("Grounding Sources by Category")
    ax.set_ylabel("Action Count")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")

    ax = axes[1]
    total = ga["grounding_counts_total"]
    pie_vals = [total.get(src, 0) for src in sources]
    non_zero = [
        (v, src, colors[src])
        for v, src in zip(pie_vals, sources, strict=True)
        if v > 0
    ]
    if non_zero:
        nv, ns, nc = zip(*non_zero, strict=True)
        ax.pie(
            nv,
            labels=ns,
            colors=nc,
            autopct="%1.1f%%",
            startangle=90,
            textprops={"fontsize": 10},
        )
    ax.set_title("Overall Grounding Distribution")

    fig.suptitle("Planner Grounding Source Analysis", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_dir / "grounding_analysis.png", dpi=150)
    plt.close(fig)


def plot_candidate_diversity(
    metrics: dict,
    out_dir: Path,
    plt,
) -> None:
    """Plots score standard deviation and range across candidates per
    iteration.

    Args:
        metrics (dict): Output of `compute_metrics(final_state)`.
        out_dir (Path): Directory to save `candidate_diversity.png`.
        plt: `matplotlib.pyplot` module passed in by the caller.
    """
    div = metrics["candidate_diversity"]["per_iteration"]
    if not div:
        return

    iters = [d["iteration"] for d in div]
    std_vals = [d["score_std"] for d in div]
    range_vals = [d["score_range"] for d in div]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(
        iters,
        range_vals,
        color="#9ecae1",
        alpha=0.7,
        label="Score range",
    )
    ax.plot(
        iters,
        std_vals,
        "o-",
        color="#08519c",
        linewidth=2,
        label="Score std",
    )
    avg_var = metrics["candidate_diversity"]["avg_score_variance"]
    ax.set_title(f"Candidate Score Diversity  (avg variance: {avg_var:.4f})")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Score Spread")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "candidate_diversity.png", dpi=150)
    plt.close(fig)


def plot_planning_focus(
    metrics: dict,
    out_dir: Path,
    plt,
) -> None:
    """Plots a bar and pie chart of total planned actions per category.

    Args:
        metrics (dict): Output of `compute_metrics(final_state)`.
        out_dir (Path): Directory to save `planning_focus.png`.
        plt: `matplotlib.pyplot` module passed in by the caller.
    """
    focus = metrics["planning_focus"]["category_action_counts"]
    if not focus or sum(focus.values()) == 0:
        return

    categories = list(focus.keys())
    counts = [focus[c] for c in categories]
    total = sum(counts) or 1
    fractions = [c / total for c in counts]
    labels_short = [c.replace("_", " ") for c in categories]

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))

    ax = axes[0]
    x = np.arange(len(categories))
    ax.bar(x, counts, color="#74c476", alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(
        [c.replace("_", " ").title() for c in categories],
        rotation=30,
        ha="right",
        fontsize=8,
    )
    ax.set_title("Total Planning Actions per Category")
    ax.set_ylabel("Count")
    ax.grid(True, alpha=0.3, axis="y")

    ax = axes[1]
    ax.pie(
        fractions,
        labels=labels_short,
        autopct="%1.1f%%",
        startangle=90,
        textprops={"fontsize": 9},
    )
    ax.set_title("Planning Focus Distribution")

    fig.suptitle("Planner Focus Analysis", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_dir / "planning_focus.png", dpi=150)
    plt.close(fig)
