# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Shared pytest fixtures for the CANDO test suite."""

import pytest


@pytest.fixture
def raw_requirements() -> dict:
    """A minimal, valid structured layout requirements dict."""
    return {
        "elements": [
            {
                "class": "Layout Polygon",
                "polygon": [[0, 0], [0, 50], [50, 50], [50, 0]],
            },
            {
                "class": "Entrance",
                "polygon": [[0, 20], [0, 25], [2, 25], [2, 20]],
            },
            {
                "class": "Exit",
                "polygon": [[48, 20], [48, 25], [50, 25], [50, 20]],
            },
            {"class": "Shop", "width": 10, "height": 8},
        ],
        "id": 0,
    }
