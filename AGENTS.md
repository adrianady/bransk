# Repository Guidelines

## Project Overview

**ChronoKG** (internal code name: **Bransk**) is a local-first, privacy-respecting academic research workspace and chronological knowledge graph. It is tailored for empirical research, literature reviews, psychometric studies, and academic thesis writing (licență, disertație, doctorat).

### Core Capabilities
- **Chronological Knowledge Graph & Timeline**: Visualizes studies, hypotheses, theories, and concepts across time with interactive force-directed graph layouts and timelines.
- **Multi-Project Isolation**: Manages independent research projects with distinct outlines, hypotheses, questionnaires, and claims while sharing the underlying literature library.
- **Unified Academic Library**: Single search and screening interface for empirical papers, books, and researcher notes with multi-criteria filtering, deduplication, and full-text acquisition.
- **Academic Questionnaires & Instruments**: Psychometric scale design, Likert item generation, reverse-scoring specifications, and AI-driven validity critiques.
- **Zotero Integration**: Bidirectional sync and import covering local `zotero.sqlite` databases, linked/custom PDF storage, file imports (`.bib`, `.ris`, `.json`), and Web API sync.
- **Anti-Bot Defense & Sci-Hub Browser Fallback**: Automated scholarly acquisition that detects Cloudflare/bot challenge screens and falls back to structured browser download URLs and drag-and-drop PDF upload.
- **PDF RAG & Semantic Study QA**: Ingestion of paper PDFs into page-indexed text chunks, embedded with local llama.cpp or Ollama models for semantic search and grounded question answering with page citations.
- **Multi-Provider LLM Resilience**: Dynamic failover cascade across DeepSeek, OpenAI-compatible APIs, local Ollama, and OpenCode direct endpoints, equipped with active context window detection and token-level event auditing.
- **Chat Agent & Active Task Tracking**: Line-delimited bracketed tool tags (`[DOWNLOAD_STUDY: ...]`, `[SEARCH_DOCUMENTS: ...]`, etc.) with two-phase tool execution and persistent multi-step task tracking in SQLite (`chat_tasks`).

---

## Architecture & Data Flow

```
                      ┌───────────────────────────────────────────────┐
                      │             React 19 Frontend (Vite)          │
                      │  LibraryView · ProjectView · ChatPanel · Graph │
                      └──────────────────────┬────────────────────────┘
                                             │ HTTP / SSE / Multipart
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │             FastAPI Backend (/api)            │
                      │   Domain Routers: entries, project, zotero,   │
                      │   questionnaires, chat, study_qa, research    │
                      └───────┬──────────────┬──────────────┬─────────┘
                              │              │              │
           ┌──────────────────┴──┐    ┌──────┴──────┐   ┌───┴────────────────┐
           │   SQLite (WAL+FTS5) │    │  PDF & RAG  │   │   LLM Orchestration│
           │  • project          │    │ • fitz/pypdf│   │ • DeepSeek/OpenAI  │
           │  • questionnaires   │    │ • llama.cpp │   │ • Ollama / OpenCode│
           │  • entries / docs   │    │ • studies/  │   │ • Token Tracking   │
           │  • chat_tasks       │    │ • Study QA  │   │ • Failover Cascade │
           └─────────────────────┘    └─────────────┘   └────────────────────┘
```

### 1. Multi-Project Isolation
- **Database Schema**: The `project` table uses `id INTEGER PRIMARY KEY AUTOINCREMENT`. Legacy databases containing `CHECK (id = 1)` are migrated automatically via `_migrate_project_table(conn)` in `backend/app/db.py`.
- **Active State**: The active project ID is stored in `app_settings` under the key `active_project_id` and resolved per-request via `_get_active_project_id(conn)`.
- **Scoped Entities**: All project-specific resources (`questionnaires`, `project_claims`, `project_claim_evidence`, `project_ideas`, and outline structure `structure_json`) are strictly filtered by `project_id`.
- **Endpoints**:
  - `GET /api/projects`: Lists all projects and indicates `active_id`.
  - `POST /api/projects`: Creates a new project and switches active context to it.
  - `POST /api/projects/{pid}/activate`: Switches active project context.
  - `DELETE /api/projects/{pid}`: Deletes a project (refuses deletion if only one project remains).
  - `GET /api/project` & `PUT /api/project`: Reads/updates metadata, outline structure, and hypotheses for the active project.

