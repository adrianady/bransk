import { useEffect, useRef, useState } from 'react'
import 'vis-timeline/styles/vis-timeline-graph2d.min.css'
import { api } from '../lib/api'
import type { TimelineEntry, TimelineStats } from '../lib/types'

interface Props {
  onSelectEntry: (entry: TimelineEntry) => void
  onCreateEntry: () => void
}

const importanceColors: Record<number, string> = {
  1: '#94a3b8', 2: '#6366f1', 3: '#f59e0b', 4: '#ef4444', 5: '#8b5cf6'
}

const importanceLabels: Record<number, string> = {
  1: 'Low', 2: 'Medium', 3: 'Important', 4: 'Very important', 5: 'Critical'
}

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export function TimelineView({ onSelectEntry, onCreateEntry }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const timelineRef = useRef<any>(null)
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [stats, setStats] = useState<TimelineStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [filterTag, setFilterTag] = useState('')
  const [filterFrom, setFilterFrom] = useState('')
  const [filterTo, setFilterTo] = useState('')
  const [allTags, setAllTags] = useState<string[]>([])
  const [visLoaded, setVisLoaded] = useState(false)

  useEffect(() => {
    // Dynamic import vis-timeline (it's a large library)
    import('vis-timeline/standalone').then(() => setVisLoaded(true))
  }, [])

  const loadEntries = async () => {
    setLoading(true)
    try {
      const data = await api.timeline.get({
        from_date: filterFrom || undefined,
        to_date: filterTo || undefined,
        tags: filterTag || undefined,
      })
      setEntries(data.entries)
      const s = await api.timeline.stats()
      setStats(s)
      const tags = new Set<string>()
      data.entries.forEach(e => e.tags.split(',').map(t => t.trim()).filter(Boolean).forEach(t => tags.add(t)))
      setAllTags(Array.from(tags).sort())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadEntries() }, [filterTag, filterFrom, filterTo])

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

      entries.forEach(e => {
        if (!e.entry_date) return
        const groupName = e.tags.split(',')[0]?.trim() || 'default'
        const color = importanceColors[e.importance] || '#6366f1'
        const preview = (e.content_preview || '').slice(0, 200)
        items.add({
          id: e.id,
          content: `<div style="font-weight:600;font-size:13px;color:${color}">${escapeHtml(e.title)}</div>`,
          title: `${escapeHtml(e.title)}<br/><b>${escapeHtml(e.entry_date)}</b><br/>${escapeHtml(preview)}`,
          start: e.entry_date,
          group: groupName,
        })
      })

      const groupNames = [...new Set(entries.filter(e => e.entry_date).map(e => e.tags.split(',')[0]?.trim() || 'default'))]
      groupNames.forEach(g => groups.add({ id: g, content: g }))

      const options = {
        stack: true,
        groupOrder: 'content',
        horizontalScroll: true,
        zoomKey: 'ctrlKey' as const,
        height: '500px',
        start: stats?.earliest || undefined,
        end: stats?.latest || undefined,
      }

      const containerElement = containerRef.current
      if (containerElement) {
        const tl = new Timeline(containerElement, items, groups, options)
        timelineRef.current = tl

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
  }, [entries, stats, visLoaded, onSelectEntry])

  const importanceColor = (imp: number) => {
    const colors: Record<number, string> = { 1: 'bg-slate-400', 2: 'bg-indigo-500', 3: 'bg-amber-500', 4: 'bg-red-500', 5: 'bg-purple-500' }
    return colors[imp] || 'bg-indigo-500'
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Timeline</h2>
          {stats && <p className="text-sm text-gray-500">{stats.total} entries · {stats.earliest || 'no dates'} → {stats.latest || 'no dates'}</p>}
        </div>
        <button onClick={onCreateEntry} className="btn-primary">+ New Entry</button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <input type="date" value={filterFrom} onChange={e => setFilterFrom(e.target.value)}
          className="input w-auto" placeholder="From date" />
        <input type="date" value={filterTo} onChange={e => setFilterTo(e.target.value)}
          className="input w-auto" placeholder="To date" />
        <select value={filterTag} onChange={e => setFilterTag(e.target.value)}
          className="input w-auto">
          <option value="">All tags</option>
          {allTags.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        {(filterFrom || filterTo || filterTag) && (
          <button onClick={() => { setFilterFrom(''); setFilterTo(''); setFilterTag('') }}
            className="btn-ghost text-sm">Clear filters</button>
        )}
      </div>

      {/* Timeline */}
      {loading ? (
        <div className="text-center py-8 text-gray-500">Loading timeline...</div>
      ) : entries.filter(e => e.entry_date).length === 0 ? (
        <div className="card text-center py-8">
          <p className="text-gray-500 mb-2">No dated entries on the timeline yet.</p>
          <p className="text-sm text-gray-400">Create an entry with a date to see it here.</p>
          <button onClick={onCreateEntry} className="btn-primary mt-4">Create your first entry</button>
        </div>
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
          <div className="card">
            <div ref={containerRef} style={{ width: '100%', minHeight: '500px' }} />
          </div>
        </>
      )}

      {/* Entry list below timeline */}
      <div className="mt-6 space-y-2">
        <h3 className="font-semibold text-sm text-gray-500 uppercase tracking-wider mb-3">
          All Entries ({entries.length})
        </h3>
        {entries.map(entry => (
          <button key={entry.id} onClick={() => onSelectEntry(entry)}
            className="w-full card text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors flex items-center gap-3"
          >
            <div className={`w-3 h-3 rounded-full shrink-0 ${importanceColor(entry.importance)}`} />
            <div className="flex-1 min-w-0">
              <p className="font-medium truncate">{entry.title}</p>
              {entry.tags && (
                <div className="flex gap-1 mt-0.5 flex-wrap">
                  {entry.tags.split(',').map(t => t.trim()).filter(Boolean).map(t => (
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
