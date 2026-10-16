# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Utility helpers for layout evaluation scoring.

Internal helpers for per-scenario route collection, path clearance
computation, and RE sub-metric calculation used by LayoutScorer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from cando.agents.verifiers.flow.constants import (
    ALL_SCENARIOS,
    AVERAGE_VEHICLE_SIZES,
    NO_BACKTRACKING_CLASSES,
    UNDERGROUND_CLASSES,
)
from cando.agents.verifiers.flow.tools import find_routes

logger = logging.getLogger(__name__)


# Type aliases
#: Key identifying a unique scenario: (vehicle_type, class_sequence_tuple).
_ScenarioKey = tuple[str, tuple[str, ...]]


# Internal data structures


@dataclass
class _ScenarioStats:
    """Routing statistics for a single scenario in one layout.

    Args:
        vehicle_type: Vehicle type (e.g. ``'car'``, ``'hgv'``).
        class_sequence: Ordered tuple of element classes to visit.
        is_valid: ``True`` when at least one valid path was found.
        path_length: Length of the best (shortest) valid path in layout
            units.  ``inf`` when invalid.
        num_sharp_turns: Number of sharp turns on the best valid path.
        path_coordinates: Ordered (x, y) waypoints of the best valid path,
            used for clearance computation.
    """

    vehicle_type: str
    class_sequence: tuple[str, ...]
    is_valid: bool
    path_length: float = float("inf")
    num_sharp_turns: int = 0
    path_coordinates: list[tuple[float, float]] = field(default_factory=list)


# Scenario helpers


def _build_scenarios(
    elements: list[dict[str, Any]],
) -> dict[str, list[list[str]]]:
    """Returns applicable routing scenarios for a given element set.

    Filters :data:`ALL_SCENARIOS` to retain only sequences whose required
    classes are all present in *elements*.

    Args:
        elements: List of element dicts, each with a ``'class'`` key.

    Returns:
        Dict mapping vehicle type to a list of applicable class sequences.
    """
    allowed: set[str] = {e["class"] for e in elements}
    return {
        vehicle_type: [
            seq for seq in seqs if all(cls in allowed for cls in seq)
        ]
        for vehicle_type, seqs in ALL_SCENARIOS.items()
    }


def _collect_scenario_stats(
    layout: dict[str, Any],
    scenarios: dict[str, list[list[str]]],
) -> dict[_ScenarioKey, _ScenarioStats]:
    """Runs pathfinding for every scenario and records best-path statistics.

    For each (vehicle_type, class_sequence) in *scenarios*, all valid path
    combinations are found and the one with the shortest length is retained.
    Scenarios with no feasible path are recorded as invalid.

    This function passes the full list of sequences for each vehicle type to
    :func:`~cando.agents.verifiers.flow.tools.find_routes` at once,
    matching the evaluation semantics of :class:`FlowEvaluator`.

    Args:
        layout: Layout dict with an ``'elements'`` list.
        scenarios: Per-vehicle-type list of class sequences (as returned by
            :func:`_build_scenarios`).

    Returns:
        Dict mapping :data:`_ScenarioKey` to :class:`_ScenarioStats`.
    """
    stats: dict[_ScenarioKey, _ScenarioStats] = {}

    for vehicle_type, seq_list in scenarios.items():
        if not seq_list:
            continue

        vehicle_size = AVERAGE_VEHICLE_SIZES[vehicle_type]

        _, route_results, _ = find_routes(
            layout=layout,
            routes=seq_list,
            average_vehicle_size=vehicle_size,
            ignored_classes=list(UNDERGROUND_CLASSES),
            no_backtracking=list(NO_BACKTRACKING_CLASSES),
        )

        for route_data in route_results:
            class_sequence: tuple[str, ...] = tuple(route_data["route"])
            key: _ScenarioKey = (vehicle_type, class_sequence)
            paths: list[dict[str, Any]] = route_data.get("paths", [])

            if paths:
                best = min(paths, key=lambda p: p.get("length", float("inf")))
                stats[key] = _ScenarioStats(
                    vehicle_type=vehicle_type,
                    class_sequence=class_sequence,
                    is_valid=True,
                    path_length=float(best.get("length", float("inf"))),
                    num_sharp_turns=int(best.get("num_sharp_turns", 0)),
                    path_coordinates=list(best.get("coordinates", [])),
                )
            else:
                stats[key] = _ScenarioStats(
                    vehicle_type=vehicle_type,
                    class_sequence=class_sequence,
                    is_valid=False,
                )

    return stats


# Clearance computation


def compute_path_clearance(
    path_coordinates: list[tuple[float, float]],
    elements: list[dict[str, Any]],
) -> float:
    """Computes the average path clearance along a sequence of path points.

    For each point on the path, the clearance is the minimum Euclidean
    distance to any element polygon boundary in the layout (including the
    layout-premise exterior, which acts as an outer wall).  The reported
    value is the mean clearance across all path points.

    Args:
        path_coordinates: Ordered (x, y) coordinates of path waypoints.
        elements: Layout element dicts, each with a ``'polygon'`` key that
            holds either a Shapely :class:`~shapely.geometry.Polygon` or an
            iterable of coordinate pairs.

    Returns:
        Mean clearance in layout units, or ``0.0`` when the path or element
        list is empty.
    """
    if not path_coordinates or not elements:
        return 0.0

    boundaries = []
    for elem in elements:
        raw = elem.get("polygon")
        if raw is None:
            continue
        if not isinstance(raw, Polygon):
            try:
                raw = Polygon(raw)
            except Exception as exc:
                logger.debug("Skipping element with invalid polygon: %s", exc)
                continue
        if raw.is_valid and not raw.is_empty:
            boundaries.append(raw.exterior)

    if not boundaries:
        return 0.0

    combined = unary_union(boundaries)
    clearances = [Point(p).distance(combined) for p in path_coordinates]
    return float(sum(clearances) / len(clearances))
