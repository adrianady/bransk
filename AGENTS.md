# ChronoKG - Chronological Knowledge Graph

**Generated:** 2026-08-03
**Git:** `master` @ `4465eed` (uncommitted changes on top)

## OVERVIEW

Local-first web app for building and exploring a chronological knowledge graph
with AI: timeline + force-directed graph, LLM chat with tool actions, a
meta-analysis research pipeline, study PDF RAG, spaced repetition, todos,
reminders, thoughts, and a theory aggregator.

## STRUCTURE

```
chronokg/
├── backend/                 # FastAPI + SQLite (Python)
├── frontend/                # React 19 + Vite + Tailwind (TypeScript)
├── backend/tests/           # pytest suite (hermetic DB + mock LLM)
├── CHRONOKG_*.md            # audit/review history
├── .agents/                 # agent artifacts (gitignored)
├── .chunkhound.db*          # ChunkHound search index (regenerable)
├── launch.bat               # one-click launcher (backend + frontend + browser)
├── ChronoKG-Desktop.bat     # hardened launcher with health wait
└── README.md                # features + quick start
```

## WHERE TO LOOK

| Task | Location |
|------|----------|
| Add API endpoint / domain logic | `backend/app/routers/<domain>.py` + `models.py` + schema in `db.py` |
| Change UI / view | `frontend/src/components/`, `frontend/src/App.tsx` |
| Frontend API contract | `frontend/src/lib/types.ts` + `lib/api.ts` |
| Change DB schema | `backend/app/db.py` `init_db()` (idempotent additive migrations) |
| LLM provider behavior | `backend/app/llm.py` (DeepSeek -> Ollama -> mock failover) |
| Research pipeline | `backend/app/routers/research.py` |
| Chat tool actions | `backend/app/routers/chat.py` |
| PDF RAG / study storage | `backend/app/pdf_rag.py`, `backend/app/studies_store.py` |
| Operational status | `GET /api/health` (backend) |

## CONVENTIONS

- Raw SQL via `sqlite3` (no ORM); FTS5 triggers keep `entries_fts` in sync; WAL + `busy_timeout=5000`.
- One router per domain, all mounted under `/api` in `main.py`.
- Every LLM call goes through `app/llm.py` (retry/backoff + `ai_events` observability); providers auto-fallback; `mock` mode for offline dev and tests.
- Blocking work in async endpoints is offloaded via `anyio.to_thread` / `run_sync`.
- Study PDF downloads reject private/internal hosts (SSRF guard in `studies_store.py`); do not weaken it.
- Tests are hermetic: temp DB + `LLM_PROVIDER=mock` set in `backend/tests/conftest.py`.

## COMMANDS

```bash
cd backend && pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000

cd frontend && npm install && npm run dev

cd backend && python -m pytest
cd frontend && npm run build
```

## NOTES

- Do not edit `frontend/dist/` or `frontend/node_modules/` (generated, gitignored).
- Live DB is `backend/data/chronokg.db` (gitignored); `.bak-*` backups exist. Schema changes must be additive `ALTER TABLE` in `init_db()`.
- `backend/.env` holds API keys; copy `backend/.env.example` for configuration.
- The `CHRONOKG_*` audit docs record known issues, fixes, and verification results - read them before changing behavior in these areas.
