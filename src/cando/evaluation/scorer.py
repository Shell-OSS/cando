# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Layout scoring for the CANDO benchmark.

LayoutScorer computes the full benchmark score for a (generated,
ground-truth) layout pair, covering constraint satisfaction (CS) and
routing efficiency (RE).
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
import os
import signal
import statistics
import warnings
from dataclasses import dataclass
from typing import Any

from tqdm import tqdm

from cando.agents.verifiers.assembly.agent import AssemblyEvaluator
from cando.agents.verifiers.flow.agent import FlowEvaluator
from cando.agents.verifiers.hard_constraints.agent import (
    HardConstraintsEvaluator,
)
from cando.agents.verifiers.orientation.agent import OrientationEvaluator
from cando.agents.verifiers.proximity.agent import ProximityEvaluator
from cando.errors import ScoringError

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
from .utils import (
    _build_scenarios,
    _collect_scenario_stats,
    _ScenarioKey,
    _ScenarioStats,
    compute_path_clearance,
)

_logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")


@dataclass
class ScoringWeights:
    """Formula weights for :class:`LayoutScorer`.

    All alpha parameters correspond to the notation in the CANDO paper.

    Attributes:
        alpha_g: Weight of hc_geo (geometric) in HCM. Default 0.2.
        alpha_f: Weight of hc_flow (flow) in HCM. Default 0.8.
        alpha_p: Weight of PR (proximity) in SCM. Default 0.6.
        alpha_c: Weight of CO (compositionality) in SCM. Default 0.3.
        alpha_o: Weight of OR (orientation) in SCM. Default 0.1.
        alpha: Penalty factor applied to SCM when HCM < 1.0
            (``CS = alpha * HCM * SCM``). Default 0.5.
        alpha_pl: Weight of PL (path length) in RE. Default 1/3.
        alpha_mn: Weight of MN (maneuverability) in RE. Default 1/3.
        alpha_pc: Weight of PC (path clearance) in RE. Default 1/3.
        alpha_cs: Weight of CS in the final score. Default 0.5.
        alpha_re: Weight of RE in the final score. Default 0.5.
    """

    alpha_g: float = 0.2
    alpha_f: float = 0.8
    alpha_p: float = 0.6
    alpha_c: float = 0.3
    alpha_o: float = 0.1
    alpha: float = 0.5
    alpha_pl: float = 1 / 3
    alpha_mn: float = 1 / 3
    alpha_pc: float = 1 / 3
    alpha_cs: float = 0.5
    alpha_re: float = 0.5


# Used when no scenarios are applicable for a layout pair
_NO_SCENARIO_RE = REScores(pl=0.5, mn=0.5, pc=0.5, score=0.5)

# Process-pool worker state (one LayoutScorer per worker process)
_worker_scorer: LayoutScorer | None = None


