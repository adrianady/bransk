import { useEffect, useRef, useState } from 'react'
import { Plus, Filter, ChevronDown, ChevronUp } from 'lucide-react'
import 'vis-timeline/styles/vis-timeline-graph2d.min.css'
import { api } from '../lib/api'
import { collectSubjectTags, subjectTags } from '../lib/tags'
import type { TimelineBucket, TimelineBuckets, TimelineEntry, TimelineStats } from '../lib/types'

interface Props {
  onSelectEntry: (entry: TimelineEntry) => void
  onCreateEntry: () => void
}

const importanceColors: Record<number, string> = {
  1: '#8d99a9', 2: '#2f5fa8', 3: '#a8843c', 4: '#a2403f', 5: '#2c6f63'
}

const importanceLabels: Record<number, string> = {
  1: 'Scăzută', 2: 'Medie', 3: 'Importantă', 4: 'Foarte importantă', 5: 'Critică'
}

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const DOMAIN_DEFS: { id: string; label: string; match: RegExp }[] = [
  { id: 'neuro', label: '🧠 Neuroștiințe & Creier', match: /neuro|brain|synap|cortex|cortical|eeg|fmri|glial|astrocyte|myelin|connectome|ketamine/i },
  { id: 'ai', label: '🤖 AI & BCI', match: /ai|machine learning|deepmind|neural network|bci|decoding|brain-computer/i },
  { id: 'cognition', label: '💡 Psihologie & Cogniție', match: /psychol|cognit|memory|emotion|attention|perception|decision|language|working memory/i },
  { id: 'philosophy', label: '🏛️ Filosofie & Conștiință', match: /philosophy|consciousness|dualism|descartes|mind theories|iit|gnw|introspec/i },
  { id: 'mindfulness', label: '🧘 Meditație & Mindfulness', match: /mindfulness|meditation|mbsr/i },
  { id: 'other', label: '📚 Istoric & Alte studii', match: /.*/ },
]

// On a phone the group panel leaves only ~127px for the axis when the full
// names are used ("🧠 Neuroștiințe & Creier"), so the axis labels clip. The
// group id stays the full label; only what the panel draws is shortened.
const COMPACT_DOMAIN_LABELS: Record<string, string> = {
  neuro: '🧠 Neuro',
  ai: '🤖 AI/BCI',
  cognition: '💡 Psihologie',
  philosophy: '🏛️ Filosofie',
  mindfulness: '🧘 Mindfulness',
  other: '📚 Altele',
}

const getDomain = (e: TimelineEntry): string => {
  const text = `${e.tags || ''} ${e.title || ''}`
  for (const def of DOMAIN_DEFS) {
    if (def.match.test(text)) return def.label
  }
  return '📚 Istoric & Alte studii'
}

type GroupByMode = 'domain' | 'none' | 'decade' | 'century' | 'tag'

