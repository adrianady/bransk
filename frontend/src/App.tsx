import { useState, useEffect } from 'react'
import { TimelineView } from './components/Timeline'
import { GraphView } from './components/GraphView'
import { ChatPanel } from './components/ChatPanel'
import { EntryForm } from './components/EntryForm'
import { EntryDetail } from './components/EntryDetail'
import { ReviewPanel } from './components/ReviewPanel'
import { TodoList } from './components/TodoList'
import { ThoughtsView } from './components/ThoughtsView'
import type { Entry } from './lib/types'

type View = 'timeline' | 'graph' | 'entries' | 'review' | 'todos' | 'thoughts' | 'settings'

export default function App() {
  const [view, setView] = useState<View>('timeline')
  const [showEntryForm, setShowEntryForm] = useState(false)
  const [selectedEntry, setSelectedEntry] = useState<Entry | null>(null)
  const [showChat, setShowChat] = useState(false)
  const [darkMode, setDarkMode] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches
  )

  const toggleDark = () => {
    const next = !darkMode
    setDarkMode(next)
    document.documentElement.classList.toggle('dark', next)
  }

  const navItems: { id: View; label: string; icon: string }[] = [
    { id: 'timeline', label: 'Timeline', icon: '📅' },
    { id: 'graph', label: 'Graph', icon: '🕸️' },
    { id: 'entries', label: 'Entries', icon: '📝' },
    { id: 'review', label: 'Review', icon: '🔄' },
    { id: 'todos', label: 'Tasks', icon: '✅' },
    { id: 'thoughts', label: 'Thoughts', icon: '💭' },
  ]

  return (
    <div className={`min-h-screen ${darkMode ? 'dark' : ''}`}>
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
                onClick={() => setView(item.id)}
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
        <main className="flex-1 overflow-auto relative">
          <div className="p-4 lg:p-6">
            {view === 'timeline' && (
              <TimelineView
                onSelectEntry={(e) => { setSelectedEntry(e); setView('entries') }}
                onCreateEntry={() => setShowEntryForm(true)}
              />
            )}
            {view === 'graph' && (
              <GraphView
                onSelectEntry={(e) => { setSelectedEntry(e); setView('entries') }}
              />
            )}
            {view === 'entries' && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-xl font-bold">{selectedEntry ? selectedEntry.title : 'Entries'}</h2>
                  <button onClick={() => setShowEntryForm(true)} className="btn-primary">
                    + New Entry
                  </button>
                </div>
                {selectedEntry ? (
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
          <aside className="w-80 lg:w-96 border-l border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 flex flex-col">
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

  useEffect(() => {
    import('./lib/api').then(({ api }) => {
      api.entries.list().then(setEntries).finally(() => setLoading(false))
    })
  }, [])

  if (loading) return <div className="text-center py-8 text-gray-500">Loading...</div>

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {entries.map(entry => (
        <button key={entry.id} onClick={() => onSelect(entry)}
          className="card text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors"
        >
          <h3 className="font-semibold mb-1">{entry.title}</h3>
          {entry.entry_date && (
            <p className="text-xs text-gray-500 mb-1">{entry.entry_date}</p>
          )}
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
  )
}
