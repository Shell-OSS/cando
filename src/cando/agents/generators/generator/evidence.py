# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Verifier-evidence builder for the direct (no_planner) Generator path.

Converts a VerifierResult into a plain evidence dict that the direct
Generator can consume in place of a PlannerResult.
"""

from cando.agents.verifiers.models import VerifierResult

CATEGORIES = (
    "hard_constraints",
    "assembly",
    "proximity",
    "orientation",
    "flow",
)


def build_verifier_evidence(
    verification: VerifierResult,
    weights: dict | None,
) -> dict:
    """Builds a plain evidence dict from a VerifierResult.

    Args:
        verification (VerifierResult): The verification result to convert.
        weights (dict | None): Optional per-category weights. Missing keys
            default to 1.0.

    Returns:
        dict: Evidence dict with an "overall" entry plus one entry per
            verification category (score, compliant, feedback, raw_data,
            weight).
    """
    weights = weights or {}

    evidence = {
        "overall": {
            "score": verification.score,
            "valid": verification.valid,
            "compliant": verification.compliant,
        }
    }

    for category in CATEGORIES:
        worker = getattr(
            verification.feedback,
            category,
        )

        evidence[category] = {
            "score": worker.score,
            "compliant": worker.compliant,
            "feedback": worker.feedback,
            "raw_data": verification.raw_data.get(
                category,
                {},
            ),
            "weight": weights.get(category, 1.0),
        }

    return evidence
