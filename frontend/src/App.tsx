import { useCallback, useState, useEffect } from 'react'
import {
  CalendarDays, Network, BookOpenText, Dna, FileText, RotateCcw, ListChecks, Lightbulb,
  Bot, Sun, Moon, Search, Plus, Download, Upload, AlertTriangle, RefreshCw, Settings2,
} from 'lucide-react'
import { TimelineView } from './components/Timeline'
import { GraphView } from './components/GraphView'
import { ChatPanel } from './components/ChatPanel'
import { EntryForm } from './components/EntryForm'
import { EntryDetail } from './components/EntryDetail'
import { ReviewPanel } from './components/ReviewPanel'
import { TodoList } from './components/TodoList'
import { ThoughtsView } from './components/ThoughtsView'
import { StudiesView } from './components/StudiesView'
import { TheoriesView } from './components/TheoriesView'
import { api } from './lib/api'
import { useToast } from './components/Toast'
import { AiStatusModal } from './components/AiStatusModal'
import type { Entry, TimelineEntry, Tag } from './lib/types'

type View = 'timeline' | 'graph' | 'studies' | 'theories' | 'entries' | 'review' | 'todos' | 'thoughts'

export default function App() {
  const toast = useToast()
  const [view, setView] = useState<View>('timeline')
  const [showEntryForm, setShowEntryForm] = useState(false)
  const [showAiStatus, setShowAiStatus] = useState(false)
  const [selectedEntry, setSelectedEntry] = useState<Entry | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [showChat, setShowChat] = useState(false)
  const [darkMode, setDarkMode] = useState(() => {
    const stored = localStorage.getItem('chronokg-theme')
    if (stored === 'light') return false
    if (stored === 'dark') return true
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  const toggleDark = () => {
    const next = !darkMode
    setDarkMode(next)
    localStorage.setItem('chronokg-theme', next ? 'dark' : 'light')
    document.documentElement.classList.toggle('dark', next)
  }

  // Follow the OS theme only while the user hasn't made an explicit choice.
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => {
      if (!localStorage.getItem('chronokg-theme')) {
        setDarkMode(e.matches)
        document.documentElement.classList.toggle('dark', e.matches)
      }
    }
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])

  // Keep the root <html> dark class in sync on first mount too, so canvas
  // visualizations (vis-network/vis-timeline) can read the active theme.
  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
  }, [darkMode])

  // Async handler to fetch full Entry from Timeline/Graph selection
  const handleSelectTimelineEntry = useCallback(async (item: TimelineEntry) => {
    setLoadingDetail(true)
    setView('entries')
    try {
      const fullEntry = await api.entries.get(item.id)
      setSelectedEntry(fullEntry)
    } catch (err) {
      toast.error('Failed to fetch entry detail: ' + (err instanceof Error ? err.message : 'unknown error'))
    } finally {
      setLoadingDetail(false)
    }
  }, [toast])

  const handleSelectStudy = useCallback((entry: Entry) => {
    setSelectedEntry(entry)
    setView('entries')
  }, [])

  const navItems: { id: View; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { id: 'timeline', label: 'Timeline', icon: CalendarDays },
    { id: 'graph', label: 'Graph', icon: Network },
    { id: 'studies', label: 'Studies', icon: BookOpenText },
    { id: 'theories', label: 'Theories', icon: Dna },
    { id: 'entries', label: 'Entries', icon: FileText },
    { id: 'review', label: 'Review', icon: RotateCcw },
    { id: 'todos', label: 'Tasks', icon: ListChecks },
    { id: 'thoughts', label: 'Thoughts', icon: Lightbulb },
  ]

  return (
    <div className={`min-h-screen bg-gray-50 dark:bg-gray-950 ${darkMode ? 'dark' : ''}`}>
      <div className="flex h-screen">
        {/* Sidebar */}
        <nav className="w-16 lg:w-48 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 flex flex-col shrink-0">
          <div className="p-3 lg:p-4 border-b border-gray-200 dark:border-gray-700">
            <h1 className="hidden lg:block text-lg font-bold text-chrono-700">ChronoKG</h1>
            <span className="lg:hidden text-xl">🧠</span>
          </div>

          <div className="flex-1 p-2 space-y-1">
            {navItems.map(item => (
              <button
                key={item.id}
                title={item.label}
                aria-current={view === item.id ? 'page' : undefined}
                onClick={() => {
                  if (item.id === 'entries') setSelectedEntry(null)
                  setView(item.id)
                }}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  view === item.id
                    ? 'bg-chrono-100 text-chrono-700 dark:bg-chrono-900/30 dark:text-chrono-400'
                    : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800'
                }`}
              >
                <item.icon className="w-5 h-5 shrink-0" />
                <span className="hidden lg:block">{item.label}</span>
              </button>
            ))}
          </div>

          <div className="p-2 border-t border-gray-200 dark:border-gray-700 space-y-1">
            <button onClick={() => setShowChat(!showChat)} title="AI Chat"
              aria-expanded={showChat}
              aria-label="Toggle AI Chat panel"
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                showChat ? 'bg-chrono-100 text-chrono-700 dark:bg-chrono-900/30' : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800'
              }`}
            >
              <Bot className="w-5 h-5 shrink-0" />
              <span className="hidden lg:block">AI Chat</span>
            </button>
            <button onClick={toggleDark}
              title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
              aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
            >
              {darkMode ? <Sun className="w-5 h-5 shrink-0" /> : <Moon className="w-5 h-5 shrink-0" />}
              <span className="hidden lg:block">{darkMode ? 'Light' : 'Dark'}</span>
            </button>
            <button onClick={() => setShowAiStatus(true)}
              title="AI status & settings"
              aria-label="AI status & settings"
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
            >
              <Settings2 className="w-5 h-5 shrink-0" />
              <span className="hidden lg:block">AI Status</span>
            </button>
          </div>
        </nav>

        {/* Main content */}
        <main className="flex-1 overflow-auto relative bg-gray-50 dark:bg-gray-950">
          <div className="p-4 lg:p-6">
            {view === 'timeline' && (
              <TimelineView
                onSelectEntry={handleSelectTimelineEntry}
                onCreateEntry={() => setShowEntryForm(true)}
              />
            )}
            {view === 'graph' && (
              <GraphView
                onSelectEntry={handleSelectTimelineEntry}
              />
            )}
            {view === 'studies' && (
              <StudiesView onSelectEntry={handleSelectStudy} />
            )}
            {view === 'theories' && (
              <TheoriesView onSelectEntry={handleSelectStudy} />
            )}
            {view === 'entries' && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-xl font-bold">{selectedEntry ? selectedEntry.title : 'Entries'}</h2>
                  <button onClick={() => setShowEntryForm(true)} className="btn-primary">
                    <Plus className="w-4 h-4" /> New Entry
                  </button>
                </div>
                {loadingDetail ? (
                  <div className="text-center py-12 text-gray-500">Loading entry details...</div>
                ) : selectedEntry ? (
                  <EntryDetail
                    entry={selectedEntry}
                    onBack={() => setSelectedEntry(null)}
                    onUpdate={(e) => setSelectedEntry(e)}
                    onSelectEntry={handleSelectStudy}
                  />
                ) : (
                  <EntryList onSelect={setSelectedEntry} />
                )}
              </div>
            )}
            {view === 'review' && <ReviewPanel />}
            {view === 'todos' && <TodoList onSelectEntry={handleSelectStudy} />}
            {view === 'thoughts' && <ThoughtsView onSelectEntry={handleSelectStudy} />}
          </div>
        </main>

        {/* AI Chat Panel */}
        {showChat && (
          <aside className="w-full sm:w-[22rem] lg:w-[30rem] xl:w-[34rem] shrink-0 border-l border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 flex flex-col">
            <ChatPanel onClose={() => setShowChat(false)} />
          </aside>
        )}
      </div>

      {/* Entry Form Modal */}
      {showEntryForm && (
        <EntryForm
          onClose={() => setShowEntryForm(false)}
          onSaved={(entry) => {
            setShowEntryForm(false)
            setSelectedEntry(entry)
            setView('entries')
          }}
        />
      )}

      {showAiStatus && <AiStatusModal onClose={() => setShowAiStatus(false)} />}
    </div>
  )
}

