#!/usr/bin/env bash
# Pornește serverul de embeddings local (llama.cpp, pe CPU) — fără Ollama.
#
# Cere două lucruri, amândouă locale:
#   * llama-server, din llama.cpp (deja instalat pe acest calculator)
#   * un model de embedding GGUF de pe Hugging Face, descărcat o singură dată
#
# Modelul implicit e bge-m3: 1024 dimensiuni, multilingv, deci merge și cu
# întrebări în română peste studii în engleză.
#
#   ./start-embeddings.sh              pornește pe 127.0.0.1:8199
#   ./start-embeddings.sh 8200         alt port
#   MODEL=/cale/catre.gguf ./start-embeddings.sh
set -euo pipefail

PORT="${1:-8199}"
HOST="127.0.0.1"
MODELS_DIR="${MODELS_DIR:-$HOME/models}"
MODEL="${MODEL:-$MODELS_DIR/bge-m3-Q8_0.gguf}"
REPO="${REPO:-ggml-org/bge-m3-Q8_0-GGUF}"
FILE="${FILE:-bge-m3-q8_0.gguf}"
THREADS="${THREADS:-6}"

# Binarul CPU explicit. NU ne bazăm pe "llama-server" din PATH: wrapper-ul din
# ~/.local/bin/llama-server exportă libs CUDA și rulează build-ul CUDA, deci
# serverul ajungea pe GPU deși scriptul se vrea pe CPU. Calea implicită folosește
# $USER, ca scriptul să nu înglobeze un nume de utilizator; suprascrie cu
# LLAMA_SERVER_BIN / LLAMA_CPU_LIB dacă instalarea e în alt loc.
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-/home/$USER/.local/opt/llama-cpu/bin/llama-server}"
LLAMA_CPU_LIB="${LLAMA_CPU_LIB:-/home/$USER/.local/opt/llama-cpu/lib}"

# Build-ul CPU are nevoie de propriile librării. Punem calea CPU PRIMA (are
# prioritate), păstrând restul LD_LIBRARY_PATH dacă era deja setat.
if [ -d "$LLAMA_CPU_LIB" ]; then
  export LD_LIBRARY_PATH="$LLAMA_CPU_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

if [ ! -x "$LLAMA_SERVER_BIN" ]; then
  echo "llama-server (build CPU) lipsește sau nu e executabil: $LLAMA_SERVER_BIN" >&2
  echo "Instalează build-ul CPU de llama.cpp sau setează LLAMA_SERVER_BIN=/cale/catre/llama-server." >&2
  exit 1
fi

mkdir -p "$MODELS_DIR"
if [ ! -f "$MODEL" ]; then
  echo "→ Descarc modelul de embedding ($FILE, ~600 MB) de pe Hugging Face…"
  curl -L --fail --progress-bar -o "$MODEL" \
    "https://huggingface.co/$REPO/resolve/main/$FILE"
fi

if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "Portul $PORT e deja ocupat — serverul pare pornit."
  curl -s -m 3 "http://$HOST:$PORT/health" && echo
  exit 0
fi

echo "→ Pornesc llama-server (CPU) pe $HOST:$PORT ($THREADS fire, -ngl 0)"
echo "  binar: $LLAMA_SERVER_BIN"
echo "  lib:   $LLAMA_CPU_LIB"
echo "  model: $MODEL"
exec "$LLAMA_SERVER_BIN" \
  --model "$MODEL" \
  --embeddings --pooling cls \
  --ctx-size 1024 \
  --threads "$THREADS" \
  --host "$HOST" --port "$PORT" \
  -ngl 0
