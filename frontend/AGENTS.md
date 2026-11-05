# ChronoKG Frontend (React 19 + Vite + Tailwind)

## OVERVIEW

SPA served by Vite on :5173, talking to the FastAPI backend through `/api`
(dev proxy) or `VITE_API_BASE_URL`. No router library: view state lives in
`App.tsx`.

## STRUCTURE

```
frontend/
├── src/
│   ├── App.tsx              # shell: sidebar nav, theme, EntryList + search + export/import
│   ├── main.tsx             # React root + ToastProvider
│   ├── lib/api.ts           # typed fetch client (30s default timeout, 300s for research)
│   ├── lib/types.ts         # shared interfaces (Entry, ChatAction, ResearchResult, Theory, ...)
│   ├── components/          # one component per view/panel
│   └── index.css            # Tailwind + custom layer classes (.btn-primary, .card, .badge, .input)
├── dist/                    # generated build output - never edit
├── index.html
├── package.json
├── tailwind.config.js       # chrono color palette + darkMode: 'class'
├── tsconfig.json            # strict TS
└── vite.config.ts           # port 5173, /api proxy to :8000
```

## COMPONENT MAP

- `Timeline.tsx` - vis-timeline view
- `GraphView.tsx` - vis-network force graph
- `ChatPanel.tsx` - SSE chat, streaming render, tool-action results
- `EntryDetail.tsx` / `EntryForm.tsx` - entry CRUD UI (TipTap deps/styles present, editor not wired)
- `ReviewPanel.tsx` - data-integrity re-analysis + spaced repetition modes
- `StudiesView.tsx` - study list + PDF download controls
- `TheoriesView.tsx` - theory tree + pro/contra evidence
- `ThoughtsView.tsx`, `TodoList.tsx`, `AiStatusModal.tsx`, `Toast.tsx` - supporting views/utilities

## API CONTRACT

- All backend calls go through `api.*` in `lib/api.ts`; add new endpoints there and mirror types in `lib/types.ts`.
- SSE streams (`chat`, `maintenance`) are parsed via `ReadableStream` in `ChatPanel.tsx` / `ReviewPanel.tsx`; pass `AbortSignal` for cancel/timeout. Research progress arrives as events inside `/chat/stream` (e.g. `phase: research`).
- Batch PDF downloads and the research API client can take minutes - keep long timeouts (300s) on those calls.

## CONVENTIONS

- Tailwind utilities plus the component classes defined in `index.css`; dark mode via the `dark` class on `<html>` (persisted in `localStorage.chronokg-theme`).
- Components receive callbacks (`onSelectEntry`, `onClose`) instead of using a global store; there is no Redux/Zustand.
- Icons from `lucide-react`; `@tiptap` packages and editor styles exist in `package.json` / `index.css`, but no live editor is wired into a component yet.
- TypeScript is strict, but `noUnusedLocals`/`noUnusedParameters` are off; build runs `tsc && vite build`.

## COMMANDS

```bash
npm install
npm run dev        # :5173 with /api proxy to :8000
npm run build      # tsc && vite build
npm run preview
```

## NOTES

- `dist/` is generated output - never edit it.
- `VITE_API_BASE_URL` overrides `/api` for non-proxied deployments.
- `EntryDetail.tsx` (~570 lines), `ChatPanel.tsx` (~536 lines), and `TheoriesView.tsx` (~505 lines) carry most of the UI complexity; read before editing.
