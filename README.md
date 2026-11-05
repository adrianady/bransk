# Bransk — Research Knowledge Graph

Bransk is a local-first application for thesis research, built on ChronoKG's chronological knowledge graph foundation.

## Project origin

Bransk is built on ChronoKG as its technical base and extends it with dissertation-focused workflows for study notes, source-linked evidence, and thesis mapping. The product name is Bransk; ChronoKG remains the underlying project foundation.

The original ChronoKG GitHub URL could not be confirmed from this checkout: no Git remote is configured, and no matching public source repository was identified. The existing attribution below links to BrainBook, from which portions were ported.

Bransk repository: [github.com/adrianady/bransk](https://github.com/adrianady/bransk) (private for now).

## Features

- **📅 Timeline** — Horizontal timeline visualization of your entries by date
- **🕸️ Knowledge Graph** — Interactive force-directed graph showing relationships between entries
- **🤖 AI Chat** — Chat with local Ollama AI that has full access to your knowledge base (Reasonix provider)
- **📝 Rich Entries** — Create entries with title, date, content, tags, source, and importance
- **📄 Study evidence** — Ask questions over indexed PDFs, save source-linked notes, and open the retrieved fragment again
- **🎓 Thesis map** — Connect chapters, claims, studies, and PDF evidence; writing and evidence progress comes from saved data
- **🔄 Spaced Repetition** — SM-2 algorithm for review scheduling (ported from BrainBook)
- **✅ Tasks** — Todo/task management per entry
- **💭 Thoughts** — Tag-based dynamic collections
- **🔗 Relationships** — Link entries with typed relationships
- **🌙 Dark Mode** — Automatic and manual theme switching

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+

### One-Click Launch
```
launch.bat
```

### Manual Launch

**Backend:**
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

### Set Up AI
Create `backend/.env`:
```
DEEPSEEK_API_KEY=your-deepseek-api-key
DEEPSEEK_MODEL=deepseek-chat
```

An OpenCode server can be used as an optional native provider. Start a local
server, then open **AI status → Configurează OpenCode nativ** in Bransk and
enter its URL and the absolute project directory:

```bash
opencode serve --hostname 127.0.0.1 --port 4096
```

Bransk discovers models through the OpenCode HTTP API and streams answers
through temporary sessions. Session tool permissions are denied, and the
session is deleted after the response. OpenCode's HTTP API is experimental;
the integration targets v2.1.2. For Basic Auth, set
`OPENCODE_SERVER_PASSWORD` and `OPENCODE_SERVER_URL` in `backend/.env` or the
backend process environment; the URL must exactly match the UI setting. The
password is not stored in SQLite or returned by Bransk's configuration API.
The URL pin prevents a modified local settings row from redirecting the
credential. If the server uses a non-default username, also set
`OPENCODE_SERVER_USERNAME` (default: `opencode`).

## Tech Stack
- **Backend**: Python + FastAPI + SQLite (FTS5)
- **Frontend**: React 19 + TypeScript + Vite + Tailwind CSS
- **Visualization**: vis-timeline + vis-network
- **AI**: Ollama, DeepSeek, or optional native OpenCode server
- **UI**: TipTap (ported concept from BrainBook)

## Architecture
```
Frontend (React)           Backend (FastAPI)          Database
┌──────────────┐          ┌──────────────────┐      ┌─────────┐
│ Timeline      │  REST   │ /api/entries     │───▶  │ SQLite  │
│ Graph View    │◀──────▶│ /api/timeline     │◀──▶  │ + FTS5  │
│ AI Chat (SSE) │         │ /api/search       │      └─────────┘
│ Entry Form    │          │ /api/chat (SSE)   │          ▲
│ Review (SM-2) │          │ /api/spaced-rep   │          │
│ Tasks         │          │ /api/todos        │     DeepSeek API
│ Thoughts      │          │ /api/thoughts     │
└──────────────┘          └──────────────────┘
```

## License
The original README declared Apache 2.0 and credits portions ported from [BrainBook](https://github.com/wliant/AhLianBrainBook). No `LICENSE` file is present in this checkout, and the original ChronoKG license could not be verified; confirm the license before making this repository public.
