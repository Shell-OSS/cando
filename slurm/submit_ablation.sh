#!/usr/bin/env bash
#
# submit_ablation.sh
# -------------------------------------------------------------------------
# Convenience wrapper around slurm/run_ablation.slurm. Submits one SLURM job
# per requested ablation mode, forwarding evaluation parameters as exported
# environment variables (consumed by run_ablation.slurm).
#
# Usage:
#   ./slurm/submit_ablation.sh [options] MODE [MODE ...]
#   ./slurm/submit_ablation.sh [options] all
#
# Modes: full no_historian no_visual no_strategies no_planner
#        no_tool_verifier no_code no_icl
#
# Examples:
#   # Submit just the no_planner ablation with defaults
#   ./slurm/submit_ablation.sh no_planner
#
#   # Submit every mode, sharing one seed/pregen root across the whole matrix
#   ./slurm/submit_ablation.sh \
#     --project-dir /path/to/deployment/root/cando \
#     --input /path/to/test_constraints.jsonl \
#     --pregen-layouts-root /path/to/cando/data/evaluation/inputs \
#     --require-pregen \
#     --inference-profile fast \
#     --seed 20260726 \
#     --n-reps 1 --n-workers 2 --max-iterations 20 \
#     --n-branches 4 --branch-steps 10 --n-candidates 6 \
#     --local-patience -1 --plateau-patience 6 --history-window 6 \
#     all
#
#   # Preview the sbatch command without submitting
#   ./slurm/submit_ablation.sh --dry-run no_visual no_strategies
#
#   # Resume an interrupted/partially-failed run in its existing results dir
#   # (auto-resume triggers because summary.json already exists there; the
#   # summary's own failed entries must be flipped back to "pending" first,
#   # e.g. via scripts/reset_failed_runs.py)
#   ./slurm/submit_ablation.sh \
#     --output-dir logs/cando_ablation_no_tool_verifier_12345 \
#     no_tool_verifier
# -------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SLURM_TEMPLATE="${SCRIPT_DIR}/run_ablation.slurm"

ALL_MODES=(full no_historian no_visual no_strategies no_planner no_tool_verifier no_code no_icl)

# ---------------------------------------------------------------------------
# Defaults (mirrors run_ablation.slurm's own defaults; only values you pass
# here are forwarded, everything else falls back to the .slurm file).
# ---------------------------------------------------------------------------
PROJECT_ROOT=""
PROJECT_DIR=""
INPUT=""
OUTPUT_ROOT=""
OUTPUT_DIR=""
INFERENCE_PROFILE=""
SEED=""
N_REPS=""
N_WORKERS=""
MAX_ITERATIONS=""
N_BRANCHES=""
BRANCH_STEPS=""
N_CANDIDATES=""
LOCAL_PATIENCE=""
PLATEAU_PATIENCE=""
HISTORY_WINDOW=""
WEIGHTS_JSON=""
PREGEN_LAYOUTS_ROOT=""
REQUIRE_PREGEN="1"
MODEL_DIR=""
MODEL_NAME=""
CANDO_MODEL_ID=""
SKIP_GPUS=""

# SBATCH overrides (passed straight to `sbatch`, not exported into the job)
PARTITION=""
ACCOUNT=""
TIME=""
GRES=""
CPUS_PER_TASK=""
NODELIST=""
EXCLUDE=""

DRY_RUN=0
MODES=()