### 2. Questionnaires & Research Instruments
- **Data Model**: Stored in the `questionnaires` table (`project_id`, `title`, `description`, `construct`, `target_population`, `items_json`, `reliability_notes`).
- **Items Structure**: `items_json` stores an array of questionnaire items, each specifying question text, scale type (e.g. Likert 1-5), reverse scoring flags (`reverse: true`), and subscale dimensions.
- **AI Tooling**:
  - `POST /api/questionnaires/{id}/ai-generate`: Prompts the active LLM with project hypotheses and target population to draft or expand psychometric scale items.
  - `POST /api/questionnaires/{id}/ai-critique`: Analyzes items for psychometric construct validity, ambiguous wording, response bias, and alignment with target demographics.
- **Frontend**: Managed inside `ProjectView.tsx` under the dedicated `QuestionnairesPanel.tsx` tab.

### 3. Zotero Integration
- **Router**: `backend/app/routers/zotero.py`.
- **Local SQLite Ingestion**:
  - `GET /api/zotero/status`: Locates local profile directory (`~/Zotero/zotero.sqlite`) and PDF storage (`storage/` or custom ZotFile/linked directories configured via `zotero_pdf_folder` in `app_settings`).
  - `POST /api/zotero/import/local`: Direct read of SQLite items, collections, creators, and attachment metadata. Copies attached PDFs directly into `backend/data/studies/` and indexes them into RAG.
  - Deduplication: Normalized DOI matching and fuzzy title comparison (`titles_match`) prevent duplicate library entries.
- **File & API Import**:
  - `POST /api/zotero/import/file`: Accepts multipart uploads of `.bib` (BibTeX), `.ris` (RIS), and `.json` (CSL-JSON) files up to 50MB.
  - `POST /api/zotero/sync/api`: Pulls collections and items from Zotero Web API using user/group API credentials.
- **Export**: `GET /api/zotero/export?format=csl-json|bibtex|ris&ids=...` exports selected entries or the entire library.

### 4. Sci-Hub & Anti-Bot Browser Download Fallback
- **Acquisition Pipeline**: `backend/app/fulltext.py` and `backend/app/studies_store.py` attempt multi-source retrieval (OpenAlex, Unpaywall, Europe PMC, arXiv, direct publisher, Sci-Hub).
- **Challenge Detection**: `detect_bot_challenge(status_code, content_type, body)` flags responses containing Cloudflare security checkpoints, Turnstile CAPTCHA, 403 Forbidden, 429 Rate Limit, or HTML challenge pages masquerading as 200 OK responses.
- **Graceful Fallback**: Returns structured metadata:
  ```json
  {
    "status": "failed",
    "bot_challenge": true,
    "manual_upload_needed": true,
    "browser_download_url": "https://doi.org/10.xxxx/...",
    "browser_download_url_alt": "https://sci-hub.se/10.xxxx/..."
  }
  ```
- **Manual Upload**: `POST /api/entries/{id}/pdf` accepts multipart `UploadFile`, validates PDF magic header (`%PDF-`), writes to `backend/data/studies/{id}.pdf`, and immediately triggers `index_pdf(entry_id, path)`.

### 5. Unified Academic Library
- **Component**: `frontend/src/components/LibraryView.tsx`.
- **View Filter Pills**: Unified view across all library records:
  - `all`: All entries.
  - `studies`: Empirical papers and literature entries.
  - `notes`: Standalone researcher notes and observations.
  - `has_pdf`: Entries with downloaded full-text PDFs.
  - `no_pdf`: Studies requiring full-text acquisition.
- **State Preservation**: `frontend/src/lib/libraryState.ts` persists scroll position, active filter pill, search query, and current page in `sessionStorage`. Uses `suspendLibraryScroll` during unmounting to ensure returning from entry details preserves user scroll coordinates.
- **History & Routing**: Native browser Back/Forward integration using `history.pushState({ chronokgDetail })` and window `popstate` listeners in `App.tsx`.

