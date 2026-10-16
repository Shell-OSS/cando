#!/bin/bash
#SBATCH --job-name=cando_generate_zero
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --gres=gpu:8
#SBATCH --output=logs/logfiles/%x_%j.out
#SBATCH --error=logs/logfiles/%x_%j.err
#
# Fills incomplete GeneratorZero caches for use with run_ablation.slurm.
# Existing complete entries are preserved by scripts/run_generate_zero.py.
# Set PROJECT_ROOT, MODEL_DIR, INPUT_CONSTRAINTS_JSONL and DATASET_JSONL_PATH.
# The vLLM environment and LB_SCRIPT are external dependencies, as in
# run_ablation.slurm. Submit from the repository root (create logs/logfiles
# before submitting so Slurm can open the output files).
#
# Override any setting with sbatch --export, for example:
#   sbatch --export=ALL,N_LAYOUTS=3,CACHE_MODE=full slurm/run_generate_zero.slurm

set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/path/to/deployment/root}"
PROJECT_DIR="${PROJECT_DIR:-${PROJECT_ROOT}/cando}"
INPUT_CONSTRAINTS_JSONL="${INPUT_CONSTRAINTS_JSONL:-}"
CACHE_MODE="${CACHE_MODE:-full}"
CACHE_DIR="${CACHE_DIR:-${PROJECT_DIR}/data/evaluation/inputs/${CACHE_MODE}}"
N_LAYOUTS="${N_LAYOUTS:-3}"
NUM_WORKERS="${NUM_WORKERS:-100}"
MAX_GENERATE_ITERATIONS="${MAX_GENERATE_ITERATIONS:-10}"
INFERENCE_PROFILE="${INFERENCE_PROFILE:-max}"

MODEL_DIR="${MODEL_DIR:-/path/to/models/Qwen3.6-35B-A3B-FP8}"
MODEL_NAME="${MODEL_NAME:-Qwen}"
MODEL_CONFIG_JSON="${MODEL_DIR}/config.json"
VLLM_VENV_DIR="${VLLM_VENV_DIR:-${PROJECT_ROOT}/vllm-serving/.venv}"
EVAL_VENV_DIR="${EVAL_VENV_DIR:-${PROJECT_DIR}/.venv}"
LB_SCRIPT="${LB_SCRIPT:-${PROJECT_ROOT}/load_balancer.py}"
VLLM_BIN="${VLLM_VENV_DIR}/bin/vllm"
LB_PYTHON_BIN="${VLLM_VENV_DIR}/bin/python"
EVAL_PYTHON_BIN="${EVAL_VENV_DIR}/bin/python"

BASE_VLLM_PORT="${BASE_VLLM_PORT:-18000}"
LB_PORT="${LB_PORT:-19000}"
VLLM_INITIAL_WAIT_SECONDS="${VLLM_INITIAL_WAIT_SECONDS:-30}"
VLLM_READY_PROBE_INTERVAL_SECONDS="${VLLM_READY_PROBE_INTERVAL_SECONDS:-5}"
VLLM_STARTUP_TIMEOUT_SECONDS="${VLLM_STARTUP_TIMEOUT_SECONDS:-1200}"
VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.95}"
VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-128}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-65536}"
VLLM_TENSOR_PARALLEL_SIZE="${VLLM_TENSOR_PARALLEL_SIZE:-1}"
VLLM_PIPELINE_PARALLEL_SIZE="${VLLM_PIPELINE_PARALLEL_SIZE:-1}"

read -ra SKIP_GPUS <<< "${SKIP_GPUS:-}"
AVAILABLE_GPUS=()
for ((gpu=0; gpu<${SLURM_GPUS_ON_NODE:-8}; gpu++)); do
    skip_gpu=0
    for skipped_gpu in ${SKIP_GPUS[@]+"${SKIP_GPUS[@]}"}; do
        if [[ "${gpu}" == "${skipped_gpu}" ]]; then
            skip_gpu=1
            break
        fi
    done
    if [[ "${skip_gpu}" -eq 0 ]]; then
        AVAILABLE_GPUS+=("${gpu}")
    fi
