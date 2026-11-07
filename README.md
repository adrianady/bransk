# Bransk — Research Knowledge Graph

Bransk is a local-first application for thesis research, built on ChronoKG's chronological knowledge graph foundation.

## Project origin

Bransk is built on ChronoKG as its technical base and extends it with dissertation-focused workflows for study notes, source-linked evidence, and thesis mapping. The product name is Bransk; ChronoKG remains the underlying project foundation.

The original ChronoKG GitHub URL could not be confirmed from this checkout: no Git remote is configured, and no matching public source repository was identified. The existing attribution below links to BrainBook, from which portions were ported.

Bransk repository: [github.com/adrianady/bransk](https://github.com/adrianady/bransk).

## Features

- **📅 Timeline** — Horizontal timeline visualization of your entries by date
- **🕸️ Knowledge Graph** — Interactive force-directed graph showing relationships between entries
- **🤖 AI Chat** — Chat with local Ollama AI that has full access to your knowledge base (Reasonix provider)
- **📝 Rich Entries** — Create entries with title, date, content, tags, source, and importance
- **📚 Unified library** — Studies and entries are two tabs of one screen (nav: „Bibliotecă”), with PDF/DOI/year filters, screening state, and server-side entry sorting
- **📄 Study evidence** — Ask questions over indexed PDFs, save source-linked notes, and open the retrieved fragment again
- **🎓 Thesis map** — Connect chapters, claims, studies, and PDF evidence; writing and evidence progress comes from saved data
- **📖 Citation manager** — BibTeX/RIS export for the whole library, APA bibliography derived from saved data, `(Autor, An)` cite snippets; the thesis export can also produce **Word (.docx)** with the generated bibliography
- **🔬 PRISMA screening** — record include/exclude/maybe per study with a reason and a live flow summary
- **🖍️ PDF highlights** — select text in the native reader to save page-anchored highlights; the AI reads them in chat and study QA as user-marked evidence
- **⌘K Global search** — one query over entries, studies, notes, highlights and indexed PDF chunks (open with `Ctrl/Cmd+K`)
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

### Network access (LAN or Tailscale)

From the repository root, run `./start.sh -n [port]` to listen on network
interfaces. This mode requires the shared access token; open the printed
Tailscale address from another tailnet device when one is online. Tailscale
Serve is not required for the direct `http://<tailnet-ip>:<port>` route.

On the already-authorized browser, the first access screen can configure one
shared username and password. Other devices can then sign in with those
credentials; the server stores a salted scrypt hash and issues a revocable
30-day HttpOnly session cookie. The existing bearer token remains supported,
and password reset requires that token. Signing out revokes the current device's
session; changing the password revokes all sessions.

The direct tailnet URL is HTTP at the application layer, not HTTPS. The session
cookie's `Secure` flag is therefore enabled only when the app is reached over
HTTPS. Use the private Tailscale address for remote access; do not send the
password over an untrusted LAN or public network.

If ChronoKG is already running, the launcher only accepts it as network-ready
when its listener is not loopback-only and its auth-status endpoint confirms a
token is required. Stop and restart a localhost-only instance with `-n` rather
than opening a second server on the same port.

PDF import summaries distinguish studies added, PDFs saved, PDFs not obtained,
and PDFs saved but not indexed. The in-app reader shows measured PDF.js byte
progress when available and offers a retry after load errors; batch operations
show a busy state without inventing a percentage.

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

For direct OpenCode model inference, open **Furnizori AI și stare**, select the
**OpenCode** provider card, then enter a key created in
[OpenCode Console](https://opencode.ai/console). The card has three separate
methods, each with its own key and model:

- **Console direct** — pay-as-you-go credits, base URL
  `https://opencode.ai/inference`. Free-tier models (Big Pickle, Beam Free and
  the `*-free` family) are not available to external clients on this endpoint
  (HTTP 403/429); Bransk hides them from the Console catalogue and refuses to
  activate them.
- **Go (abonament)** — Go, Go Plus or Go Teams subscription, base URL
  `https://opencode.ai/inference/go`. Usage comes out of the monthly allowance
  instead of credits, and the free models included in Go stay selectable.
- **Server local** — the native OpenCode connector, with credentials managed by
  OpenCode itself.

Bransk lists models at `/v1/models` and calls `/openai/v1/chat/completions` on
the selected base, sending a stable `x-opencode-session` header and its own
`bransk/1.0` User-Agent. Console inference does not use browser OAuth: its
documented model API requires an API key. HTTP 402 means the plan has no usable
billing for that service — check the Console credits for pay-as-you-go, or the
Go subscription and monthly limit for Go. The key
stays in backend settings, is never returned by the configuration API, and is
not stored in browser storage. It is stored in the
local SQLite database without encryption at rest, so protect the database and
its backups. If the backend is exposed beyond loopback, configure
`CHRONOKG_AUTH_TOKEN`. The Console connector accepts the official `opencode.ai`
host and loopback URLs only, requires HTTPS for the official remote host, and
does not follow redirects.

To use OpenCode's own provider authentication instead, connect the provider in
the OpenCode client (`/connect`) and start its local server with
`opencode serve`. Bransk's separate **OpenCode nativ** connector delegates
requests to that local server, so Bransk does not receive the provider
credential. OAuth for Console MCP is separate from model inference. The generic
custom OpenAI-compatible provider remains a third, independent option.

## Tech Stack
- **Backend**: Python + FastAPI + SQLite (FTS5)
- **Frontend**: React 19 + TypeScript + Vite + Tailwind CSS
- **Visualization**: vis-timeline + vis-network
- **AI**: Ollama, DeepSeek, direct OpenCode Console inference, or the optional native OpenCode server
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

Bransk is licensed under the [Apache License, Version 2.0](LICENSE). Portions ported from [BrainBook](https://github.com/wliant/AhLianBrainBook) remain subject to that same license; see [NOTICE](NOTICE) for the attribution. The original ChronoKG attribution could not be verified from this checkout — if you are the ChronoKG author and require different terms, open an issue.