function EntryList({ onSelect }: { onSelect: (e: Entry) => void }) {
  const toast = useToast()
  const [entries, setEntries] = useState<Entry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<{ entry: Entry; rank: number }[] | null>(null)
  const [searching, setSearching] = useState(false)

  const load = async (nextOffset = 0, archived = showArchived) => {
    setLoading(true)
    setError('')
    try {
      const data = await api.entries.list({ limit: 100, offset: nextOffset, archived })
      if (nextOffset === 0) setEntries(data)
      else setEntries(prev => [...prev, ...data])
      setHasMore(data.length === 100)
      setOffset(nextOffset)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load entries')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setQuery('')
    setSearchResults(null)
    setEntries([])
    load(0, showArchived)
  }, [showArchived])

  const runSearch = async () => {
    const q = query.trim()
    if (!q) { setSearchResults(null); return }
    setSearching(true)
    try {
      setSearchResults(await api.search(q))
    } catch (err) {
      toast.error('Search failed: ' + (err instanceof Error ? err.message : 'unknown error'))
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const exportData = async () => {
    try {
      // Export everything, not just the first 500 entries: page through the
      // whole list until the API returns fewer than a full page.
      const entriesData: Entry[] = []
      let offset = 0
      while (true) {
        const page = await api.entries.list({ limit: 500, offset })
        entriesData.push(...page)
        if (page.length < 500) break
        offset += 500
      }
      const [relationships, tags] = await Promise.all([
        api.relationships.list(),
        api.tags.list(),
      ])
      const payload = {
        app: 'chronokg',
        exported_at: new Date().toISOString(),
        entries: entriesData,
        relationships,
        tags,
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `chronokg-export-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err: any) {
      toast.error('Export failed: ' + (err?.message || 'unknown error'))
    }
  }

  const importData = async (file: File) => {
    try {
      const data = JSON.parse(await file.text())
      if (!Array.isArray(data.entries)) throw new Error('Not a ChronoKG export file')
      if (!confirm(`Import ${data.entries.length} entries, ${(data.tags || []).length} tags, ${(data.relationships || []).length} relationships?`)) return

      // Pre-fetch existing tags and entries once for deduping
      const [existingTags, existingEntries] = await Promise.all([
        api.tags.list().catch(() => [] as Tag[]),
        api.entries.list({ limit: 500 }).catch(() => [] as Entry[]),
      ])
      const existingTagNames = new Set<string>(existingTags.map(t => String(t.name).toLowerCase()))
      const existingTitles = new Set<string>(existingEntries.map(e => e.title.trim().toLowerCase().replace(/\s+/g, ' ')))

      const normalizeTitle = (t: any) => String(t).trim().toLowerCase().replace(/\s+/g, ' ')
      const isDuplicateKeyError = (err: any) => {
        if (!err) return false
        const msg = `${err.status ?? ''} ${err.detail ?? ''} ${err.message ?? ''}`
        return err.status === 409 || /duplicate|unique|already exists/i.test(msg)
      }

      const idMap: Record<number, number> = {}
      let created = 0
      let skipped = 0
      let failed = 0

      for (const e of data.entries) {
        if (e.archived) continue
        const normTitle = normalizeTitle(e.title)
        if (!normTitle) { failed++; continue }
        if (existingTitles.has(normTitle)) { skipped++; continue }
        try {
          const entry = await api.entries.create({
            title: String(e.title).slice(0, 500),
            content: e.content || '',
            entry_date: e.entry_date || null,
            tags: e.tags || '',
            source: e.source || '',
            importance: e.importance ?? 3,
            study_type: e.study_type || '',
            authors: e.authors || '',
            quality: e.quality ?? null,
            url_validated: !!e.url_validated,
            design: e.design || '',
            effect_direction: e.effect_direction || '',
            // pdf_path is intentionally not restored: the PDF file itself is
            // not part of the export, so a stale path would render dead links.
          })
          idMap[e.id] = entry.id
          created++
          existingTitles.add(normTitle)
        } catch (err: any) {
          if (isDuplicateKeyError(err)) { skipped++ }
          else failed++
        }
      }
      for (const t of data.tags || []) {
        if (!t.name) continue
        const tagName = String(t.name)
        const normName = tagName.toLowerCase()
        if (existingTagNames.has(normName)) { skipped++; continue }
        try {
          await api.tags.create({ name: tagName, color: t.color })
          existingTagNames.add(normName)
        } catch (err: any) {
          if (isDuplicateKeyError(err)) { skipped++ }
          else failed++
        }
      }
      for (const r of data.relationships || []) {
        const from = idMap[r.from_id]
        const to = idMap[r.to_id]
        if (from && to && from !== to) {
          try { await api.relationships.create({ from_id: from, to_id: to, label: r.label || '' }) }
          catch (err: any) {
            if (isDuplicateKeyError(err)) { skipped++ }
            else failed++
          }
        }
      }
      toast.success(`Imported ${created} entries, skipped ${skipped} duplicates, ${failed} failed`)
      setSearchResults(null)
      load(0, showArchived)
    } catch (err: any) {
      toast.error('Import failed: ' + (err?.message || 'unknown error'))
    }
  }

  if (loading && entries.length === 0) return <div className="text-center py-8 text-gray-500">Loading...</div>

  return (
    <div className="space-y-4">
      {/* Toolbar: search, archive toggle, export/import */}
      <div className="flex flex-wrap gap-2">
        <div className="flex flex-1 min-w-[220px] gap-2">
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runSearch()}
            className="input flex-1"
            placeholder="Search entries (full-text)..."
            aria-label="Search entries (full-text)"
          />
          <button onClick={runSearch} disabled={searching} className="btn-secondary" aria-label="Search">
            <Search className="w-4 h-4" />
          </button>
        </div>
        <button
          onClick={() => setShowArchived(!showArchived)}
          className={`btn-ghost text-sm ${showArchived ? 'text-chrono-600' : ''}`}
        >
          {showArchived ? 'Archived' : 'Show archived'}
        </button>
        <button onClick={exportData} className="btn-ghost text-sm">
          <Download className="w-4 h-4" /> Export
        </button>
        <label className="btn-ghost text-sm cursor-pointer">
          <Upload className="w-4 h-4" /> Import
          <input
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) importData(f)
              e.target.value = ''
            }}
          />
        </label>
      </div>

      {searching ? (
        <div className="text-center py-8 text-gray-500">Searching...</div>
      ) : error && entries.length === 0 ? (
        <div className="card text-center py-10 border-red-200 dark:border-red-900 space-y-3">
          <p className="text-sm text-red-600 dark:text-red-400 flex items-center justify-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </p>
          <button onClick={() => load(0, showArchived)} className="btn-secondary text-sm">
            <RefreshCw className="w-4 h-4" /> Retry
          </button>
        </div>
      ) : searchResults !== null ? (
        searchResults.length === 0 ? (
          <div className="card text-center py-10 text-gray-500">No results for "{query.trim()}"</div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {searchResults.map(({ entry, rank }) => (
              <button key={entry.id} onClick={() => onSelect(entry)}
                className="card text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors"
              >
                <h3 className="font-semibold mb-1">{entry.title}</h3>
                {entry.entry_date && <p className="text-xs text-gray-500 mb-1">{entry.entry_date}</p>}
                <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-2">{entry.content}</p>
                <p className="text-xs text-gray-400 mt-1">relevance {rank.toFixed(2)}</p>
              </button>
            ))}
          </div>
        )
      ) : (
        <>
          <p className="text-sm text-gray-500">{entries.length} entries {showArchived ? '(archived)' : ''}</p>
          {error && (
            <div className="card border-red-200 dark:border-red-900 text-sm text-red-600 dark:text-red-400 flex items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" /> {error}
              </span>
              <button onClick={() => load(0, showArchived)} className="btn-ghost text-sm">
                <RefreshCw className="w-4 h-4" /> Retry
              </button>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {entries.map(entry => (
              <button key={entry.id} onClick={() => onSelect(entry)}
                className="card text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors"
              >
                <h3 className="font-semibold mb-1">{entry.title}</h3>
                {entry.entry_date && <p className="text-xs text-gray-500 mb-1">{entry.entry_date}</p>}
                <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-2">{entry.content}</p>
                {entry.tags && (
                  <div className="flex gap-1 mt-2 flex-wrap">
                    {entry.tags.split(',').map(t => t.trim()).filter(Boolean).map(t => (
                      <span key={t} className="badge bg-chrono-100 text-chrono-700 dark:bg-chrono-900/50 dark:text-chrono-400">{t}</span>
                    ))}
                  </div>
                )}
              </button>
            ))}
          </div>
          {hasMore && (
            <div className="text-center pt-2">
              <button onClick={() => load(offset + 100)} disabled={loading} className="btn-secondary">
                {loading ? 'Loading...' : 'Load more'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