done
TOTAL_GPUS="${#AVAILABLE_GPUS[@]}"
GPUS_PER_SERVER=$((VLLM_TENSOR_PARALLEL_SIZE * VLLM_PIPELINE_PARALLEL_SIZE))
if (( GPUS_PER_SERVER < 1 || TOTAL_GPUS < 1 || TOTAL_GPUS % GPUS_PER_SERVER != 0 )); then
    echo "GPU allocation must be nonempty and divisible by GPUS_PER_SERVER=${GPUS_PER_SERVER}." >&2
    exit 1
fi
NUM_SERVERS="${NUM_SERVERS:-$((TOTAL_GPUS / GPUS_PER_SERVER))}"
if (( NUM_SERVERS < 1 || NUM_SERVERS * GPUS_PER_SERVER > TOTAL_GPUS )); then
    echo "NUM_SERVERS=${NUM_SERVERS} exceeds available GPUs." >&2
    exit 1
fi

VLLM_PIDS=()
VLLM_PORTS=()
LB_PID=""

cleanup() {
    local exit_code=$?
    if [[ -n "${LB_PID}" ]] && kill -0 "${LB_PID}" 2>/dev/null; then
        kill "${LB_PID}" 2>/dev/null || true
        wait "${LB_PID}" 2>/dev/null || true
    fi
    for pid in "${VLLM_PIDS[@]:-}"; do
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" 2>/dev/null || true
            wait "${pid}" 2>/dev/null || true
        fi
    done
    echo "Job finished with exit code ${exit_code} at $(date)"
}

wait_for_http_ready() {
    local name="$1" url="$2" pid="$3"
    local attempt=1 start_time_seconds=${SECONDS}
    while true; do
        if curl --silent --fail --noproxy '*' "${url}" >/dev/null; then
            echo "${name} is ready after ${attempt} probe(s)."
            return 0
        fi
        if ! kill -0 "${pid}" 2>/dev/null; then
            echo "${name} exited before becoming ready." >&2
            return 1
        fi
        if (( SECONDS - start_time_seconds >= VLLM_STARTUP_TIMEOUT_SECONDS )); then
            echo "Timed out waiting for ${name}." >&2
            return 1
        fi
        echo "Waiting for ${name} (probe ${attempt})..."
        sleep "${VLLM_READY_PROBE_INTERVAL_SECONDS}"
        attempt=$((attempt + 1))
    done
}

trap cleanup EXIT INT TERM

if [[ -n "${EXTRA_ENV_SCRIPT:-}" ]]; then
    source "${EXTRA_ENV_SCRIPT}"
fi

[[ "${CACHE_MODE}" == "full" || "${CACHE_MODE}" == "no_icl" ]] || {
    echo "CACHE_MODE must be full or no_icl." >&2
    exit 1
}
[[ -d "${PROJECT_DIR}" ]] || { echo "Missing project directory: ${PROJECT_DIR}" >&2; exit 1; }
[[ -f "${INPUT_CONSTRAINTS_JSONL}" ]] || {
    echo "Missing input file: ${INPUT_CONSTRAINTS_JSONL}" >&2
    exit 1
}
[[ -f "${MODEL_CONFIG_JSON}" ]] || { echo "Missing model config: ${MODEL_CONFIG_JSON}" >&2; exit 1; }
[[ -f "${DATASET_JSONL_PATH:-}" ]] || { echo "Set DATASET_JSONL_PATH to the training constraints JSONL." >&2; exit 1; }
[[ -x "${VLLM_BIN}" ]] || { echo "Missing vLLM: ${VLLM_BIN}" >&2; exit 1; }
[[ -x "${EVAL_PYTHON_BIN}" ]] || { echo "Missing Python: ${EVAL_PYTHON_BIN}" >&2; exit 1; }
[[ -x "${LB_PYTHON_BIN}" ]] || { echo "Missing load balancer Python: ${LB_PYTHON_BIN}" >&2; exit 1; }
[[ -f "${LB_SCRIPT}" ]] || { echo "Missing load balancer: ${LB_SCRIPT}" >&2; exit 1; }
cd "${PROJECT_DIR}"
mkdir -p "${CACHE_DIR}" "${PROJECT_DIR}/logs/logfiles"

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
export DATASET_JSONL_PATH
export FEATURE_VECTORS_PARQUET_PATH="${FEATURE_VECTORS_PARQUET_PATH:-${PROJECT_DIR}/data/feature_vectors/feature_vectors.parquet}"
export SCALER_CONFIG_PATH="${SCALER_CONFIG_PATH:-${PROJECT_DIR}/data/feature_vectors/scaler_config.json}"
[[ -f "${FEATURE_VECTORS_PARQUET_PATH}" ]] || { echo "Missing feature vectors: ${FEATURE_VECTORS_PARQUET_PATH}" >&2; exit 1; }
[[ -f "${SCALER_CONFIG_PATH}" ]] || { echo "Missing scaler: ${SCALER_CONFIG_PATH}" >&2; exit 1; }
export CANDO_MODEL_ID="${CANDO_MODEL_ID:-openai:${MODEL_NAME}}"

