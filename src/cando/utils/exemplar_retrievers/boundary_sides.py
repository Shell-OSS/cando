# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Boundary-side reasoning for the entrance/exit same-side feature."""

from collections import defaultdict

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

# Max heading deviation from a side's mean heading before a new side starts
ANGLE_TOL = np.deg2rad(30.0)
# Edges shorter than this share of the perimeter never start a new side
MIN_EDGE_FRAC = 0.03
# Buffer around an entrance/exit (layout units) when testing boundary contact
FOOTPRINT_TOL = 1.0
# Boundary length needed to touch a side, as a share of the element's extent
MIN_TOUCH_FRAC = 0.25


def _angle_diff(a: float, b: float) -> float:
    """Returns the absolute difference between two angles in radians."""
    return abs((a - b + np.pi) % (2 * np.pi) - np.pi)


def segment_boundary(layout: Polygon) -> tuple[np.ndarray, np.ndarray]:
    """Splits the layout boundary into sides (near-straight runs of edges).

    Args:
        layout (Polygon): Layout polygon; only its exterior ring is used.

    Returns:
        tuple[np.ndarray, np.ndarray]: Counter-clockwise ring vertices
            with shape (n, 2) and the side id of each edge with shape (n,).
    """
    # Counter-clockwise ring without the duplicated closing vertex
    ring = orient(Polygon(layout.exterior), 1.0).exterior
    coords = np.asarray(ring.coords)[:-1]
    n = len(coords)

    # Edge lengths and headings
    vec = np.roll(coords, -1, axis=0) - coords
    lens = np.hypot(vec[:, 0], vec[:, 1])
    head = np.arctan2(vec[:, 1], vec[:, 0])

    # Only edges of meaningful length may start a new side
    significant = lens >= MIN_EDGE_FRAC * lens.sum()
    if not significant.any():
        significant[:] = True
    idx = np.flatnonzero(significant)

    # Start at the sharpest corner so no side straddles the ring start
    turns = [
        _angle_diff(head[idx[k]], head[idx[k - 1]]) for k in range(len(idx))
    ]
    start = int(idx[int(np.argmax(turns))])

    edge_to_side = np.zeros(n, dtype=int)
    side = -1
    mean = np.zeros(2)  # Length-weighted heading sum of the current side
    for i in ((start + k) % n for k in range(n)):
        if significant[i]:
            # Open a new side once the heading leaves the side's mean heading
            mean_head = np.arctan2(mean[1], mean[0])
            if side < 0 or _angle_diff(head[i], mean_head) > ANGLE_TOL:
                side += 1
                mean = np.zeros(2)
            mean += lens[i] * np.array([np.cos(head[i]), np.sin(head[i])])
        # Short edges join the side that is currently open
        edge_to_side[i] = side

    return coords, edge_to_side


def touched_sides(
    coords: np.ndarray, edge_to_side: np.ndarray, elem: Polygon
) -> set[int]:
    """Returns the ids of the boundary sides an element touches.

    Args:
        coords (np.ndarray): Ring vertices from `segment_boundary`.
        edge_to_side (np.ndarray): Side id per edge from `segment_boundary`.
        elem (Polygon): Entrance or exit polygon.

    Returns:
        set[int]: Touched side ids; an element off the boundary gets the
            side of its nearest edge.
    """
    n = len(coords)
    edges = [LineString([coords[e], coords[(e + 1) % n]]) for e in range(n)]

    # Boundary length inside the element's buffered footprint, per side
    footprint = elem.buffer(FOOTPRINT_TOL)
    length: dict[int, float] = defaultdict(float)
    for e, seg in enumerate(edges):
        length[int(edge_to_side[e])] += seg.intersection(footprint).length

    # A side counts as touched if enough of it lies inside the footprint
    minx, miny, maxx, maxy = elem.bounds
    needed = MIN_TOUCH_FRAC * max(maxx - minx, maxy - miny)
    sides = {s for s, total in length.items() if total >= needed}
    if sides:
        return sides

    # Fallback for elements off the boundary: side of the nearest edge
    nearest = min(range(n), key=lambda e: edges[e].distance(elem))
    return {int(edge_to_side[nearest])}


def share_side(
    layout: Polygon, entrances: list[Polygon], exits: list[Polygon]
) -> bool:
    """Checks whether any entrance and any exit touch a common side.

    Args:
        layout (Polygon): Layout polygon.
        entrances (list[Polygon]): Entrance polygons.
        exits (list[Polygon]): Exit polygons.

    Returns:
        bool: True if some entrance/exit pair shares a boundary side.
    """
    if not entrances or not exits:
        return False

    coords, edge_to_side = segment_boundary(layout)

    # Union of sides touched by any exit
    exit_sides: set[int] = set()
    for poly in exits:
        exit_sides |= touched_sides(coords, edge_to_side, poly)

    # Any entrance touching one of those sides means a shared side
    return any(
        touched_sides(coords, edge_to_side, poly) & exit_sides
        for poly in entrances
    )
