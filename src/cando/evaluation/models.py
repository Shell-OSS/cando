# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Data models for layout evaluation scoring.

Pydantic models for benchmark scoring results, covering
constraint-satisfaction (CS) and routing-efficiency (RE) dimensions, as
well as batch aggregate statistics.
"""

from pydantic import BaseModel, Field


class HCMScores(BaseModel):
    """Hard-constraint measure (HCM) sub-scores.

    HCM combines two independent checks, weighted by
    `ScoringWeights.alpha_g`/`alpha_f`:
    - `hc_geo`: geometric validity (elements present, inside boundary, no
      overlaps).
    - `hc_flow`: routing feasibility (fraction of predefined scenarios for
      which a valid path exists).

    When any required element is absent from the generated layout, both
    `hc_flow` and `hc_geo` are still recorded individually, but
    `score` is forced to `0.0` regardless.
    """

    hc_geo: float = Field(
        description=(
            "Geometric validity score in [0, 1]. Starts at 1 and is "
            "penalised proportionally to the area of out-of-boundary or "
            "overlapping regions. Set to 0 when required elements are "
            "missing."
        )
    )
    hc_flow: float = Field(
        description=(
            "Routing-feasibility score in [0, 1]: fraction of predefined "
            "scenarios for which a valid vehicle path exists."
        )
    )
    score: float = Field(
        description=(
            "Aggregate HCM score: 0.0 when required elements are missing, "
            "otherwise alpha_g * hc_geo + alpha_f * hc_flow (default "
            "weights 0.2 / 0.8)."
        )
    )


class CSScores(BaseModel):
    """Constraint-satisfaction scores.

    Captures how well the generated layout satisfies the full set of design
    rules, decomposed across four axes: hard-constraint measure (HCM),
    compositionality (CO), proximity (PR), and orientation (OR).

    Aggregate formula::

        SCM = alpha_p * PR + alpha_c * CO + alpha_o * OR
        CS  = SCM                    (when HCM == 1.0)
        CS  = alpha * HCM * SCM      (otherwise)

    A hard-constraint violation (HCM < 1.0) scales down the whole soft-
    constraint measure (SCM) rather than contributing a fixed share, so
    CS is 0 whenever HCM is 0.
    """

    hcm: HCMScores = Field(description="Hard-constraint measure sub-scores.")
    co: float = Field(
        description=(
            "Compositionality (CO) score in [0, 1]. Penalises fragmented "
            "element clusters and wrong spatial formation patterns."
        )
    )
    pr: float = Field(
        description=(
            "Proximity (PR) score in [0, 1]. Penalises violations of "
            "stacking/adjacency, clearance, and max-distance rules."
        )
    )
    or_: float = Field(
        alias="or",
        description=(
            "Orientation (OR) score in [0, 1]. Penalises within-group "
            "orientation inconsistencies; penalties scale with angular error."
        ),
    )
    score: float = Field(
        description=(
            "Aggregate CS score in [0, 1]: SCM when HCM == 1.0, otherwise "
            "alpha * HCM * SCM (default alpha 0.5)."
        )
    )

    model_config = {"populate_by_name": True}


class REScores(BaseModel):
    """Routing-efficiency comparison scores.

    Measures routing quality relative to the withheld ground-truth layout
    across three dimensions: path length (PL), maneuverability (MN), and
    path clearance (PC).

    All three scores use normalized ratios bounded in [0, 1] where 0.5
    indicates parity with the ground truth.  If any routing scenario in T
    is infeasible in the generated layout, all three scores are set to 0.

    Aggregate formula::

        RE = (PL + MN + PC) / 3

    A value of 0.5 on RE represents routing efficiency equal to the
    ground truth.
    """

    pl: float = Field(
        description=(
            "Path-length score in [0, 1]. "
            "Normalized ratio l̄_gt / (l̄_gt + l̄_gen); "
            "> 0.5 means shorter routes than ground truth."
        )
    )
    mn: float = Field(
        description=(
            "Maneuverability score in [0, 1]. "
            "Normalized ratio n̄_gt / (n̄_gt + n̄_gen); "
            "> 0.5 means fewer sharp turns than ground truth."
        )
    )
    pc: float = Field(
        description=(
            "Path-clearance score in [0, 1]. "
            "Normalized ratio c̄_gen / (c̄_gt + c̄_gen); "
            "> 0.5 means wider clearance than ground truth."
        )
    )
    score: float = Field(
        description=(
            "Aggregate RE score in [0, 1]: alpha_pl * PL + alpha_mn * MN "
            "+ alpha_pc * PC (default equal weights 1/3 each)."
        )
    )


class ScoringResult(BaseModel):
    """Full scoring result for a (generated, ground-truth) layout pair.

    The final score is a weighted combination of the two aggregate
    components::

        score = alpha_cs * CS + alpha_re * RE

    With the default equal weights (0.5 / 0.5) this is the mean of CS and
    RE — unlike a product, a layout can still score above 0 when routing
    is fully infeasible (RE = 0) as long as CS is high, and vice versa.
    """

    cs: CSScores = Field(description="Constraint-satisfaction scores.")
    re: REScores = Field(description="Routing-efficiency comparison scores.")
    score: float = Field(
        description=(
            "Final layout score in [0, 1]: alpha_cs * CS + alpha_re * RE."
        )
    )


# Batch aggregate statistics
class MetricStats(BaseModel):
    """Descriptive statistics for a single scalar metric across a batch."""

    mean: float = Field(description="Arithmetic mean across all instances.")
    median: float = Field(description="Median across all instances.")
    std: float = Field(
        description="Sample standard deviation across all instances (0.0 for "
        "a batch of size 1)."
    )


class HCMStats(BaseModel):
    """Aggregate statistics for hard-constraint measure sub-scores."""

    score: MetricStats
    hc_geo: MetricStats
    hc_flow: MetricStats


class CSStats(BaseModel):
    """Aggregate statistics for constraint-satisfaction scores."""

    score: MetricStats
    hcm: HCMStats
    co: MetricStats
    pr: MetricStats
    or_: MetricStats = Field(alias="or")

    model_config = {"populate_by_name": True}


class REStats(BaseModel):
    """Aggregate statistics for routing-efficiency scores."""

    score: MetricStats
    pl: MetricStats
    mn: MetricStats
    pc: MetricStats


class AggregateStats(BaseModel):
    """Batch-level descriptive statistics over all :class:`ScoringResult`
    items.

    The structure mirrors :class:`ScoringResult`: every scalar metric that
    appears in an individual result has a corresponding
    :class:`MetricStats` entry here.
    """

    score: MetricStats = Field(
        description="Statistics for the final CS x RE score."
    )
    cs: CSStats = Field(
        description="Statistics for constraint-satisfaction scores."
    )
    re: REStats = Field(
        description="Statistics for routing-efficiency scores."
    )


class BatchScoringResult(BaseModel):
    """Result of scoring a batch of layout instances.

    Combines per-instance scores with batch-level aggregate statistics.
    """

    results: list[ScoringResult] = Field(
        description=(
            "Per-instance scoring results in the same order as the input "
            "``instances`` list."
        )
    )
    aggregate: AggregateStats = Field(
        description=(
            "Batch-level descriptive statistics (mean, median, std) computed "
            "over all per-instance results."
        )
    )