usage() {
    grep '^#' "${BASH_SOURCE[0]}" | sed -e 's/^#!\/usr\/bin\/env bash//' -e 's/^# \{0,1\}//'
    echo
    echo "Available modes: ${ALL_MODES[*]}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-root)        PROJECT_ROOT="$2"; shift 2 ;;
        --project-dir)         PROJECT_DIR="$2"; shift 2 ;;
        --input)                INPUT="$2"; shift 2 ;;
        --output-root)          OUTPUT_ROOT="$2"; shift 2 ;;
        --output-dir)            OUTPUT_DIR="$2"; shift 2 ;;
        --inference-profile)    INFERENCE_PROFILE="$2"; shift 2 ;;
        --seed)                 SEED="$2"; shift 2 ;;
        --n-reps)                N_REPS="$2"; shift 2 ;;
        --n-workers)             N_WORKERS="$2"; shift 2 ;;
        --max-iterations)        MAX_ITERATIONS="$2"; shift 2 ;;
        --n-branches)            N_BRANCHES="$2"; shift 2 ;;
        --branch-steps)          BRANCH_STEPS="$2"; shift 2 ;;
        --n-candidates)          N_CANDIDATES="$2"; shift 2 ;;
        --local-patience)        LOCAL_PATIENCE="$2"; shift 2 ;;
        --plateau-patience)      PLATEAU_PATIENCE="$2"; shift 2 ;;
        --history-window)        HISTORY_WINDOW="$2"; shift 2 ;;
        --weights)               WEIGHTS_JSON="$2"; shift 2 ;;
        --pregen-layouts-root)   PREGEN_LAYOUTS_ROOT="$2"; shift 2 ;;
        --require-pregen)        REQUIRE_PREGEN="1"; shift ;;
        --no-require-pregen)     REQUIRE_PREGEN="0"; shift ;;
        --model-dir)             MODEL_DIR="$2"; shift 2 ;;
        --model-name)            MODEL_NAME="$2"; shift 2 ;;
        --model-id)              CANDO_MODEL_ID="$2"; shift 2 ;;
        --skip-gpus)             SKIP_GPUS="$2"; shift 2 ;;
        --partition)             PARTITION="$2"; shift 2 ;;
        --account)               ACCOUNT="$2"; shift 2 ;;
        --time)                  TIME="$2"; shift 2 ;;
        --gres)                  GRES="$2"; shift 2 ;;
        --cpus-per-task)         CPUS_PER_TASK="$2"; shift 2 ;;
        --nodelist)              NODELIST="$2"; shift 2 ;;
        --exclude)               EXCLUDE="$2"; shift 2 ;;
        --dry-run)               DRY_RUN=1; shift ;;
        -h|--help)               usage; exit 0 ;;
        all)                     MODES=("${ALL_MODES[@]}"); shift ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
        *)
            MODES+=("$1")
            shift
            ;;
    esac
done

