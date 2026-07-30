# ChronoKG — Chronological Knowledge Graph

A local-first web application for building and exploring a chronological knowledge graph with AI-powered navigation.

## Features

- **📅 Timeline** — Horizontal timeline visualization of your entries by date
- **🕸️ Knowledge Graph** — Interactive force-directed graph showing relationships between entries
- **🤖 AI Chat** — Chat with local Ollama AI that has full access to your knowledge base (Reasonix provider)
- **📝 Rich Entries** — Create entries with title, date, content, tags, source, and importance
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

## Tech Stack
- **Backend**: Python + FastAPI + SQLite (FTS5)
- **Frontend**: React 19 + TypeScript + Vite + Tailwind CSS
- **Visualization**: vis-timeline + vis-network
- **AI**: Ollama (local, Reasonix provider) or DeepSeek API
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
Apache 2.0 — portions ported from [BrainBook](https://github.com/wliant/AhLianBrainBook)
