import { useState, useEffect } from 'react'
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
import type { Entry, TimelineEntry } from './lib/types'

type View = 'timeline' | 'graph' | 'studies' | 'theories' | 'entries' | 'review' | 'todos' | 'thoughts'

export default function App() {
  const [view, setView] = useState<View>('timeline')
  const [showEntryForm, setShowEntryForm] = useState(false)
  const [selectedEntry, setSelectedEntry] = useState<Entry | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [showChat, setShowChat] = useState(false)
  const [darkMode, setDarkMode] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches
  )

  const toggleDark = () => {
    const next = !darkMode
    setDarkMode(next)
    document.documentElement.classList.toggle('dark', next)
  }

  // Keep the root <html> dark class in sync on first mount too, so canvas
  // visualizations (vis-network/vis-timeline) can read the active theme.
  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
  }, [darkMode])

  // Async handler to fetch full Entry from Timeline/Graph selection
  const handleSelectTimelineEntry = async (item: TimelineEntry) => {
    setLoadingDetail(true)
    setView('entries')
    try {
      const fullEntry = await api.entries.get(item.id)
      setSelectedEntry(fullEntry)
    } catch (err) {
      console.error('Failed to fetch entry detail:', err)
    } finally {
      setLoadingDetail(false)
    }
  }

  const handleSelectStudy = (entry: Entry) => {
    setSelectedEntry(entry)
    setView('entries')
  }

  const navItems: { id: View; label: string; icon: string }[] = [
    { id: 'timeline', label: 'Timeline', icon: '📅' },
    { id: 'graph', label: 'Graph', icon: '🕸️' },
    { id: 'studies', label: 'Studies', icon: '📚' },
    { id: 'theories', label: 'Theories', icon: '🧬' },
    { id: 'entries', label: 'Entries', icon: '📝' },
    { id: 'review', label: 'Review', icon: '🔄' },
    { id: 'todos', label: 'Tasks', icon: '✅' },
    { id: 'thoughts', label: 'Thoughts', icon: '💭' },
  ]

  return (
    <div className={`min-h-screen bg-gray-50 dark:bg-gray-950 ${darkMode ? 'dark' : ''}`}>
      <div className="flex h-screen">
        {/* Sidebar */}
        <nav className="w-16 lg:w-48 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 flex flex-col shrink-0">
          <div className="p-3 lg:p-4 border-b border-gray-200 dark:border-gray-700">
            <h1 className="hidden lg:block text-lg font-bold text-chrono-600">ChronoKG</h1>
            <span className="lg:hidden text-xl">🧠</span>
          </div>

          <div className="flex-1 p-2 space-y-1">
            {navItems.map(item => (
              <button
                key={item.id}
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
                <span className="text-lg">{item.icon}</span>
                <span className="hidden lg:block">{item.label}</span>
              </button>
            ))}
          </div>

          <div className="p-2 border-t border-gray-200 dark:border-gray-700 space-y-1">
            <button onClick={() => setShowChat(!showChat)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                showChat ? 'bg-chrono-100 text-chrono-700 dark:bg-chrono-900/30' : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800'
              }`}
            >
              <span className="text-lg">🤖</span>
              <span className="hidden lg:block">AI Chat</span>
            </button>
            <button onClick={toggleDark}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
            >
              <span className="text-lg">{darkMode ? '☀️' : '🌙'}</span>
              <span className="hidden lg:block">{darkMode ? 'Light' : 'Dark'}</span>
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
                    + New Entry
                  </button>
                </div>
                {loadingDetail ? (
                  <div className="text-center py-12 text-gray-500">Loading entry details...</div>
                ) : selectedEntry ? (
                  <EntryDetail
                    entry={selectedEntry}
                    onBack={() => setSelectedEntry(null)}
                    onUpdate={(e) => setSelectedEntry(e)}
                  />
                ) : (
                  <EntryList onSelect={setSelectedEntry} />
                )}
              </div>
            )}
            {view === 'review' && <ReviewPanel />}
            {view === 'todos' && <TodoList />}
            {view === 'thoughts' && <ThoughtsView />}
          </div>
        </main>

        {/* AI Chat Panel */}
        {showChat && (
          <aside className="w-[22rem] lg:w-[30rem] xl:w-[34rem] shrink-0 border-l border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 flex flex-col">
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
    </div>
  )
}

function EntryList({ onSelect }: { onSelect: (e: Entry) => void }) {
  const [entries, setEntries] = useState<Entry[]>([])
  const [loading, setLoading] = useState(true)
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<{ entry: Entry; rank: number }[] | null>(null)
  const [searching, setSearching] = useState(false)

  const load = async (nextOffset = 0, archived = showArchived) => {
    setLoading(true)
    try {
      const data = await api.entries.list({ limit: 100, offset: nextOffset, archived })
      if (nextOffset === 0) setEntries(data)
      else setEntries(prev => [...prev, ...data])
      setHasMore(data.length === 100)
      setOffset(nextOffset)
    } catch (err) {
      console.error('Failed to load entries:', err)
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
      console.error('Search failed:', err)
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const exportData = async () => {
    try {
      const [entriesData, relationships, tags] = await Promise.all([
        api.entries.list({ limit: 500 }),
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
      alert('Export failed: ' + err.message)
    }
  }

  const importData = async (file: File) => {
    try {
      const data = JSON.parse(await file.text())
      if (!Array.isArray(data.entries)) throw new Error('Not a ChronoKG export file')
      if (!confirm(`Import ${data.entries.length} entries, ${(data.tags || []).length} tags, ${(data.relationships || []).length} relationships?`)) return

      const idMap: Record<number, number> = {}
      let created = 0
      for (const e of data.entries) {
        if (e.archived) continue
        const entry = await api.entries.create({
          title: String(e.title).slice(0, 500),
          content: e.content || '',
          entry_date: e.entry_date || null,
          tags: e.tags || '',
          source: e.source || '',
          importance: e.importance ?? 3,
        })
        idMap[e.id] = entry.id
        created++
      }
      for (const t of data.tags || []) {
        if (t.name) await api.tags.create({ name: String(t.name), color: t.color })
      }
      for (const r of data.relationships || []) {
        const from = idMap[r.from_id]
        const to = idMap[r.to_id]
        if (from && to && from !== to) {
          try { await api.relationships.create({ from_id: from, to_id: to, label: r.label || '' }) } catch { /* dedupe */ }
        }
      }
      alert(`Import complete: ${created} entries restored`)
      setSearchResults(null)
      load(0, showArchived)
    } catch (err: any) {
      alert('Import failed: ' + err.message)
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
          />
          <button onClick={runSearch} disabled={searching} className="btn-secondary">🔎</button>
        </div>
        <button
          onClick={() => setShowArchived(!showArchived)}
          className={`btn-ghost text-sm ${showArchived ? 'text-chrono-600' : ''}`}
        >
          {showArchived ? '📦 Archived' : '🗄️ Show archived'}
        </button>
        <button onClick={exportData} className="btn-ghost text-sm">⬇️ Export</button>
        <label className="btn-ghost text-sm cursor-pointer">
          ⬆️ Import
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
