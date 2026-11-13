#!/usr/bin/env bash
# launch-chronokg.sh — Bransk desktop launcher
#
#   ./launch-chronokg.sh              pornește/deschide pe portul 8000, cu rețea
#   ./launch-chronokg.sh 8010         alt port
#   ./launch-chronokg.sh --local      doar acest calculator (fără expunere LAN)
#   ./launch-chronokg.sh --force      oprește orice instanță existentă pe port și repornește
#   ./launch-chronokg.sh --keep-open  nu închide fereastra la final
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8000
HOST_ARG="-n"
KEEP_OPEN=0
FORCE=0
EMBED_PORT="${LLAMA_EMBED_PORT:-8199}"

for arg in "$@"; do
  case "$arg" in
    --keep-open) KEEP_OPEN=1 ;;
    --local)     HOST_ARG="" ;;
    --force)     FORCE=1 ;;
    ''|*[!0-9]*) echo "Argument necunoscut: $arg" >&2; exit 2 ;;
    *)           PORT="$arg" ;;
  esac
done

URL="http://localhost:$PORT"
TOKEN_FILE="$ROOT/backend/data/auth-token"

# The token the running instance expects (empty when it is local-only).
access_token() {
  if [ -n "${CHRONOKG_AUTH_TOKEN:-}" ]; then
    printf '%s' "$CHRONOKG_AUTH_TOKEN"
  elif [ -s "$TOKEN_FILE" ]; then
    tr -d '[:space:]' < "$TOKEN_FILE"
  fi
}

pause_if_window() {
  if [ "$KEEP_OPEN" = "1" ] && [ -t 0 ]; then
    echo
    read -r -p "Apasă Enter ca să închizi fereastra… " _ || true
  fi
}

open_browser() {
  for opener in xdg-open gio sensible-browser x-www-browser; do
    if command -v "$opener" >/dev/null 2>&1; then
      ("$opener" "$1" >/dev/null 2>&1 &)
      return 0
    fi
  done
  return 1
}

kill_existing_on_port() {
  local port="$1"
  local pids
  pids=$(ss -ltnp 2>/dev/null | grep ":$port " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u)
  if [ -n "$pids" ]; then
    echo "⚠ Oprește procesele existente pe portul $port (PID: $pids)…" >&2
    kill -TERM $pids 2>/dev/null || true
    sleep 1
    # Force kill if still alive
    pids=$(ss -ltnp 2>/dev/null | grep ":$port " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u)
    if [ -n "$pids" ]; then
      kill -KILL $pids 2>/dev/null || true
      sleep 0.5
    fi
  fi
}

# ── 1. Rulează deja? ────────────────────────────────────────────────────────
if curl -fsS -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  if [ "$FORCE" = "1" ]; then
    echo "Bransk rulează deja pe portul $PORT — forțez restart (--force)."
    kill_existing_on_port "$PORT"
  else
    echo "Bransk rulează deja pe portul $PORT."
    echo "  ($URL)"
    open_browser "$URL" || true
    server_state="deja pornit"
    pause_if_window
    exit 0
  fi
else
  # Portul e ocupat de altceva: nu forțăm, spunem ce e acolo.
  if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    if [ "$FORCE" = "1" ]; then
      echo "Portul $PORT este ocupat — forțez eliberare (--force)." >&2
      kill_existing_on_port "$PORT"
    else
      echo "⚠ Portul $PORT este ocupat de alt program:" >&2
      ss -ltnp 2>/dev/null | grep ":$PORT " >&2 || true
      echo "Închide-l sau pornește Bransk pe alt port: ./launch-chronokg.sh 8010" >&2
      echo "Sau forțează restart: ./launch-chronokg.sh --force $PORT" >&2
      pause_if_window
      exit 1
    fi
  fi

  echo "Pornesc Bransk pe portul $PORT…"
  LOG="/tmp/chronokg-$PORT.log"
  # shellcheck disable=SC2086
  setsid nohup "$ROOT/start.sh" $HOST_ARG "$PORT" >>"$LOG" 2>&1 &
  echo "  log: $LOG"

  echo -n "  aștept serverul"
  started=0
  for _ in $(seq 1 60); do
    if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
      started=1
      break
    fi
    echo -n "."
    sleep 1
  done
  echo
  if [ "$started" != "1" ]; then
    echo "⚠ Serverul nu a răspuns în 60 s. Ultimele rânduri din log:" >&2
    tail -20 "$LOG" >&2 || true
    pause_if_window
    exit 1
  fi
  server_state="pornit acum"
  open_browser "$URL" || echo "Deschide manual: $URL"
fi

# ── 2. Modelul de embedding (llama.cpp pe CPU) răspunde? ───────────────────
echo
if curl -fsS -m 4 "http://127.0.0.1:$EMBED_PORT/health" >/dev/null 2>&1; then
  # Report what actually answers, not just "it is up".
  model="$(curl -fsS -m 4 "http://127.0.0.1:$EMBED_PORT/v1/models" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print(d["data"][0].get("id","?"))
except Exception:
    print("?")' 2>/dev/null)"
  echo "Embeddings: pornit pe portul $EMBED_PORT (llama.cpp, CPU) — model: ${model:-?}"
else
  echo "Embeddings: NU răspunde pe portul $EMBED_PORT."
  echo "  Căutarea semantică e oprită; serverul se repară cu './embedding-supervisor.sh $EMBED_PORT'"
  echo "  sau repornește aplicația cu './launch-chronokg.sh'."
fi

# ── 3. Ce face reindexarea în fundal ───────────────────────────────────────
ACCESS_TOKEN="$(access_token)"
AUTH_ARGS=()
if [ -n "$ACCESS_TOKEN" ]; then
  AUTH_ARGS=(-H "Authorization: Bearer $ACCESS_TOKEN")
fi
if command -v python3 >/dev/null 2>&1; then
  curl -fsS -m 5 "${AUTH_ARGS[@]}" \
    "http://127.0.0.1:$PORT/api/ai/status" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    st=json.load(sys.stdin)
except Exception:
    sys.exit(0)
r=st.get("reembed") or {}
if r.get("running"):
    print(f"Reindexare vectori: {r.get(\"done\",0)}/{r.get(\"total\",0)} fragmente (CPU, în fundal)")
elif r.get("last_error"):
    print(f"Reindexare vectori: oprită — {r[\"last_error\"]}")
elif r.get("finished_at"):
    print(f"Căutare semantică: {st.get(\"pdf_chunks_matchable\",0)}/{st.get(\"pdf_chunks_indexed\",0)} fragmente utilizabile")' 2>/dev/null
fi

echo
if [ -n "$ACCESS_TOKEN" ]; then
  echo "Token de acces (îl introduci o dată în aplicație): $ACCESS_TOKEN"
fi
echo "Bransk ($server_state): $URL"
pause_if_window
