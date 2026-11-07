import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Filter, ChevronDown, ChevronUp } from 'lucide-react'
import { api } from '../lib/api'
import { collectSubjectTags, subjectTags } from '../lib/tags'
import type { GraphData, GraphEdge, GraphNode, RelationLabel, TimelineEntry } from '../lib/types'

interface Props {
  onSelectEntry: (entry: TimelineEntry) => void
}

// Colours by importance stay the node's own signal; relationship TYPE is what
// the edges carry. Node size follows degree so hubs read as hubs.
const importanceColors: Record<number, string> = {
  1: '#8d99a9', 2: '#2f5fa8', 3: '#a8843c', 4: '#a2403f', 5: '#2c6f63'
}

const importanceLabels: Record<number, string> = {
  1: 'Scăzută', 2: 'Medie', 3: 'Importantă', 4: 'Foarte importantă', 5: 'Critică'
}

// Muted, mineral data colours: they separate categories without turning the
// canvas into a highlighter set.
const tagPalette = [
  '#2f5fa8', '#2c6f63', '#a8843c', '#a2403f', '#5c6f8f',
  '#7a5a8a', '#3d8477', '#8c6d4f', '#4f79c8', '#677385',
]

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** Lowercase and strip Romanian diacritics so "teorie" finds "Teoríe". */
const fold = (s: string) =>
  s.toLowerCase()
    .replace(/ă/g, 'a').replace(/â/g, 'a').replace(/î/g, 'i')
    .replace(/ș/g, 's').replace(/ț/g, 't')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')

type Period = 'all' | 'year' | 'decade'

