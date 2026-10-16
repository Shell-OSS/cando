# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Live logging utilities for the CANDO pipeline.

Helper functions that nodes call to write per-iteration and per-round
artifacts to disk while the graph is still running.
"""

import base64
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_iteration_log(
    log_dir: str,
    round_num: int,
    branch_id: int,
    iteration: int,
    summary: Mapping[str, Any],
    layout_image_b64: str | None = None,
) -> None:
    """Write one iteration's summary and layout visualisation to disk.

    Creates the directory tree if it does not already exist.  Called
    as a side effect inside build_history_node immediately after the
    IterationSummary is assembled, so the file is on disk before the
    node returns.

    Args:
        log_dir (str): Root output directory for this graph run.
        round_num (int): 1-based fan-out round counter.
        branch_id (int): 0-based branch index within the current round.
        iteration (int): Global (0-based) iteration index.
        summary (Mapping[str, Any]): IterationSummary dict to serialise as
            JSON.
        layout_image_b64 (str | None): Base64-encoded PNG of the winning
            candidate layout.  Written as `layout.png` when present.

    Returns:
        None
    """
    try:
        iter_dir = (
            Path(log_dir)
            / f"round_{round_num:03d}"
            / f"branch_{branch_id:02d}"
            / f"iteration_{iteration:03d}"
        )
        iter_dir.mkdir(parents=True, exist_ok=True)

        with open(iter_dir / "summary.json", "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, default=str)

        if layout_image_b64:
            img_bytes = base64.b64decode(layout_image_b64)
            with open(iter_dir / "layout.png", "wb") as fh:
                fh.write(img_bytes)

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to write iteration log (round=%s, branch=%s, "
            "iter=%s): %s",
            round_num,
            branch_id,
            iteration,
            exc,
        )


def write_reduce_summary(
    log_dir: str,
    round_num: int,
    summary: dict,
) -> None:
    """Write the reduce-step summary for a round to disk.

    Called as a side effect inside reduce_node after the winning branch
    is selected and all state fields are computed.  The file lands at
    `{log_dir}/round_{round_num:03d}/reduce_summary.json`.

    Args:
        log_dir (str): Root output directory for this graph run.
        round_num (int): 1-based fan-out round counter.
        summary (dict): Reduce metrics to serialise (winning branch id,
            scores, improvement flag, exit reason, per-branch breakdown).

    Returns:
        None
    """
    try:
        round_dir = Path(log_dir) / f"round_{round_num:03d}"
        round_dir.mkdir(parents=True, exist_ok=True)

        with open(
            round_dir / "reduce_summary.json", "w", encoding="utf-8"
        ) as fh:
            json.dump(summary, fh, indent=2, default=str)

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to write reduce summary (round=%s): %s",
            round_num,
            exc,
        )