export function TimelineView({ onSelectEntry, onCreateEntry }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const timelineRef = useRef<any>(null)
  const requestSeq = useRef(0)
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [stats, setStats] = useState<TimelineStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filterTag, setFilterTag] = useState('')
  const [filterFrom, setFilterFrom] = useState('')
  const [filterTo, setFilterTo] = useState('')
  const [allTags, setAllTags] = useState<string[]>([])
  const [visLoaded, setVisLoaded] = useState(false)
  const [search, setSearch] = useState('')
  const [density, setDensity] = useState<TimelineBuckets | null>(null)
  const [bucketSize, setBucketSize] = useState<'century' | 'decade'>('century')
  const [showMobileFilters, setShowMobileFilters] = useState(false)
  // Grouping by scientific domain splits dense clusters into parallel swimlanes,
  // preventing a single period from stacking into a 1400px tall vertical tower.
  const [groupBy, setGroupBy] = useState<GroupByMode>('domain')
  useEffect(() => {
    // Dynamic import vis-timeline (it's a large library)
    import('vis-timeline/standalone')
      .then(() => setVisLoaded(true))
      .catch(err => {
        console.error('[Timeline] vis-timeline failed to load:', err)
        setError('Biblioteca cronologiei nu s-a putut încărca: ' + (err?.message || err))
      })
  }, [])

  const loadEntries = async () => {
    const seq = ++requestSeq.current
    setLoading(true)
    setError('')
    try {
      const data = await api.timeline.get({
        from_date: filterFrom || undefined,
        to_date: filterTo || undefined,
        tags: filterTag || undefined,
        q: search.trim() || undefined,
      })
      if (seq !== requestSeq.current) return
      setEntries(data.entries)
      const s = await api.timeline.stats()
      if (seq !== requestSeq.current) return
      setStats(s)
      const tags = new Set<string>(collectSubjectTags(data.entries))
      // Include tags that exist but have no entry in the current view, so the
      // filter never hides a tag the user can't otherwise select.
      try {
        const all = await api.tags.list()
        all.forEach(t => { if (t.name && !/^-?\d{4}-\d{2}-\d{2}$/.test(t.name)) tags.add(t.name) })
      } catch { /* tag list is best-effort */ }
      if (seq !== requestSeq.current) return
      setAllTags(Array.from(tags).sort())
    } catch (err) {
      if (seq !== requestSeq.current) return
      setError(err instanceof Error ? err.message : 'Cronologia nu s-a putut încărca')
    } finally {
      if (seq === requestSeq.current) setLoading(false)
    }
  }

  useEffect(() => {
    loadEntries()
    return () => { requestSeq.current++ }
  }, [filterTag, filterFrom, filterTo, search])

  // Density strip: counts per period, so the user can see WHERE the mass of
  // entries is before scrolling a 1676-year axis.
  useEffect(() => {
    let alive = true
    api.timeline.buckets({ bucket: bucketSize, tags: filterTag || undefined })
      .then(d => { if (alive) setDensity(d) })
      .catch(() => { if (alive) setDensity(null) })
    return () => { alive = false }
  }, [bucketSize, filterTag, entries.length])

  useEffect(() => {
    if (!containerRef.current || entries.length === 0 || !visLoaded) return
    let isMounted = true

    const initTimeline = async () => {
      const vis = await import('vis-timeline/standalone')
      if (!isMounted) return
      const { Timeline, DataSet } = vis

      if (timelineRef.current) {
        timelineRef.current.destroy()
        timelineRef.current = null
      }

      const items = new DataSet<any>([])
      const groups = new DataSet<any>([])

      // Narrow screens (phones) cannot fit long labels inside the timeline
      // window, so the visible label is shortened while the tooltip keeps the
      // full title.
      const compact = typeof window !== 'undefined' && window.innerWidth < 640
      const maxLabel = compact ? 24 : 70

      const groupKeyOf = (e: TimelineEntry): string | undefined => {
        if (groupBy === 'none') return undefined
        if (groupBy === 'domain') return getDomain(e)
        if (groupBy === 'tag') {
          return subjectTags(e.tags)[0] || 'fără etichetă'
        }
        const year = Number((e.entry_date || '').slice(0, 4))
        if (!year) return 'fără dată'
        if (groupBy === 'century') {
          return year <= 0 ? 'sec. av. Hr.' : `sec. ${((year - 1) / 100 | 0) + 1}`
        }
        return `${Math.floor(year / 10) * 10}–${Math.floor(year / 10) * 10 + 9}`
      }

      entries.forEach(e => {
        if (!e.entry_date) return
        const groupName = groupKeyOf(e)
        const preview = (e.content_preview || '').slice(0, 200)
        const label = e.title.length > maxLabel ? e.title.slice(0, maxLabel - 1) + '…' : e.title
        items.add({
          id: e.id,
          type: 'box',
          content: escapeHtml(label),
          className: `tl-imp-${e.importance || 0}${compact ? ' tl-compact' : ''}`,
          title: `${escapeHtml(e.title)}<br/><b>${escapeHtml(e.entry_date)}</b><br/>${escapeHtml(preview)}`,
          start: e.entry_date,
          ...(groupName ? { group: groupName } : {}),
        })
      })

      if (groupBy === 'domain') {
        DOMAIN_DEFS.forEach((def, index) => {
          const content = compact ? (COMPACT_DOMAIN_LABELS[def.id] || def.label) : def.label
          groups.add({ id: def.label, content, order: index })
        })
      } else if (groupBy !== 'none') {
        const rawGroupNames = [...new Set(entries.filter(e => e.entry_date).map(groupKeyOf).filter(Boolean) as string[])]
        rawGroupNames.sort((a, b) => {
          const na = parseInt(a, 10), nb = parseInt(b, 10)
          return Number.isFinite(na) && Number.isFinite(nb) ? na - nb : a.localeCompare(b)
        })
        rawGroupNames.forEach((g, idx) => groups.add({ id: g, content: g, order: idx }))
      }

      const hasPreModern = entries.some(e => e.entry_date && Number(e.entry_date.slice(0, 4)) < 1950)
      const nowYear = new Date().getFullYear()
      const defaultStart = hasPreModern ? '1960-01-01' : undefined
      const defaultEnd = hasPreModern ? `${nowYear + 1}-01-01` : undefined

      const options = {
        stack: true,
        groupOrder: 'order',
        horizontalScroll: true,
        verticalScroll: true,
        zoomKey: 'ctrlKey' as const,
        groupHeightMode: 'fitItems' as const,
        maxHeight: '480px',
        margin: { item: { horizontal: 4, vertical: 3 }, axis: 4 },
        start: filterFrom || defaultStart,
        end: filterTo || defaultEnd,
      }

      const containerElement = containerRef.current
      if (containerElement) {
        const tl = groupBy !== 'none'
          ? new Timeline(containerElement, items, groups, options)
          : new Timeline(containerElement, items, options)
        timelineRef.current = tl

        // Set modern camera window immediately on load if no custom filter
        if (hasPreModern && !filterFrom && !filterTo) {
          setTimeout(() => {
            if (timelineRef.current) {
              timelineRef.current.setWindow(new Date('1960-01-01'), new Date(`${nowYear + 1}-01-01`), { animation: false })
            }
          }, 40)
        }

        tl.on('select', (props: { items: number[] }) => {
          if (props.items.length > 0) {
            const entry = entries.find(e => e.id === props.items[0])
            if (entry) onSelectEntry(entry)
          }
        })
      }
    }

    initTimeline()

    return () => {
      isMounted = false
      if (timelineRef.current) {
        timelineRef.current.destroy()
        timelineRef.current = null
      }
    }
  }, [entries, stats, visLoaded, loading, onSelectEntry, groupBy])

  // Jump straight to an era instead of zooming by hand across sixteen
  // centuries; the values match the density strip's century keys.
  const applyEra = (era: 'modern' | 'all' | '20' | '21' | 'recent') => {
    const now = new Date()
    const nowYear = now.getFullYear()
    if (era === 'all') {
      setFilterFrom('')
      setFilterTo('')
      timelineRef.current?.fit({ animation: true })
      return
    }
    let start = '1950-01-01'
    let end = `${nowYear + 1}-01-01`
    if (era === '20') { start = '1901-01-01'; end = '2000-12-31' }
    if (era === '21') { start = '2001-01-01'; end = `${nowYear + 1}-01-01` }
    if (era === 'recent') {
      const then = new Date(nowYear - 10, now.getMonth(), now.getDate())
      start = then.toISOString().slice(0, 10)
      end = `${nowYear + 1}-01-01`
    }
    setFilterFrom(start)
    setFilterTo(end)
    if (timelineRef.current) {
      timelineRef.current.setWindow(new Date(start), new Date(end), { animation: true })
    }
  }

  const applyBucket = (b: TimelineBucket) => {
    setFilterFrom(b.start)
    setFilterTo(b.end)
  }

  const importanceColor = (imp: number) => {
    const colors: Record<number, string> = { 1: 'bg-muted/60', 2: 'bg-accent', 3: 'bg-brass', 4: 'bg-madder', 5: 'bg-verdigris' }
    return colors[imp] || 'bg-accent'
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <h2 className="text-xl font-bold">Cronologie</h2>
          {stats && <p className="text-xs sm:text-sm text-gray-500">{stats.total} intrări · {stats.earliest || 'nicio dată'} → {stats.latest || 'nicio dată'}</p>}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowMobileFilters(v => !v)}
            className="sm:hidden btn-secondary text-xs flex items-center gap-1.5 py-1.5 px-2.5"
            aria-expanded={showMobileFilters}
          >
            <Filter className="w-3.5 h-3.5 text-chrono-600 dark:text-chrono-400" />
            <span>{showMobileFilters ? 'Ascunde' : 'Filtre'}</span>
            {showMobileFilters ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
          <button onClick={onCreateEntry} className="btn-primary text-xs sm:text-sm py-1.5 px-2.5 flex items-center gap-1">
            <Plus className="w-4 h-4" />
            <span>Intrare nouă</span>
          </button>
        </div>
      </div>

      {/* Era presets: one click to the period that holds most of the entries. */}
      <div className="flex gap-1.5 overflow-x-auto no-scrollbar pb-1 mb-2">
        {([
          ['modern', 'Modern (1950–azi)'],
          ['21', 'Sec. XXI'],
          ['20', 'Sec. XX'],
          ['recent', 'Ultimii 10 ani'],
          ['all', 'Toată istoria (350–2026)'],
        ] as const).map(([era, label]) => (
          <button
            key={era}
            onClick={() => applyEra(era)}
            className="btn-secondary text-xs whitespace-nowrap shrink-0 py-1 px-2.5"
            aria-label={`Arată ${label}`}
          >{label}</button>
        ))}
      </div>

      {/* Grouping: splitting into scientific domain lanes spreads studies evenly across time tracks */}
      <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-1 mb-2">
        <span className="text-xs text-gray-500 dark:text-gray-400 shrink-0">Grupează:</span>
        {([
          ['domain', 'Domeniu'],
          ['none', 'Continuă'],
          ['decade', 'Deceniu'],
          ['century', 'Secol'],
          ['tag', 'Etichetă'],
        ] as const).map(([mode, label]) => (
          <button
            key={mode}
            onClick={() => setGroupBy(mode)}
            className={`btn-ghost text-xs whitespace-nowrap shrink-0 py-1 px-2 ${groupBy === mode ? 'text-accent-strong font-semibold bg-accent-soft' : ''}`}
            aria-pressed={groupBy === mode}
            aria-label={`Grupează cronologia după ${label.toLowerCase()}`}
          >{label}</button>
        ))}
      </div>

      {/* Advanced filters & Density strip (collapsible on mobile) */}
      <div className={`${showMobileFilters ? 'block' : 'hidden sm:block'} space-y-3 mb-3`}>
        {/* Density strip: where the entries actually are. */}
        {density && density.buckets.length > 0 && (
          <div className="card">
            <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400">
                Unde sunt intrările ({density.total} cu dată)
              </p>
              <div className="flex gap-1">
                <button
                  onClick={() => setBucketSize('century')}
                  className={`btn-ghost text-xs ${bucketSize === 'century' ? 'text-chrono-600' : ''}`}
                  aria-pressed={bucketSize === 'century'}
                >Secole</button>
                <button
                  onClick={() => setBucketSize('decade')}
                  className={`btn-ghost text-xs ${bucketSize === 'decade' ? 'text-chrono-600' : ''}`}
                  aria-pressed={bucketSize === 'decade'}
                >Decenii</button>
              </div>
            </div>
            <div className="flex items-end gap-1 h-16 overflow-x-auto pb-1" role="group" aria-label="Densitatea intrărilor pe perioade">
              {density.buckets.map(b => {
                const h = Math.max(6, Math.round((b.count / Math.max(1, density.max_count)) * 56))
                const active = filterFrom === b.start && filterTo === b.end
                return (
                  <button
                    key={b.key}
                    onClick={() => (active ? applyEra('all') : applyBucket(b))}
                    className={`shrink-0 flex flex-col items-center justify-end min-w-[34px] rounded-b transition-colors ${active ? 'bg-accent-soft' : 'hover:bg-surface-2'}`}
                    style={{ height: 64 }}
                    title={`${b.count} intrări${b.top_tags.length ? ' · ' + b.top_tags.join(', ') : ''}`}
                    aria-label={`${b.count} intrări în perioada ${b.key}; apasă pentru a filtra`}
                    aria-pressed={active}
                  >
                    <span className="text-[10px] text-gray-500 leading-none mb-0.5">{b.count}</span>
                    <span className="w-5 rounded-t bg-accent" style={{ height: h }} />
                    <span className="text-[10px] text-gray-400 leading-none mt-0.5">{b.key}</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {/* Search + filters */}
        <div className="flex flex-wrap gap-2">
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="input flex-1 min-w-[160px] text-sm"
            placeholder="Caută în titlu sau conținut…"
            aria-label="Caută în intrarea cronologiei după titlu sau conținut"
          />
          <div className="flex items-center gap-1.5">
            <label htmlFor="timeline-filter-from" className="text-xs text-gray-600 dark:text-gray-400">De la</label>
            <input id="timeline-filter-from" type="date" value={filterFrom} onChange={e => setFilterFrom(e.target.value)}
              className="input w-auto text-xs py-1" />
          </div>
          <div className="flex items-center gap-1.5">
            <label htmlFor="timeline-filter-to" className="text-xs text-gray-600 dark:text-gray-400">Până la</label>
            <input id="timeline-filter-to" type="date" value={filterTo} onChange={e => setFilterTo(e.target.value)}
              className="input w-auto text-xs py-1" />
          </div>
          <select value={filterTag} onChange={e => setFilterTag(e.target.value)}
            className="input w-auto text-xs py-1">
            <option value="">Toate etichetele</option>
            {allTags.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          {(filterFrom || filterTo || filterTag || search) && (
            <button onClick={() => { setFilterFrom(''); setFilterTo(''); setFilterTag(''); setSearch('') }}
              className="btn-ghost text-xs">Golește</button>
          )}
        </div>
      </div>

      <p className="text-sm text-gray-500 mb-2">
        {entries.length} din {stats?.total ?? entries.length} intrări în vedere
        {(filterFrom || filterTo) && <> · interval {filterFrom || 'început'} → {filterTo || 'azi'}</>}
      </p>

      {/* Timeline */}
      {loading ? (
        <div className="text-center py-8 text-gray-500">Se încarcă cronologia…</div>
      ) : error && entries.length === 0 ? (
        <div className="card text-center py-8 border-madder/25">
          <p className="text-madder mb-2">⚠️ {error}</p>
          <button onClick={loadEntries} className="btn-secondary">↻ Reîncearcă</button>
        </div>
      ) : entries.filter(e => e.entry_date).length === 0 ? (
        entries.length > 0 ? (
          <div className="card text-center py-8">
            <p className="text-gray-500 mb-2">Nicio intrare cu dată nu corespunde filtrelor actuale.</p>
            <button
              onClick={() => { setFilterFrom(''); setFilterTo(''); setFilterTag('') }}
              className="btn-ghost text-sm"
            >Golește filtrele</button>
          </div>
        ) : (
          <div className="card text-center py-8">
            <p className="text-gray-500 mb-2">Nicio intrare cu dată încă.</p>
            <p className="text-sm text-gray-400">Creează o intrare cu dată ca să apară aici.</p>
            <button onClick={onCreateEntry} className="btn-primary mt-4">Creează prima intrare</button>
          </div>
        )
      ) : (
        <>
          {/* Importance legend */}
          <div className="flex flex-wrap gap-3 mb-2 px-1 text-xs text-gray-500">
            {[1, 2, 3, 4, 5].map(n => (
              <span key={n} className="flex items-center gap-1.5">
                <span className="w-3 h-3 rounded-full inline-block" style={{ background: importanceColors[n] }} />
                {importanceLabels[n]}
              </span>
            ))}
          </div>
          {/* Interactive Timeline Graph — fully responsive across desktop and mobile */}
          <div className="card block overflow-hidden p-1 sm:p-3">
            <div
              ref={containerRef}
              className="cg-timeline w-full"
              style={{ width: '100%', height: '420px', maxHeight: '520px' }}
            />
          </div>
        </>
      )}

      {/* Entry list below timeline */}
      <div className="mt-6 space-y-2">
        <h3 className="font-semibold text-sm text-gray-500 uppercase tracking-wider mb-3">
          Intrările afișate ({entries.length})
        </h3>
        {entries.map(entry => (
          <button key={entry.id} onClick={() => onSelectEntry(entry)}
            className="w-full card text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors flex items-center gap-3"
          >
            <div className={`w-3 h-3 rounded-full shrink-0 ${importanceColor(entry.importance)}`} />
            <div className="flex-1 min-w-0">
              {/* Two lines, not one: measured on a 390px phone the list gives a
                  title ~161px while the longest needs ~325px, so a single-line
                  truncate hid half of 131 titles. */}
              <p className="font-medium line-clamp-2 break-words">{entry.title}</p>
              {entry.tags && (
                <div className="flex gap-1 mt-0.5 flex-wrap">
                  {subjectTags(entry.tags).map(t => (
                    <span key={t} className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">{t}</span>
                  ))}
                </div>
              )}
            </div>
            <div className="text-right shrink-0">
              {entry.entry_date && <p className="text-xs text-gray-500">{entry.entry_date}</p>}
              {entry.relationship_count > 0 && (
                <p className="text-xs text-chrono-500">🔗 {entry.relationship_count}</p>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
