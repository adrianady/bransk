import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import type { TodoItem, Entry } from '../lib/types'

export function TodoList() {
  const [todos, setTodos] = useState<(TodoItem & { entry?: Entry })[]>([])
  const [loading, setLoading] = useState(true)

  const loadTodos = async () => {
    setLoading(true)
    try {
      const items = await api.todos.list()
      const entries = await api.entries.list()
      const enriched = items.map(t => ({
        ...t,
        entry: entries.find(e => e.id === t.entry_id),
      }))
      setTodos(enriched)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadTodos() }, [])

  const toggleTodo = async (todo: TodoItem) => {
    await api.todos.update(todo.entry_id, { completed: !todo.completed })
    await loadTodos()
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
        <button onClick={loadTodos} className="btn-secondary">↻ Refresh</button>
      </div>

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
                <p className={`font-medium truncate ${todo.completed ? 'line-through' : ''}`}>
                  {todo.entry?.title || `Entry #${todo.entry_id}`}
                </p>
                <div className="flex items-center gap-2 mt-1">
                  <span className={`badge text-xs ${priorityColors[todo.priority] || priorityColors.normal}`}>
                    {todo.priority}
                  </span>
                  {todo.due_date && (
                    <span className="text-xs text-gray-500">Due: {todo.due_date}</span>
                  )}
                  <span className="text-xs text-gray-500">Effort: {todo.effort}</span>
                </div>
              </div>
              <button onClick={async () => {
                await api.todos.delete(todo.entry_id)
                await loadTodos()
              }} className="btn-ghost text-red-400 text-xs">Remove</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
