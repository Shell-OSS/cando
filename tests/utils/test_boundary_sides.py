# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

import pytest
from shapely.geometry import Polygon, box

from cando.utils.exemplar_retrievers.base import BaseExemplarRetriever
from cando.utils.exemplar_retrievers.boundary_sides import (
    segment_boundary,
    share_side,
)

RECT = Polygon([(0, 0), (100, 0), (100, 50), (0, 50)])
# Bottom side with a 2-unit jog that must not split it
JOGGED = Polygon(
    [(0, 0), (40, 0), (40, 2), (60, 2), (60, 0), (100, 0), (100, 50), (0, 50)]
)
# Bottom side interrupted by a deep notch that separates the two halves
NOTCHED = Polygon(
    [
        (0, 0),
        (30, 0),
        (30, 20),
        (70, 20),
        (70, 0),
        (100, 0),
        (100, 50),
        (0, 50),
    ]
)
# Right triangle whose hypotenuse is a slanted side
TRIANGLE = Polygon([(0, 0), (100, 0), (0, 60)])


def test_rectangle_has_four_sides():
    _, edge_to_side = segment_boundary(RECT)

    assert len(set(edge_to_side)) == 4


def test_small_jog_does_not_split_a_side():
    _, edge_to_side = segment_boundary(JOGGED)

    assert len(set(edge_to_side)) == 4


@pytest.mark.parametrize(
    ("layout", "entrance", "exit_", "expected"),
    [
        # Same bottom side
        (RECT, box(10, -1, 14, 1), box(60, -1, 64, 1), True),
        # Aligned in x but on opposite sides
        (RECT, box(10, -1, 14, 1), box(10, 49, 14, 51), False),
        # Corner entrance also touches the left side used by the exit
        (RECT, box(-1, -1, 3, 3), box(-1, 20, 1, 24), True),
        # A small jog keeps both elements on one side
        (JOGGED, box(10, -1, 14, 1), box(80, -1, 84, 1), True),
        # A deep notch separates the two halves of the bottom line
        (NOTCHED, box(10, -1, 14, 1), box(80, -1, 84, 1), False),
        # Exit off the boundary falls back to its nearest side
        (RECT, box(-1, 30, 1, 34), box(5, 20, 7, 24), True),
    ],
)
def test_share_side(layout, entrance, exit_, expected):
    assert share_side(layout, [entrance], [exit_]) is expected


def test_slanted_side_is_shared():
    # Both elements straddle the hypotenuse x/100 + y/60 = 1
    entrance = box(59, 23, 61, 25)
    exit_ = box(19, 47, 21, 49)

    assert share_side(TRIANGLE, [entrance], [exit_])


def test_share_side_without_entrances_or_exits():
    assert not share_side(RECT, [], [box(10, -1, 14, 1)])
    assert not share_side(RECT, [box(10, -1, 14, 1)], [])


def _constraints(entrance: Polygon, exit_: Polygon) -> dict:
    return {
        "elements": [
            {
                "class": "Layout Polygon",
                "polygon": list(RECT.exterior.coords),
            },
            {"class": "Entrance", "polygon": list(entrance.exterior.coords)},
            {"class": "Exit", "polygon": list(exit_.exterior.coords)},
        ]
    }


def test_compute_features_uses_boundary_sides():
    columns = ["entrance_exit_same_side"]
    top = box(10, 49, 14, 51)
    bottom = box(10, -1, 14, 1)

    aligned_opposite = BaseExemplarRetriever.compute_features(
        _constraints(bottom, top), columns
    )
    same_side = BaseExemplarRetriever.compute_features(
        _constraints(bottom, box(60, -1, 64, 1)), columns
    )

    assert aligned_opposite["entrance_exit_same_side"] == pytest.approx(0.0)
    assert same_side["entrance_exit_same_side"] == pytest.approx(1.0)
