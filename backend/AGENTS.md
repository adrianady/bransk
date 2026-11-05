# ChronoKG Backend (FastAPI + SQLite)

## OVERVIEW

Async FastAPI app serving the ChronoKG API on :8000. All routes live under
`/api`; storage is raw SQLite (FTS5); LLM access is centralized with
DeepSeek/Ollama/mock failover.

## STRUCTURE

```
backend/
├── app/
│   ├── main.py          # app factory, CORS, lifespan (init_db + reminder ticker), router registration, /api/health
│   ├── db.py            # sqlite3 connection + full schema + FTS5 + additive migrations
│   ├── models.py        # Pydantic request/response models per domain
│   ├── llm.py           # complete/stream_complete, retry/backoff, ai_events logging, provider failover
│   ├── embeddings.py    # Ollama embeddings + cosine rerank (graceful disable when offline)
│   ├── web_search.py    # Serper -> Brave -> DuckDuckGo fallback + URL validation
│   ├── pdf_rag.py       # PDF text extraction, chunking, embedding, documents-table search
│   ├── studies_store.py # study PDF downloads with SSRF guard; files in backend/data/studies
│   └── routers/         # 14 domain routers
├── tests/               # pytest (hermetic DB + mock LLM)
├── data/                # gitignored SQLite DB + studies folder
├── .env.example         # configuration reference
└── requirements.txt / pyproject.toml
```

## ROUTERS (all under `/api`)

| Router | Key endpoints |
|--------|---------------|
| entries | CRUD `/entries`, `/entries/{id}` |
| search | `/search` (FTS5 + tags/date filters), `/search/suggest` |
| timeline | `/timeline`, `/timeline/stats` |
| relationships | CRUD + `/relationships/graph` |
| tags | list/create/delete |
| chat | `/chat`, `/chat/stream` (SSE), `/chat/confirm`, `/chat/history`, `/chat/health` |
| spaced_repetition | items, queue, review (SM-2) |
| todos | per-entry CRUD |
| reminders | CRUD + notify + recurring reminder advance |
| thoughts | CRUD + `/thoughts/{id}/entries` |
| research | `/research`, `/research/stream` (SSE meta-analysis), `/studies*`, PDF download/serve |
| theory | CRUD + `/theories/tree` + evidence |
| maintenance | `/maintenance/analyze` (SSE data-integrity re-analysis) |
| ai | `/ai/events`, `/ai/status` |

## KEY CONVENTIONS

- Sync SQLite calls inside async endpoints must be offloaded (`anyio.to_thread` / `run_sync`); chat and research already do this.
- Never call provider APIs directly outside `llm.py`: retries, failover, and `ai_events` observability live there.
- `LLM_PROVIDER=mock` returns placeholder content; tests rely on it.
- Schema changes go through idempotent `ALTER TABLE` in `init_db()`; never drop/recreate tables.
- Provider failures surface as HTTP 503; request validation via Pydantic models.
- The action parser in `chat.py` and theory/research saving are covered by `tests/test_chat_actions.py`, `tests/test_theory.py`, `tests/test_improvements.py`; keep them green when touching those paths.

## COMMANDS

```bash
# Run (from backend/)
$env:PYTHONPATH = "C:\bransk\chronokg\backend"
python -m uvicorn app.main:app --reload --port 8000

# Tests (from backend/)
python -m pytest
```

## NOTES

- `.env` is loaded from `backend/.env`; defaults documented in `.env.example`.
- PDF downloads must pass `assert_public_download_url` (SSRF guard) - keep it.
- `chat.py` (~1100 lines) and `research.py` (~1000 lines) are the largest, riskiest files; read them before editing.
