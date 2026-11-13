import { useState, useEffect, useRef } from 'react'
import { Search, Download, Upload, AlertTriangle, RefreshCw } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { useToast } from './Toast'
import {
  LIBRARY_PAGE_KEY,
  LIBRARY_SEARCH_KEY,
  LIBRARY_SORT_KEY,
  readStoredNumber,
  readStoredString,
  writeStored,
} from '../lib/libraryState'
import type { Entry, Relationship, Tag } from '../lib/types'

const PAGE_SIZE = 100

interface Props {
  onSelect: (e: Entry) => void
  /** „Toate intrările” vs the notes/ideas facet of the unified library. */
  mode?: 'all' | 'notes'
}

export function EntryList({ onSelect, mode = 'all' }: Props) {
  const toast = useToast()
  const [entries, setEntries] = useState<Entry[]>([])
  const [totalCount, setTotalCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [page, setPage] = useState(() => Math.max(1, readStoredNumber(LIBRARY_PAGE_KEY, 1)))
  const [hasMore, setHasMore] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [sortBy, setSortBy] = useState(() => readStoredString(LIBRARY_SORT_KEY, 'date_desc'))
  const [query, setQuery] = useState(() => readStoredString(LIBRARY_SEARCH_KEY))
  const [searchResults, setSearchResults] = useState<{ entry: Entry; rank: number }[] | null>(null)
  const [searching, setSearching] = useState(false)
  const firstLoad = useRef(true)
  // Notes are a client-side facet of /entries (the server has no study_type
  // filter), so the first visit loads the matching rows once and paginates
  // them locally. Without this, a page of 100 studies hid every note.
  const notesCache = useRef<{ key: string; notes: Entry[] } | null>(null)

  const load = async (nextPage = page, archived = showArchived, force = false) => {
    setLoading(true)
    setError('')
    try {
      const offset = (nextPage - 1) * PAGE_SIZE
      if (mode === 'notes') {
        const key = `${archived ? 1 : 0}|${sortBy}`
        if (force) notesCache.current = null
        let notes = notesCache.current?.key === key ? notesCache.current.notes : null
        if (!notes) {
          const all: Entry[] = []
          let loaded = 0
          while (true) {
            const chunk = await api.entries.listPage({
              limit: 500,
              offset: loaded,
              archived,
              sort: sortBy,
            })
            all.push(...chunk.items)
            if (chunk.items.length === 0 || all.length >= chunk.total) break
            loaded += chunk.items.length
          }
          notes = all.filter(e => !e.study_type)
          notesCache.current = { key, notes }
        }
        setEntries(notes.slice(offset, offset + PAGE_SIZE))
        setTotalCount(notes.length)
        setHasMore(offset + PAGE_SIZE < notes.length)
        setPage(nextPage)
        return
      }
      const { items, total } = await api.entries.listPage({
        limit: PAGE_SIZE,
        offset,
        archived,
        sort: sortBy,
      })
      setEntries(items)
      setTotalCount(total)
      setHasMore(offset + items.length < total)
      setPage(nextPage)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Intrările nu s-au putut încărca')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (firstLoad.current) {
      // Restore the page the user left on when they opened an entry.
      firstLoad.current = false
      void load(page, showArchived)
      return
    }
    setPage(1)
    setSearchResults(null)
    void load(1, showArchived)
  }, [showArchived, sortBy, mode])

  useEffect(() => { writeStored(LIBRARY_PAGE_KEY, page) }, [page])
  useEffect(() => { writeStored(LIBRARY_SORT_KEY, sortBy) }, [sortBy])
  useEffect(() => { writeStored(LIBRARY_SEARCH_KEY, query) }, [query])

  const runSearch = async () => {
    const q = query.trim()
    if (!q) { setSearchResults(null); return }
    setSearching(true)
    try {
      const results = await api.search(q)
      setSearchResults(mode === 'notes' ? results.filter(r => !r.entry.study_type) : results)
    } catch (err) {
      toast.error('Căutarea a eșuat: ' + (err instanceof Error ? err.message : 'eroare necunoscută'))
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  // Restore the search the user had typed before opening an entry.
  useEffect(() => {
    if (query.trim()) void runSearch()
    // Only on mount: later edits are submitted explicitly.
  }, [])

  // Entries imported/downloaded by chat or another tab must appear without a
  // manual browser refresh; refresh the current page when the tab is focused.
  const loadRef = useRef(load)
  loadRef.current = load
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') void loadRef.current(undefined, undefined, true) }
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [])

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
      // are guaranteed to fail. The DB has UNIQUE(title) on entries. The
      // import must see the whole library, not just the first page: page
      // through every entry so a paper beyond offset 500 still counts as a
      // duplicate instead of silently re-importing.
      const existingTags = await api.tags.list().catch(() => [] as Tag[])
      const existingEntries: Entry[] = []
      {
        const IMPORT_PAGE = 500
        let offset = 0
        let total = Infinity
        while (existingEntries.length < total) {
          const page = await api.entries.listPage({ limit: IMPORT_PAGE, offset }).catch(() => null)
          if (!page || page.items.length === 0) break
          existingEntries.push(...page.items)
          total = page.total
          offset += page.items.length
        }
      }
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
      notesCache.current = null
      load(1, showArchived)
    } catch (err: any) {
      toast.error('Importul a eșuat: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const pageCount = totalCount != null ? Math.max(1, Math.ceil(totalCount / PAGE_SIZE)) : null
  const showPager = (pageCount != null && pageCount > 1) || (mode === 'notes' && (page > 1 || hasMore))

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
          <button onClick={() => load(1, showArchived)} className="btn-secondary text-sm">
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
            {mode === 'notes'
              ? totalCount != null && totalCount > entries.length
                ? `Pagina ${page}: ${entries.length} din ${totalCount} notițe`
                : `${entries.length} notițe`
              : totalCount != null && totalCount > entries.length
                ? `Pagina ${page}: ${entries.length} din ${totalCount} intrări`
                : `${entries.length} intrări`}{' '}
            {showArchived ? '(arhivate)' : ''}
          </p>
          {error && (
            <div className="card border-madder/25 text-sm text-madder flex items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" /> {error}
              </span>
              <button onClick={() => load(page, showArchived)} className="btn-ghost text-sm">
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
          {showPager && (
            <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
              <button onClick={() => load(page - 1)} disabled={page <= 1 || loading} className="btn-secondary text-sm">
                ← Pagina anterioară
              </button>
              <span className="text-sm text-gray-500">
                Pagina {page}{pageCount != null ? ` din ${pageCount}` : ''}
              </span>
              <button onClick={() => load(page + 1)} disabled={!hasMore || loading} className="btn-secondary text-sm">
                Pagina următoare →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
