#!/bin/bash
#SBATCH --job-name=cando_ablation
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --gres=gpu:8
#SBATCH --output=logs/logfiles/%x_%j.out
#SBATCH --error=logs/logfiles/%x_%j.err
#
# run_ablation.slurm
# -------------------------------------------------------------------------
# Generic CANDO component-ablation runner (scripts/run_eval.py
# --mode). Starts a multi-GPU vLLM server cluster + load balancer for a
# Qwen model, then runs scripts/run_eval.py with the selected --mode against
# it. Every parameter below can be overridden without editing this file by
# exporting the corresponding environment variable, e.g.:
#
#   sbatch --export=ALL,MODE=no_planner,SEED=20260726 slurm/run_ablation.slurm
#
# Normally you should submit this via slurm/submit_ablation.sh instead,
# which sets job name / output paths per mode and lets you submit several
# modes in one call.
# -------------------------------------------------------------------------

# Note: --cpus-per-task above is a starting point for typical H100 nodes;
# adjust it (via `sbatch --cpus-per-task=N` in submit_ablation.sh, which
# overrides the directive above) to match actual node capacity and cluster
# policy. The workload is parallel at the application level (process pool,
# branch concurrency, Planner/Generator/Verifier threads, candidate
# batching, GeoPandas/Matplotlib rendering), so it benefits from more than
# a handful of cores even though each individual math library is pinned to
# a single thread below.

set -euo pipefail

# ---------------------------------------------------------------------------
# Ablation / evaluation configuration (all overridable via --export)
# ---------------------------------------------------------------------------
MODE="${MODE:-full}"
INFERENCE_PROFILE="${INFERENCE_PROFILE:-max}"
SEED="${SEED:-20260726}"
N_REPS="${N_REPS:-1}"
NUM_WORKERS="${NUM_WORKERS:-100}"
MAX_ITERATIONS="${MAX_ITERATIONS:-20}"
N_BRANCHES="${N_BRANCHES:-4}"
BRANCH_STEPS="${BRANCH_STEPS:-10}"
N_CANDIDATES="${N_CANDIDATES:-6}"
LOCAL_PATIENCE="${LOCAL_PATIENCE:--1}"
PLATEAU_PATIENCE="${PLATEAU_PATIENCE:-21}"
HISTORY_WINDOW="${HISTORY_WINDOW:-6}"

# WEIGHTS_JSON is passed base64-encoded (WEIGHTS_B64) because sbatch's
# --export splits on commas, which a raw JSON weights object would corrupt.
WEIGHTS_B64="${WEIGHTS_B64:-}"
WEIGHTS_JSON=""
if [[ -n "${WEIGHTS_B64}" ]]; then
    WEIGHTS_JSON="$(printf '%s' "${WEIGHTS_B64}" | base64 -d)"
fi

# --require-pregen is only passed when PREGEN_LAYOUTS_ROOT is set. Set
# REQUIRE_PREGEN=0 to disable the flag while still resolving a root.
REQUIRE_PREGEN="${REQUIRE_PREGEN:-1}"
PREGEN_LAYOUTS_ROOT="${PREGEN_LAYOUTS_ROOT:-}"

# ---------------------------------------------------------------------------
# Paths (set PROJECT_ROOT and input paths for your deployment)
# ---------------------------------------------------------------------------
PROJECT_ROOT="${PROJECT_ROOT:-/path/to/deployment/root}"
PROJECT_DIR="${PROJECT_DIR:-${PROJECT_ROOT}/cando}"
JOB_NAME="${JOB_NAME:-CANDO_ABLATION_${MODE}}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_DIR}/logs/${JOB_NAME}_${SLURM_JOB_ID}}"
INPUT_CONSTRAINTS_JSONL="${INPUT_CONSTRAINTS_JSONL:-}"

MODEL_DIR="${MODEL_DIR:-/path/to/models/Qwen3.6-35B-A3B-FP8}"
MODEL_NAME="${MODEL_NAME:-Qwen}"
MODEL_CONFIG_JSON="${MODEL_DIR}/config.json"

