#!/usr/bin/env bash
# Ține serverul de embeddings (llama.cpp, pe CPU) pornit tot timpul.
#
# Pornit de start.sh în fundal, o singură dată: dacă procesul moare (crash,
# OOM, kill), pornește din nou după câteva secunde. Aplicația îl găsește pe
# 127.0.0.1:8199 fără să pornească un al doilea exemplar.
#
#   ./embedding-supervisor.sh            pornește și supraveghează (port 8199)
#   ./embedding-supervisor.sh 8200       alt port
#   ./embedding-supervisor.sh --once     pornește doar dacă lipsește, iese
#
# Log: /tmp/chronokg-embeddings-<port>.log
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8199
ONCE=0

for arg in "$@"; do
  case "$arg" in
    --once) ONCE=1 ;;
    ''|*[!0-9]*) echo "Argument necunoscut: $arg" >&2; exit 2 ;;
    *) PORT="$arg" ;;
  esac
done

LOG="/tmp/chronokg-embeddings-$PORT.log"
PIDFILE="/tmp/chronokg-embeddings-$PORT.pid"
MONITOR_PIDFILE="/tmp/chronokg-embeddings-supervisor-$PORT.pid"

healthy() {
  curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1
}

# Already serving? Nothing to do — a second llama-server would fail on the port.
if healthy; then
  exit 0
fi

if [ "$ONCE" = "1" ]; then
  exec "$ROOT/start-embeddings.sh" "$PORT"
fi

# One supervisor per port, so repeated start.sh runs never stack up.
if [ -f "$MONITOR_PIDFILE" ] && kill -0 "$(cat "$MONITOR_PIDFILE" 2>/dev/null)" 2>/dev/null; then
  exit 0
fi
echo $$ > "$MONITOR_PIDFILE"

child=""
cleanup() {
  [ -n "$child" ] && kill "$child" 2>/dev/null
  rm -f "$PIDFILE" "$MONITOR_PIDFILE"
  exit 0
}
trap cleanup TERM INT

BACKOFF=5
while true; do
  if healthy; then
    sleep 15
    continue
  fi

  {
    echo "── $(date '+%F %T') — pornesc serverul de embeddings pe portul $PORT"
  } >> "$LOG"

  # start-embeddings.sh face față amândurouă cazurilor: port liber sau deja
  # ocupat (răspunde health) și descarcă modelul o singură dată.
  "$ROOT/start-embeddings.sh" "$PORT" >> "$LOG" 2>&1 &
  child=$!
  echo "$child" > "$PIDFILE"

  # Așteaptă să răspundă (modelul se încarcă în memorie, câteva secunde).
  for _ in $(seq 1 60); do
    if healthy; then break; fi
    if ! kill -0 "$child" 2>/dev/null; then break; fi
    sleep 2
  done

  if healthy; then
    BACKOFF=5
    # Stai cât trăiește procesul copil. Când moare, bucla repornește.
    while kill -0 "$child" 2>/dev/null; do
      sleep 10
      if ! healthy && kill -0 "$child" 2>/dev/null; then
        : # încă încarcă / temporar indisponibil; continuă să aștepți
      fi
    done
    echo "── $(date '+%F %T') — serverul de embeddings s-a oprit, repornesc în ${BACKOFF}s" >> "$LOG"
  else
    echo "── $(date '+%F %T') — nu a pornit (backoff ${BACKOFF}s); vezi $LOG" >> "$LOG"
  fi

  wait "$child" 2>/dev/null
  sleep "$BACKOFF"
  BACKOFF=$(( BACKOFF < 60 ? BACKOFF * 2 : 60 ))
done
