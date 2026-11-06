#!/usr/bin/env bash
# Bransk — pornește backendul (care servește și interfața).
#
#   ./start.sh            pornește pe 127.0.0.1:8000 (doar acest calculator)
#   ./start.sh 8080       pornește pe alt port, tot pe localhost
#   ./start.sh -n         expune pe rețea: telefon în Wi-Fi, Tailscale
#   ./start.sh -n 8080    amândouă
#
# Expunerea pe rețea se cere explicit, cu -n, și primește automat un token
# comun: fără el orice dispozitiv din rețea citește și scrie toată baza. Pe
# bucla locală nu se cere token. Poți fixa tu valoarea cu CHRONOKG_AUTH_TOKEN.
#
# Interfața se construiește automat dacă lipsește build-ul.
# Mediul Python stă în backend/.venv (pe disc, nu în /tmp).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PORT=8000
HOST=127.0.0.1

for arg in "$@"; do
  case "$arg" in
    -n|--network) HOST=0.0.0.0 ;;
    ''|*[!0-9]*)  echo "Argument necunoscut: $arg (port numeric sau -n)" >&2; exit 2 ;;
    *)            PORT="$arg" ;;
  esac
done

# Node.js vine din nvm, care nu e în PATH-ul unui shell neinteractiv.
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nvm/nvm.sh"
fi

# 0a. Un singur server per port. Două instanțe pe același port și aceeași bază
#     SQLite (WAL) scriu una peste alta; dacă aici răspunde deja Bransk,
#     launcher-ul deschide browserul. În modul rețea verificăm și bind-ul și
#     tokenul: un server localhost sau unul neprotejat nu satisface cererea -n.
if curl -fsS -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  if [ "$HOST" = "0.0.0.0" ]; then
    LISTENERS="$(ss -ltnH "sport = :$PORT" 2>/dev/null || true)"
    if ! printf '%s\n' "$LISTENERS" | awk -v suffix=":$PORT" '
      $1 == "LISTEN" {
        address = $4
        if (length(address) >= length(suffix) && substr(address, length(address) - length(suffix) + 1) == suffix) {
          sub(suffix "$", "", address)
          if (address !~ /^127\./ && address != "::1" && address != "[::1]") exposed = 1
        }
      }
      END { exit !exposed }
    '; then
      echo "Bransk răspunde pe portul $PORT, dar ascultă doar pe localhost. Oprește instanța și repornește cu: ./start.sh -n $PORT" >&2
      exit 1
    fi

    AUTH_STATUS="$(curl -fsS -m 3 "http://127.0.0.1:$PORT/api/auth/status" 2>/dev/null || true)"
    if ! printf '%s' "$AUTH_STATUS" | grep -Eq '"required"[[:space:]]*:[[:space:]]*true'; then
      echo "Bransk este expus în rețea fără autentificare verificabilă. Oprește instanța și repornește cu ./start.sh -n $PORT pentru a activa tokenul." >&2
      exit 1
    fi
  fi
  echo "Bransk rulează deja pe portul $PORT — nu pornesc o a doua instanță."
  echo "  http://localhost:$PORT"
  exit 0
fi
# Portul ocupat de ALT program: pornirea ar eșua oricum, dar cu mesaj clar.
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "Portul $PORT este ocupat de alt program:" >&2
  ss -ltnp 2>/dev/null | grep ":$PORT " >&2 || true
  echo "Închide-l sau pornește cu alt port: ./start.sh 8010" >&2
  exit 1
fi

# 0. Token de acces pentru modul rețea. Se păstrează între porniri, ca telefonul
#    să nu ceară un token nou la fiecare restart. Pe localhost nu se folosește.
AUTH_TOKEN="${CHRONOKG_AUTH_TOKEN:-}"
TOKEN_FILE="$BACKEND/data/auth-token"
if [ -z "$AUTH_TOKEN" ] && [ "$HOST" = "0.0.0.0" ]; then
  mkdir -p "$BACKEND/data"
  if [ -s "$TOKEN_FILE" ]; then
    AUTH_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
  else
    AUTH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    (umask 077; printf '%s\n' "$AUTH_TOKEN" > "$TOKEN_FILE")
  fi
