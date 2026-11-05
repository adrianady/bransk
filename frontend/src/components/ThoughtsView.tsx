import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { Thought, Entry, Tag } from '../lib/types'

interface Props {
  onSelectEntry?: (entry: Entry) => void
}

export function ThoughtsView({ onSelectEntry }: Props) {
  const toast = useToast()
  const [thoughts, setThoughts] = useState<Thought[]>([])
  const [entries, setEntries] = useState<Record<number, Entry[]>>({})
  const [allTags, setAllTags] = useState<Tag[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Thought | null>(null)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([])
  const [selectedThought, setSelectedThought] = useState<number | null>(null)

  const loadThoughts = async () => {
    setLoading(true)
    try {
      const items = await api.thoughts.list()
      setThoughts(items)
      if (selectedThought) {
        const ents = await api.thoughts.entries(selectedThought)
        setEntries(prev => ({ ...prev, [selectedThought]: ents }))
      }
    } catch (err: any) {
      toast.error('Gândurile nu s-au putut încărca: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadThoughts()
    api.tags.list().then(setAllTags).catch(() => {})
  }, [])

  useEffect(() => {
    if (selectedThought) {
      api.thoughts.entries(selectedThought).then(ents => {
        setEntries(prev => ({ ...prev, [selectedThought!]: ents }))
      })
    }
  }, [selectedThought])

  const openCreate = () => {
    setEditing(null)
    setNewName('')
    setNewDescription('')
    setSelectedTagIds([])
    setShowForm(true)
  }

  const openEdit = (thought: Thought) => {
    setEditing(thought)
    setNewName(thought.name)
    setNewDescription(thought.description)
    setSelectedTagIds(thought.tag_ids.split(',').map(t => t.trim()).filter(Boolean))
    setShowForm(true)
  }

  const toggleTag = (tagId: string) => {
    setSelectedTagIds(prev =>
      prev.includes(tagId) ? prev.filter(t => t !== tagId) : [...prev, tagId]
    )
  }

  const saveThought = async () => {
    const name = newName.trim()
    if (!name) return
    const tagIds = selectedTagIds.join(',')
    try {
      if (editing) {
        await api.thoughts.update(editing.id, { name, description: newDescription, tag_ids: tagIds })
      } else {
        await api.thoughts.create({ name, description: newDescription, tag_ids: tagIds })
      }
      setShowForm(false)
      setEditing(null)
      await loadThoughts()
    } catch (err: any) {
      toast.error('Gândul nu s-a putut salva: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  if (loading) return <div className="text-center py-8 text-gray-500">Se încarcă gândurile…</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Gânduri</h2>
          <p className="text-sm text-gray-500">Colecții dinamice după etichete · {thoughts.length} gânduri</p>
        </div>
        <button onClick={openCreate} className="btn-primary">+ Gând nou</button>
      </div>

      {showForm && (
        <div className="card mb-4 space-y-3">
          <input value={newName} onChange={e => setNewName(e.target.value)}
            className="input" placeholder="Numele gândului"
            onKeyDown={e => e.key === 'Enter' && saveThought()} autoFocus />
          <textarea value={newDescription} onChange={e => setNewDescription(e.target.value)}
            className="input min-h-[60px]" placeholder="Descriere (opțional)" />
          <div>
            <p className="text-xs font-medium text-gray-500 mb-1.5">Etichete — intrările cu aceste etichete intră în colecție</p>
            {allTags.length === 0 ? (
              <p className="text-xs text-gray-400">Nicio etichetă încă. Creează etichete mai întâi din formularul de intrare.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {allTags.map(tag => {
                  const active = selectedTagIds.includes(String(tag.id))
                  return (
                    <button key={tag.id} onClick={() => toggleTag(String(tag.id))}
                      className={`px-2.5 py-1 rounded-full text-xs transition-colors ${
                        active
                          ? 'bg-chrono-600 text-white'
                          : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700'
                      }`}
                    >{tag.name}</button>
                  )
                })}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={saveThought} disabled={!newName.trim()} className="btn-primary">Salvează</button>
            <button onClick={() => { setShowForm(false); setEditing(null) }} className="btn-ghost">Anulează</button>
          </div>
        </div>
      )}

      {thoughts.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-500 mb-2">Niciun gând încă.</p>
          <p className="text-sm text-gray-400">Creează un gând ca să strângi intrări după etichetă.</p>
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
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold">{thought.name}</h3>
                  {thought.description && (
                    <p className="text-sm text-gray-500 mt-1">{thought.description}</p>
                  )}
                  {thought.tag_ids && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {thought.tag_ids.split(',').filter(Boolean).map(tid => {
                        const tag = allTags.find(t => String(t.id) === tid.trim())
                        return tag ? (
                          <span key={tid} className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">{tag.name}</span>
                        ) : null
                      })}
                    </div>
                  )}
                </div>
                <div className="flex gap-1 shrink-0">
                  <button onClick={(e) => { e.stopPropagation(); openEdit(thought) }}
                    className="btn-ghost px-2 py-1 min-w-[28px] text-xs"
                    aria-label={`Editează gândul ${thought.name}`}
                    title="Editează gândul"
                  >✏️</button>
                  <button onClick={async (e) => {
                    e.stopPropagation()
                    if (confirm('Ștergi acest gând?')) {
                      try {
                        await api.thoughts.delete(thought.id)
                        await loadThoughts()
                      } catch (err: any) {
                        toast.error('Gândul nu s-a putut șterge: ' + (err?.message || 'eroare necunoscută'))
                      }
                    }
                  }} className="btn-ghost px-2 py-1 min-w-[28px] text-madder text-xs"
                    aria-label={`Șterge gândul ${thought.name}`}
                    title="Șterge gândul"
                  >✕</button>
                </div>
              </div>

              {selectedThought === thought.id && (
                <div className="mt-3 border-t border-gray-200 dark:border-gray-700 pt-3">
                  <p className="text-xs text-gray-500 mb-2">
                    Intrări: {(entries[thought.id] || []).length}
                  </p>
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {(entries[thought.id] || []).map(entry => (
                      <button
                        key={entry.id}
                        onClick={() => onSelectEntry?.(entry)}
                        className={`text-sm p-1.5 bg-gray-50 dark:bg-gray-800 rounded text-left w-full ${
                          onSelectEntry ? 'hover:bg-chrono-50 dark:hover:bg-chrono-900/20' : ''
                        }`}
                        title={onSelectEntry ? 'Deschide intrarea' : undefined}
                      >
                        <p className="font-medium truncate">{entry.title}</p>
                        {entry.entry_date && <p className="text-xs text-gray-500">{entry.entry_date}</p>}
                      </button>
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
