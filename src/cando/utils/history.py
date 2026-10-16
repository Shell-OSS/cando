# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""History formatting utilities for the CANDO pipeline.

Provides format_history_for_prompt(), which converts a sliding window of
IterationSummary records from state["history"] into a compact,
LLM-readable context string injected into the Planner and Generator
system prompts.
"""

from cando.graph.state import IterationRecord


def format_history_for_prompt(
    history: list[IterationRecord],
    window: int = 5,
) -> str:
    """Converts a sliding window of IterationRecords into a prompt string.

    Only the ``summary`` portion of each record is used — ``layout_snapshot``
    is never serialised into agent context. The window is applied to the
    *most recent* entries so that deltas always refer to the immediately
    preceding iteration.

    Args:
        history (list[IterationRecord]): The full history list from
            ``state["history"]``.
        window (int): Maximum number of recent summaries to include.
            Defaults to 5.

    Returns:
        str: A formatted multi-line string suitable for injection into an
             LLM system prompt. Returns an empty string when history is empty.
    """
    if not history:
        return ""

    recent = history[-window:]
    blocks: list[str] = []

    for record in recent:
        summary = record["summary"]
        lines: list[str] = []

        iter_idx: int = summary["iteration"]
        score_before: float = summary["score_before"]
        score_after: float = summary["score_after"]
        delta: float = summary["score_delta"]

        # Build header tags
        tags: list[str] = []
        if summary["is_regression"]:
            tags.append("REGRESSION")
        restored = summary.get("restored_from_iteration")
        if restored is not None:
            tags.append(f"PATIENCE EXHAUSTED (restored to iter {restored})")

        tag_str = " - " + " - ".join(tags) if tags else ""
        delta_sign = "+" if delta >= 0 else ""

        lines.append(
            f"--- Iteration {iter_idx} | "
            f"Score: {score_before:.2f} → {score_after:.2f} "
            f"({delta_sign}{delta:.2f})"
            f"{tag_str} ---"
        )

        # Planner suggestions
        planner_actions: dict = summary.get("planner_actions", {})
        flat_actions = [
            (cat, action)
            for cat, actions in planner_actions.items()
            for action in actions
            if actions
        ]
        if flat_actions:
            lines.append("")
            lines.append("Planner suggestions:")
            for cat, action in flat_actions:
                lines.append(f"  [{cat}] {action}")

        # Generator action sequence
        winning_seq: list[str] = summary.get("winning_sequence", [])
        if winning_seq:
            lines.append("")
            lines.append("Generator action sequence:")
            for action in winning_seq:
                lines.append(f"  {action}")

        # Score deltas per category
        category_deltas: dict = summary.get("category_deltas", {})
        if category_deltas:
            lines.append("")
            lines.append("Score deltas per category:")
            max_cat_len = max(len(cat) for cat in category_deltas)
            for cat, cd in category_deltas.items():
                cat_delta: float = cd["delta"]
                note: str = cd.get("note", "")
                sign = "+" if cat_delta >= 0 else ""
                lines.append(
                    f"  {cat.ljust(max_cat_len)}: "
                    f"{sign}{cat_delta:.2f}" + (f"  {note}" if note else "")
                )

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)