def _worker_init(weights: ScoringWeights | None) -> None:
    """Initializer for each worker process in the scoring pool.

    Ignores SIGINT so that Ctrl+C is handled exclusively by the main
    process, which is responsible for shutting down the pool cleanly.
    Creates a single :class:`LayoutScorer` with the given *weights* that
    is reused for every task dispatched to this worker, avoiding
    repeated evaluator instantiation.

    Args:
        weights: Scoring weights forwarded to :class:`LayoutScorer`.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    global _worker_scorer
    _worker_scorer = LayoutScorer(weights)


def _score_one(
    args: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> ScoringResult:
    """Module-level entry point called by process-pool workers.

    Args:
        args: ``(generated, ground_truth, constraints)`` triple.

    Returns:
        :class:`~cando.evaluation.models.ScoringResult`.

    Raises:
        ScoringError: If the worker's :class:`LayoutScorer` was never
            initialized (``_worker_init`` did not run).
    """
    gen, gt, constraints = args
    if _worker_scorer is None:
        raise ScoringError("Worker scorer not initialized")
    return _worker_scorer.score(gen, gt, constraints)


class LayoutScorer:
    """Computes benchmark scores for generated CANDO layouts.

    Scores are decomposed into two components:

    * **CS** (Constraint Satisfaction): evaluates hard constraints (element
      counts, boundary containment, non-overlap, routing feasibility) and
      three categories of soft constraints (compositionality, proximity,
      orientation).

    * **RE** (Routing Efficiency): compares routing quality—path length,
      maneuverability, and path clearance—between the generated layout and
      the withheld ground-truth layout across all applicable scenarios T.

    The final score is `CS x RE`.

    Verifier workers are instantiated once and reused across all scoring
    calls, making :class:`LayoutScorer` efficient for batch evaluation.
    """

    def __init__(self, weights: ScoringWeights | None = None) -> None:
        self._weights = weights or ScoringWeights()
        self._hc_evaluator = HardConstraintsEvaluator()
        self._flow_evaluator = FlowEvaluator()
        self._assembly_evaluator = AssemblyEvaluator()
        self._orientation_evaluator = OrientationEvaluator()
        self._proximity_evaluator = ProximityEvaluator()

    # Public API
    def score(
        self,
        generated: dict[str, Any],
        ground_truth: dict[str, Any],
        constraints: dict[str, Any],
    ) -> ScoringResult:
        """Scores a single (generated, ground-truth) layout pair.

        Args:
            generated: Generated layout dict with an `'elements'` list.
            ground_truth: Ground-truth layout dict with an `'elements'`
                list.  Used only for routing-efficiency comparison; its
                constraint satisfaction is not evaluated.
            constraints: Input constraints dict (passed to the hard-
                constraints evaluator for element-count validation).

        Returns:
            :class:`~cando.evaluation.models.ScoringResult` with all
            sub-metrics populated.

        Raises:
            ScoringError: If a verifier worker fails while computing CS
                or RE.
        """
        cs = self._compute_cs(generated, constraints)
        re = self._compute_re(generated, ground_truth)
        w = self._weights
        final = round(w.alpha_cs * cs.score + w.alpha_re * re.score, 3)
        return ScoringResult(cs=cs, re=re, score=final)

    def score_batch(
        self,
        instances: list[
            tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
        ],
        workers: int | None = None,
    ) -> BatchScoringResult:
        """Scores multiple layout instances in parallel using processes.

        Each worker process holds a single :class:`LayoutScorer` instance
        that is reused for every task, avoiding repeated evaluator
        instantiation. Instances that fail are skipped and logged; the
        returned ``results`` list contains only the successful
        :class:`~cando.evaluation.models.ScoringResult` objects and
        aggregate statistics are computed over those.

        Args:
            instances: List of ``(generated, ground_truth, constraints)``
                tuples, one per layout pair to evaluate.
            workers: Number of worker processes to use. Defaults to the
                number of CPU cores available on the machine, or 1 if
                that cannot be determined.

        Returns:
            :class:`~cando.evaluation.models.BatchScoringResult` containing
            per-instance :class:`~cando.evaluation.models.ScoringResult`
            objects and batch-level
            :class:`~cando.evaluation.models.AggregateStats` (mean, median,
            std for every scalar metric).

        Raises:
            ScoringError: If every instance fails and no results are
                available to aggregate.
        """
        ordered: list[ScoringResult | None] = [None] * len(instances)
        n_workers = workers or (os.cpu_count() or 1)

        with concurrent.futures.ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_worker_init,
            initargs=(self._weights,),
        ) as executor:
            futures = {
                executor.submit(_score_one, (gen, gt, c)): idx
                for idx, (gen, gt, c) in enumerate(instances)
            }
            try:
                with tqdm(
                    total=len(instances), desc="Scoring layouts"
                ) as pbar:
                    for future in concurrent.futures.as_completed(futures):
                        idx = futures[future]
                        try:
                            ordered[idx] = future.result()
                        except Exception as err:
                            _logger.warning(
                                "Scoring failed for instance %d: %s",
                                idx,
                                err,
                                exc_info=True,
                            )
                        pbar.update(1)
            except KeyboardInterrupt:
                # Stop workers immediately instead of waiting for
                # in-flight work, then re-raise for a clean interrupt.
                executor.shutdown(wait=False, cancel_futures=True)
                raise

        results: list[ScoringResult] = [r for r in ordered if r is not None]
        aggregate = _compute_aggregate_stats(results)
        return BatchScoringResult(results=results, aggregate=aggregate)

    # Constraint-satisfaction scoring (CS)
    def _compute_cs(
        self,
        layout: dict[str, Any],
        constraints: dict[str, Any],
    ) -> CSScores:
        """Computes the full constraint-satisfaction score for *layout*.

        Runs :class:`AssemblyEvaluator` first to obtain element groups, then
        evaluates the remaining workers in parallel.

        Formula::

            SCM = alpha_p * PR + alpha_c * CO + alpha_o * OR
            CS  = SCM                    (when HCM == 1.0)
            CS  = alpha * HCM * SCM      (otherwise)

        where HCM = 0 for any hard-constraints violation, otherwise
        ``alpha_g * hc_geo + alpha_f * hc_flow``.

        Args:
            layout: Layout dict to evaluate.
            constraints: Constraints dict used for element-count validation.

        Returns:
            :class:`~cando.evaluation.models.CSScores`.

        Raises:
            ScoringError: If any of the four verifier workers raises.
        """
        # Assembly must run first to provide groups to orientation/proximity.
        assembly_result = self._assembly_evaluator.evaluate(layout)
        groups: dict[str, Any] = assembly_result.raw_data.get("groups", {})

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_hc = executor.submit(
                self._hc_evaluator.evaluate, layout, constraints
            )
            future_flow = executor.submit(
                self._flow_evaluator.evaluate, layout
            )
            future_or = executor.submit(
                self._orientation_evaluator.evaluate, layout, groups
            )
            future_pr = executor.submit(
                self._proximity_evaluator.evaluate, layout, groups
            )

            try:
                hc_result = future_hc.result()
            except Exception as err:
                raise ScoringError(
                    "HardConstraintsEvaluator failed in _compute_cs"
                ) from err
            try:
                flow_result = future_flow.result()
            except Exception as err:
                raise ScoringError(
                    "FlowEvaluator failed in _compute_cs"
                ) from err
            try:
                or_result = future_or.result()
            except Exception as err:
                raise ScoringError(
                    "OrientationEvaluator failed in _compute_cs"
                ) from err
            try:
                pr_result = future_pr.result()
            except Exception as err:
                raise ScoringError(
                    "ProximityEvaluator failed in _compute_cs"
                ) from err

        # Force HCM to zero for qualitative failures (missing elements or
        # dimension violations)
        hc_raw = hc_result.raw_data
        hard_zero = not hc_raw.get("count_validation", {}).get(
            "matches_constraints", True
        ) or bool(hc_raw.get("dimension_violations"))

        w = self._weights
        hc_geo = hc_result.score
        hc_flow = flow_result.score
        hcm_score = (
            0.0
            if hard_zero
            else round(w.alpha_g * hc_geo + w.alpha_f * hc_flow, 3)
        )

        hcm = HCMScores(hc_geo=hc_geo, hc_flow=hc_flow, score=hcm_score)

        co = assembly_result.score
        pr = pr_result.score
        or_ = or_result.score
        scm_score = round(
            w.alpha_p * pr + w.alpha_c * co + w.alpha_o * or_, 3
        )
        cs_score = (
            scm_score
            if math.isclose(hcm_score, 1.0)
            else round(w.alpha * hcm_score * scm_score, 3)
        )

        return CSScores(
            hcm=hcm,
            co=co,
            pr=pr,
            **{"or": or_},
            score=cs_score,
        )

    # Routing-efficiency scoring (RE)
    def _compute_re(
        self,
        generated: dict[str, Any],
        ground_truth: dict[str, Any],
    ) -> REScores:
        """Computes routing-efficiency scores by comparing both layouts.

        Defines the scenario set T from the ground-truth elements, runs
        pathfinding on both layouts for each scenario, then computes PL,
        MN, and PC.

        If T is empty (no routing scenarios apply), parity scores (0.5) are
        returned. GT scenarios with invalid or non-finite paths are excluded
        from the comparison; if all GT scenarios are invalid, parity scores
        are returned. If any remaining scenario is infeasible in the
        generated layout, all sub-scores are set to 0.

        Formula::

            RE = alpha_pl * PL + alpha_mn * MN + alpha_pc * PC

        Args:
            generated: Generated layout dict.
            ground_truth: Ground-truth layout dict.

        Returns:
            :class:`~cando.evaluation.models.REScores`.

        Raises:
            ScoringError: If scenario-stats collection fails for either
                layout.
        """
        gt_elements: list[dict[str, Any]] = ground_truth.get("elements", [])
        t_dict = _build_scenarios(gt_elements)

        scenario_keys: list[_ScenarioKey] = [
            (vt, tuple(seq)) for vt, seqs in t_dict.items() for seq in seqs
        ]

        if not scenario_keys:
            return _NO_SCENARIO_RE

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_gen = executor.submit(
                _collect_scenario_stats, generated, t_dict
            )
            future_gt = executor.submit(
                _collect_scenario_stats, ground_truth, t_dict
            )
            try:
                gen_stats = future_gen.result()
            except Exception as err:
                raise ScoringError(
                    "_collect_scenario_stats failed for generated layout"
                ) from err
            try:
                gt_stats = future_gt.result()
            except Exception as err:
                raise ScoringError(
                    "_collect_scenario_stats failed for ground-truth layout"
                ) from err

        gen_elements: list[dict[str, Any]] = generated.get("elements", [])

        # Filter to scenarios where the GT path is valid and finite — a GT
        # routing failure is a data-quality issue, not a generation failure,
        # so we simply exclude those scenarios from comparison.
        valid_keys: list[_ScenarioKey] = []
        for key in scenario_keys:
            gt_stat = gt_stats.get(key)
            if (
                gt_stat is None
                or not gt_stat.is_valid
                or not math.isfinite(gt_stat.path_length)
            ):
                continue
            valid_keys.append(key)

        if not valid_keys:
            return _NO_SCENARIO_RE

        for key in valid_keys:
            gen_s = gen_stats.get(key)
            if gen_s is None or not gen_s.is_valid:
                return REScores(pl=0.0, mn=0.0, pc=0.0, score=0.0)

        lengths_gt: list[float] = []
        lengths_gen: list[float] = []
        turns_gt: list[float] = []
        turns_gen: list[float] = []
        clearances_gt: list[float] = []
        clearances_gen: list[float] = []

        for key in valid_keys:
            gt_s: _ScenarioStats = gt_stats[key]
            gen_s = gen_stats[key]

            lengths_gt.append(gt_s.path_length)
            lengths_gen.append(gen_s.path_length)
            turns_gt.append(float(gt_s.num_sharp_turns))
            turns_gen.append(float(gen_s.num_sharp_turns))

            clearances_gt.append(
                compute_path_clearance(gt_s.path_coordinates, gt_elements)
            )
            clearances_gen.append(
                compute_path_clearance(gen_s.path_coordinates, gen_elements)
            )

        n = len(valid_keys)
        l_gt = sum(lengths_gt) / n
        l_gen = sum(lengths_gen) / n
        n_gt = sum(turns_gt) / n
        n_gen = sum(turns_gen) / n
        c_gt = sum(clearances_gt) / n
        c_gen = sum(clearances_gen) / n

        w = self._weights
        pl = _safe_ratio(l_gt, l_gt + l_gen, default=0.5)
        mn = _maneuverability_score(n_gt, n_gen)
        pc = _safe_ratio(c_gen, c_gt + c_gen, default=0.5)
        re = round(w.alpha_pl * pl + w.alpha_mn * mn + w.alpha_pc * pc, 3)

        return REScores(
            pl=round(pl, 3),
            mn=round(mn, 3),
            pc=round(pc, 3),
            score=re,
        )


# Batch aggregate statistics helper
def _compute_aggregate_stats(
    results: list[ScoringResult],
) -> AggregateStats:
    """Computes mean, median, and std for every scalar metric in a batch.

    Args:
        results: Non-empty list of per-instance scoring results.

    Returns:
        :class:`~cando.evaluation.models.AggregateStats` populated from
        *results*.

    Raises:
        ScoringError: If *results* is empty (every instance failed).
    """
    if not results:
        raise ScoringError(
            "Cannot compute aggregate statistics: "
            "no successful scoring results"
        )

    def _stat(values: list[float]) -> MetricStats:
        mean = round(statistics.mean(values), 4)
        median = round(statistics.median(values), 4)
        std = round(statistics.stdev(values) if len(values) > 1 else 0.0, 4)
        return MetricStats(mean=mean, median=median, std=std)

    return AggregateStats(
        score=_stat([r.score for r in results]),
        cs=CSStats(
            score=_stat([r.cs.score for r in results]),
            hcm=HCMStats(
                score=_stat([r.cs.hcm.score for r in results]),
                hc_geo=_stat([r.cs.hcm.hc_geo for r in results]),
                hc_flow=_stat([r.cs.hcm.hc_flow for r in results]),
            ),
            co=_stat([r.cs.co for r in results]),
            pr=_stat([r.cs.pr for r in results]),
            **{"or": _stat([r.cs.or_ for r in results])},
        ),
        re=REStats(
            score=_stat([r.re.score for r in results]),
            pl=_stat([r.re.pl for r in results]),
            mn=_stat([r.re.mn for r in results]),
            pc=_stat([r.re.pc for r in results]),
        ),
    )


# Private arithmetic helpers


def _safe_ratio(
    numerator: float, denominator: float, default: float
) -> float:
    """Returns `numerator / denominator`, or *default* when denominator ≈ 0.

    Args:
        numerator: Ratio numerator.
        denominator: Ratio denominator.
        default: Value returned when the denominator is effectively zero.

    Returns:
        Bounded ratio in [0, 1].
    """
    if denominator < 1e-9:  # guards against floating-point underflow
        return default
    return float(numerator / denominator)


def _maneuverability_score(n_gt: float, n_gen: float) -> float:
    """Computes the maneuverability score with division-by-zero smoothing.

    Formula::

        MN = n_gt / (n_gt + n_gen)

    When both averages are zero (perfectly straight paths in both layouts),
    the smoothed variant is used instead::

        MN = (n_gt + 1) / (n_gt + n_gen + 2)

    Args:
        n_gt: Average number of sharp turns across scenarios in the
            ground-truth layout.
        n_gen: Average number of sharp turns across scenarios in the
            generated layout.

    Returns:
        Maneuverability score in [0, 1].
    """
    if n_gt + n_gen < 1e-9:  # guards against floating-point underflow
        return float((n_gt + 1) / (n_gt + n_gen + 2))
    return float(n_gt / (n_gt + n_gen))
