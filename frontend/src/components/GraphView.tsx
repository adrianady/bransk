import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { GraphData, TimelineEntry } from '../lib/types'

interface Props {
  onSelectEntry: (entry: TimelineEntry) => void
}

export function GraphView({ onSelectEntry }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<any>(null)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [visLoaded, setVisLoaded] = useState(false)
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [timeFilter, setTimeFilter] = useState<'all' | 'year' | 'decade'>('all')

  useEffect(() => {
    import('vis-network/standalone').then(() => setVisLoaded(true))
  }, [])

  useEffect(() => {
    const load = async () => {
      try {
        const [graph, timeline] = await Promise.all([
          api.relationships.graph(),
          api.timeline.get(),
        ])
        setGraphData(graph)
        setEntries(timeline.entries)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  useEffect(() => {
    if (!graphData) return
    let isMounted = true

    const initNetwork = async () => {
      const vis = await import('vis-network/standalone')
      if (!isMounted || !containerRef.current) return
      const { Network } = vis

      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }

      const nodes = new vis.DataSet(graphData.nodes)
      const edges = new vis.DataSet(graphData.edges)

      const options = {
        nodes: {
          shape: 'dot',
          scaling: { min: 10, max: 30, label: { min: 8, max: 20, drawThreshold: 12 } },
          font: { size: 12, face: 'Inter, sans-serif' },
        },
        edges: {
          width: 1,
          smooth: { type: 'continuous' },
          font: { size: 10, align: 'middle' },
        },
        physics: {
          stabilization: { iterations: 50 },
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {
            gravitationalConstant: -40,
            centralGravity: 0.005,
            springLength: 150,
            springConstant: 0.02,
          },
        },
        interaction: { hover: true, tooltipDelay: 200, zoomView: true, dragView: true },
        layout: { improvedLayout: true },
      }

      // Explicit null check for containerRef.current resolves TS compiler error
      const containerElement = containerRef.current
      if (containerElement) {
        const net = new Network(containerElement, { nodes, edges }, options)
        networkRef.current = net

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
  }, [graphData, visLoaded, entries, onSelectEntry])

  if (loading) return <div className="text-center py-8 text-gray-500">Loading graph...</div>

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div className="card text-center py-12">
        <p className="text-gray-500 mb-2">No graph data yet.</p>
        <p className="text-sm text-gray-400">Create entries and link them to see the knowledge graph.</p>
        <button onClick={() => window.location.reload()} className="btn-secondary mt-4">Refresh</button>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Knowledge Graph</h2>
          <p className="text-sm text-gray-500">{graphData.nodes.length} nodes · {graphData.edges.length} edges</p>
        </div>
        <button onClick={() => {
          if (networkRef.current) {
            networkRef.current.fit({ animation: true })
          }
        }} className="btn-secondary">Reset View</button>
      </div>

      <div className="card" style={{ height: '70vh' }} ref={containerRef} />
    </div>
  )
}
