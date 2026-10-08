# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Node to reset current_layout to best_layout and clear patience when
verify_node flags should_restore=True.

The plateau counter (`iterations_since_best_changed`) is intentionally
NOT reset so that repeated restores cannot defer the plateau exit.
"""

import copy
import logging
from typing import cast

from cando.graph.state import CandoState
from cando.utils.visualizer import plot_layout

logger = logging.getLogger(__name__)


def restore_node(state: CandoState) -> dict:
    """Resets `current_layout` to `best_layout` and clear patience.

    No LLM call is made — this node is purely deterministic. It:
    1. Deep-copies `best_layout` into `current_layout` so that
       `plan_node` and `generate_node` work from the global optimum.
    2. Resets `iterations_without_improvement` to 0 so that the patience
       clock restarts after the restore.
    3. Clears `should_restore` to False.
    4. Records `restored_from_iteration` (= `best_layout_iteration`) so
       that `build_history_node` can log which checkpoint was restored.

    Args:
        state (CandoState): Current pipeline state.

    Returns:
        dict: Partial state update containing the reset fields.
    """
    restored_layout = copy.deepcopy(state["best_layout"])
    if restored_layout is None:
        raise ValueError("best_layout is None: cannot restore from it")
    logger.info(
        "restore_node: branch=%s reverting to best_layout_iteration=%s",
        state.get("branch_id"),
        state.get("best_layout_iteration", 0),
    )
    layout_image: str | None = cast(
        "str | None",
        plot_layout(restored_layout, show=False, return_base64=True),
    )

    return {
        "current_layout": restored_layout,
        "iterations_without_improvement": 0,
        "should_restore": False,
        "restored_from_iteration": state.get("best_layout_iteration", 0),
        "layout_image": layout_image,
    }
