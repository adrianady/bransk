import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
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
  const [todoDraft, setTodoDraft] = useState<{ priority: string; effort: string; due_date: string }>({ priority: 'normal', effort: 'm', due_date: '' })
  const [srItem, setSrItem] = useState<SpacedRepetitionItem | null>(null)
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [showReminderForm, setShowReminderForm] = useState(false)
  const [reminderTitle, setReminderTitle] = useState('')
  const [reminderTrigger, setReminderTrigger] = useState('')
  const [reminderType, setReminderType] = useState('once')
  const [activeTab, setActiveTab] = useState<'info' | 'links' | 'todo' | 'review' | 'reminders'>('info')

  useEffect(() => {
    Promise.all([
      api.relationships.list(entry.id),
      api.entries.list({ limit: 500 }),
    ]).then(([rels, entries]) => {
      setRelationships(rels)
      setAllEntries(entries.filter(e => e.id !== entry.id))
    })
    api.todos.list().then(todos => {
      const t = todos.find(t => t.entry_id === entry.id)
      if (t) {
        setTodo(t)
        setTodoDraft({ priority: t.priority, effort: t.effort, due_date: t.due_date || '' })
      }
    })
    api.spacedRepetition.list().then(items => {
      const item = items.find(i => i.entry_id === entry.id)
      if (item) setSrItem(item)
    })
    api.reminders.list().then(rs => {
      setReminders(rs.filter(r => r.entry_id === entry.id))
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
    setTodoDraft({ priority: item.priority, effort: item.effort, due_date: item.due_date || '' })
  }

  const saveTodoDraft = async () => {
    if (!todo) return
    const updated = await api.todos.update(entry.id, {
      priority: todoDraft.priority,
      effort: todoDraft.effort,
      due_date: todoDraft.due_date || null,
    })
    setTodo(updated)
    setTodoDraft({ priority: updated.priority, effort: updated.effort, due_date: updated.due_date || '' })
  }

  const addReminder = async () => {
    if (!reminderTrigger) return
    const created = await api.reminders.create(entry.id, {
      title: reminderTitle,
      trigger_at: reminderTrigger,
      reminder_type: reminderType,
    })
    setReminders(prev => [...prev, created])
    setShowReminderForm(false)
    setReminderTitle('')
    setReminderTrigger('')
    setReminderType('once')
  }

  const importanceLabel = (n: number) => {
    const labels: Record<number, string> = { 1: 'Low', 2: 'Medium', 3: 'Important', 4: 'Very Important', 5: 'Critical' }
    return labels[n] || 'Normal'
  }

  const qualityLabel = (n: number | null) => {
    if (!n) return ''
    const labels: Record<number, string> = {
      1: 'Weak', 2: 'Limited', 3: 'Moderate', 4: 'Strong', 5: 'High quality',
    }
    return labels[n] || ''
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
            <button
              onClick={async () => {
                const updated = await api.entries.update(entry.id, { archived: !entry.archived })
                onUpdate(updated)
              }}
              className="btn-ghost"
              title={entry.archived ? 'Restore entry' : 'Archive entry'}
            >{entry.archived ? '♻️' : '🗄️'}</button>
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

        {/* Study metadata */}
        {entry.study_type && (
          <div className="mb-4 p-3 rounded-lg bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-900 text-sm space-y-1">
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span className="badge bg-indigo-100 text-indigo-700 dark:bg-indigo-900/60 dark:text-indigo-300">{entry.study_type}</span>
              {entry.entry_date && <span>📅 {entry.entry_date.slice(0, 4)}</span>}
              {entry.quality != null && (
                <span title={qualityLabel(entry.quality)}>
                  {'★'.repeat(entry.quality)}{'☆'.repeat(5 - entry.quality)}
                  <span className="text-gray-500 ml-1">quality {entry.quality}/5</span>
                </span>
              )}
            </div>
            {entry.authors && <p className="text-gray-600 dark:text-gray-400"><strong>Authors:</strong> {entry.authors}</p>}
            {entry.source && (
              <p className="text-gray-600 dark:text-gray-400 break-all">
                <strong>Source:</strong>{' '}
                {entry.source.startsWith('http') ? (
                  <a href={entry.source} target="_blank" rel="noreferrer" className="text-chrono-600 hover:underline">{entry.source}</a>
                ) : entry.source}
              </p>
            )}
          </div>
        )}

        {/* Content */}
        <div className="prose prose-sm dark:prose-invert max-w-none mt-4">
          {entry.content ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.content}</ReactMarkdown>
          ) : (
            <p className="text-gray-400 italic">No content</p>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700">
        {(['info', 'links', 'todo', 'review', 'reminders'] as const).map(tab => (
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
            {tab === 'reminders' && `⏰ Reminders (${reminders.length})`}
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
              <div className="grid grid-cols-3 gap-2 pt-1">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Priority</label>
                  <select
                    value={todoDraft.priority}
                    onChange={e => setTodoDraft(d => ({ ...d, priority: e.target.value }))}
                    className="input text-sm"
                  >
                    <option value="critical">critical</option>
                    <option value="important">important</option>
                    <option value="normal">normal</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Effort</label>
                  <select
                    value={todoDraft.effort}
                    onChange={e => setTodoDraft(d => ({ ...d, effort: e.target.value }))}
                    className="input text-sm"
                  >
                    <option value="s">S</option>
                    <option value="m">M</option>
                    <option value="l">L</option>
                    <option value="xl">XL</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Due</label>
                  <input
                    type="date"
                    value={todoDraft.due_date}
                    onChange={e => setTodoDraft(d => ({ ...d, due_date: e.target.value }))}
                    className="input text-sm"
                  />
                </div>
              </div>
              <button onClick={saveTodoDraft} className="btn-secondary text-sm">Save task details</button>
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

      {/* Reminders Tab */}
      {activeTab === 'reminders' && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Reminders</h3>
            <button onClick={() => setShowReminderForm(!showReminderForm)} className="btn-primary text-sm">+ Add Reminder</button>
          </div>

          {showReminderForm && (
            <div className="mb-4 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-2">
              <input value={reminderTitle} onChange={e => setReminderTitle(e.target.value)}
                className="input" placeholder="Reminder title (optional)" />
              <div className="grid grid-cols-2 gap-2">
                <input type="datetime-local" value={reminderTrigger} onChange={e => setReminderTrigger(e.target.value)}
                  className="input" />
                <select value={reminderType} onChange={e => setReminderType(e.target.value)} className="input">
                  <option value="once">Once</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>
              <div className="flex gap-2">
                <button onClick={addReminder} disabled={!reminderTrigger} className="btn-primary text-sm">Save</button>
                <button onClick={() => setShowReminderForm(false)} className="btn-ghost text-sm">Cancel</button>
              </div>
            </div>
          )}

          {reminders.length === 0 ? (
            <p className="text-sm text-gray-500">No reminders for this entry yet.</p>
          ) : (
            <div className="space-y-2">
              {reminders.map(rem => (
                <div key={rem.id} className="flex items-center justify-between p-2 bg-gray-50 dark:bg-gray-800 rounded-lg text-sm">
                  <div>
                    <p className="font-medium">{rem.title || 'Reminder'}</p>
                    <p className="text-xs text-gray-500">
                      {rem.trigger_at.replace('T', ' ')} · {rem.reminder_type}
                      {rem.notified ? ' · ✅ notified' : ' · ⏳ pending'}
                    </p>
                  </div>
                  <button onClick={async () => {
                    await api.reminders.delete(entry.id, rem.id)
                    setReminders(prev => prev.filter(r => r.id !== rem.id))
                  }} className="text-red-400 hover:text-red-600 text-xs">Remove</button>
                </div>
              ))}
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