# VLLM_VENV_DIR is intentionally separate from EVAL_VENV_DIR: vLLM's own
# pinned dependencies commonly conflict with this repo's (see
# pyproject.toml), so it is expected to live in its own environment.
# LB_SCRIPT (a small HTTP load balancer that round-robins requests across
# the vLLM servers started below) is an external dependency not included in
# this repository -- point it at your own implementation.
VLLM_VENV_DIR="${VLLM_VENV_DIR:-${PROJECT_ROOT}/vllm-serving/.venv}"
EVAL_VENV_DIR="${EVAL_VENV_DIR:-${PROJECT_DIR}/.venv}"
LB_SCRIPT="${LB_SCRIPT:-${PROJECT_ROOT}/load_balancer.py}"
VLLM_BIN="${VLLM_VENV_DIR}/bin/vllm"
LB_PYTHON_BIN="${VLLM_VENV_DIR}/bin/python"
EVAL_PYTHON_BIN="${EVAL_VENV_DIR}/bin/python"

BASE_VLLM_PORT="${BASE_VLLM_PORT:-18000}"
LB_PORT="${LB_PORT:-19000}"

# Physical GPU indices to exclude (space-separated), e.g. SKIP_GPUS="2 5"
# for broken/reserved hardware. The remaining indices are used, in order,
# to assign GPUs to vLLM servers below.
read -ra SKIP_GPUS <<< "${SKIP_GPUS:-}"
AVAILABLE_GPUS=()
for (( _g=0; _g<${SLURM_GPUS_ON_NODE:-8}; _g++ )); do
    _skip=0
    for _s in ${SKIP_GPUS[@]+"${SKIP_GPUS[@]}"}; do
        if [[ "${_g}" == "${_s}" ]]; then
            _skip=1
            break
        fi
    done
    if [[ "${_skip}" -eq 0 ]]; then
        AVAILABLE_GPUS+=("${_g}")
    fi
done
TOTAL_GPUS="${#AVAILABLE_GPUS[@]}"

VLLM_TENSOR_PARALLEL_SIZE="${VLLM_TENSOR_PARALLEL_SIZE:-1}"
VLLM_PIPELINE_PARALLEL_SIZE="${VLLM_PIPELINE_PARALLEL_SIZE:-1}"
GPUS_PER_SERVER=$((VLLM_TENSOR_PARALLEL_SIZE * VLLM_PIPELINE_PARALLEL_SIZE))
NUM_SERVERS="${NUM_SERVERS:-$((TOTAL_GPUS / GPUS_PER_SERVER))}"
VLLM_INITIAL_WAIT_SECONDS="${VLLM_INITIAL_WAIT_SECONDS:-30}"
VLLM_READY_PROBE_INTERVAL_SECONDS="${VLLM_READY_PROBE_INTERVAL_SECONDS:-5}"
VLLM_STARTUP_TIMEOUT_SECONDS="${VLLM_STARTUP_TIMEOUT_SECONDS:-900}"
VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.95}"
VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-128}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-65536}"
VLLM_PORTS=()
VLLM_PIDS=()
LB_PID=""


cleanup() {
    local exit_code=$?

    if [[ -n "${LB_PID}" ]] && kill -0 "${LB_PID}" 2>/dev/null; then
        echo "Stopping load balancer (pid=${LB_PID})"
        kill "${LB_PID}" 2>/dev/null || true
        wait "${LB_PID}" 2>/dev/null || true
    fi

    for pid in "${VLLM_PIDS[@]:-}"; do
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            echo "Stopping vLLM server (pid=${pid})"
            kill "${pid}" 2>/dev/null || true
            wait "${pid}" 2>/dev/null || true
        fi
    done

    echo "Job finished with exit code ${exit_code} at $(date)"
}


wait_for_http_ready() {
    local name="$1"
    local url="$2"
    local pid="$3"
    local attempt=1
    local start_time_seconds=${SECONDS}

    while true; do
        if curl --silent --fail --noproxy '*' "${url}" >/dev/null; then
            echo "${name} is ready after ${attempt} probe(s)."
            return 0
        fi

        if [[ -n "${pid}" ]] && ! kill -0 "${pid}" 2>/dev/null; then
            echo "${name} exited before becoming ready." >&2
            return 1
        fi

        local elapsed_seconds=$((SECONDS - start_time_seconds))
        if (( elapsed_seconds >= VLLM_STARTUP_TIMEOUT_SECONDS )); then
            echo "Timed out waiting for ${name}." >&2
            return 1
        fi

        echo "Waiting for ${name} to become ready (probe ${attempt}, elapsed ${elapsed_seconds}s/${VLLM_STARTUP_TIMEOUT_SECONDS}s)..."
        sleep "${VLLM_READY_PROBE_INTERVAL_SECONDS}"
        attempt=$((attempt + 1))
    done
}