### 6. PDF RAG & Semantic Study QA
- **Chunking Pipeline**: `backend/app/pdf_rag.py` uses PyMuPDF (`fitz`) and `pypdf` to extract text with page coordinates. Divides content into overlapping token windows (~600–800 tokens, 100-token overlap) stored in `documents` and indexed into `documents_fts`.
- **Embeddings Layer**: `backend/app/embeddings.py` interfaces with a local `llama.cpp` OpenAI-compatible endpoint (`http://127.0.0.1:8081/v1/embeddings`), falling back to Ollama or in-memory vector cache with cosine reranking.
- **Semantic QA**:
  - `POST /api/studies/{id}/qa`: Retrieves relevant chunks from the study PDF, constructs a strict citation prompt, and streams grounded answers referencing page numbers.
  - `GET /api/studies/{id}/sources`: Fetches indexed PDF fragments with page numbers for transparency.
  - `POST /api/studies/{id}/ai-appraisal`: Analyzes methodology, sample size, variables, effect direction, and methodological limitations from the PDF text.

### 7. LLM Providers, Failover & Token Tracking
- **Central Dispatch**: `backend/app/llm.py` (`complete` and `stream_complete`).
- **Failover Chain**: UI-chosen active provider -> `LLM_PROVIDERS` list -> DeepSeek -> Ollama -> Mock fallback.
- **Supported Providers**:
  - `deepseek`: DeepSeek Chat / Reasoner API.
  - `openai`, `groq`, `mistral`, `together`, `openrouter`: Standard OpenAI-compatible cloud endpoints.
  - `custom`: User-configured custom OpenAI-compatible endpoint stored in `app_settings`.
  - `opencode_console` / `opencode_go`: Direct bridge to local or managed OpenCode routing.
  - `ollama`: Local offline LLM server.
  - `mock`: Hermetic offline test runner.
- **Context Windows**: `active_context_window()` queries provider model capabilities with TTL caching (conservative fallback of 8,192 tokens when unreachable).
- **Audit & Cost Observability**: `log_ai_event()` logs every request to `ai_events` table (`provider`, `model`, `endpoint`, `ok`, `error`, `latency_ms`, `prompt_chars`, `reply_chars`, `prompt_tokens`, `completion_tokens`).

### 8. Chat Tools & Active Task Tracking
- **Tag-Based Function Calling**: Model replies emit line-delimited bracketed tags:
  - `[SEARCH_KB: query]` / `[SEARCH_TIMELINE: from|to|keyword]`
  - `[SEARCH_STUDIES: academic query]` / `[SEARCH_DOCUMENTS: query]`
  - `[DOWNLOAD_STUDY: entry_id | DOI | URL | exact title | missing]`
  - `[ADD_ENTRY: title | content | date | tags | type | importance]`
  - `[UPDATE_ENTRY: id | title=... | content=...]`
  - `[ADD_TODO: entry_id | priority | text]` / `[ADD_REMINDER: ...]`
  - `[ADD_STUDY_NOTE: entry_id | note text]` / `[SCREEN_STUDY: entry_id | include/exclude/maybe | reason]`
  - `[CREATE_RELATIONSHIP: from_id | to_id | relation]`
- **Two-Phase Loop**: Read/search actions execute first; tool output is fed back into a second model pass to produce a fully synthesized response with citations.
- **Persistent Tasks**: Long-running actions (batch downloads, screening, research sweeps) are registered in the `chat_tasks` table (`kind`, `goal`, `status`, `details`, `progress_json`).
- **UI Task Banner**: `ChatPanel.tsx` checks `GET /api/chat/task` and renders an interactive task banner with progress updates, cancel buttons, and dismiss controls.

---

## Key Directories