export function GraphView({ onSelectEntry }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<any>(null)
  const requestSeq = useRef(0)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [relationLabels, setRelationLabels] = useState<RelationLabel[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [visLoaded, setVisLoaded] = useState(false)
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [tagFilter, setTagFilter] = useState('')
  const [minImportance, setMinImportance] = useState(0)
  const [period, setPeriod] = useState<Period>('all')
  const [relationFilter, setRelationFilter] = useState('')
  const [relationCategory, setRelationCategory] = useState<'all' | 'causal' | 'empirical' | 'structural'>('all')
  const [structuralOnly, setStructuralOnly] = useState(true)
  // Two spaces instead of one crowded one: the connected graph, and a
  // searchable list of entries with no link at all. Measured on the real data,
  // parking 99 isolated labels around the 43 connected ones drove the label
  // font to 8px — unreadable — so they get their own space instead.
  const [showIsolated, setShowIsolated] = useState(false)
  const [isolatedSearch, setIsolatedSearch] = useState('')
  const [search, setSearch] = useState('')
  // Focus mode: one node + its direct neighbours, everything else dimmed.
  const [focusId, setFocusId] = useState<number | null>(null)
  const [physics, setPhysics] = useState(true)
  const [showMobileFilters, setShowMobileFilters] = useState(false)
  // True once vis-network has drawn a settled layout. Until then an overlay
  // covers the empty 68vh box instead of showing a blank white area.
  const [layoutReady, setLayoutReady] = useState(false)
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
      const [graph, timeline, labels] = await Promise.all([
        api.relationships.graph(),
        api.timeline.get(),
        api.relationships.labels().catch(() => ({ total: 0, labels: [] as RelationLabel[] })),
      ])
      if (seq !== requestSeq.current) return
      setGraphData(graph)
      setEntries(timeline.entries)
      setRelationLabels(labels.labels)
    } catch (err) {
      if (seq !== requestSeq.current) return
      setError(err instanceof Error ? err.message : 'Graful nu s-a putut încărca')
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
    return collectSubjectTags(graphData.nodes)
  }, [graphData])

  const tagColors = useMemo(() => {
    const map: Record<string, string> = {}
    allTags.forEach((t, i) => { map[t] = tagPalette[i % tagPalette.length] })
    return map
  }, [allTags])

  const nodeById = useMemo(() => {
    const m = new Map<number, GraphNode>()
    graphData?.nodes.forEach(n => m.set(n.id, n))
    return m
  }, [graphData])

  const visible = useMemo(() => {
    if (!graphData) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[], hiddenIsolated: 0 }
    let maxDate = 0
    graphData.nodes.forEach(n => {
      const t = n.entry_date ? Date.parse(n.entry_date) : NaN
      if (!isNaN(t) && t > maxDate) maxDate = t
    })
    const cutoff =
      period === 'year' ? maxDate - 365 * 86400000
      : period === 'decade' ? maxDate - 3650 * 86400000
      : 0
    let hiddenIsolated = 0
    const nodes = graphData.nodes.filter(n => {
      if (tagFilter && !subjectTags(n.tags).includes(tagFilter)) return false
      if (minImportance > 0 && n.value < minImportance) return false
      if (n.isolated) { hiddenIsolated++; return false }
      if (cutoff > 0) {
        const t = n.entry_date ? Date.parse(n.entry_date) : NaN
        if (isNaN(t) || t < cutoff) return false
      }
      return true
    })
    const nodeIds = new Set(nodes.map(n => n.id))
    const edges = graphData.edges.filter(e => {
      if (!nodeIds.has(e.from) || !nodeIds.has(e.to)) return false
      if (relationCategory === 'causal' && !['causes', 'modulates', 'inhibits'].includes(e.label)) return false
      if (relationCategory === 'empirical' && !['supports', 'contradicts', 'correlates_with'].includes(e.label)) return false
      if (relationCategory === 'structural' && !['derives_from', 'extends', 'precedes', 'includes'].includes(e.label)) return false
      if (relationFilter && e.label !== relationFilter) return false
      if (structuralOnly && (e.label === 'relates_to' || !e.label)) return false
      return true
    })
    return { nodes, edges, hiddenIsolated }
  }, [graphData, tagFilter, minImportance, period, relationFilter, relationCategory, structuralOnly])

  const handleZoomIn = useCallback(() => {
    if (networkRef.current) {
      const scale = networkRef.current.getScale()
      networkRef.current.moveTo({ scale: scale * 1.3, animation: { duration: 250 } })
    }
  }, [])

  const handleZoomOut = useCallback(() => {
    if (networkRef.current) {
      const scale = networkRef.current.getScale()
      networkRef.current.moveTo({ scale: scale * 0.77, animation: { duration: 250 } })
    }
  }, [])

  const handleFit = useCallback(() => {
    if (networkRef.current) {
      networkRef.current.fit({ animation: { duration: 350 } })
    }
  }, [])

  // Direct neighbours of the focused node, with the relation that connects them.
  const focus = useMemo(() => {
    if (focusId === null || !graphData) return null
    const node = nodeById.get(focusId)
    if (!node) return null
    const neighbours = graphData.edges
      .filter(e => e.from === focusId || e.to === focusId)
      .map(e => ({
        edge: e,
        node: nodeById.get(e.from === focusId ? e.to : e.from),
        direction: e.from === focusId ? 'out' : 'in',
      }))
      .filter(nb => !!nb.node)
    return { node, neighbours }
  }, [focusId, graphData, nodeById])

  // Best matching node by title or tag, for the "Mergi la nod" button.
  // Hook order matters: this must stay above the early returns below, or React
  // sees a different hook count once loading flips and the whole view blanks.
  const isolatedNodes = useMemo(
    () => (graphData ? graphData.nodes.filter(n => n.isolated) : []),
    [graphData],
  )

  const searchMatch = useMemo(() => {
    const q = fold(search.trim())
    if (!q || !graphData) return null
    return graphData.nodes.find(n => fold(n.title).includes(q))
      || graphData.nodes.find(n => subjectTags(n.tags).some(t => fold(t).includes(q)))
      || null
  }, [search, graphData])

  const focusOn = useCallback((id: number) => {
    setFocusId(id)
    const net = networkRef.current
    if (net) {
      net.focus(id, { scale: 1.1, animation: { duration: 400, easingFunction: 'easeInOutQuad' } })
      net.selectNodes([id])
    }
  }, [])

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

      const neighbourIds = new Set<number>()
      if (focusId !== null) {
        graphData.edges.forEach(e => {
          if (e.from === focusId) neighbourIds.add(e.to)
          else if (e.to === focusId) neighbourIds.add(e.from)
        })
      }
      const maxDegree = Math.max(1, ...visible.nodes.map(n => n.degree))

      // Which nodes get a label. Measured with vis-network itself: with all 43
      // labels on, the mandatory zoom-to-fit leaves the font at 0.6px and the
      // layout still overlaps 361 label pairs. Compressing to a readable zoom
      // keeps the labels overlapping. So labels are shown only where they carry
      // meaning — the focused node, its neighbours and the hubs — and every
      // other node keeps its full title in the tooltip and in the panel.
      const cutLabel = (t: string) => {
        // Wrap to two short lines: a narrow box spreads better under the
        // physics solver than a 200px-wide slab that collides with neighbours.
        const cut = t.length > 40 ? t.slice(0, 39) + '…' : t
        if (cut.length <= 20) return cut
        const words = cut.split(' ')
        const line1: string[] = []
        let len = 0
        let i = 0
        while (i < words.length && len < 20) {
          line1.push(words[i])
          len += words[i].length + 1
          i += 1
        }
        const rest = words.slice(i).join(' ')
        return rest ? line1.join(' ') + '\n' + rest : cut
      }

      const graphNodes = visible.nodes.filter(n => !n.isolated)
      const nodes = new vis.DataSet(graphNodes.map(n => {
        // Colour by the first real subject, not by a stray year in the tag list.
        const firstTag = subjectTags(n.tags)[0]
        const date = n.entry_date ? `<br/><b>${escapeHtml(n.entry_date)}</b>` : ''
        const links = n.isolated ? '<br/><i>fără legături</i>' : `<br/>${n.degree} legături`
        const dimmed = focusId !== null && n.id !== focusId && !neighbourIds.has(n.id)
        const fillColor = firstTag ? tagColors[firstTag] || n.color : n.color
        const borderColor = fillColor
        const accent = isDark ? '#8fb3ff' : '#2f5fa8'
        return {
          ...n,
          label: cutLabel(n.title),
          shape: 'box',
          shapeProperties: { borderRadius: 7 },
          margin: { top: 7, right: 11, bottom: 7, left: 11 },
          color: {
            background: isDark ? '#141b25' : '#ffffff',
            border: borderColor,
            highlight: {
              background: isDark ? '#1a2a44' : '#e2ebf8',
              border: accent,
            },
            hover: {
              background: isDark ? '#1b2430' : '#f1f4f8',
              border: borderColor,
            },
          },
          borderColor,
          borderWidth: 1.5,
          font: {
            size: 12.5,
            face: '"Source Sans 3 Variable", "Source Sans 3", sans-serif',
            color: isDark ? '#e8edf4' : '#141c28',
          },
          shadow: {
            enabled: true,
            color: isDark ? 'rgba(0,0,0,0.4)' : 'rgba(15,23,42,0.09)',
            size: 3,
            x: 0,
            y: 1,
          },
          opacity: dimmed ? 0.15 : 1,
          title: `<b>${escapeHtml(n.title)}</b>${date}${links}<br/>${escapeHtml((n.content_preview || '').slice(0, 200))}`,
        }
      }))

      const edges = new vis.DataSet(visible.edges.map(e => {
        const isFocusEdge = focusId !== null && (e.from === focusId || e.to === focusId)
        const isGeneric = e.label === 'relates_to' || !e.label
        const accent = isDark ? '#8fb3ff' : '#2f5fa8'
        return {
          ...e,
          width: isFocusEdge ? 2.4 : (isGeneric ? 1 : 1.6),
          dashes: isGeneric ? [4, 4] : false,
          // Gentle continuous arcs: straight lines maximise crossings through
          // the graph centre, while low-roundness curves arc around nodes and
          // distribute the intersections where they are less noticeable.
          smooth: {
            enabled: true,
            type: 'continuous',
            roundness: 0.1,
          },
          color: {
            color: isGeneric ? (isDark ? '#3a4554' : '#b7c1ce') : (e.color || '#2f5fa8'),
            opacity: focusId !== null && !isFocusEdge ? 0.08 : (isGeneric ? 0.35 : 0.8),
            highlight: accent,
            hover: accent,
          },
          arrows: isGeneric ? undefined : { to: { enabled: true, scaleFactor: 0.65 } },
          // Only label the edges of the focused node: 128 labels at once was
          // the main reason the graph looked like noise on the phone.
          label: isFocusEdge ? e.label_display : undefined,
          title: `<b>${escapeHtml(e.label_display)}</b>${e.detail ? `<br/>${escapeHtml(e.detail)}` : ''}`,
          font: {
            size: 11,
            align: 'middle',
            face: '"Source Sans 3 Variable", "Source Sans 3", sans-serif',
            color: isDark ? '#c8d0dc' : '#3a4554',
            background: isDark ? '#141b25' : '#ffffff',
          },
        }
      }))


      const options = {
        nodes: {
          shape: 'box',
          shapeProperties: { borderRadius: 7 },
          font: {
            size: 12.5,
            face: '"Source Sans 3 Variable", "Source Sans 3", sans-serif',
          },
          margin: { top: 7, right: 11, bottom: 7, left: 11 },
          widthConstraint: { maximum: 150 },
          labelHighlightBold: true,
        },
          physics: {
          enabled: physics,
          stabilization: { iterations: physics ? 220 : 0, updateInterval: 25 },
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {
            // Stronger repulsion + longer springs spread the boxes so the
            // wrapped two-line labels do not collide (the old -75/130 pair
            // stacked 200px boxes on top of each other).
            gravitationalConstant: -150,
            centralGravity: 0.02,
            springLength: 180,
            springConstant: 0.05,
            damping: 0.5,
            avoidOverlap: 1,
          },
        },
        interaction: { hover: true, tooltipDelay: 150, zoomView: true, dragView: true },
        // improvedLayout runs a Kamada-Kawai init that costs seconds on a graph
        // this size and leaves the canvas blank while it works.
        layout: { improvedLayout: false, randomSeed: 42 },
      }

      const containerElement = containerRef.current
      if (containerElement) {
        const net = new Network(containerElement, { nodes, edges }, options)
        networkRef.current = net

        // Fit the viewport once physics settles so no node is clipped at the
        // edges and the layout fills the container instead of leaving dead space.
        net.once('stabilizationIterationsDone', () => {
          if (networkRef.current) networkRef.current.fit({ animation: false })
          if (isMounted) setLayoutReady(true)
        })
        // Fallback: never leave the overlay up if the event never arrives.
        setTimeout(() => {
          if (!isMounted) return
          if (networkRef.current) networkRef.current.fit({ animation: false })
          setLayoutReady(true)
        }, 1500)

        // Click = focus. The neighbours panel carries the "Deschide" action, so
        // selecting a node no longer immediately jumps away from the graph.
        net.on('click', (props: { nodes: number[] }) => {
          if (props.nodes.length > 0) setFocusId(props.nodes[0])
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
  }, [graphData, visLoaded, entries, visible, tagColors, physics, isDark, focusId])

  const openEntry = (id: number) => {
    const entry = entries.find(e => e.id === id)
    if (entry) { onSelectEntry(entry); return }
    // Not on the loaded timeline page: build the shape the detail view needs.
    const node = nodeById.get(id)
    if (node) {
      onSelectEntry({
        id: node.id, title: node.title, entry_date: node.entry_date,
        tags: node.tags, importance: node.value,
        relationship_count: node.degree, content_preview: node.content_preview,
      })
    }
  }

  if (loading) {
    return (
      <div>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-xl font-bold">Graful cunoștințelor</h2>
            <p className="text-sm text-muted">
              <span className="inline-block h-3 w-48 rounded bg-surface-2 animate-pulse align-middle" />
            </p>
          </div>
        </div>
        <div
          role="status"
          aria-live="polite"
          className="rounded-xl border border-line bg-surface-2 animate-pulse flex items-center justify-center overflow-hidden"
          style={{ height: '68vh' }}
        >
          <span className="text-sm text-muted">Se încarcă graful…</span>
        </div>
      </div>
    )
  }

  if (error && !graphData) {
    return (
      <div className="card text-center py-12 border-madder/30">
        <p className="text-madder mb-2">⚠️ {error}</p>
        <button onClick={loadData} className="btn-secondary">↻ Reîncearcă</button>
      </div>
    )
  }

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div className="card text-center py-12">
        <p className="text-muted mb-2">Încă nu există date pentru graf.</p>
        <p className="text-sm text-muted">Creează intrări și leagă-le pentru a vedea graful cunoștințelor.</p>
        <button onClick={loadData} className="btn-secondary mt-4">↻ Reîmprospătează</button>
      </div>
    )
  }

  const isolatedTotal = graphData.nodes.filter(n => n.isolated).length

  return (
    <div>
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <h2 className="text-xl font-bold">Graful cunoștințelor</h2>
          <p className="text-sm text-muted">
            {visible.nodes.length} din {graphData.nodes.length} noduri · {visible.edges.length} legături
            {isolatedTotal > 0 && (
              <> · <span className="text-brass">{isolatedTotal} fără nicio legătură</span></>
            )}
          </p>
        </div>
        <div className="flex gap-2 flex-wrap items-center">
          <button onClick={() => {
            if (networkRef.current) networkRef.current.fit({ animation: true })
          }} className="btn-secondary text-xs sm:text-sm" aria-label="Încadrează tot graful în ecran">⛶ Încadrează</button>
          <button onClick={() => setPhysics(!physics)} className={`btn-ghost text-xs sm:text-sm ${physics ? 'text-chrono-600' : ''}`}>
            {physics ? '⚛️ Fizică on' : '⚛️ Fizică off'}
          </button>
          <button
            onClick={() => setShowMobileFilters(v => !v)}
            className="sm:hidden btn-secondary text-xs flex items-center gap-1.5 font-medium px-2.5 py-1.5"
            aria-expanded={showMobileFilters}
          >
            <Filter className="w-3.5 h-3.5 text-chrono-600 dark:text-chrono-400" />
            <span>{showMobileFilters ? 'Ascunde filtre' : 'Filtre & Căutare'}</span>
            {showMobileFilters ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Search & Filters (Collapsible on mobile) */}
      <div className={`${showMobileFilters ? 'block' : 'hidden sm:block'} space-y-2 mb-3`}>
        {/* Search — the fastest way to reach one node among 137 names. */}
        <div className="flex flex-wrap gap-2 mb-2 items-center">
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && searchMatch) focusOn(searchMatch.id) }}
            className="input flex-1 min-w-[180px] text-sm"
            placeholder="Caută o intrare după titlu sau etichetă…"
            aria-label="Caută un nod în graf după titlu sau etichetă"
          />
          <button
            onClick={() => searchMatch && focusOn(searchMatch.id)}
            disabled={!searchMatch}
            className="btn-secondary text-sm disabled:opacity-40"
            aria-label="Mergi la nodul găsit"
          >
            🔍 Mergi la nod
          </button>
          {search.trim() && !searchMatch && <span className="text-xs text-muted">nimic găsit</span>}
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 mb-2">
          <select value={tagFilter} onChange={e => setTagFilter(e.target.value)} className="input w-auto text-sm" aria-label="Filtrează după etichetă">
            <option value="">Toate etichetele</option>
            {allTags.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <select value={relationFilter} onChange={e => setRelationFilter(e.target.value)} className="input w-auto text-sm" aria-label="Filtrează după tipul legăturii">
            <option value="">Toate legăturile</option>
            {relationLabels.map(l => <option key={l.code} value={l.code}>{l.display} ({l.count})</option>)}
          </select>
          <select value={relationCategory} onChange={e => setRelationCategory(e.target.value as any)} className="input w-auto text-sm font-medium" aria-label="Filtrează după categorie relații">
            <option value="all">Toate categoriile</option>
            <option value="causal">🔬 Doar Cauzale (cauză, modulație, inhibiție)</option>
            <option value="empirical">📊 Doar Empirice (susține, contrazice)</option>
            <option value="structural">🏛️ Doar Structurale (derivă din, extinde, precede)</option>
          </select>
          <button
            onClick={() => setStructuralOnly(v => !v)}
            className={`btn-ghost text-sm ${structuralOnly ? 'text-chrono-600 font-semibold' : ''}`}
            aria-pressed={structuralOnly}
            title="Exclude legăturile generice (relates_to) pentru o hartă aerisită, axată doar pe relații cauzale și ierarhice"
          >
            {structuralOnly ? '✓ Doar relații structurale' : 'Toate relațiile'}
          </button>
          <select value={minImportance} onChange={e => setMinImportance(Number(e.target.value))} className="input w-auto text-sm" aria-label="Filtrează după importanță">
            <option value={0}>Orice importanță</option>
            <option value={3}>3★ și peste</option>
            <option value={4}>4★ și peste</option>
            <option value={5}>doar 5★</option>
          </select>
          <select value={period} onChange={e => setPeriod(e.target.value as Period)} className="input w-auto text-sm" aria-label="Filtrează după perioadă">
            <option value="all">Toată perioada</option>
            <option value="year">Ultimul an</option>
            <option value="decade">Ultimul deceniu</option>
          </select>
          <button
            onClick={() => setShowIsolated(v => !v)}
            className={`btn-ghost text-sm ${showIsolated ? 'text-chrono-600' : ''}`}
            aria-pressed={showIsolated}
            aria-label="Arată lista intrărilor fără nicio legătură"
          >
            {showIsolated ? '✓ Fără legături' : `Fără legături (${isolatedTotal})`}
          </button>
          {(tagFilter || minImportance > 0 || period !== 'all' || relationFilter || relationCategory !== 'all') && (
            <button
              onClick={() => { setTagFilter(''); setMinImportance(0); setPeriod('all'); setRelationFilter(''); setRelationCategory('all') }}
              className="btn-ghost text-sm"
            >Golește</button>
          )}
        </div>

        {/* Legend: importance (node colour) + relation types (edge colour). */}
        <div className="flex flex-wrap gap-x-3 gap-y-1 mb-2 px-1 text-xs text-muted">
          {[1, 2, 3, 4, 5].map(n => (
            <span key={n} className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-full inline-block" style={{ background: importanceColors[n] }} />
              {importanceLabels[n]}
            </span>
          ))}
          {relationLabels.length > 0 && <span className="w-px h-3 bg-line" aria-hidden />}
          {relationLabels.slice(0, 8).map(l => (
            <span key={l.code} className="flex items-center gap-1.5">
              <span className="inline-block w-5 h-0.5" style={{ background: l.color }} aria-hidden />
              {l.display}
            </span>
          ))}
          {tagFilter && allTags.includes(tagFilter) && (
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-full inline-block" style={{ background: tagColors[tagFilter] }} />
              etichetă: {tagFilter}
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-3">
        <div className="relative flex-1 min-w-0">
          {/* Interactive Knowledge Graph Canvas — renders across desktop and mobile */}
          <div className="card graph-canvas block w-full relative overflow-hidden p-0" style={{ height: '65vh', minHeight: '380px' }} ref={containerRef} />

          {/* Floating Zoom & Fit Controls */}
          <div className="flex absolute bottom-4 right-4 z-10 flex-col gap-0.5 bg-surface/90 backdrop-blur-sm p-1 rounded-lg border border-line shadow-card">
            <button onClick={handleZoomIn} className="w-7 h-7 flex items-center justify-center rounded text-muted hover:bg-surface-2 hover:text-ink font-bold text-base transition-colors" title="Mărește (Zoom in)">+</button>
            <button onClick={handleZoomOut} className="w-7 h-7 flex items-center justify-center rounded text-muted hover:bg-surface-2 hover:text-ink font-bold text-base transition-colors" title="Micșorează (Zoom out)">−</button>
            <button onClick={handleFit} className="w-7 h-7 flex items-center justify-center rounded text-muted hover:bg-surface-2 hover:text-ink text-xs font-semibold transition-colors" title="Încadrează tot graful (Fit)">⛶</button>
          </div>
          {!layoutReady && (
            <div
              role="status"
              aria-live="polite"
              className="flex absolute inset-0 rounded-xl bg-surface/95 items-center justify-center animate-pulse"
            >
              <span className="text-sm text-muted">Se așază nodurile…</span>
            </div>
          )}
        </div>

        {focus && (
          <aside
            className="card lg:w-80 shrink-0 max-h-[68vh] overflow-y-auto"
            aria-label="Legăturile nodului selectat"
          >
            <div className="flex items-start justify-between gap-2 mb-2">
              <h3 className="font-semibold text-sm leading-snug">{focus.node.title}</h3>
              <button onClick={() => setFocusId(null)} className="btn-ghost text-xs shrink-0" aria-label="Închide panoul de legături">✕</button>
            </div>
            <p className="text-xs text-muted mb-3">
              {focus.node.entry_date || 'fără dată'} · {focus.node.degree} legături
            </p>
            <button onClick={() => openEntry(focus.node.id)} className="btn-primary text-xs w-full mb-2">
              Deschide intrarea
            </button>
            <button onClick={() => setFocusId(null)} className="btn-secondary text-xs w-full mb-3">
              Vezi tot graful
            </button>

            {focus.neighbours.length === 0 ? (
              <p className="text-xs text-brass">
                Nicio legătură. Poți cere AI-ului din chat să lege această intrare de altele.
              </p>
            ) : (
              <ul className="space-y-2">
                {focus.neighbours.map(({ edge, node, direction }) => (
                  <li key={edge.id} className="border-t border-line pt-2 first:border-0 first:pt-0">
                    <div className="flex items-center gap-1.5 text-[11px] text-muted mb-0.5">
                      <span className="inline-block w-3 h-0.5" style={{ background: edge.color }} aria-hidden />
                      <span className="font-medium">{direction === 'out' ? '→' : '←'} {edge.label_display}</span>
                    </div>
                    <button onClick={() => node && focusOn(node.id)} className="text-left text-xs hover:text-accent hover:underline leading-snug transition-colors">
                      {node?.title}
                    </button>
                    {edge.detail && <p className="text-[11px] text-gray-400 mt-0.5">{edge.detail}</p>}
                    <p className="text-[11px] text-gray-400">{node?.entry_date || 'fără dată'}</p>
                  </li>
                ))}
              </ul>
            )}
          </aside>
        )}
      </div>

      <p className="text-xs text-muted mt-2">
        Apasă pe un nod ca să vezi doar legăturile lui. Graful arată doar intrările legate
        ({graphData.nodes.length - isolatedTotal}); celelalte {isolatedTotal} au propria listă mai sus.
      </p>

      {/* The isolated entries, where they are readable: searchable, with the one
          action that matters for them (open it and link it). They are not drawn
          on the canvas because 99 long labels drown the 43 connected nodes. */}
      {showIsolated && (
        <section className="card mt-3" aria-label="Intrări fără nicio legătură">
          <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
            <h3 className="font-semibold text-sm">
              Intrări fără nicio legătură ({isolatedTotal})
            </h3>
            <button
              onClick={() => setShowIsolated(false)}
              className="btn-ghost text-xs"
              aria-label="Închide lista intrărilor fără legături"
            >✕</button>
          </div>
          <p className="text-xs text-muted mb-2">
            Nu apar în graf pentru că nu au nicio legătură: nu adaugă informație acolo, doar aglomerează.
            Deschide una și cere AI-ului din chat să o lege de intrarea potrivită.
          </p>
          <input
            value={isolatedSearch}
            onChange={e => setIsolatedSearch(e.target.value)}
            className="input w-full text-sm mb-2"
            placeholder="Caută printre intrările nelegate…"
            aria-label="Caută printre intrările fără legături"
          />
          <ul className="max-h-64 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-800">
            {isolatedNodes.filter(n => {
              const q = fold(isolatedSearch.trim())
              if (!q) return true
              return fold(n.title).includes(q) || subjectTags(n.tags).some(t => fold(t).includes(q))
            }).map(n => (
              <li key={n.id} className="py-1.5 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: n.color }} aria-hidden />
                <span className="flex-1 min-w-0 text-sm truncate" title={n.title}>{n.title}</span>
                <span className="text-xs text-muted shrink-0">{n.entry_date || 'fără dată'}</span>
                <button
                  onClick={() => openEntry(n.id)}
                  className="btn-ghost text-xs shrink-0"
                  aria-label={`Deschide intrarea ${n.title}`}
                >Deschide</button>
              </li>
            ))}
          </ul>
          {isolatedNodes.length === 0 && <p className="text-sm text-gray-500">Toate intrările au cel puțin o legătură.</p>}
        </section>
      )}
    </div>
  )
}