trap cleanup EXIT INT TERM

if [[ -n "${EXTRA_ENV_SCRIPT:-}" ]]; then
    source "${EXTRA_ENV_SCRIPT}"
fi

if (( GPUS_PER_SERVER < 1 )); then
    echo "GPUS_PER_SERVER must be at least 1." >&2
    exit 1
fi

if (( TOTAL_GPUS % GPUS_PER_SERVER != 0 )); then
    echo "TOTAL_GPUS=${TOTAL_GPUS} must be divisible by GPUS_PER_SERVER=${GPUS_PER_SERVER}." >&2
    exit 1
fi

if (( NUM_SERVERS < 1 )); then
    echo "NUM_SERVERS must be at least 1." >&2
    exit 1
fi

if (( NUM_SERVERS * GPUS_PER_SERVER > TOTAL_GPUS )); then
    echo "NUM_SERVERS=${NUM_SERVERS} with GPUS_PER_SERVER=${GPUS_PER_SERVER} exceeds TOTAL_GPUS=${TOTAL_GPUS}." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Preflight checks -- fail fast on a bad path/input instead of only
# discovering the problem after every vLLM server has already started.
# ---------------------------------------------------------------------------
[[ -d "${PROJECT_DIR}" ]] || {
    echo "Missing project directory: ${PROJECT_DIR}" >&2
    exit 1
}

[[ -f "${INPUT_CONSTRAINTS_JSONL}" ]] || {
    echo "Set INPUT_CONSTRAINTS_JSONL to an existing constraints JSONL: ${INPUT_CONSTRAINTS_JSONL}" >&2
    exit 1
}
if [[ ( -z "${PREGEN_LAYOUTS_ROOT}" || "${REQUIRE_PREGEN}" != "1" ) && ! -f "${DATASET_JSONL_PATH:-}" ]]; then
    echo "Set DATASET_JSONL_PATH to training constraints when generating initial layouts." >&2
    exit 1
fi

[[ -f "${MODEL_CONFIG_JSON}" ]] || {
    echo "Missing model config: ${MODEL_CONFIG_JSON}" >&2
    exit 1
}

[[ -x "${VLLM_BIN}" ]] || {
    echo "Missing vLLM executable: ${VLLM_BIN}" >&2
    exit 1
}

[[ -x "${EVAL_PYTHON_BIN}" ]] || {
    echo "Missing evaluation Python: ${EVAL_PYTHON_BIN}" >&2
    exit 1
}

[[ -x "${LB_PYTHON_BIN}" ]] || {
    echo "Missing load balancer Python: ${LB_PYTHON_BIN}" >&2
    exit 1
}

[[ -f "${LB_SCRIPT}" ]] || {
    echo "Missing load balancer: ${LB_SCRIPT}" >&2
    exit 1
}

if [[ -n "${PREGEN_LAYOUTS_ROOT}" ]]; then
    [[ -d "${PREGEN_LAYOUTS_ROOT}/full" ]] || {
        echo "Missing pregen cache: ${PREGEN_LAYOUTS_ROOT}/full" >&2
        exit 1
    }
    [[ -d "${PREGEN_LAYOUTS_ROOT}/no_icl" ]] || {
        echo "Missing pregen cache: ${PREGEN_LAYOUTS_ROOT}/no_icl" >&2
        exit 1
    }
fi

MODEL_NATIVE_MAX_CONTEXT="$(${LB_PYTHON_BIN} - "${MODEL_CONFIG_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    config = json.load(handle)

print(config.get("text_config", {}).get("max_position_embeddings", "unknown"))
PY
)"
MODEL_DIR_SIZE="$(du -sh "${MODEL_DIR}" | awk '{print $1}')"

echo "----------------- Job Info ------------------"
/usr/bin/scontrol show job -d "${SLURM_JOB_ID}"

echo "---------------- Environment ----------------"
cd "${PROJECT_DIR}"

