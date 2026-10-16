# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Configurations for hard constraints verification."""

from cando.constants.classes import (
    INPUT_CLASSES,
)
from cando.constants.classes import (
    UNDERGROUND_CLASSES as SHARED_UNDERGROUND_CLASSES,
)

OUT_OF_BOUNDARIES_TOLERANCE = 0.1  # in meters
OVERLAP_TOLERANCE = 0.1  # in meters
DIMENSION_TOLERANCE = 0.3  # in meters — max allowed per-side deviation

ALLOWED_OVERLAPS = [
    (INPUT_CLASSES[1], INPUT_CLASSES[0]),  # Entrance may overlap with layout
    (INPUT_CLASSES[2], INPUT_CLASSES[0]),  # Exit may overlap with layout
]
UNDERGROUND_CLASSES = SHARED_UNDERGROUND_CLASSES
