import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { GraphData, GraphNode, TimelineEntry } from '../lib/types'

interface Props {
  onSelectEntry: (entry: TimelineEntry) => void
}

const importanceColors: Record<number, string> = {
  1: '#94a3b8', 2: '#6366f1', 3: '#f59e0b', 4: '#ef4444', 5: '#8b5cf6'
}

const importanceLabels: Record<number, string> = {
  1: 'Low', 2: 'Medium', 3: 'Important', 4: 'Very important', 5: 'Critical'
}

const tagPalette = [
  '#0ea5e9', '#10b981', '#f97316', '#ec4899', '#84cc16',
  '#14b8a6', '#a855f7', '#f43f5e', '#eab308', '#3b82f6',
]

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

type Period = 'all' | 'year' | 'decade'

export function GraphView({ onSelectEntry }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<any>(null)
  const requestSeq = useRef(0)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [visLoaded, setVisLoaded] = useState(false)
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [tagFilter, setTagFilter] = useState('')
  const [minImportance, setMinImportance] = useState(0)
  const [period, setPeriod] = useState<Period>('all')
  const [physics, setPhysics] = useState(true)
  const [isDark, setIsDark] = useState(() => document.documentElement.classList.contains('dark'))

  useEffect(() => {
    import('vis-network/standalone').then(() => setVisLoaded(true))
  }, [])

  // Keep canvas text colors in sync with the active theme.
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.classList.contains('dark'))
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  const loadData = async () => {
    const seq = ++requestSeq.current
    setLoading(true)
    setError('')
    try {
      const [graph, timeline] = await Promise.all([
        api.relationships.graph(),
        api.timeline.get(),
      ])
      if (seq !== requestSeq.current) return
      setGraphData(graph)
      setEntries(timeline.entries)
    } catch (err) {
      if (seq !== requestSeq.current) return
      setError(err instanceof Error ? err.message : 'Failed to load the graph')
    } finally {
      if (seq === requestSeq.current) setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
    return () => { requestSeq.current++ }
  }, [])

  const allTags = useMemo(() => {
    if (!graphData) return []
    const set = new Set<string>()
    graphData.nodes.forEach(n => n.tags.split(',').map(t => t.trim()).filter(Boolean).forEach(t => set.add(t)))
    return [...set].sort()
  }, [graphData])

  const tagColors = useMemo(() => {
    const map: Record<string, string> = {}
    allTags.forEach((t, i) => { map[t] = tagPalette[i % tagPalette.length] })
    return map
  }, [allTags])

  const visible = useMemo(() => {
    if (!graphData) return { nodes: [], edges: [] }
    let maxDate = 0
    graphData.nodes.forEach(n => {
      const t = n.entry_date ? Date.parse(n.entry_date) : NaN
      if (!isNaN(t) && t > maxDate) maxDate = t
    })
    const cutoff =
      period === 'year' ? maxDate - 365 * 86400000
      : period === 'decade' ? maxDate - 3650 * 86400000
      : 0
    const nodes = graphData.nodes.filter(n => {
      if (tagFilter && !n.tags.split(',').map(t => t.trim()).includes(tagFilter)) return false
      if (minImportance > 0 && n.value < minImportance) return false
      if (cutoff > 0) {
        const t = n.entry_date ? Date.parse(n.entry_date) : NaN
        if (isNaN(t) || t < cutoff) return false
      }
      return true
    })
    const nodeIds = new Set(nodes.map(n => n.id))
    const edges = graphData.edges.filter(e => nodeIds.has(e.from) && nodeIds.has(e.to))
    return { nodes, edges }
  }, [graphData, tagFilter, minImportance, period])

  useEffect(() => {
    if (!graphData || !visLoaded) return
    let isMounted = true

    const initNetwork = async () => {
      const vis = await import('vis-network/standalone')
      if (!isMounted || !containerRef.current) return
      const { Network } = vis

      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }

      const nodes = new vis.DataSet(visible.nodes.map(n => {
        const firstTag = n.tags.split(',')[0]?.trim()
        const date = n.entry_date ? `<br/><b>${escapeHtml(n.entry_date)}</b>` : ''
        return {
          ...n,
          color: firstTag ? tagColors[firstTag] || n.color : n.color,
          title: `<b>${escapeHtml(n.title)}</b>${date}<br/>${escapeHtml((n.content_preview || '').slice(0, 200))}`,
          borderWidth: 1,
        }
      }))
      const edges = new vis.DataSet(visible.edges.map(e => ({
        ...e,
        title: e.label ? `<b>${escapeHtml(e.label)}</b>` : undefined,
        font: { size: 10, align: 'middle', color: isDark ? '#9ca3af' : '#94a3b8' },
      })))

      const options = {
        nodes: {
          shape: 'dot',
          scaling: { min: 12, max: 32, label: { min: 10, max: 22, drawThreshold: 14 } },
          font: { size: 12, face: 'Inter, sans-serif', color: isDark ? '#e5e7eb' : '#374151' },
          // Wrap long study/theory titles instead of clipping them.
          widthConstraint: { maximum: 260 },
          shadow: true,
          labelHighlightBold: true,
        },
        edges: {
          width: 1.2,
          smooth: { enabled: true, type: 'continuous', roundness: 0.5 },
          arrows: { to: { enabled: true, scaleFactor: 0.6 } },
        },
        physics: {
          enabled: physics,
          stabilization: { iterations: physics ? 200 : 0, updateInterval: 25 },
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {
            gravitationalConstant: -70,
            centralGravity: 0.004,
            springLength: 220,
            springConstant: 0.05,
            damping: 0.4,
          },
        },
        interaction: { hover: true, tooltipDelay: 150, zoomView: true, dragView: true },
        layout: { improvedLayout: true, randomSeed: 42 },
      }

      const containerElement = containerRef.current
      if (containerElement) {
        const net = new Network(containerElement, { nodes, edges }, options)
        networkRef.current = net

        // Fit the viewport once physics settles so no node is clipped at the
        // edges and the layout fills the container instead of leaving dead space.
        net.once('stabilizationIterationsDone', () => {
          if (networkRef.current) networkRef.current.fit({ animation: false })
        })
        setTimeout(() => {
          if (networkRef.current) networkRef.current.fit({ animation: false })
        }, 350)

        net.on('click', (props: { nodes: number[] }) => {
          if (props.nodes.length > 0) {
            const nodeId = props.nodes[0]
            const entry = entries.find(e => e.id === nodeId)
            if (entry) onSelectEntry(entry)
          }
        })
      }
    }

    initNetwork()

    return () => {
      isMounted = false
      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }
    }
  }, [graphData, visLoaded, entries, onSelectEntry, visible, tagColors, physics, isDark])

  if (loading) return <div className="text-center py-8 text-gray-500">Loading graph...</div>

  if (error && !graphData) {
    return (
      <div className="card text-center py-12 border-red-200 dark:border-red-900">
        <p className="text-red-600 dark:text-red-400 mb-2">⚠️ {error}</p>
        <button onClick={loadData} className="btn-secondary">↻ Retry</button>
      </div>
    )
  }

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div className="card text-center py-12">
        <p className="text-gray-500 mb-2">No graph data yet.</p>
        <p className="text-sm text-gray-400">Create entries and link them to see the knowledge graph.</p>
        <button onClick={loadData} className="btn-secondary mt-4">↻ Refresh</button>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Knowledge Graph</h2>
          <p className="text-sm text-gray-500">{visible.nodes.length} of {graphData.nodes.length} nodes · {visible.edges.length} edges</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => {
            if (networkRef.current) networkRef.current.fit({ animation: true })
          }} className="btn-secondary">⛶ Fit</button>
          <button onClick={() => setPhysics(!physics)} className={`btn-ghost text-sm ${physics ? 'text-chrono-600' : ''}`}>
            {physics ? '⚛️ Physics on' : '⚛️ Physics off'}
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-3">
        <select value={tagFilter} onChange={e => setTagFilter(e.target.value)} className="input w-auto text-sm">
          <option value="">All tags</option>
          {allTags.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={minImportance} onChange={e => setMinImportance(Number(e.target.value))} className="input w-auto text-sm">
          <option value={0}>Any importance</option>
          <option value={3}>3★ and up</option>
          <option value={4}>4★ and up</option>
          <option value={5}>5★ only</option>
        </select>
        <select value={period} onChange={e => setPeriod(e.target.value as Period)} className="input w-auto text-sm">
          <option value="all">All time</option>
          <option value="year">Last year</option>
          <option value="decade">Last decade</option>
        </select>
        {(tagFilter || minImportance > 0 || period !== 'all') && (
          <button onClick={() => { setTagFilter(''); setMinImportance(0); setPeriod('all') }} className="btn-ghost text-sm">Clear</button>
        )}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mb-2 px-1 text-xs text-gray-500">
        {[1, 2, 3, 4, 5].map(n => (
          <span key={n} className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full inline-block" style={{ background: importanceColors[n] }} />
            {importanceLabels[n]}
          </span>
        ))}
        {tagFilter && allTags.includes(tagFilter) && (
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full inline-block" style={{ background: tagColors[tagFilter] }} />
            tag: {tagFilter}
          </span>
        )}
      </div>

      <div className="card" style={{ height: '68vh' }} ref={containerRef} />
    </div>
  )
}
