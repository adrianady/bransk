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
        // Needed only for the create dropdown; the todo rows carry their own
        // joined entry_title now, and the list ships previews instead of
        // full content since the 2026-11 projection fix.
        api.entries.list({ limit: 500 }),
        api.reminders.due(),
      ])
      const titleMap: Record<number, string> = {}
      items.forEach(t => { if (t.entry_title) titleMap[t.entry_id] = t.entry_title })
      entries.forEach(e => { if (!titleMap[e.id]) titleMap[e.id] = e.title })
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
      toast.error('Sarcina nu s-a putut actualiza: ' + (err?.message || 'eroare necunoscută'))
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
      toast.success('Detaliile sarcinii au fost salvate')
      await loadTodos()
    } catch (err: any) {
      toast.error('Sarcina nu s-a putut salva: ' + (err?.message || 'eroare necunoscută'))
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
      toast.success('Sarcina a fost creată')
      await loadTodos()
    } catch (err: any) {
      toast.error('Sarcina nu s-a putut crea: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setSaving(false)
    }
  }

  const priorityColors: Record<string, string> = {
    critical: 'bg-madder-soft text-madder',
    important: 'bg-brass-soft text-brass',
    normal: 'bg-accent-soft text-accent-strong',
  }

  // Display-only labels: the stored values (critical/important/normal, s/m/l/xl)
  // stay canonical so the API and the colours above keep working.
  const priorityLabels: Record<string, string> = {
    critical: 'critică', important: 'importantă', normal: 'normală',
  }
  const effortLabels: Record<string, string> = { s: 'mic', m: 'mediu', l: 'mare', xl: 'foarte mare' }

  if (loading) return <div className="text-center py-8 text-gray-500">Se încarcă sarcinile…</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Sarcini</h2>
          <p className="text-sm text-gray-500">{todos.filter(t => !t.completed).length} în așteptare · {todos.length} în total</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowCreate(!showCreate)} className="btn-primary">
            <Plus className="w-4 h-4" /> Sarcină nouă
          </button>
          <button onClick={loadTodos} className="btn-secondary" aria-label="Reîmprospătează sarcinile">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {showCreate && (
        <div className="card mb-4 space-y-2">
          <h3 className="font-semibold text-sm">Sarcină nouă</h3>
          <select
            value={createDraft.entry_id}
            onChange={e => setCreateDraft(d => ({ ...d, entry_id: e.target.value }))}
            className="input"
            aria-label="Intrarea pentru sarcina nouă"
          >
            <option value="">Alege intrarea…</option>
            {allEntries.map(e => (
              <option key={e.id} value={e.id}>{e.title}</option>
            ))}
          </select>
          <div className="flex flex-wrap gap-2">
            <select
              value={createDraft.priority}
              onChange={e => setCreateDraft(d => ({ ...d, priority: e.target.value }))}
              className="input text-sm w-auto"
              aria-label="Prioritate"
            >
              <option value="critical">critică</option>
              <option value="important">importantă</option>
              <option value="normal">normală</option>
            </select>
            <select
              value={createDraft.effort}
              onChange={e => setCreateDraft(d => ({ ...d, effort: e.target.value }))}
              className="input text-sm w-auto"
              aria-label="Efort"
            >
              <option value="s">mic</option>
              <option value="m">mediu</option>
              <option value="l">mare</option>
              <option value="xl">foarte mare</option>
            </select>
            <input
              type="date"
              value={createDraft.due_date}
              onChange={e => setCreateDraft(d => ({ ...d, due_date: e.target.value }))}
              className="input text-sm w-auto"
              aria-label="Data scadentă"
            />
          </div>
          <div className="flex gap-2">
            <button onClick={createTask} disabled={!createDraft.entry_id || saving} className="btn-primary text-sm">
              {saving ? 'Se creează…' : 'Creează sarcina'}
            </button>
            <button onClick={() => setShowCreate(false)} className="btn-ghost text-sm">Anulează</button>
          </div>
        </div>
      )}

      {dueReminders.length > 0 && (
        <div className="card mb-4 border-brass/25 bg-brass-soft/50">
          <h3 className="font-semibold mb-2 text-brass">⏰ Memento-uri scadente ({dueReminders.length})</h3>
          <div className="space-y-2">
            {dueReminders.map(rem => (
              <div key={rem.id} className="flex items-center justify-between gap-2 text-sm">
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{rem.title || 'Memento'}</p>
                  <p className="text-xs text-gray-500 truncate">
                    {new Date(rem.trigger_at).toLocaleString()} · {entryTitles[rem.entry_id] || `Intrarea nr. ${rem.entry_id}`}
                  </p>
                </div>
                <button
                  onClick={async () => { await api.reminders.notify(rem.id); await loadTodos() }}
                  className="btn-ghost text-xs shrink-0"
                >Am văzut</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {todos.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-500">Nicio sarcină încă.</p>
          <p className="text-sm text-gray-400">Adaugă o sarcină din pagina de detaliu a unei intrări.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {todos.map(todo => (
            <div key={todo.id} className={`card flex items-center gap-3 ${todo.completed ? 'opacity-60' : ''}`}>
              <span className="touch-40-neg flex items-center justify-center shrink-0">
                <input type="checkbox" checked={todo.completed}
                  onChange={() => toggleTodo(todo)}
                  className="rounded w-5 h-5 shrink-0" />
              </span>
              <div className="flex-1 min-w-0">
                {onSelectEntry && todo.entry ? (
                  <button
                    onClick={() => onSelectEntry(todo.entry!)}
                    className={`font-medium truncate block w-full max-w-full text-left py-0.5 hover:text-accent ${todo.completed ? 'line-through text-gray-400' : ''}`}
                    title="Deschide intrarea"
                  >
                    {todo.entry.title}
                  </button>
                ) : (
                  <p className={`font-medium truncate block w-full max-w-full ${todo.completed ? 'line-through' : ''}`}>
                    {todo.entry?.title || entryTitles[todo.entry_id] || `Intrarea nr. ${todo.entry_id}`}
                  </p>
                )}
                {editingId === todo.id ? (
                  <div className="flex flex-wrap gap-2 mt-2">
                    <select value={draft.priority} onChange={e => setDraft(d => ({ ...d, priority: e.target.value }))} className="input text-sm w-auto">
                      <option value="critical">critică</option>
                      <option value="important">importantă</option>
                      <option value="normal">normală</option>
                    </select>
                    <select value={draft.effort} onChange={e => setDraft(d => ({ ...d, effort: e.target.value }))} className="input text-sm w-auto">
                      <option value="s">mic</option>
                      <option value="m">mediu</option>
                      <option value="l">mare</option>
                      <option value="xl">foarte mare</option>
                    </select>
                    <input type="date" value={draft.due_date} onChange={e => setDraft(d => ({ ...d, due_date: e.target.value }))} className="input text-sm w-auto" />
                    <button onClick={() => saveEdit(todo)} className="btn-secondary text-sm">Salvează</button>
                    <button onClick={() => setEditingId(null)} className="btn-ghost text-sm">Anulează</button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 mt-1">
                    <span className={`badge text-xs ${priorityColors[todo.priority] || priorityColors.normal}`}>{priorityLabels[todo.priority] || todo.priority}</span>
                    {todo.due_date && <span className="text-xs text-gray-500">Scadent: {todo.due_date}</span>}
                    <span className="text-xs text-gray-500">Efort: {effortLabels[todo.effort] || todo.effort}</span>
                  </div>
                )}
              </div>
              <div className="flex gap-1 shrink-0">
                {editingId !== todo.id && (
                  <button onClick={() => startEdit(todo)} className="icon-btn p-1 touch-40-neg" aria-label="Editează sarcina" title="Editează sarcina">
                    <Pencil className="w-4 h-4" />
                  </button>
                )}
                <button onClick={async () => {
                  try {
                    await api.todos.delete(todo.entry_id)
                    toast.success('Sarcina a fost ștearsă')
                    await loadTodos()
                  } catch (err: any) {
                    toast.error('Sarcina nu s-a putut șterge: ' + (err?.message || 'eroare necunoscută'))
                  }
                }} className="icon-btn p-1 touch-40-neg text-madder" aria-label="Șterge sarcina" title="Șterge sarcina">
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
