import { useState, useEffect } from 'react'
import { Search, Download, Upload, AlertTriangle, RefreshCw } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { useToast } from './Toast'
import type { Entry, Relationship, Tag } from '../lib/types'

export function EntryList({ onSelect }: { onSelect: (e: Entry) => void }) {
  const toast = useToast()
  const [entries, setEntries] = useState<Entry[]>([])
  const [totalCount, setTotalCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [sortBy, setSortBy] = useState('date_desc')
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<{ entry: Entry; rank: number }[] | null>(null)
  const [searching, setSearching] = useState(false)

  const load = async (nextOffset = 0, archived = showArchived) => {
    setLoading(true)
    setError('')
    try {
      const { items, total } = await api.entries.listPage({ limit: 100, offset: nextOffset, archived, sort: sortBy })
      if (nextOffset === 0) setEntries(items)
      else setEntries(prev => [...prev, ...items])
      setTotalCount(total)
      setHasMore(nextOffset + items.length < total)
      setOffset(nextOffset)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Intrările nu s-au putut încărca')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setQuery('')
    setSearchResults(null)
    setEntries([])
    load(0, showArchived)
  }, [showArchived, sortBy])

  const runSearch = async () => {
    const q = query.trim()
    if (!q) { setSearchResults(null); return }
    setSearching(true)
    try {
      setSearchResults(await api.search(q))
    } catch (err) {
      toast.error('Căutarea a eșuat: ' + (err instanceof Error ? err.message : 'eroare necunoscută'))
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const exportData = async () => {
    try {
      // Export everything, not just the first 500 entries: page through the
      // whole list until the API returns fewer than a full page.
      const entriesData: Entry[] = []
      let offset = 0
      while (true) {
        // include=content: the export needs the full text, which the regular
        // list view no longer ships (preview only).
        const page = await api.entries.list({ limit: 500, offset, include: 'content' })
        entriesData.push(...page)
        if (page.length < 500) break
        offset += 500
      }
      const [relationships, tags] = await Promise.all([
        api.relationships.list().catch(() => [] as Relationship[]),
        api.tags.list().catch(() => [] as Tag[]),
      ])
      const payload = {
        exported_at: new Date().toISOString(),
        version: 1,
        entries: entriesData,
        relationships,
        tags,
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `bransk-export-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      toast.success(`Export complet: ${entriesData.length} intrări salvate`)
    } catch (err) {
      toast.error('Exportul a eșuat: ' + (err instanceof Error ? err.message : 'eroare necunoscută'))
    }
  }

  const importData = async (file: File) => {
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      if (!data.entries || !Array.isArray(data.entries)) {
        toast.error('Fișierul JSON nu conține o listă de intrări validă')
        return
      }

      // Pre-fetch tags and existing entries so we do not attempt inserts that
      // are guaranteed to fail. The DB has UNIQUE(title) on entries.
      const [existingTags, existingEntries] = await Promise.all([
        api.tags.list().catch(() => [] as Tag[]),
        api.entries.list({ limit: 500 }).catch(() => [] as Entry[]),
      ])
      const existingTagNames = new Set<string>(existingTags.map(t => String(t.name).toLowerCase()))
      const existingTitles = new Set<string>(existingEntries.map(e => e.title.trim().toLowerCase().replace(/\s+/g, ' ')))

      const normalizeTitle = (t: any) => String(t).trim().toLowerCase().replace(/\s+/g, ' ')
      // 409 is the backend's duplicate signal. It used to be unreachable: the
      // client threw a plain Error and dropped the status. Keep the text match
      // as a fallback for proxies that rewrite the status.
      const isDuplicateKeyError = (err: any) => {
        if (!err) return false
        if (err instanceof ApiError && err.status === 409) return true
        const detail = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail ?? '')
        const msg = `${err.status ?? ''} ${detail} ${err.message ?? ''}`
        return /duplicate|unique|already exists/i.test(msg)
      }

      const idMap: Record<number, number> = {}
      let created = 0
      let skipped = 0
      let failed = 0

      for (const e of data.entries) {
        if (e.archived) continue
        const normTitle = normalizeTitle(e.title)
        if (!normTitle) { failed++; continue }
        if (existingTitles.has(normTitle)) { skipped++; continue }
        try {
          const entry = await api.entries.create({
            title: String(e.title).slice(0, 500),
            content: e.content || '',
            entry_date: e.entry_date || null,
            tags: e.tags || '',
            source: e.source || '',
            importance: e.importance ?? 3,
            study_type: e.study_type || '',
            authors: e.authors || '',
            quality: e.quality ?? null,
            url_validated: !!e.url_validated,
            design: e.design || '',
            effect_direction: e.effect_direction || '',
            // pdf_path is intentionally not restored: the PDF file itself is
            // not part of the export, so a stale path would render dead links.
          })
          idMap[e.id] = entry.id
          created++
          existingTitles.add(normTitle)
        } catch (err: any) {
          if (isDuplicateKeyError(err)) { skipped++ }
          else failed++
        }
      }
      for (const t of data.tags || []) {
        if (!t.name) continue
        const tagName = String(t.name)
        const normName = tagName.toLowerCase()
        if (existingTagNames.has(normName)) { skipped++; continue }
        try {
          await api.tags.create({ name: tagName, color: t.color })
          existingTagNames.add(normName)
        } catch (err: any) {
          if (isDuplicateKeyError(err)) { skipped++ }
          else failed++
        }
      }
      for (const r of data.relationships || []) {
        const from = idMap[r.from_id] || r.from_id
        const to = idMap[r.to_id] || r.to_id
        if (from && to && from !== to) {
          try { await api.relationships.create({ from_id: from, to_id: to, label: r.label || '' }) }
          catch (err: any) {
            if (isDuplicateKeyError(err)) { skipped++ }
            else failed++
          }
        }
      }
      toast.success(`Importate ${created} intrări, omise ${skipped} duplicate, ${failed} eșuate`)
      setSearchResults(null)
      load(0, showArchived)
    } catch (err: any) {
      toast.error('Importul a eșuat: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  if (loading && entries.length === 0) return <div className="text-center py-8 text-gray-500">Se încarcă…</div>

  return (
    <div className="space-y-4">
      {/* Toolbar: search, archive toggle, export/import */}
      <div className="flex flex-wrap gap-2 items-center">
        <div className="flex flex-1 min-w-[200px] gap-2">
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runSearch()}
            className="input flex-1 min-w-0"
            placeholder="Caută în intrări…"
            aria-label="Caută în intrări"
          />
          <button onClick={runSearch} disabled={searching} className="btn-secondary shrink-0" aria-label="Caută">
            <Search className="w-4 h-4" />
          </button>
        </div>
        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value)}
          className="input w-auto text-sm"
          aria-label="Ordonează intrările"
          title="Ordonare (se aplică server-side, împreună cu paginarea)"
        >
          <option value="date_desc">Data ↓ (nou)</option>
          <option value="date_asc">Data ↑ (vechi)</option>
          <option value="title">Titlu A–Z</option>
          <option value="importance">Importanță ↓</option>
        </select>
        <button
          onClick={() => setShowArchived(!showArchived)}
          className={`btn-ghost text-sm ${showArchived ? 'text-chrono-600' : ''}`}
        >
          {showArchived ? 'Arhivate' : 'Arată arhivate'}
        </button>
        <button onClick={exportData} className="btn-ghost text-sm">
          <Download className="w-4 h-4" /> Exportă
        </button>
        <label className="btn-ghost text-sm cursor-pointer">
          <Upload className="w-4 h-4" /> Importă
          <input
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) importData(f)
              e.target.value = ''
            }}
          />
        </label>
      </div>

      {searching ? (
        <div className="text-center py-8 text-gray-500">Se caută…</div>
      ) : error && entries.length === 0 ? (
        <div className="card text-center py-10 border-madder/25 space-y-3">
          <p className="text-sm text-madder flex items-center justify-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </p>
          <button onClick={() => load(0, showArchived)} className="btn-secondary text-sm">
            <RefreshCw className="w-4 h-4" /> Reîncearcă
          </button>
        </div>
      ) : searchResults !== null ? (
        searchResults.length === 0 ? (
          <div className="card text-center py-10 text-gray-500">Niciun rezultat pentru „{query.trim()}”</div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {searchResults.map(({ entry, rank }) => (
              <button key={entry.id} onClick={() => onSelect(entry)}
                className="card text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors min-w-0"
              >
                <h3 className="font-semibold mb-1 break-words">{entry.title}</h3>
                {entry.entry_date && <p className="text-xs text-gray-500 mb-1">{entry.entry_date}</p>}
                <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-2 break-words">{entry.content_preview || entry.content}</p>
                <p className="text-xs text-gray-400 mt-1">relevanță {rank.toFixed(2)}</p>
              </button>
            ))}
          </div>
        )
      ) : (
        <>
          <p className="text-sm text-gray-500">
            {totalCount != null && totalCount > entries.length
              ? `${entries.length} din ${totalCount} intrări`
              : `${entries.length} intrări`}{' '}
            {showArchived ? '(arhivate)' : ''}
          </p>
          {error && (
            <div className="card border-madder/25 text-sm text-madder flex items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" /> {error}
              </span>
              <button onClick={() => load(0, showArchived)} className="btn-ghost text-sm">
                <RefreshCw className="w-4 h-4" /> Reîncearcă
              </button>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {entries.map(entry => (
              <button key={entry.id} onClick={() => onSelect(entry)}
                className="card text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors min-w-0"
              >
                <h3 className="font-semibold mb-1 break-words">{entry.title}</h3>
                {entry.entry_date && <p className="text-xs text-gray-500 mb-1">{entry.entry_date}</p>}
                <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-2 break-words">{entry.content_preview || entry.content}</p>
                {entry.tags && (
                  <div className="flex gap-1 mt-2 flex-wrap">
                    {entry.tags.split(',').map(t => t.trim()).filter(Boolean).map(t => (
                      <span key={t} className="badge bg-chrono-100 text-chrono-700 dark:bg-chrono-900/50 dark:text-chrono-400">{t}</span>
                    ))}
                  </div>
                )}
              </button>
            ))}
          </div>
          {hasMore && (
            <div className="text-center pt-2">
              <button onClick={() => load(offset + 100)} disabled={loading} className="btn-secondary">
                {loading ? 'Se încarcă…' : 'Încarcă mai multe'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
