import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { BookOpen, FileText, Highlighter, Loader2, Search, StickyNote, Database } from 'lucide-react'
import { api } from '../lib/api'
import type { GlobalSearchResponse } from '../lib/types'

interface Props {
  open: boolean
  onClose: () => void
  onSelectEntry: (entryId: number) => void
}

type Row = { key: string; entryId: number; icon: ReactNode; group: string; label: string; detail: string }

/**
 * Ctrl/Cmd+K palette: one query over entries, studies, personal notes,
 * reader highlights and indexed PDF chunks. Picking a result opens that
 * entry; chunk results say the page so the reader lands on it.
 */
export function CommandPalette({ open, onClose, onSelectEntry }: Props) {
  const [query, setQuery] = useState('')
  const [data, setData] = useState<GlobalSearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setData(null)
      setActive(0)
      setTimeout(() => inputRef.current?.focus(), 30)
    }
  }, [open])

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) { setData(null); return }
    setLoading(true)
    const t = setTimeout(async () => {
      try {
        setData(await api.globalSearch(q))
        setActive(0)
      } catch {
        setData(null)
      } finally {
        setLoading(false)
      }
    }, 250)
    return () => clearTimeout(t)
  }, [query])

  const rows = useMemo<Row[]>(() => {
    if (!data) return []
    const out: Row[] = []
    for (const e of data.entries) {
      out.push({ key: `e${e.id}`, entryId: e.id, icon: <FileText className="w-3.5 h-3.5" />, group: 'Intrări',
                 label: e.title, detail: e.content_preview?.slice(0, 90) || e.entry_date || '' })
    }
    for (const s of data.studies) {
      out.push({ key: `s${s.id}`, entryId: s.id, icon: <BookOpen className="w-3.5 h-3.5" />, group: 'Studii',
                 label: s.title, detail: [s.authors, s.entry_date?.slice(0, 4), s.doi].filter(Boolean).join(' · ') })
    }
    for (const n of data.notes) {
      out.push({ key: `n${n.id}`, entryId: n.entry_id, icon: <StickyNote className="w-3.5 h-3.5" />, group: 'Notițe',
                 label: n.entry_title, detail: (n.page_number ? `pag. ${n.page_number} — ` : '') + n.content.slice(0, 90) })
    }
    for (const h of data.highlights) {
      out.push({ key: `h${h.id}`, entryId: h.entry_id, icon: <Highlighter className="w-3.5 h-3.5 text-yellow-500" />, group: 'Evidențieri',
                 label: h.entry_title, detail: (h.page_number ? `pag. ${h.page_number} — ` : '') + h.quote.slice(0, 90) })
    }
    for (const c of data.chunks) {
      out.push({ key: `d${c.id}`, entryId: c.entry_id, icon: <Database className="w-3.5 h-3.5" />, group: 'PDF',
                 label: c.entry_title, detail: (c.page_number ? `pag. ${c.page_number} — ` : '') + c.preview.slice(0, 90) })
    }
    return out
  }, [data])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose() }
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, rows.length - 1)) }
      if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
      if (e.key === 'Enter') {
        e.preventDefault()
        const row = rows[active]
        if (row) { onClose(); onSelectEntry(row.entryId) }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, rows, active, onClose, onSelectEntry])

  if (!open) return null

  let lastGroup = ''

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh] px-4" role="dialog" aria-label="Căutare globală">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-xl card shadow-2xl overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-gray-200 dark:border-gray-800">
          <Search className="w-4 h-4 text-gray-400 shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Caută în intrări, studii, notițe, evidențieri, PDF-uri…"
            className="flex-1 bg-transparent outline-none text-sm text-gray-800 dark:text-gray-200 placeholder:text-gray-400"
          />
          {loading && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
          <kbd className="hidden sm:inline text-[10px] text-gray-400 border border-gray-200 dark:border-gray-700 rounded px-1 py-0.5">Esc</kbd>
        </div>
        <div className="max-h-[50vh] overflow-y-auto">
          {query.trim().length < 2 && (
            <p className="px-4 py-6 text-xs text-gray-400 text-center">
              Scrie cel puțin două caractere. Ctrl/Cmd+K deschide această căutare de oriunde.
            </p>
          )}
          {query.trim().length >= 2 && !loading && rows.length === 0 && (
            <p className="px-4 py-6 text-xs text-gray-400 text-center">Niciun rezultat pentru „{query.trim()}”.</p>
          )}
          {rows.map((row, i) => {
            const header = row.group !== lastGroup ? row.group : null
            lastGroup = row.group
            return (
              <div key={row.key}>
                {header && (
                  <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider font-bold text-gray-400 bg-gray-50 dark:bg-gray-900/60">
                    {header}
                  </div>
                )}
                <button
                  onMouseEnter={() => setActive(i)}
                  onClick={() => { onClose(); onSelectEntry(row.entryId) }}
                  className={`w-full text-left px-3 py-2 flex items-start gap-2.5 transition-colors ${
                    i === active ? 'bg-chrono-50 dark:bg-chrono-950/40' : 'hover:bg-gray-50 dark:hover:bg-gray-800/60'
                  }`}
                >
                  <span className="mt-0.5 shrink-0 text-gray-400">{row.icon}</span>
                  <span className="min-w-0">
                    <span className="block text-sm text-gray-800 dark:text-gray-200 truncate">{row.label}</span>
                    {row.detail && <span className="block text-xs text-gray-500 truncate">{row.detail}</span>}
                  </span>
                </button>
              </div>
            )
          })}
        </div>
        {rows.length > 0 && (
          <div className="px-3 py-1.5 border-t border-gray-100 dark:border-gray-800 text-[10px] text-gray-400 flex gap-3">
            <span>↑↓ navighează</span><span>Enter deschide</span><span>Esc închide</span>
          </div>
        )}
      </div>
    </div>
  )
}