mkdir -p "${OUTPUT_DIR}" "${PROJECT_DIR}/logs/logfiles"

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
export no_proxy="127.0.0.1,localhost"
export NO_PROXY="127.0.0.1,localhost"

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENAI_API_KEY="${OPENAI_API_KEY:-test}"
export OPENAI_API_BASE="http://127.0.0.1:${LB_PORT}/v1"
export DATASET_JSONL_PATH="${DATASET_JSONL_PATH:-}"
export FEATURE_VECTORS_PARQUET_PATH="${FEATURE_VECTORS_PARQUET_PATH:-${PROJECT_DIR}/data/feature_vectors/feature_vectors.parquet}"
export SCALER_CONFIG_PATH="${SCALER_CONFIG_PATH:-${PROJECT_DIR}/data/feature_vectors/scaler_config.json}"
if [[ -z "${PREGEN_LAYOUTS_ROOT}" || "${REQUIRE_PREGEN}" != "1" ]]; then
    [[ -f "${FEATURE_VECTORS_PARQUET_PATH}" ]] || { echo "Missing feature vectors: ${FEATURE_VECTORS_PARQUET_PATH}" >&2; exit 1; }
    [[ -f "${SCALER_CONFIG_PATH}" ]] || { echo "Missing scaler: ${SCALER_CONFIG_PATH}" >&2; exit 1; }
fi
# Points every ModelsConfig role (see src/cando/config.py) at the local
# vLLM cluster started below instead of the default Azure OpenAI model.
export CANDO_MODEL_ID="${CANDO_MODEL_ID:-openai:${MODEL_NAME}}"

echo "--------------- Sanity Checks ---------------"
echo "Project dir:          ${PROJECT_DIR}"
echo "Output dir:           ${OUTPUT_DIR}"
echo "Input constraints:    ${INPUT_CONSTRAINTS_JSONL}"
echo "Model dir:            ${MODEL_DIR}"
echo "Model size on disk:   ${MODEL_DIR_SIZE}"
echo "Native model max context: ${MODEL_NATIVE_MAX_CONTEXT}"
echo "Configured VLLM_MAX_MODEL_LEN=${VLLM_MAX_MODEL_LEN}"
echo "TOTAL_GPUS=${TOTAL_GPUS}"
echo "AVAILABLE_GPUS:       ${AVAILABLE_GPUS[*]}"
echo "SKIP_GPUS:            ${SKIP_GPUS[*]:-none}"
echo "GPUS_PER_SERVER=${GPUS_PER_SERVER}"
echo "Requested servers:    ${NUM_SERVERS}"
echo "CANDO_MODEL_ID:       ${CANDO_MODEL_ID}"
echo "-- Ablation mode --"
echo "MODE:                 ${MODE}"
echo "INFERENCE_PROFILE:    ${INFERENCE_PROFILE}"
echo "SEED:                 ${SEED}"
echo "N_REPS:               ${N_REPS}"
echo "NUM_WORKERS:          ${NUM_WORKERS}"
echo "MAX_ITERATIONS:       ${MAX_ITERATIONS}"
echo "N_BRANCHES:           ${N_BRANCHES}"
echo "BRANCH_STEPS:         ${BRANCH_STEPS}"
echo "N_CANDIDATES:         ${N_CANDIDATES}"
echo "LOCAL_PATIENCE:       ${LOCAL_PATIENCE}"
echo "PLATEAU_PATIENCE:     ${PLATEAU_PATIENCE}"
echo "HISTORY_WINDOW:       ${HISTORY_WINDOW}"
echo "PREGEN_LAYOUTS_ROOT:  ${PREGEN_LAYOUTS_ROOT:-<none>}"
echo "REQUIRE_PREGEN:       ${REQUIRE_PREGEN}"
echo "Host: $(hostname)"
echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-<unset>}"
nvidia-smi

echo -n "This run started on: "
date

for ((i=0; i<NUM_SERVERS; i++)); do
    port=$((BASE_VLLM_PORT + i))
    gpu_start=$((i * GPUS_PER_SERVER))
    # Slice AVAILABLE_GPUS to get the physical indices for this server (this
    # honours SKIP_GPUS instead of assuming a contiguous GPU range).
    _gpu_slice=( "${AVAILABLE_GPUS[@]:${gpu_start}:${GPUS_PER_SERVER}}" )
    gpu_list=$(IFS=,; echo "${_gpu_slice[*]}")
    log_file="${PROJECT_DIR}/logs/logfiles/vllm_${SLURM_JOB_ID}/server${i}.log"
    mkdir -p "$(dirname "${log_file}")"
    VLLM_PORTS+=("${port}")

    echo "------------- Starting vLLM API on GPUs ${gpu_list} / port ${port} -------------"

    vllm_cmd=(
        "${VLLM_BIN}" serve "${MODEL_DIR}"
        --port "${port}"
        --tensor-parallel-size "${VLLM_TENSOR_PARALLEL_SIZE}"
        --pipeline-parallel-size "${VLLM_PIPELINE_PARALLEL_SIZE}"
        --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION}"
        --max-num-seqs "${VLLM_MAX_NUM_SEQS}"
        --enable-prefix-caching
        --enable-chunked-prefill
        --chat-template-content-format openai
        --reasoning-parser qwen3
        --enable-auto-tool-choice
        --tool-call-parser qwen3_coder
        --served-model-name "${MODEL_NAME}"
    )
    if [[ "${VLLM_MAX_MODEL_LEN}" != "native" ]]; then
        vllm_cmd+=(--max-model-len "${VLLM_MAX_MODEL_LEN}")
    fi

    CUDA_VISIBLE_DEVICES="${gpu_list}" "${vllm_cmd[@]}" > "${log_file}" 2>&1 &
    VLLM_PIDS+=("$!")
