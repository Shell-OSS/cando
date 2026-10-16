# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Evaluation module for the CANDO benchmark.

Public API for layout scoring, exposing LayoutScorer and the scoring
result models.
"""

from .models import (
    AggregateStats,
    BatchScoringResult,
    CSScores,
    CSStats,
    HCMScores,
    HCMStats,
    MetricStats,
    REScores,
    REStats,
    ScoringResult,
)
from .scorer import LayoutScorer, ScoringWeights

__all__ = [
    "LayoutScorer",
    "ScoringWeights",
    "ScoringResult",
    "BatchScoringResult",
    "AggregateStats",
    "CSScores",
    "CSStats",
    "HCMScores",
    "HCMStats",
    "REScores",
    "REStats",
    "MetricStats",
]
