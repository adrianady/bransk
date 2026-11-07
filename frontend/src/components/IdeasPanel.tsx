import { useEffect, useState } from 'react'
import { Plus, Trash2, Lightbulb, Loader2, Wand2, Pencil, Check, X } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { Idea } from '../lib/types'

const CATEGORIES: { id: string; label: string }[] = [
  { id: 'general', label: 'General' },
  { id: 'intrebare', label: 'Întrebare' },
  { id: 'teorie', label: 'Teorie' },
  { id: 'metoda', label: 'Metodă' },
  { id: 'rezultat', label: 'Rezultat' },
  { id: 'limita', label: 'Limită' },
  { id: 'citire', label: 'De citit' },
]

const CATEGORY_STYLE: Record<string, string> = {
  general: 'badge-muted',
  intrebare: 'badge-accent',
  teorie: 'badge-verdigris',
  metoda: 'badge-accent',
  rezultat: 'badge-verdigris',
  limita: 'badge-brass',
  citire: 'badge-muted',
}

/**
 * The brainstorming pad: half-formed hunches get a place of their own so they
 * never have to look like finished entries on the timeline.
 */
export function IdeasPanel() {
  const toast = useToast()
  const [ideas, setIdeas] = useState<Idea[]>([])
  const [loading, setLoading] = useState(true)
  const [text, setText] = useState('')
  const [category, setCategory] = useState('general')
  const [filter, setFilter] = useState('')
  const [saving, setSaving] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  const [suggestions, setSuggestions] = useState<{ text: string; category: string }[]>([])
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)

  const startEdit = (idea: Idea) => {
    setEditingId(idea.id)
    setEditDraft(idea.text)
  }

  const saveEdit = async (ideaId: number) => {
    const clean = editDraft.trim()
    if (!clean) return
    setSavingEdit(true)
    try {
      const updated = await api.project.ideas.update(ideaId, { text: clean })
      setIdeas(prev => prev.map(i => (i.id === ideaId ? updated : i)))
      setEditingId(null)
    } catch (err) {
      toast.error('Idea nu s-a putut salva: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setSavingEdit(false)
    }
  }

  const load = async (cat = filter) => {
    setLoading(true)
    try {
      setIdeas(await api.project.ideas.list(cat))
    } catch (err) {
      toast.error('Nu am putut încărca ideile: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const add = async (ideaText: string, ideaCategory: string) => {
    const clean = ideaText.trim()
    if (!clean) return
    setSaving(true)
    try {
      const created = await api.project.ideas.create({ text: clean, category: ideaCategory })
      // The filter may hide the new idea; show it anyway, since the user just
      // wrote it and must see that it was kept.
      if (filter && filter !== created.category) setFilter('')
      setIdeas(prev => [created, ...prev.filter(i => filter === '' || i.category === filter || i.id !== created.id)])
      setText('')
    } catch (err) {
      toast.error('Ideea nu s-a salvat: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (id: number) => {
    try {
      await api.project.ideas.remove(id)
      setIdeas(prev => prev.filter(i => i.id !== id))
    } catch (err) {
      toast.error('Ștergerea a eșuat: ' + (err instanceof Error ? err.message : ''))
    }
  }

  const suggest = async () => {
    setSuggesting(true)
    try {
      const res = await api.project.ideas.suggest()
      setSuggestions(res.ideas)
      if (!res.ideas.length) {
        toast.error(res.note || 'Fără idei: verifică titlul temei și cheia LLM.')
      }
    } catch (err) {
      toast.error('Sugestiile au eșuat: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setSuggesting(false)
    }
  }

  const counts = ideas.reduce<Record<string, number>>((acc, i) => {
    acc[i.category] = (acc[i.category] || 0) + 1
    return acc
  }, {})

  return (
    <div className="space-y-4">
      {/* ── Write an idea ── */}
      <div className="card">
        <h3 className="font-semibold mb-1 flex items-center gap-2">
          <Lightbulb className="w-4 h-4" /> Idei de lucru
        </h3>
        <p className="text-sm text-gray-500 mb-3">
          Gânduri în lucru: ce vrei să verifici, ce ți se pare suspect, ce trebuie citit.
          Nu intră în cronologie — sunt schițe, nu intrări.
        </p>

        <div className="flex flex-col sm:flex-row gap-2">
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) add(text, category)
            }}
            rows={2}
            placeholder="Ex: hipocampul mediază relația dintre adversitate și reziliență?"
            className="input flex-1 text-sm resize-y min-w-0"
            aria-label="Textul ideii"
          />
          <div className="flex sm:flex-col gap-2 shrink-0">
            <select value={category} onChange={e => setCategory(e.target.value)} className="input text-sm w-auto">
              {CATEGORIES.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
            <button onClick={() => add(text, category)} disabled={saving || !text.trim()} className="btn-primary text-sm">
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              Adaugă
            </button>
          </div>
        </div>
        <p className="text-xs text-gray-400 mt-1">Ctrl+Enter salvează.</p>
      </div>

      {/* ── Optional: ask the model for ideas grounded in the library ── */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <h3 className="font-semibold">Sugestii pe tema ta</h3>
            <p className="text-sm text-gray-500">
              Cere modelului idei legate strict de studiile din bibliotecă. Costă un apel.
            </p>
          </div>
          <button onClick={suggest} disabled={suggesting} className="btn-secondary text-sm shrink-0">
            {suggesting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
            Sugerează
          </button>
        </div>
        {suggestions.length > 0 && (
          <ul className="mt-3 space-y-2">
            {suggestions.map((s, i) => (
              <li key={i} className="flex items-start gap-2 min-w-0">
                <span className={`badge text-xs shrink-0 ${CATEGORY_STYLE[s.category] || CATEGORY_STYLE.general}`}>
                  {CATEGORIES.find(c => c.id === s.category)?.label || s.category}
                </span>
                <span className="text-sm flex-1 min-w-0 break-words">{s.text}</span>
                <button onClick={() => add(s.text, s.category)} className="btn-ghost text-xs shrink-0"
                  title="Păstrează ideea">
                  <Plus className="w-3.5 h-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── The list ── */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h3 className="font-semibold">
            Ideile tale <span className="text-sm font-normal text-gray-500">({ideas.length})</span>
          </h3>
          <select
            value={filter}
            onChange={e => { setFilter(e.target.value); load(e.target.value) }}
            className="input text-sm w-auto"
            aria-label="Filtrează după categorie"
          >
            <option value="">Toate categoriile</option>
            {CATEGORIES.map(c => (
              <option key={c.id} value={c.id}>
                {c.label}{counts[c.id] ? ` (${counts[c.id]})` : ''}
              </option>
            ))}
          </select>
        </div>

        {loading ? (
          <p className="text-sm text-gray-500 py-4">Se încarcă…</p>
        ) : ideas.length === 0 ? (
          <p className="text-sm text-gray-500 py-4">
            Nicio idee încă. Scrie prima mai sus — chiar și o întrebare fără răspuns e utilă.
          </p>
        ) : (
          <ul className="space-y-2">
            {ideas.map(idea => (
              <li key={idea.id} className="flex items-start gap-2 min-w-0 group">
                <span className={`badge text-xs shrink-0 ${CATEGORY_STYLE[idea.category] || CATEGORY_STYLE.general}`}>
                  {CATEGORIES.find(c => c.id === idea.category)?.label || idea.category}
                </span>
                {editingId === idea.id ? (
                  <div className="flex-1 min-w-0 flex gap-1.5">
                    <input
                      value={editDraft}
                      onChange={e => setEditDraft(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') saveEdit(idea.id)
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                      autoFocus
                      className="input flex-1 min-w-0 text-sm py-1"
                      aria-label="Editează idea"
                    />
                    <button onClick={() => saveEdit(idea.id)} disabled={savingEdit || !editDraft.trim()}
                      className="btn-primary text-xs py-1 px-2 shrink-0" aria-label="Salvează ideea">
                      <Check className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => setEditingId(null)}
                      className="btn-ghost text-xs shrink-0" aria-label="Renunță la editare">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ) : (
                  <>
                    <span className="text-sm flex-1 min-w-0 break-words">{idea.text}</span>
                    <button onClick={() => startEdit(idea)}
                      className="btn-ghost text-xs shrink-0 opacity-60 hover:opacity-100"
                      aria-label={`Editează ideea: ${idea.text.slice(0, 40)}`}>
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => remove(idea.id)}
                      className="btn-ghost text-xs shrink-0 opacity-60 hover:opacity-100"
                      aria-label={`Șterge ideea: ${idea.text.slice(0, 40)}`}>
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
