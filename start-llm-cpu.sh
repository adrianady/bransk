#!/usr/bin/env bash
# Optional local text model for Bransk. This runner is intentionally CPU-only:
# use the dedicated CPU build, disable device offload, and keep the server local.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
MODEL="${LLM_CPU_MODEL:-$HOME/models/qwen25-coder/qwen2.5-coder-7b-instruct-q4_k_m-00001-of-00002.gguf}"
MODEL_ALIAS="${LLM_CPU_MODEL_ALIAS:-qwen2.5-coder-7b-instruct-q4_k_m}"
PORT="${LLM_CPU_PORT:-8198}"
THREADS="${LLM_CPU_THREADS:-2}"
CTX_SIZE="${LLM_CPU_CTX_SIZE:-4096}"
BATCH_SIZE="${LLM_CPU_BATCH_SIZE:-128}"
UBATCH_SIZE="${LLM_CPU_UBATCH_SIZE:-64}"
CACHE_RAM="${LLM_CPU_CACHE_RAM:-512}"
API_KEY="${LLM_CPU_API_KEY:-bransk-local-cpu-only}"

LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-$HOME/.local/opt/llama-cpu/bin/llama-server}"
LLAMA_CPU_LIB="${LLAMA_CPU_LIB:-$HOME/.local/opt/llama-cpu/lib}"

if [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
  echo "llama-server CPU build is missing or not executable: $LLAMA_SERVER_BIN" >&2
  exit 1
fi
if [[ ! -f "$MODEL" ]]; then
  echo "Local GGUF model not found: $MODEL (this runner never downloads models)." >&2
  exit 1
fi
if [[ "$MODEL" == *-00001-of-00002.gguf ]]; then
  SECOND_SHARD="${MODEL%-00001-of-00002.gguf}-00002-of-00002.gguf"
  if [[ ! -f "$SECOND_SHARD" ]]; then
    echo "Second GGUF shard not found: $SECOND_SHARD" >&2
    exit 1
  fi
  MODEL_BYTES=$(( $(stat -c '%s' "$MODEL") + $(stat -c '%s' "$SECOND_SHARD") ))
else
  MODEL_BYTES=$(stat -c '%s' "$MODEL")
fi

# Do not compete with other work or push the machine into swap just to load a
# model. Require the full model size plus 1 GiB of headroom before starting.
AVAILABLE_KIB="$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)"
MODEL_KIB=$(( (MODEL_BYTES + 1023) / 1024 ))
REQUIRED_KIB=$(( MODEL_KIB + 1024 * 1024 ))
if (( AVAILABLE_KIB < REQUIRED_KIB )); then
  echo "Not enough available RAM: need at least $(( REQUIRED_KIB / 1024 )) MiB; have $(( AVAILABLE_KIB / 1024 )) MiB." >&2
  echo "No model process was started. Close other work or lower the model size." >&2
  exit 1
fi
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; then
  echo "Port $PORT is already in use; refusing to attach to an unknown server." >&2
  exit 1
fi

# Prefer the CPU build's own libraries and hide accelerator devices as defense
# in depth. `--device none -ngl 0` below is the actual no-offload guarantee.
if [[ -d "$LLAMA_CPU_LIB" ]]; then
  export LD_LIBRARY_PATH="$LLAMA_CPU_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export CUDA_VISIBLE_DEVICES=""
export HIP_VISIBLE_DEVICES=""
export ROCR_VISIBLE_DEVICES=""
export GGML_VULKAN_VISIBLE_DEVICES=""

API_KEY_FILE="${LLM_CPU_API_KEY_FILE:-$BACKEND/data/cpu-llm-api-key}"
if [[ ! -s "$API_KEY_FILE" ]]; then
  mkdir -p "$(dirname "$API_KEY_FILE")"
  (umask 077; printf '%s\n' "$API_KEY" > "$API_KEY_FILE")
fi

echo "Starting local text model on 127.0.0.1:$PORT (CPU only; no downloads)."
echo "Model: $MODEL_ALIAS · threads=$THREADS · context=$CTX_SIZE · parallel=1 · RAM preflight passed"
exec nice -n 10 "$LLAMA_SERVER_BIN" \
  --model "$MODEL" \
  --alias "$MODEL_ALIAS" \
  --host 127.0.0.1 --port "$PORT" \
  --device none -ngl 0 --fit off \
  --ctx-size "$CTX_SIZE" --parallel 1 \
  --threads "$THREADS" --threads-batch "$THREADS" --threads-http 1 \
  --batch-size "$BATCH_SIZE" --ubatch-size "$UBATCH_SIZE" \
  --cache-ram "$CACHE_RAM" \
  --api-key-file "$API_KEY_FILE" \
  --offline --no-webui --no-warmup