```
bransk/
├── AGENTS.md                  # Workspace root repository guidelines (this file)
└── chronokg/                  # Core application git repository
    ├── AGENTS.md              # Synchronized repository guidelines
    ├── start.sh               # Primary production/desktop runner (FastAPI + SPA build)
    ├── start-embeddings.sh    # Local llama.cpp embeddings server starter
    ├── launch-chronokg.sh     # Network & browser launcher
    ├── backend/               # FastAPI backend
    │   ├── requirements.txt   # Python dependencies
    │   ├── pyproject.toml     # Packaging metadata
    │   ├── data/              # SQLite database (chronokg.db), auth-token, studies/
    │   ├── app/
    │   │   ├── main.py        # FastAPI initialization and route mounting
    │   │   ├── db.py          # SQLite connection, additive migrations, FTS5 triggers
    │   │   ├── llm.py         # Multi-provider LLM failover, context calculation, event logging
    │   │   ├── pdf_rag.py     # PDF chunking, document indexing, and extraction
    │   │   ├── embeddings.py  # llama.cpp / Ollama embedding client and cosine reranker
    │   │   ├── fulltext.py    # Multi-source paper search, acquisition, and bot detection
    │   │   ├── studies_store.py # PDF download validation, SSRF checks, file management
    │   │   ├── study_dedup.py # DOI normalization and fuzzy title matching
    │   │   └── routers/       # Domain route modules
    │   │       ├── project.py        # Project CRUD, outline, claims, topic brainstorming
    │   │       ├── entries.py        # Knowledge graph entries, timeline, PDF upload
    │   │       ├── questionnaires.py  # Psychometric scale design & AI critique
    │   │       ├── zotero.py         # Local SQLite, file import, API sync, export
    │   │       ├── chat.py           # Tag parser, 2-phase execution, chat_tasks tracking
    │   │       ├── study_qa.py       # Grounded PDF Q&A and AI appraisal
    │   │       ├── research.py       # Qualitative research pipeline
    │   │       ├── citations.py      # Citation network and BibTeX generator
    │   │       ├── timeline.py       # Chronological range queries
    │   │       └── search.py         # FTS5 + semantic hybrid search
    │   └── tests/             # Pytest suite with hermetic per-process DBs
    └── frontend/              # React 19 frontend
        ├── package.json       # Node dependencies and scripts
        ├── vite.config.ts     # Vite bundler configuration
        ├── vitest.config.ts   # Unit/component test setup
        └── src/
            ├── App.tsx        # Shell navigation, detail routes, popstate handling
            ├── main.tsx       # Application root render
            ├── components/    # Feature panels and modals
            │   ├── LibraryView.tsx          # Unified academic library with filter pills
            │   ├── ProjectView.tsx          # Active thesis dashboard, outline, claims
            │   ├── QuestionnairesPanel.tsx  # Psychometric instrument editor
            │   ├── ImportStudiesModal.tsx   # Study import dialog & Zotero tab
            │   ├── ChatPanel.tsx            # AI chat interface & chat_tasks banner
            │   ├── StudyPdfModal.tsx        # In-browser PDF reader
            │   ├── StudyQA.tsx              # Grounded PDF Q&A panel
            │   └── ThesisEvidenceMap.tsx    # Chapter-to-claim evidence linkage
            └── lib/
                ├── api.ts           # Typed API client with token auth
                ├── types.ts         # TypeScript schema definitions
                └── libraryState.ts  # Session storage state preservation
```

---

## Development Commands

### 1. Backend Setup & Local Development
```bash
cd chronokg/backend

# Create virtual environment with uv or python3 venv
uv venv --python 3.12 .venv || python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run backend with auto-reload for local development
uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port 8000
```
> **Note on Process Reloads**: In production or desktop launcher mode (`./start.sh`), uvicorn runs without `--reload`. If you modify router definitions or database migrations on a running production instance, you must restart the background uvicorn process for routes to take effect.

### 2. Frontend Development
```bash
cd chronokg/frontend

# Install dependencies
npm install

# Start Vite dev server with proxy to :8000
npm run dev
```

### 3. Full Application Launch
```bash
cd chronokg

# Standard local start (builds frontend if missing, launches FastAPI on :8000)
./start.sh

# Network mode (binds 0.0.0.0, enables token authentication for phone/LAN access)
./start.sh -n

# Custom port
./start.sh 8080
```

### 4. Local Embeddings Server
```bash
cd chronokg

# Launches llama-server on port 8081 for semantic retrieval
./start-embeddings.sh
```

