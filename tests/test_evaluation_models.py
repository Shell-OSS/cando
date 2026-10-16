# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for src/cando/evaluation/models.py Pydantic schemas."""

import pytest
from pydantic import ValidationError

from cando.evaluation.models import (
    CSScores,
    HCMScores,
    REScores,
    ScoringResult,
)


def test_hcm_scores_requires_all_fields() -> None:
    hcm = HCMScores(hc_geo=1.0, hc_flow=0.8, score=0.9)
    assert hcm.hc_geo == pytest.approx(1.0)
    assert hcm.hc_flow == pytest.approx(0.8)
    assert hcm.score == pytest.approx(0.9)


def test_hcm_scores_missing_field_raises() -> None:
    with pytest.raises(ValidationError):
        HCMScores(hc_geo=1.0, hc_flow=0.8)


def test_cs_scores_accepts_or_alias() -> None:
    hcm = HCMScores(hc_geo=1.0, hc_flow=1.0, score=1.0)
    cs = CSScores(hcm=hcm, co=0.5, pr=0.5, score=0.75, **{"or": 0.6})

    assert cs.or_ == pytest.approx(0.6)


def test_cs_scores_accepts_field_name_via_populate_by_name() -> None:
    hcm = HCMScores(hc_geo=1.0, hc_flow=1.0, score=1.0)
    cs = CSScores(hcm=hcm, co=0.5, pr=0.5, or_=0.6, score=0.75)

    assert cs.or_ == pytest.approx(0.6)


def test_cs_scores_serializes_back_to_or_alias() -> None:
    hcm = HCMScores(hc_geo=1.0, hc_flow=1.0, score=1.0)
    cs = CSScores(hcm=hcm, co=0.5, pr=0.5, or_=0.6, score=0.75)

    dumped = cs.model_dump(by_alias=True)
    assert dumped["or"] == pytest.approx(0.6)
    assert "or_" not in dumped


def test_re_scores_aggregate_formula_fields() -> None:
    re_scores = REScores(pl=0.5, mn=0.5, pc=0.5, score=0.5)
    assert re_scores.score == pytest.approx(
        (re_scores.pl + re_scores.mn + re_scores.pc) / 3
    )


def test_scoring_result_nests_cs_and_re() -> None:
    hcm = HCMScores(hc_geo=1.0, hc_flow=1.0, score=1.0)
    cs = CSScores(hcm=hcm, co=1.0, pr=1.0, or_=1.0, score=1.0)
    re_scores = REScores(pl=1.0, mn=1.0, pc=1.0, score=1.0)

    result = ScoringResult(cs=cs, re=re_scores, score=1.0)

    assert result.cs.hcm.hc_geo == pytest.approx(1.0)
    assert result.re.pl == pytest.approx(1.0)
    assert result.score == pytest.approx(1.0)
