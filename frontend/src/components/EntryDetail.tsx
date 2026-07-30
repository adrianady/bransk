import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import type { Entry, Relationship, TodoItem, SpacedRepetitionItem, Reminder } from '../lib/types'
import { EntryForm } from './EntryForm'

interface Props {
  entry: Entry
  onBack: () => void
  onUpdate: (entry: Entry) => void
}

export function EntryDetail({ entry, onBack, onUpdate }: Props) {
  const [relationships, setRelationships] = useState<Relationship[]>([])
  const [allEntries, setAllEntries] = useState<Entry[]>([])
  const [showEdit, setShowEdit] = useState(false)
  const [showLinkPicker, setShowLinkPicker] = useState(false)
  const [linkLabel, setLinkLabel] = useState('')
  const [linkTarget, setLinkTarget] = useState<number | null>(null)
  const [todo, setTodo] = useState<TodoItem | null>(null)
  const [srItem, setSrItem] = useState<SpacedRepetitionItem | null>(null)
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [activeTab, setActiveTab] = useState<'info' | 'links' | 'todo' | 'review'>('info')

  useEffect(() => {
    Promise.all([
      api.relationships.list(entry.id),
      api.entries.list(),
    ]).then(([rels, entries]) => {
      setRelationships(rels)
      setAllEntries(entries.filter(e => e.id !== entry.id))
    })
    api.todos.list().then(todos => {
      const t = todos.find(t => t.entry_id === entry.id)
      if (t) setTodo(t)
    })
    api.spacedRepetition.list().then(items => {
      const item = items.find(i => i.entry_id === entry.id)
      if (item) setSrItem(item)
    })
    api.reminders.list().then(rs => {
      // reminders are per-entry, we'll just show the list
    })
  }, [entry.id])

  const addRelation = async () => {
    if (!linkTarget) return
    await api.relationships.create({ from_id: entry.id, to_id: linkTarget, label: linkLabel })
    const rels = await api.relationships.list(entry.id)
    setRelationships(rels)
    setShowLinkPicker(false)
    setLinkLabel('')
    setLinkTarget(null)
  }

  const addToReview = async () => {
    const item = await api.spacedRepetition.add(entry.id)
    setSrItem(item)
  }

  const addTodo = async () => {
    const item = await api.todos.create(entry.id)
    setTodo(item)
  }

  const importanceLabel = (n: number) => {
    const labels: Record<number, string> = { 1: 'Low', 2: 'Medium', 3: 'Important', 4: 'Very Important', 5: 'Critical' }
    return labels[n] || 'Normal'
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="card">
        <div className="flex items-start justify-between mb-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <button onClick={onBack} className="btn-ghost p-1">←</button>
              <h2 className="text-xl font-bold truncate">{entry.title}</h2>
            </div>
            <div className="flex items-center gap-3 mt-1 text-sm text-gray-500">
              {entry.entry_date && <span>📅 {entry.entry_date}</span>}
              <span>⭐ {importanceLabel(entry.importance)}</span>
              {entry.source && <span>📚 {entry.source}</span>}
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setShowEdit(true)} className="btn-ghost">✏️</button>
            <button onClick={async () => {
              if (confirm('Delete this entry?')) {
                await api.entries.delete(entry.id)
                onBack()
              }
            }} className="btn-ghost text-red-500">🗑️</button>
          </div>
        </div>

        {entry.tags && (
          <div className="flex gap-1 flex-wrap mb-3">
            {entry.tags.split(',').map(t => t.trim()).filter(Boolean).map(t => (
              <span key={t} className="badge bg-chrono-100 text-chrono-700 dark:bg-chrono-900/50 dark:text-chrono-400">{t}</span>
            ))}
          </div>
        )}

        {/* Content */}
        <div className="prose prose-sm dark:prose-invert max-w-none mt-4">
          {entry.content ? (
            <pre className="whitespace-pre-wrap font-sans text-sm">{entry.content}</pre>
          ) : (
            <p className="text-gray-400 italic">No content</p>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700">
        {(['info', 'links', 'todo', 'review'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? 'border-chrono-600 text-chrono-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab === 'info' && '📋 Info'}
            {tab === 'links' && `🔗 Links (${relationships.length})`}
            {tab === 'todo' && '✅ Todo'}
            {tab === 'review' && '🔄 Review'}
          </button>
        ))}
      </div>

      {/* Links Tab */}
      {activeTab === 'links' && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Relationships</h3>
            <button onClick={() => setShowLinkPicker(true)} className="btn-primary text-sm">+ Link Entry</button>
          </div>

          {showLinkPicker && (
            <div className="mb-4 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-2">
              <select onChange={e => setLinkTarget(Number(e.target.value))} className="input">
                <option value="">Select entry to link...</option>
                {allEntries.map(e => (
                  <option key={e.id} value={e.id}>{e.title}</option>
                ))}
              </select>
              <input value={linkLabel} onChange={e => setLinkLabel(e.target.value)}
                className="input" placeholder="Relationship label (e.g., 'caused', 'inspired')" />
              <div className="flex gap-2">
                <button onClick={addRelation} className="btn-primary text-sm">Add Link</button>
                <button onClick={() => setShowLinkPicker(false)} className="btn-ghost text-sm">Cancel</button>
              </div>
            </div>
          )}

          {relationships.length === 0 ? (
            <p className="text-sm text-gray-500">No relationships yet. Link this entry to others in your knowledge graph.</p>
          ) : (
            <div className="space-y-2">
              {relationships.map(rel => (
                <div key={rel.id} className="flex items-center justify-between p-2 bg-gray-50 dark:bg-gray-800 rounded-lg text-sm">
                  <span>
                    {rel.from_id === entry.id ? '→' : '←'} <strong>{allEntries.find(e => e.id === (rel.from_id === entry.id ? rel.to_id : rel.from_id))?.title || `Entry ${rel.from_id === entry.id ? rel.to_id : rel.from_id}`}</strong>
                    {rel.label && <span className="text-gray-500 ml-1">({rel.label})</span>}
                  </span>
                  <button onClick={async () => {
                    await api.relationships.delete(rel.id)
                    setRelationships(prev => prev.filter(r => r.id !== rel.id))
                  }} className="text-red-400 hover:text-red-600 text-xs">Remove</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Todo Tab */}
      {activeTab === 'todo' && (
        <div className="card">
          <h3 className="font-semibold mb-3">Task</h3>
          {todo ? (
            <div className="space-y-2">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={todo.completed}
                  onChange={async () => {
                    const updated = await api.todos.update(entry.id, { completed: !todo.completed })
                    setTodo(updated)
                  }}
                  className="rounded" />
                <span className={todo.completed ? 'line-through text-gray-400' : ''}>
                  {todo.completed ? 'Completed' : 'In progress'}
                </span>
              </label>
              <p className="text-sm">Priority: <span className="font-medium">{todo.priority}</span></p>
              {todo.due_date && <p className="text-sm">Due: {todo.due_date}</p>}
              <button onClick={async () => {
                await api.todos.delete(entry.id)
                setTodo(null)
              }} className="btn-ghost text-sm text-red-500">Remove task</button>
            </div>
          ) : (
            <div>
              <p className="text-sm text-gray-500 mb-2">No task for this entry.</p>
              <button onClick={addTodo} className="btn-primary text-sm">+ Add as Task</button>
            </div>
          )}
        </div>
      )}

      {/* Review Tab */}
      {activeTab === 'review' && (
        <div className="card">
          <h3 className="font-semibold mb-3">Spaced Repetition</h3>
          {srItem ? (
            <div className="space-y-2 text-sm">
              <p>Ease factor: <span className="font-medium">{srItem.ease_factor.toFixed(2)}</span></p>
              <p>Interval: <span className="font-medium">{srItem.interval} days</span></p>
              <p>Repetitions: <span className="font-medium">{srItem.repetitions}</span></p>
              {srItem.next_review && <p>Next review: <span className="font-medium">{srItem.next_review}</span></p>}
              <div className="mt-3">
                <p className="text-xs text-gray-500 mb-2">Rate your recall:</p>
                <div className="flex gap-1">
                  {[0, 1, 2, 3, 4, 5].map(q => (
                    <button key={q} onClick={async () => {
                      const updated = await api.spacedRepetition.review(srItem.id, q)
                      setSrItem(updated)
                    }}
                      className="btn-ghost text-xs px-2 py-1"
                    >{q}</button>
                  ))}
                </div>
              </div>
              <button onClick={async () => {
                await api.spacedRepetition.remove(entry.id)
                setSrItem(null)
              }} className="btn-ghost text-sm text-red-500 mt-2">Remove from review</button>
            </div>
          ) : (
            <div>
              <p className="text-sm text-gray-500 mb-2">Not in review rotation.</p>
              <button onClick={addToReview} className="btn-primary text-sm">+ Add to Review</button>
            </div>
          )}
        </div>
      )}

      {/* Edit Modal */}
      {showEdit && (
        <EntryForm
          editEntry={entry}
          onClose={() => setShowEdit(false)}
          onSaved={(updated) => { onUpdate(updated); setShowEdit(false) }}
        />
      )}
    </div>
  )
}