### 5. Running Tests
```bash
# Run entire backend test suite
cd chronokg/backend
pytest

# Run targeted test suites
cd chronokg/backend
pytest tests/test_multi_projects.py tests/test_zotero.py tests/test_questionnaires.py

# Run frontend tests
cd chronokg/frontend
npm run test

# Run frontend production build check
cd chronokg/frontend
npm run build
```

---

## Code Conventions & Common Patterns

### 1. Database & Migrations
- **Zero ORM**: Write direct, parameterized SQL using `sqlite3`. Never format raw variables into SQL queries.
- **Connection Factory**: Always use `conn = get_db()` from `app.db`. Return rows as dictionaries using `dict(row)` or `sqlite3.Row` field lookups.
- **WAL Mode & Locks**: Databases run in `PRAGMA journal_mode = WAL` with `PRAGMA busy_timeout = 5000`. Commit write transactions promptly and close or exit context cleanly.
- **Additive Migrations**: Schema alterations must be placed in `init_db()` in `app/db.py` wrapped in `try/except sqlite3.OperationalError` for idempotency (`ALTER TABLE ... ADD COLUMN ...`).
- **Constraint Rebuilds**: Because SQLite cannot alter existing `CHECK` constraints, schema redesigns (like multi-project support) must create a shadow table, copy columns, drop old, and rename under disabled foreign keys.
- **Project Scoping**: Always obtain the active project ID via `_get_active_project_id(conn)` and include `WHERE project_id = ?` when querying or mutating questionnaires, claims, or outline data.

### 2. Async Endpoints & Blocking I/O
- Fast endpoints are declared `async def`.
- Blocking file writes, network downloads, or heavy computation must be offloaded via `anyio.to_thread.run_sync` or wrapped background workers to avoid freezing the FastAPI event loop.

### 3. Security & SSRF Defense
- Any download of arbitrary URLs for PDFs or web content MUST pass through `assert_public_download_url(url)` in `studies_store.py`.
- Reject loopback (`127.0.0.0/8`), link-local (`169.254.0.0/16`), private RFC 1918 ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), and IPv6 equivalents.

### 4. Romanian NLP & Declension Rules
- When writing heuristics or title analysis for Romanian academic works (e.g. `_title_stats` in `app/routers/project.py`), never use exact whole-word matching for population terms.
- Romanian nouns inflect heavily (e.g. `adulți`, `adulților`, `adulții`). Always use stem matching (`adult`, `copi`, `adolescent`, `tiner`, `student`) or explicit declension sets.

### 5. Frontend Navigation & Persistence
- When building new panels or library filters, store persistent view parameters in `sessionStorage` via `lib/libraryState.ts`.
- When navigating to an entry detail view, invoke `suspendLibraryScroll()` to prevent the list from unmounting back to coordinate `y: 0`.
- Maintain synchronization with the global browser history (`pushState` with `{ chronokgDetail }`) to respect native Back button gestures.

---

## Important Files

