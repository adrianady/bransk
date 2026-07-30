import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import type { Thought, Entry } from '../lib/types'

export function ThoughtsView() {
  const [thoughts, setThoughts] = useState<Thought[]>([])
  const [entries, setEntries] = useState<Record<number, Entry[]>>({})
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [selectedThought, setSelectedThought] = useState<number | null>(null)

  const loadThoughts = async () => {
    setLoading(true)
    try {
      const items = await api.thoughts.list()
      setThoughts(items)

      // Load entries for selected thought
      if (selectedThought) {
        const ents = await api.thoughts.entries(selectedThought)
        setEntries(prev => ({ ...prev, [selectedThought]: ents }))
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadThoughts() }, [])

  useEffect(() => {
    if (selectedThought) {
      api.thoughts.entries(selectedThought).then(ents => {
        setEntries(prev => ({ ...prev, [selectedThought!]: ents }))
      })
    }
  }, [selectedThought])

  const createThought = async () => {
    if (!newName.trim()) return
    await api.thoughts.create({ name: newName.trim() })
    setNewName('')
    setShowCreate(false)
    await loadThoughts()
  }

  if (loading) return <div className="text-center py-8 text-gray-500">Loading thoughts...</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Thoughts</h2>
          <p className="text-sm text-gray-500">Tag-based dynamic collections · {thoughts.length} thoughts</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary">+ New Thought</button>
      </div>

      {showCreate && (
        <div className="card mb-4 flex gap-2">
          <input value={newName} onChange={e => setNewName(e.target.value)}
            className="input flex-1" placeholder="Thought name"
            onKeyDown={e => e.key === 'Enter' && createThought()} autoFocus />
          <button onClick={createThought} className="btn-primary">Create</button>
          <button onClick={() => setShowCreate(false)} className="btn-ghost">Cancel</button>
        </div>
      )}

      {thoughts.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-500 mb-2">No thoughts yet.</p>
          <p className="text-sm text-gray-400">Create a thought to collect entries by tag.</p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {thoughts.map(thought => (
            <div key={thought.id}
              className={`card cursor-pointer transition-colors ${
                selectedThought === thought.id ? 'border-chrono-500 ring-2 ring-chrono-200' : ''
              }`}
              onClick={() => setSelectedThought(selectedThought === thought.id ? null : thought.id)}
            >
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold">{thought.name}</h3>
                  {thought.description && (
                    <p className="text-sm text-gray-500 mt-1">{thought.description}</p>
                  )}
                </div>
                <button onClick={async (e) => {
                  e.stopPropagation()
                  if (confirm('Delete this thought?')) {
                    await api.thoughts.delete(thought.id)
                    await loadThoughts()
                  }
                }} className="btn-ghost p-1 text-red-400 text-xs">✕</button>
              </div>

              {selectedThought === thought.id && (
                <div className="mt-3 border-t border-gray-200 dark:border-gray-700 pt-3">
                  <p className="text-xs text-gray-500 mb-2">
                    Entries: {(entries[thought.id] || []).length}
                  </p>
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {(entries[thought.id] || []).map(entry => (
                      <div key={entry.id} className="text-sm p-1.5 bg-gray-50 dark:bg-gray-800 rounded">
                        <p className="font-medium truncate">{entry.title}</p>
                        {entry.entry_date && <p className="text-xs text-gray-500">{entry.entry_date}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
