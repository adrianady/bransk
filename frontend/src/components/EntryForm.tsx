import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api'
import type { Entry, Tag } from '../lib/types'

interface Props {
  onClose: () => void
  onSaved: (entry: Entry) => void
  editEntry?: Entry | null
}

export function EntryForm({ onClose, onSaved, editEntry }: Props) {
  const [title, setTitle] = useState(editEntry?.title || '')
  const [content, setContent] = useState(editEntry?.content || '')
  const [entryDate, setEntryDate] = useState(editEntry?.entry_date || '')
  const [tags, setTags] = useState(editEntry?.tags || '')
  const [source, setSource] = useState(editEntry?.source || '')
  const [importance, setImportance] = useState(editEntry?.importance || 3)
  const [saving, setSaving] = useState(false)
  const [allTags, setAllTags] = useState<Tag[]>([])
  const [showTagPicker, setShowTagPicker] = useState(false)

  useEffect(() => {
    api.tags.list().then(setAllTags)
  }, [])

  const addTag = (tagName: string) => {
    const current = tags.split(',').map(t => t.trim()).filter(Boolean)
    if (!current.includes(tagName)) {
      current.push(tagName)
      setTags(current.join(', '))
    }
    setShowTagPicker(false)
  }

  const handleSubmit = async () => {
    if (!title.trim()) return
    setSaving(true)
    try {
      if (editEntry) {
        const updated = await api.entries.update(editEntry.id, {
          title: title.trim(),
          content,
          entry_date: entryDate || null,
          tags,
          source,
          importance,
        })
        onSaved(updated)
      } else {
        const created = await api.entries.create({
          title: title.trim(),
          content,
          entry_date: entryDate || null,
          tags,
          source,
          importance,
        })
        onSaved(created)
      }
    } catch (err: any) {
      alert('Error saving: ' + err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-bold">{editEntry ? 'Edit Entry' : 'New Entry'}</h2>
          <button onClick={onClose} className="btn-ghost p-1 text-xl">✕</button>
        </div>

        <div className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Title *</label>
            <input value={title} onChange={e => setTitle(e.target.value)}
              className="input" placeholder="Entry title" autoFocus />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Date</label>
              <input type="date" value={entryDate} onChange={e => setEntryDate(e.target.value)}
                className="input" />
              <p className="text-xs text-gray-500 mt-0.5">When this event/info happened</p>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Importance</label>
              <div className="flex gap-2">
                {[1, 2, 3, 4, 5].map(n => (
                  <button key={n} onClick={() => setImportance(n)}
                    className={`w-8 h-8 rounded-full text-xs font-bold transition-colors ${
                      importance === n
                        ? 'bg-chrono-600 text-white ring-2 ring-chrono-300'
                        : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200'
                    }`}
                  >{n}</button>
                ))}
              </div>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Tags</label>
            <div className="flex gap-2 items-center">
              <input value={tags} onChange={e => setTags(e.target.value)}
                className="input flex-1" placeholder="science, history, physics" />
              <div className="relative">
                <button onClick={() => setShowTagPicker(!showTagPicker)}
                  className="btn-secondary text-sm">+ Pick</button>
                {showTagPicker && (
                  <div className="absolute right-0 top-full mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-2 z-10 w-48 max-h-48 overflow-y-auto">
                    {allTags.map(tag => (
                      <button key={tag.id} onClick={() => addTag(tag.name)}
                        className="block w-full text-left px-2 py-1 text-sm rounded hover:bg-gray-100 dark:hover:bg-gray-700"
                      >{tag.name}</button>
                    ))}
                    {allTags.length === 0 && <p className="text-xs text-gray-500 p-1">No tags yet</p>}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Source</label>
            <input value={source} onChange={e => setSource(e.target.value)}
              className="input" placeholder="Book, article, URL, or person" />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Content (Markdown)</label>
            <textarea value={content} onChange={e => setContent(e.target.value)}
              className="input min-h-[200px] font-mono text-sm"
              placeholder="Write your notes here... Supports markdown"
              rows={8}
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 p-4 border-t border-gray-200 dark:border-gray-700">
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={handleSubmit} disabled={saving || !title.trim()}
            className="btn-primary disabled:opacity-50"
          >
            {saving ? 'Saving...' : editEntry ? 'Update' : 'Create Entry'}
          </button>
        </div>
      </div>
    </div>
  )
}
