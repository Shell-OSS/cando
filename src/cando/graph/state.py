# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""LangGraph state definition for the CANDO generation and refinement
pipeline.

Shared state TypedDict passed between all graph nodes. The pipeline has
two phases:

1. Generation - generate_zero_node (parses constraints internally via
   GeneratorZero; produces the first set of candidates: [initial_layout])
2. Refinement - fan-out / reduce strategy: snapshot → fan_out → N branch
   sub-graphs (each running verify→build_history→plan→generate for
   branch_steps steps) → reduce. reduce selects the winning branch,
   merges state, and either loops or exits.
"""

from typing import Annotated

from typing_extensions import TypedDict

from cando.agents.generators.generator.models import ActionSequence
from cando.agents.planners.models import PlannerResult
from cando.agents.verifiers.models import VerifierResult
from cando.experiments.ablations import AblationConfig


def _branches_reducer(existing: list, update: list | None) -> list:
    """Reducer for completed_branch_states.

    Returning `None` from reduce_node resets the accumulator to `[]`
    for the next round.
    """
    if update is None:
        return []
    return existing + update


class CategoryDelta(TypedDict):
    """Score change for a single verification category in one iteration."""

    score_before: float
    """Category score at the start of this iteration."""

    score_after: float
    """Category score at the end of this iteration (winning candidate)."""

    delta: float
    """score_after - score_before. Negative means regression."""

    note: str
    """LLM-generated explanation of what changed, produced by
    build_history_node by comparing the two consecutive VerifierResult
    objects for this category. e.g. 'elements 11 and 13 are now clustered'
    or 'distance between elements 5 and 6 grew slightly'."""


class IterationSummary(TypedDict):
    """Compact, LLM-readable record of one refinement iteration.

    This is what gets formatted into a history string and injected into
    the Planner and Generator system prompts. Keep it dense but informative.
    """

    iteration: int
    """0-based iteration index."""

    score_before: float
    """Compliance score of the best candidate from the *previous* iteration
    (prev_verification.score). Represents the baseline before any of this
    iteration's new candidates are evaluated. 0.0 on the first iteration
    (when prev_verification is None)."""

    score_after: float
    """Overall compliance score of the winning candidate."""

    score_delta: float
    """score_after - score_before."""

    is_regression: bool
    """True if this iteration's score_after < score_before (local dip).
    Distinct from should_restore: a single regression does not trigger
    restore; only patience consecutive non-improvements do."""

    restored_from_iteration: int | None
    """If a restore happened this iteration, the 0-based index of the
    iteration whose layout was restored. None otherwise."""

    planner_actions: dict[str, list[str]]
    """High-level actions suggested by the Planner, keyed by category.
    Only the action-level summaries (not steps) to keep this compact.
    Example: {"assembly": ["cluster elements 11 and 13 near element 9"],
              "hard_constraints": ["move element 3 away from element 7"]}"""

    winning_sequence: list[str]
    """String representations of the actions in the winning generator
    sequence (the candidate that achieved score_after).
    Example: ["move(element_id=3, dx=-2.5, dy=0.0)",
              "rotate(element_id=5, degrees=90)"]"""

    category_deltas: dict[str, CategoryDelta]
    """Per-category score deltas. Keys are the five category names:
    hard_constraints, assembly, proximity, orientation, flow."""

    # Experiment tracking

    selected_strategies: list[str]
    """Keys of the StrategyConfig objects sampled by generate_node this
    iteration, in the same order as candidates / action_sequences.
    e.g. ["conservative", "aggressive", "surgical"]. Empty list on the
    initial (generate_zero) iteration."""

    winning_strategy: str | None
    """StrategyConfig.key of the candidate that won verification this
    iteration. Derived from selected_strategies[winning_candidate_idx].
    None on the first iteration (generate_zero produces the only candidate
    and the concept of a "selected strategy" does not apply)."""

    candidate_scores: list[float | None]
    """Verification scores for every candidate, aligned positionally with
    the candidates list. Candidates that were skipped by verify_node because
    their action sequence was empty carry None. Useful for computing
    per-iteration score variance and analysing which strategies produced
    competitive candidates."""

    grounding_counts: dict[str, dict[str, int]]
    """How many planner actions in each category were grounded by each
    information source (visual / worker / history / layout).
    Outer key: category name (hard_constraints, assembly, proximity,
    orientation, flow). Inner key: ActionGrounding literal. Example::

        {
            "assembly": {
                "visual": 0, "worker": 2,
                "history": 1, "layout": 0,
            },
            "hard_constraints": {
                "visual": 1, "worker": 0,
                "history": 0, "layout": 2,
            },
            ...
        }

    All five categories are always present; counts default to 0.
    Empty outer dict ({}) on the first iteration (no plan yet)."""


class IterationRecord(TypedDict):
    """Full record for one refinement iteration stored in CandoState.history.

    Combines the compact LLM-readable summary with the raw layout snapshot
    needed for programmatic restore. Only summary is ever serialised into
    the agent context string; layout_snapshot stays in graph memory.
    """

    summary: IterationSummary
    """Compact, LLM-readable record injected into Planner / Generator
    context. Build with IterationSummary fields."""

    layout_snapshot: dict
    """Deep copy of current_layout at the *end* of this iteration (after
    select_best_node has chosen the winning candidate). Stored for every
    iteration — not just improvements — so any past state can be restored
    by index: state["history"][i]["layout_snapshot"]."""


class CandoState(TypedDict):
    """Full shared state passed between every node in the CANDO pipeline.

    Sections
    --------
    Inputs
        Raw user-facing inputs set by the caller in graph.invoke().
        Never overwritten by any node.

    generate_zero_node outputs
        Structured artifacts produced by GeneratorZero: the parsed
        constraints dict (authoritative — no separate parse step), the
        initial polygon layout, and the internal refinement conversation
        for audit. Set once and reused by all downstream nodes.

    Refinement config
        Hyperparameters that control the refinement loop behaviour.
        Never overwritten by any node.

    Working state
        Mutable values that evolve iteration by iteration.

    Per-node outputs
        Overwritten on every iteration by the node that produces them.

    Best-result tracking
        Global best layout and score across *all* iterations.

    Audit trail
        Append-only log; one record per completed iteration.
    """

    # Inputs

    raw_requirements: dict
    """Raw user requirements as provided to the API.
    Structured dict input in the dataset-style schema.
    Never modified after graph entry."""

    # generate_zero_node outputs

    constraints: dict
    """Structured constraints dict parsed by GeneratorZero from
    raw_requirements. Schema: {"elements": [{"class": ..., "polygon"|
    "width"/"height": ...}, ...]}.
    Set once by generate_zero_node; passed to Verifier, Planner, and
    Generator downstream."""

    initial_layout: dict | None
    """Initial layout produced by GeneratorZero (polygon format).
    Set once by generate_zero_node; never overwritten.
    Preserved for before/after comparison in the final response."""

    generator_zero_history: list | None
    """Refinement conversation history from GeneratorZero's internal bbox
    refinement loop. Stored for debugging / audit; not consumed by any
    downstream node."""

    # Refinement config

    weights: dict[str, float] | None
    """Optional per-category weights for the Verifier and Planner:
    hard_constraints, assembly, proximity, orientation, flow.
    Missing keys default to 1.0 inside each agent."""

    max_iterations: int
    """Hard upper bound on refinement iterations before the graph exits
    regardless of compliance."""

    n_branches: int
    """Number of parallel branches spawned per fan-out round. Each round
    the current best layout is cloned N times and refined independently."""

    branch_steps: int
    """Number of refinement steps (B) each branch runs per round.
    Each round advances the global iteration counter by exactly B."""

    local_patience: int
    """Consecutive non-improving steps within a branch before restore_node
    fires. Set to -1 to disable within-branch restores entirely.
    Injected by fan_out_node into each branch state as `patience`."""

    patience: int
    """Within-branch patience counter (injected by fan_out_node from
    local_patience). Never set directly in the global initial state — it
    is always overridden per-branch by fan_out_node. Required here so that
    verify_node and restore_node can read it inside branch sub-graphs."""

    plateau_patience: int
    """Number of consecutive *rounds* (not individual steps) without a
    global best-score improvement before the graph exits with
    exit_reason="plateau". Measured by iterations_since_best_changed which
    is incremented once per reduce (never reset by restores).
    Typical value: 6."""

    n_candidates: int
    """Number of diverse action sequences Generator produces per iteration.
    Higher values increase coverage at the cost of parallel LLM calls."""

    history_window: int
    """Maximum number of recent IterationSummary entries to include when
    building the history context string injected into Planner and Generator
    prompts. Uses a sliding window over the last N entries so that deltas
    always refer to the immediately preceding iteration. Default: 5."""

    layout_image: str | None
    """Optional base64-encoded PNG of the *current* layout, regenerated
    externally and injected before each iteration if visual context is
    desired by the Generator and Planner."""

    log_dir: str | None
    """Root directory for live log output. Set once in graph.invoke() by
    the caller and inherited by all branch states. When set, nodes write
    per-iteration summaries and layout PNGs to disk during the run.
    None disables all live logging."""

    # Working state

    current_layout: dict | None
    """The winning candidate layout chosen by verify_node. On the first
    iteration it equals initial_layout. Updated each iteration to the
    best-scoring candidate; reset to best_layout by restore_node when
    patience is exhausted."""

    iteration: int
    """0-based refinement iteration counter. Incremented by verify_node
    at the start of each evaluation round."""

    iterations_without_improvement: int
    """Number of consecutive iterations where verify_node did not find a
    candidate that beats best_score. Reset to 0 whenever best_score
    improves; incremented otherwise. When this reaches patience,
    verify_node sets should_restore=True and the graph routes to
    restore_node before continuing."""

    iterations_since_best_changed: int
    """Total number of *rounds* since best_score last improved. Unlike
    iterations_without_improvement this counter is NEVER reset by
    restore_node, so repeated patience-triggered restores cannot defer
    the plateau exit indefinitely. Incremented once per reduce call (not
    per individual step). When this reaches plateau_patience, reduce_node
    sets exit_reason="plateau" and the graph terminates."""

    # Fan-out / reduce working state

    pre_fanout_layout: dict | None
    """Deep copy of best_layout captured by snapshot_node just before each
    fan-out round. Used by reduce_node to restore the layout when no branch
    improves on the pre-fanout score."""

    pre_fanout_score: float
    """best_score captured by snapshot_node just before fan-out. Used by
    reduce_node to determine whether any branch improved."""

    branch_states: list[dict]
    """Unused at runtime — branch state dicts are constructed inside
    fan_out_node and dispatched directly via Send. Retained in CandoState
    for introspection and testing. Always [] in production runs."""

    completed_branch_states: Annotated[list[dict], _branches_reducer]
    """Accumulated final states of all N branches. Each
    branch_refinement_node invocation appends its result via the LangGraph
    _branches_reducer. Read by reduce_node once all N branches have
    finished. reduce_node resets this field by returning None,
    which _branches_reducer treats as a "clear" signal."""

    current_round: int
    """1-based fan-out round counter. Incremented by snapshot_node at the
    start of each round and inherited by every branch sub-graph. Used to
    organise live log output: round_001/, round_002/, ..."""

    branch_id: int | None
    """0-based index assigned to each branch by fan_out_node (0..N-1).
    None in the global top-level state; set per-branch so that
    build_history_node can write logs to the correct branch sub-folder."""

    # Per-node outputs

    verification: VerifierResult | None
    """VerifierResult of the best-scoring candidate, set by verify_node.
    verify_node calls Verifier.evaluate(candidates) which evaluates all
    candidates in parallel and returns the winner — so this always holds
    the best result of the current iteration.
    Consumed by build_history_node (as score_after), plan_node, and the
    conditional edge (is_compliant check).
    None until verify_node has executed at least once."""

    prev_verification: VerifierResult | None
    """The verification result from the *previous* iteration, saved by
    verify_node just before overwriting verification with the new result.
    Used by build_history_node as score_before for CategoryDelta computation.
    None during the very first iteration."""

    plan: PlannerResult | None
    """Structured result from plan_node (Planner.plan).
    None until plan_node has executed at least once."""

    candidates: list[dict]
    """List of layout dicts to be evaluated by verify_node.
    On the first iteration: [initial_layout] (single element).
    On subsequent iterations: the N refined layouts produced by generate_node.
    Replaced each iteration by generate_node; consumed by verify_node."""

    action_sequences: list[ActionSequence]
    """The N ActionSequence objects that produced candidates, in the same
    order as candidates. Stored for logging / debugging; replaced each
    iteration."""

    winning_candidate_idx: int
    """0-based index into candidates (and action_sequences) of the
    best-scoring candidate selected by verify_node. Set each iteration by
    verify_node alongside verification. Used by build_history_node to
    recover the winning ActionSequence and derive the winning strategy."""

    selected_strategies: list[str]
    """StrategyConfig keys for the N candidates produced by generate_node,
    in the same order as candidates and action_sequences.
    Set by generate_node; consumed by build_history_node to populate
    IterationSummary.selected_strategies and winning_strategy.
    Empty list on the first iteration (generate_zero does not use
    strategies)."""

    candidate_scores: list[float | None]
    """Verification scores for every candidate, positionally aligned with
    candidates. Non-evaluated candidates (action sequence was empty) carry
    None. Set by verify_node; consumed by build_history_node."""

    # Best-result tracking

    best_layout: dict | None
    """The highest-scoring layout seen across *all* iterations so far.
    Updated by verify_node whenever the current best candidate beats the
    overall best_score."""

    best_score: float
    """Compliance score in [0.0, 1.0] of best_layout. Initialised to 0.0."""

    best_layout_iteration: int
    """The 0-based iteration index at which best_layout was last updated by
    verify_node. Initialised to 0. Used by restore_node to populate
    restored_from_iteration in the CandoState so that build_history_node
    can record which checkpoint was restored."""

    restored_from_iteration: int | None
    """Set by restore_node to best_layout_iteration at the moment it
    resets current_layout. Cleared to None by verify_node at the start of
    each new evaluation so that build_history_node only sees a non-None
    value in the iteration that immediately follows a restore."""

    is_compliant: bool
    """True if best_layout satisfies all rules (VerifierResult.compliant).
    Checked by the conditional edge after build_history_node to decide
    whether to exit to END or continue the loop."""

    should_restore: bool
    """True when iterations_without_improvement >= patience.
    Set by verify_node. Causes the conditional edge to route to restore_node,
    which resets current_layout to best_layout and resets
    iterations_without_improvement to 0 before continuing to plan_node.
    Allows the graph to tolerate short score regressions (local minima)
    before intervening."""

    # Diagnostics

    exit_reason: str | None
    """Terminal reason set by verify_node when the graph is about to exit.
    One of:
      - "compliant"       — best_result.compliant is True
      - "max_iterations"  — iteration count reached max_iterations
      - "plateau"         — iterations_since_best_changed >= plateau_patience
    None while the loop is still running. Consumed by route_after_verify
    to decide whether to send the graph to END."""

    # Audit trail

    history: list[IterationRecord]
    """One IterationRecord appended per completed refinement iteration by
    select_best_node.

    Each record has two parts:
    - summary        : compact IterationSummary formatted into the Planner
                       and Generator context strings. Carries score deltas,
                       planner actions, winning sequence, and regression
                       flags.
    - layout_snapshot: deep copy of current_layout at the end of that
                       iteration. Used by select_best_node / graph logic to
                       restore to any past state by index; never sent to LLMs.

    To build the history string injected into agent prompts, iterate over
    [r["summary"] for r in history] and format each IterationSummary."""

    # Experiment / ablation configuration

    ablation: AblationConfig
    """Resolved ablation configuration for this run (see
    cando.experiments.ablations.resolve_ablation). Never overwritten by
    any node after graph entry; every node reads this to decide whether to
    enable/disable a component instead of interpreting a mode string
    itself."""

    inference_profile: str
    """Inference profile for this run: "no_thinking" (thinking disabled for
    every role), "fast" (thinking + response tokens capped per role), or
    "max" (unchanged model behaviour, uncapped -- formerly named
    "current"; see cando.model_factory). Set once by the caller before
    graph construction (agents are instantiated at import time, so the
    profile must be set in the environment via CANDO_INFERENCE_PROFILE
    before importing the graph modules)."""

    run_seed: int | None
    """Optional per-trial seed used for repository-side randomness (e.g.
    strategy selection/ordering). Does not make remote model sampling
    deterministic. None disables repository-side seeding."""