| File | Purpose |
|------|---------|
| `chronokg/backend/app/main.py` | FastAPI application initialization, CORS, static SPA mounting, router registration. |
| `chronokg/backend/app/db.py` | SQLite connection, table schema declarations, FTS5 triggers, additive migrations. |
| `chronokg/backend/app/llm.py` | Multi-provider LLM failover, context size resolution, `ai_events` logging. |
| `chronokg/backend/app/pdf_rag.py` | PDF text extraction, document chunking, and full-text document indexing. |
| `chronokg/backend/app/embeddings.py` | llama.cpp / Ollama embedding HTTP requests and cosine similarity re-ranking. |
| `chronokg/backend/app/fulltext.py` | Scholarly paper retrieval, Sci-Hub discovery, and bot challenge detection. |
| `chronokg/backend/app/studies_store.py` | File-system PDF storage, SSRF URL validation, and PDF header verification. |
| `chronokg/backend/app/study_dedup.py` | DOI canonical normalization and fuzzy title comparison algorithms. |
| `chronokg/backend/app/routers/project.py` | Multi-project management, outline sections, claims, topics, DOCX thesis export. |
| `chronokg/backend/app/routers/questionnaires.py` | Psychometric scale CRUD, AI item generator, and AI psychometric critique. |
| `chronokg/backend/app/routers/zotero.py` | Local Zotero SQLite reader, attachment sync, RIS/BibTeX/CSL-JSON import & export. |
| `chronokg/backend/app/routers/chat.py` | Bracketed tag parser, tool execution loop, and `chat_tasks` lifecycle management. |
| `chronokg/backend/app/routers/study_qa.py` | Grounded study Q&A, citation fragment listings, and AI study appraisal. |
| `chronokg/frontend/src/App.tsx` | Main application shell, routing state, keyboard shortcuts, history popstate listener. |
| `chronokg/frontend/src/components/LibraryView.tsx` | Unified academic library with filter pills, search, and pagination. |
| `chronokg/frontend/src/components/ProjectView.tsx` | Active thesis view (tabs: overview, outline, claims, questionnaires, readiness). |
| `chronokg/frontend/src/components/QuestionnairesPanel.tsx` | Questionnaire builder, item scoring controls, AI generate/critique buttons. |
| `chronokg/frontend/src/components/ImportStudiesModal.tsx` | Academic import dialog with search, file upload, and Zotero integration tab. |
| `chronokg/frontend/src/components/ChatPanel.tsx` | Chat agent interface with live Active Task banner (`chat_tasks`). |
| `chronokg/frontend/src/lib/api.ts` | Frontend HTTP client wrapper handling authentication headers and error toasts. |
| `chronokg/frontend/src/lib/types.ts` | Core TypeScript interfaces across all domain entities. |
| `chronokg/frontend/src/lib/libraryState.ts` | SessionStorage persistence for scroll offsets, active pills, and pagination. |

---

## Runtime & Tooling Preferences

- **Language Versions**: Python 3.12+ (backend), Node.js 20+ (frontend).
- **Package Management**:
  - Backend: `uv` (recommended) or standard `pip` with `backend/requirements.txt`.
  - Frontend: `npm` with `frontend/package.json`.
- **Database & Data Paths**:
  - Live database: `chronokg/backend/data/chronokg.db`.
  - Studies PDF storage: `chronokg/backend/data/studies/`.
  - Auth token: `chronokg/backend/data/auth-token`.
- **Environment Variables**:
  - `DEEPSEEK_API_KEY`: Key for DeepSeek cloud models.
  - `LLM_PROVIDER`: Default model provider (`deepseek`, `ollama`, `mock`, etc.).
  - `LLM_PROVIDERS`: Comma-separated fallback chain.
  - `CHRONOKG_AUTH_TOKEN`: Static token override for network mode (`-n`).
  - `CHRONOKG_DB_PATH`: Custom path override for database (used in pytest).
  - `CHRONOKG_STUDIES_DIR`: Custom path override for PDF store (used in pytest).

---

## Testing & QA

### 1. Hermetic Backend Testing
The backend test suite is configured in `backend/tests/conftest.py` to be completely hermetic:
- Sets `LLM_PROVIDER = "mock"` to eliminate external network calls.
- Creates an isolated per-process SQLite database (`chronokg_pytest_<PID>.db`) and temporary studies folder inside the OS temp directory.
- Registers an `atexit` cleanup hook to guarantee no test artifacts remain.
- When adding backend features, write corresponding tests in `backend/tests/` matching existing test patterns.

### 2. Frontend Component Testing
- Tests are executed with Vitest and jsdom (`frontend/vitest.config.ts`).
- Component tests are located in `frontend/src/components/*.test.tsx`.
- Mock API responses through Vitest module mocking rather than live network calls.

### 3. Verification Checklist Before Completion
- [ ] Run `cd chronokg/backend && pytest` to confirm all backend tests pass without regressions.
- [ ] Run `cd chronokg/frontend && npm run test` to verify UI component tests pass.
- [ ] Run `cd chronokg/frontend && npm run build` to confirm TypeScript compilation and bundling succeed.
- [ ] Ensure any newly introduced database columns use additive migrations in `backend/app/db.py`.
- [ ] Verify newly created endpoints are registered in `backend/app/main.py`.
- [ ] Confirm project-scoped entities properly filter by `_get_active_project_id(conn)`.
