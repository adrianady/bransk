import { useState, useEffect } from 'react'
import { Plus, RefreshCw, Trash2, Pencil } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { TodoItem, Entry, Reminder } from '../lib/types'

interface Props {
  onSelectEntry?: (entry: Entry) => void
}

export function TodoList({ onSelectEntry }: Props) {
  const toast = useToast()
  const [todos, setTodos] = useState<(TodoItem & { entry?: Entry })[]>([])
  const [dueReminders, setDueReminders] = useState<Reminder[]>([])
  const [allEntries, setAllEntries] = useState<Entry[]>([])
  const [entryTitles, setEntryTitles] = useState<Record<number, string>>({})
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState<{ priority: string; effort: string; due_date: string }>({ priority: 'normal', effort: 'm', due_date: '' })
  const [showCreate, setShowCreate] = useState(false)
  const [createDraft, setCreateDraft] = useState<{ entry_id: string; priority: string; effort: string; due_date: string }>({ entry_id: '', priority: 'normal', effort: 'm', due_date: '' })
  const [saving, setSaving] = useState(false)

  const loadTodos = async () => {
    setLoading(true)
    try {
      const [items, entries, due] = await Promise.all([
        api.todos.list(),
        api.entries.list({ limit: 500 }),
        api.reminders.due(),
      ])
      const titleMap: Record<number, string> = {}
      entries.forEach(e => { titleMap[e.id] = e.title })
      setEntryTitles(titleMap)
      setAllEntries(entries)
      setDueReminders(due.filter(r => !r.notified))
      const enriched = items.map(t => ({ ...t, entry: entries.find(e => e.id === t.entry_id) }))
      setTodos(enriched)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadTodos() }, [])

  const toggleTodo = async (todo: TodoItem) => {
    try {
      await api.todos.update(todo.entry_id, { completed: !todo.completed })
      await loadTodos()
    } catch (err: any) {
      toast.error('Could not update task: ' + (err?.message || 'unknown error'))
    }
  }

  const startEdit = (todo: TodoItem) => {
    setEditingId(todo.id)
    setDraft({ priority: todo.priority, effort: todo.effort, due_date: todo.due_date || '' })
  }

  const saveEdit = async (todo: TodoItem) => {
    try {
      await api.todos.update(todo.entry_id, {
        priority: draft.priority,
        effort: draft.effort,
        due_date: draft.due_date || null,
      })
      setEditingId(null)
      toast.success('Task details saved')
      await loadTodos()
    } catch (err: any) {
      toast.error('Could not save task: ' + (err?.message || 'unknown error'))
    }
  }

  const createTask = async () => {
    const entryId = Number(createDraft.entry_id)
    if (!entryId) return
    setSaving(true)
    try {
      await api.todos.create(entryId, {
        priority: createDraft.priority,
        effort: createDraft.effort,
        due_date: createDraft.due_date || undefined,
      })
      setShowCreate(false)
      setCreateDraft({ entry_id: '', priority: 'normal', effort: 'm', due_date: '' })
      toast.success('Task created')
      await loadTodos()
    } catch (err: any) {
      toast.error('Could not create task: ' + (err?.message || 'unknown error'))
    } finally {
      setSaving(false)
    }
  }

  const priorityColors: Record<string, string> = {
    critical: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    important: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
    normal: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  }

  if (loading) return <div className="text-center py-8 text-gray-500">Loading tasks...</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Tasks</h2>
          <p className="text-sm text-gray-500">{todos.filter(t => !t.completed).length} pending · {todos.length} total</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowCreate(!showCreate)} className="btn-primary">
            <Plus className="w-4 h-4" /> New Task
          </button>
          <button onClick={loadTodos} className="btn-secondary" aria-label="Refresh tasks">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {showCreate && (
        <div className="card mb-4 space-y-2">
          <h3 className="font-semibold text-sm">New task</h3>
          <select
            value={createDraft.entry_id}
            onChange={e => setCreateDraft(d => ({ ...d, entry_id: e.target.value }))}
            className="input"
            aria-label="Entry for new task"
          >
            <option value="">Select entry...</option>
            {allEntries.map(e => (
              <option key={e.id} value={e.id}>{e.title}</option>
            ))}
          </select>
          <div className="flex flex-wrap gap-2">
            <select
              value={createDraft.priority}
              onChange={e => setCreateDraft(d => ({ ...d, priority: e.target.value }))}
              className="input text-sm w-auto"
              aria-label="Priority"
            >
              <option value="critical">critical</option>
              <option value="important">important</option>
              <option value="normal">normal</option>
            </select>
            <select
              value={createDraft.effort}
              onChange={e => setCreateDraft(d => ({ ...d, effort: e.target.value }))}
              className="input text-sm w-auto"
              aria-label="Effort"
            >
              <option value="s">S</option>
              <option value="m">M</option>
              <option value="l">L</option>
              <option value="xl">XL</option>
            </select>
            <input
              type="date"
              value={createDraft.due_date}
              onChange={e => setCreateDraft(d => ({ ...d, due_date: e.target.value }))}
              className="input text-sm w-auto"
              aria-label="Due date"
            />
          </div>
          <div className="flex gap-2">
            <button onClick={createTask} disabled={!createDraft.entry_id || saving} className="btn-primary text-sm">
              {saving ? 'Creating...' : 'Create task'}
            </button>
            <button onClick={() => setShowCreate(false)} className="btn-ghost text-sm">Cancel</button>
          </div>
        </div>
      )}

      {dueReminders.length > 0 && (
        <div className="card mb-4 border-red-200 dark:border-red-900">
          <h3 className="font-semibold mb-2 text-red-600 dark:text-red-400">⏰ Due reminders ({dueReminders.length})</h3>
          <div className="space-y-2">
            {dueReminders.map(rem => (
              <div key={rem.id} className="flex items-center justify-between gap-2 text-sm">
                <div className="min-w-0">
                  <p className="font-medium truncate">{rem.title || 'Reminder'}</p>
                  <p className="text-xs text-gray-500 truncate">
                    {new Date(rem.trigger_at).toLocaleString()} · {entryTitles[rem.entry_id] || `Entry #${rem.entry_id}`}
                  </p>
                </div>
                <button
                  onClick={async () => { await api.reminders.notify(rem.id); await loadTodos() }}
                  className="btn-ghost text-xs shrink-0"
                >Dismiss</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {todos.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-500">No tasks yet.</p>
          <p className="text-sm text-gray-400">Add a task from an entry's detail page.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {todos.map(todo => (
            <div key={todo.id} className={`card flex items-center gap-3 ${todo.completed ? 'opacity-60' : ''}`}>
              <input type="checkbox" checked={todo.completed}
                onChange={() => toggleTodo(todo)}
                className="rounded w-5 h-5 shrink-0" />
              <div className="flex-1 min-w-0">
                {onSelectEntry && todo.entry ? (
                  <button
                    onClick={() => onSelectEntry(todo.entry!)}
                    className={`font-medium truncate text-left hover:text-chrono-700 ${todo.completed ? 'line-through text-gray-400' : ''}`}
                    title="Open entry"
                  >
                    {todo.entry.title}
                  </button>
                ) : (
                  <p className={`font-medium truncate ${todo.completed ? 'line-through' : ''}`}>
                    {todo.entry?.title || entryTitles[todo.entry_id] || `Entry #${todo.entry_id}`}
                  </p>
                )}
                {editingId === todo.id ? (
                  <div className="flex flex-wrap gap-2 mt-2">
                    <select value={draft.priority} onChange={e => setDraft(d => ({ ...d, priority: e.target.value }))} className="input text-sm w-auto">
                      <option value="critical">critical</option>
                      <option value="important">important</option>
                      <option value="normal">normal</option>
                    </select>
                    <select value={draft.effort} onChange={e => setDraft(d => ({ ...d, effort: e.target.value }))} className="input text-sm w-auto">
                      <option value="s">S</option>
                      <option value="m">M</option>
                      <option value="l">L</option>
                      <option value="xl">XL</option>
                    </select>
                    <input type="date" value={draft.due_date} onChange={e => setDraft(d => ({ ...d, due_date: e.target.value }))} className="input text-sm w-auto" />
                    <button onClick={() => saveEdit(todo)} className="btn-secondary text-sm">Save</button>
                    <button onClick={() => setEditingId(null)} className="btn-ghost text-sm">Cancel</button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 mt-1">
                    <span className={`badge text-xs ${priorityColors[todo.priority] || priorityColors.normal}`}>{todo.priority}</span>
                    {todo.due_date && <span className="text-xs text-gray-500">Due: {todo.due_date}</span>}
                    <span className="text-xs text-gray-500">Effort: {todo.effort}</span>
                  </div>
                )}
              </div>
              <div className="flex gap-1 shrink-0">
                {editingId !== todo.id && (
                  <button onClick={() => startEdit(todo)} className="icon-btn p-1" aria-label="Edit task" title="Edit task">
                    <Pencil className="w-4 h-4" />
                  </button>
                )}
                <button onClick={async () => {
                  try {
                    await api.todos.delete(todo.entry_id)
                    toast.success('Task removed')
                    await loadTodos()
                  } catch (err: any) {
                    toast.error('Could not remove task: ' + (err?.message || 'unknown error'))
                  }
                }} className="icon-btn p-1 text-red-400" aria-label="Remove task" title="Remove task">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