fi

# 0. Serverul de embeddings (llama.cpp pe CPU) — parte din aplicație, nu un pas
#    manual. Pornește în fundal și e supravegheat: dacă moare, revine singur.
#    Căutarea semantică depinde de el; fără el aplicația merge, dar caută doar
#    în text. Se poate sări peste cu CHRONOKG_NO_EMBEDDINGS=1.
EMBED_PORT="${LLAMA_EMBED_PORT:-8199}"
if [ "${CHRONOKG_NO_EMBEDDINGS:-0}" != "1" ]; then
  if curl -fsS -m 2 "http://127.0.0.1:$EMBED_PORT/health" >/dev/null 2>&1; then
    echo "→ Embeddings: deja pornit pe $EMBED_PORT"
  else
    echo "→ Pornesc serverul de embeddings în fundal (port $EMBED_PORT) …"
    setsid nohup "$ROOT/embedding-supervisor.sh" "$EMBED_PORT" \
      >> "/tmp/chronokg-embeddings-$EMBED_PORT.log" 2>&1 &
    # Health check scurt: nu bloca pornirea aplicației dacă modelul încarcă greu.
    for _ in $(seq 1 30); do
      if curl -fsS -m 2 "http://127.0.0.1:$EMBED_PORT/health" >/dev/null 2>&1; then break; fi
      sleep 1
    done
    if curl -fsS -m 2 "http://127.0.0.1:$EMBED_PORT/health" >/dev/null 2>&1; then
      echo "  embeddings: gata (log: /tmp/chronokg-embeddings-$EMBED_PORT.log)"
    else
      echo "  embeddings: încă se încarcă; supraveghetorul repornește dacă pică"
      echo "  log: /tmp/chronokg-embeddings-$EMBED_PORT.log"
    fi
  fi
fi

# 1. Mediul Python (creat o singură dată, ținut pe disc).
if [ ! -x "$BACKEND/.venv/bin/python" ]; then
  echo "→ Creez mediul Python în backend/.venv …"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 "$BACKEND/.venv"
    uv pip install --python "$BACKEND/.venv/bin/python" -r "$BACKEND/requirements.txt"
  else
    python3 -m venv "$BACKEND/.venv"
    "$BACKEND/.venv/bin/pip" install -q -r "$BACKEND/requirements.txt"
  fi
fi

# 2. Build-ul interfeței (servit de backend, ca telefonul să aibă un singur link).
if [ ! -f "$FRONTEND/dist/index.html" ] || [ -n "${REBUILD:-}" ]; then
  echo "→ Construiesc interfața …"
  ( cd "$FRONTEND" && npm install --silent && npm run build )
fi

# 3. Adresele pe care răspunde, ca să le poți deschide de pe telefon.
echo
echo "Bransk pornește pe portul $PORT"
if [ "$HOST" = "127.0.0.1" ]; then
  echo "  pe calculator : http://localhost:$PORT"
  echo "  doar acest calculator. Pentru telefon: ./start.sh -n"
else
  IP_LOCAL="$(hostname -I 2>/dev/null | awk '{print $1}')"
  IP_TAIL="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  echo "  pe calculator : http://localhost:$PORT"
  [ -n "$IP_LOCAL" ] && echo "  pe telefon    : http://$IP_LOCAL:$PORT   (aceeași rețea Wi-Fi)"
  [ -n "$IP_TAIL" ]  && echo "  prin Tailscale: http://$IP_TAIL:$PORT    (dacă firewallul permite)"
  if [ -n "$AUTH_TOKEN" ]; then
    echo "  token de acces: $AUTH_TOKEN"
    echo "  (îl introduci o singură dată în aplicație; se păstrează în browser)"
  fi
fi
echo

cd "$BACKEND"
if [ -n "$AUTH_TOKEN" ]; then
  export CHRONOKG_AUTH_TOKEN="$AUTH_TOKEN"
fi
exec env PYTHONPATH="$BACKEND" "$BACKEND/.venv/bin/python" -m uvicorn app.main:app \
  --host "$HOST" --port "$PORT"