if [[ ${#MODES[@]} -eq 0 ]]; then
    echo "ERROR: no mode(s) given." >&2
    usage
    exit 1
fi

for mode in "${MODES[@]}"; do
    valid=0
    for m in "${ALL_MODES[@]}"; do
        [[ "${mode}" == "${m}" ]] && valid=1 && break
    done
    if [[ "${valid}" -ne 1 ]]; then
        echo "ERROR: unknown mode '${mode}'. Available modes: ${ALL_MODES[*]}" >&2
        exit 1
    fi
done

# Paired cached initial layouts are the safe default for final ablation runs:
# every mode (except no_icl) must start from the exact same initial layouts,
# so a missing cache entry should fail loudly instead of silently falling
# back to a fresh GeneratorZero layout for just one mode/rep. Use
# --no-require-pregen as a deliberate debug escape hatch.
if [[ "${REQUIRE_PREGEN}" == "1" && -z "${PREGEN_LAYOUTS_ROOT}" ]]; then
    echo "ERROR: --pregen-layouts-root is required for final ablation runs." >&2
    echo "       Pass --no-require-pregen to explicitly opt out (debug only)." >&2
    exit 1
fi

for mode in "${MODES[@]}"; do
    EXPORT_VARS="ALL,MODE=${mode}"
    JOB_NAME="cando_ablation_${mode}"

    add_export() {
        local name="$1" value="$2"
        if [[ -n "${value}" ]]; then
            EXPORT_VARS="${EXPORT_VARS},${name}=${value}"
        fi
    }

    add_export PROJECT_ROOT             "${PROJECT_ROOT}"
    add_export PROJECT_DIR              "${PROJECT_DIR}"
    add_export INPUT_CONSTRAINTS_JSONL  "${INPUT}"
    add_export INFERENCE_PROFILE        "${INFERENCE_PROFILE}"
    add_export SEED                     "${SEED}"
    add_export N_REPS                   "${N_REPS}"
    add_export NUM_WORKERS              "${N_WORKERS}"
    add_export MAX_ITERATIONS           "${MAX_ITERATIONS}"
    add_export N_BRANCHES               "${N_BRANCHES}"
    add_export BRANCH_STEPS             "${BRANCH_STEPS}"
    add_export N_CANDIDATES             "${N_CANDIDATES}"
    add_export LOCAL_PATIENCE           "${LOCAL_PATIENCE}"
    add_export PLATEAU_PATIENCE         "${PLATEAU_PATIENCE}"
    add_export HISTORY_WINDOW           "${HISTORY_WINDOW}"
    if [[ -n "${WEIGHTS_JSON}" ]]; then
        # base64-encode: sbatch --export splits on commas, which a raw JSON
        # weights object (e.g. {"hard_constraints":100,"assembly":8}) would
        # be corrupted by.
        WEIGHTS_B64="$(printf '%s' "${WEIGHTS_JSON}" | base64 -w 0)"
        add_export WEIGHTS_B64 "${WEIGHTS_B64}"
    fi
    add_export PREGEN_LAYOUTS_ROOT      "${PREGEN_LAYOUTS_ROOT}"
    add_export REQUIRE_PREGEN           "${REQUIRE_PREGEN}"
    add_export MODEL_DIR                "${MODEL_DIR}"
    add_export MODEL_NAME               "${MODEL_NAME}"
    add_export CANDO_MODEL_ID          "${CANDO_MODEL_ID}"
    add_export SKIP_GPUS                "${SKIP_GPUS}"

    # --output-dir takes precedence: it points at one exact results dir (used
    # to resume a specific existing job) rather than deriving a per-mode
    # subdirectory the way --output-root does. Only meaningful for a single
    # mode at a time.
    if [[ -n "${OUTPUT_DIR}" ]]; then
        if [[ ${#MODES[@]} -gt 1 ]]; then
            echo "ERROR: --output-dir can only be used with exactly one mode." >&2
            exit 1
        fi
        add_export OUTPUT_DIR "${OUTPUT_DIR}"
    elif [[ -n "${OUTPUT_ROOT}" ]]; then
        add_export OUTPUT_DIR "${OUTPUT_ROOT}/${mode}"
    fi

    SBATCH_CMD=(sbatch --job-name="${JOB_NAME}" --export="${EXPORT_VARS}")
    if [[ -n "${PARTITION}" ]]; then SBATCH_CMD+=(--partition="${PARTITION}"); fi
    if [[ -n "${ACCOUNT}" ]];   then SBATCH_CMD+=(--account="${ACCOUNT}"); fi
    if [[ -n "${TIME}" ]];      then SBATCH_CMD+=(--time="${TIME}"); fi
    if [[ -n "${GRES}" ]];      then SBATCH_CMD+=(--gres="${GRES}"); fi
    if [[ -n "${CPUS_PER_TASK}" ]]; then SBATCH_CMD+=(--cpus-per-task="${CPUS_PER_TASK}"); fi
    if [[ -n "${NODELIST}" ]];  then SBATCH_CMD+=(--nodelist="${NODELIST}"); fi
    if [[ -n "${EXCLUDE}" ]];   then SBATCH_CMD+=(--exclude="${EXCLUDE}"); fi
    SBATCH_CMD+=("${SLURM_TEMPLATE}")

    echo "----------------------------------------------------------------"
    echo "Mode: ${mode}"
    printf '%q ' "${SBATCH_CMD[@]}"
    echo

    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "(dry run — not submitted)"
    else
        "${SBATCH_CMD[@]}"
    fi
done