echo "Cache directory: ${CACHE_DIR}"
echo "Cache mode: ${CACHE_MODE}; layouts per input: ${N_LAYOUTS}"
echo "Workers: ${NUM_WORKERS}; GeneratorZero iterations: ${MAX_GENERATE_ITERATIONS}"
echo "GPUs: ${AVAILABLE_GPUS[*]}"

for ((index=0; index<NUM_SERVERS; index++)); do
    port=$((BASE_VLLM_PORT + index))
    gpu_start=$((index * GPUS_PER_SERVER))
    gpu_slice=("${AVAILABLE_GPUS[@]:${gpu_start}:${GPUS_PER_SERVER}}")
    gpu="$(IFS=,; echo "${gpu_slice[*]}")"
    log_file="${PROJECT_DIR}/logs/logfiles/vllm_generate_zero_${SLURM_JOB_ID}_server${index}.log"
    VLLM_PORTS+=("${port}")
    echo "Starting vLLM on GPU ${gpu}, port ${port}"
    vllm_cmd=("${VLLM_BIN}" serve "${MODEL_DIR}" \
        --port "${port}" \
        --tensor-parallel-size "${VLLM_TENSOR_PARALLEL_SIZE}" \
        --pipeline-parallel-size "${VLLM_PIPELINE_PARALLEL_SIZE}" \
        --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION}" \
        --max-num-seqs "${VLLM_MAX_NUM_SEQS}" \
        --enable-prefix-caching \
        --enable-chunked-prefill \
        --chat-template-content-format openai \
        --reasoning-parser qwen3 \
        --enable-auto-tool-choice \
        --tool-call-parser qwen3_coder \
        --served-model-name "${MODEL_NAME}")
    if [[ "${VLLM_MAX_MODEL_LEN}" != "native" ]]; then
        vllm_cmd+=(--max-model-len "${VLLM_MAX_MODEL_LEN}")
    fi
    CUDA_VISIBLE_DEVICES="${gpu}" "${vllm_cmd[@]}" > "${log_file}" 2>&1 &
    VLLM_PIDS+=("$!")
done

sleep "${VLLM_INITIAL_WAIT_SECONDS}"
for index in "${!VLLM_PORTS[@]}"; do
    wait_for_http_ready "vLLM:${VLLM_PORTS[$index]}" \
        "http://127.0.0.1:${VLLM_PORTS[$index]}/health" "${VLLM_PIDS[$index]}"
done

"${LB_PYTHON_BIN}" "${LB_SCRIPT}" --host 0.0.0.0 --port "${LB_PORT}" \
    --ports "${VLLM_PORTS[@]}" \
    > "${PROJECT_DIR}/logs/logfiles/load_balancer_generate_zero_${SLURM_JOB_ID}.log" 2>&1 &
LB_PID=$!
wait_for_http_ready "load balancer" "http://127.0.0.1:${LB_PORT}/health" "${LB_PID}"

"${EVAL_PYTHON_BIN}" -m scripts.run_generate_zero "${INPUT_CONSTRAINTS_JSONL}" \
    --n-layouts "${N_LAYOUTS}" \
    --max-iterations "${MAX_GENERATE_ITERATIONS}" \
    --output-dir "${CACHE_DIR}" \
    --mode "${CACHE_MODE}" \
    --inference-profile "${INFERENCE_PROFILE}" \
    --n-workers "${NUM_WORKERS}"