done

echo "Initial vLLM wait: ${VLLM_INITIAL_WAIT_SECONDS}s"
sleep "${VLLM_INITIAL_WAIT_SECONDS}"

for ((i=0; i<NUM_SERVERS; i++)); do
    port="${VLLM_PORTS[$i]}"
    pid="${VLLM_PIDS[$i]}"
    log_file="${PROJECT_DIR}/logs/logfiles/vllm_${SLURM_JOB_ID}/server${i}.log"
    if ! wait_for_http_ready "vLLM:${port}" "http://127.0.0.1:${port}/health" "${pid}"; then
        echo "Last log lines from ${log_file}:" >&2
        tail -n 200 "${log_file}" >&2 || true
        exit 1
    fi
done

echo "------------- Starting load balancer -------------"
"${LB_PYTHON_BIN}" "${LB_SCRIPT}" \
    --host 0.0.0.0 \
    --port "${LB_PORT}" \
    --ports "${VLLM_PORTS[@]}" \
    > "${PROJECT_DIR}/logs/logfiles/load_balancer_${SLURM_JOB_ID}.log" 2>&1 &
LB_PID=$!

if ! wait_for_http_ready "load balancer" "http://127.0.0.1:${LB_PORT}/health" "${LB_PID}"; then
    tail -n 200 "${PROJECT_DIR}/logs/logfiles/load_balancer_${SLURM_JOB_ID}.log" >&2 || true
    exit 1
fi

echo "------------ Running Ablation Evaluation (mode=${MODE}) -------------"

EVAL_ARGS=(
    "${INPUT_CONSTRAINTS_JSONL}"
    --mode "${MODE}"
    --inference-profile "${INFERENCE_PROFILE}"
    --n-reps "${N_REPS}"
    --n-workers "${NUM_WORKERS}"
    --max-iterations "${MAX_ITERATIONS}"
    --n-branches "${N_BRANCHES}"
    --branch-steps "${BRANCH_STEPS}"
    --n-candidates "${N_CANDIDATES}"
    --local-patience "${LOCAL_PATIENCE}"
    --plateau-patience "${PLATEAU_PATIENCE}"
    --history-window "${HISTORY_WINDOW}"
    --output-dir "${OUTPUT_DIR}"
)

if [[ -n "${SEED}" ]]; then
    EVAL_ARGS+=(--seed "${SEED}")
fi

if [[ -n "${WEIGHTS_JSON}" ]]; then
    EVAL_ARGS+=(--weights "${WEIGHTS_JSON}")
fi

if [[ -n "${PREGEN_LAYOUTS_ROOT}" ]]; then
    EVAL_ARGS+=(--pregen-layouts-root "${PREGEN_LAYOUTS_ROOT}")
    if [[ "${REQUIRE_PREGEN}" == "1" ]]; then
        EVAL_ARGS+=(--require-pregen)
    fi
fi

"${EVAL_PYTHON_BIN}" -m scripts.run_eval "${EVAL_ARGS[@]}"

echo "-------------- Load Balancer Tail ----------------"
tail -n 50 "${PROJECT_DIR}/logs/logfiles/load_balancer_${SLURM_JOB_ID}.log" || true

for ((i=0; i<NUM_SERVERS; i++)); do
    log_file="${PROJECT_DIR}/logs/logfiles/vllm_${SLURM_JOB_ID}/server${i}.log"
    echo "-------------- vLLM Server ${i} Log Tail ----------------"
    tail -n 50 "${log_file}" || true
done

echo -n "This run completed on: "
date
