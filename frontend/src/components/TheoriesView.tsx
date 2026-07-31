import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Entry, TheoryEvidence, TheoryNode, TheoryStatus } from '../lib/types'

interface Props {
  onSelectEntry: (entry: Entry) => void
}

const statusColors: Record<TheoryStatus, string> = {
  supported: '#10b981',
  contested: '#f59e0b',
  refuted: '#ef4444',
  emerging: '#6366f1',
}

const statusLabels: Record<TheoryStatus, string> = {
  supported: 'Supported',
  contested: 'Contested',
  refuted: 'Refuted',
  emerging: 'Emerging',
}

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export function TheoriesView({ onSelectEntry }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<any>(null)
  const [tree, setTree] = useState<TheoryNode[]>([])
  const [theories, setTheories] = useState<TheoryNode[]>([])
  const [allEntries, setAllEntries] = useState<Entry[]>([])
  const [loading, setLoading] = useState(true)
  const [visLoaded, setVisLoaded] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [evidence, setEvidence] = useState<TheoryEvidence[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [showAddEvidence, setShowAddEvidence] = useState(false)
  const [showAddSub, setShowAddSub] = useState(false)
  const [createForm, setCreateForm] = useState({ name: '', description: '', parent_id: '', status: 'emerging' as TheoryStatus })
  const [editForm, setEditForm] = useState({ name: '', description: '', status: 'emerging' as TheoryStatus, parent_id: '' })
  const [evForm, setEvForm] = useState({ entry_id: '', stance: 'pro' as 'pro' | 'contra', note: '', weight: 3 })
  const [subForm, setSubForm] = useState({ name: '', description: '' })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [treeData, entries] = await Promise.all([
        api.theories.tree(),
        api.entries.list({ limit: 500 }),
      ])
      setTree(treeData)
      const flat: TheoryNode[] = []
      const visit = (nodes: TheoryNode[]) => nodes.forEach(n => { flat.push(n); visit(n.children) })
      visit(treeData)
      setTheories(flat)
      setAllEntries(entries)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    import('vis-network/standalone').then(() => setVisLoaded(true))
  }, [load])

  useEffect(() => {
    if (selectedId) {
      api.theories.evidence(selectedId).then(setEvidence).catch(() => setEvidence([]))
      const t = theories.find(x => x.id === selectedId)
      if (t) setEditForm({ name: t.name, description: t.description, status: t.status, parent_id: t.parent_id ? String(t.parent_id) : '' })
    } else {
      setEvidence([])
    }
  }, [selectedId, theories])

  const { nodes, edges } = useMemo(() => {
    const nodes: any[] = []
    const edges: any[] = []
    const visit = (node: TheoryNode) => {
      nodes.push({
        id: `t${node.id}`,
        label: node.name.length > 42 ? node.name.slice(0, 40) + '…' : node.name,
        shape: 'box',
        margin: { top: 8, right: 12, bottom: 8, left: 12 },
        color: { background: statusColors[node.status], border: '#1f2937' },
        font: { color: '#ffffff', size: 13, face: 'Inter, sans-serif' },
        shadow: true,
        title:
          `<b>${escapeHtml(node.name)}</b><br/>` +
          `${statusLabels[node.status]} — pro ${node.pro_count} / contra ${node.contra_count}<br/>` +
          `${escapeHtml((node.description || '').slice(0, 300))}`,
      })
      node.children.forEach(child => {
        edges.push({ from: `t${node.id}`, to: `t${child.id}`, label: 'derives', arrows: 'to' })
        visit(child)
      })
    }
    tree.forEach(visit)
    return { nodes, edges }
  }, [tree])

  useEffect(() => {
    if (!visLoaded || nodes.length === 0) return
    let isMounted = true
    const init = async () => {
      const vis = await import('vis-network/standalone')
      if (!isMounted || !containerRef.current) return
      const { Network } = vis
      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }
      const net = new Network(containerRef.current, {
        nodes: new vis.DataSet(nodes),
        edges: new vis.DataSet(edges),
      }, {
        layout: {
          hierarchical: {
            direction: 'UD',
            sortMethod: 'directed',
            levelSeparation: 140,
            nodeSpacing: 130,
            treeSpacing: 180,
          },
        },
        physics: false,
        edges: {
          color: '#94a3b8',
          font: { size: 10, color: '#94a3b8' },
          smooth: { enabled: true, type: 'cubicBezier', roundness: 0.5 },
        },
        interaction: { hover: true, tooltipDelay: 150 },
      })
      networkRef.current = net
      net.on('click', (props: { nodes: string[] }) => {
        if (props.nodes.length > 0) {
          setSelectedId(Number(String(props.nodes[0]).replace('t', '')))
        }
      })
      setTimeout(() => net.fit({ animation: false }), 60)
    }
    init()
    return () => {
      isMounted = false
      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }
    }
  }, [visLoaded, nodes, edges])

  const selected = selectedId ? theories.find(t => t.id === selectedId) : null
  const proEvidence = evidence.filter(e => e.stance === 'pro')
  const contraEvidence = evidence.filter(e => e.stance === 'contra')
  const totalEvidence = theories.reduce((acc, t) => acc + t.pro_count + t.contra_count, 0)

  const saveEdit = async () => {
    if (!selectedId) return
    await api.theories.update(selectedId, {
      name: editForm.name.trim() || selected?.name,
      description: editForm.description,
      status: editForm.status,
      parent_id: editForm.parent_id ? Number(editForm.parent_id) : null,
    })
    await load()
  }

  const addEvidence = async () => {
    if (!selectedId || !evForm.entry_id) return
    await api.theories.addEvidence(selectedId, {
      entry_id: Number(evForm.entry_id),
      stance: evForm.stance,
      note: evForm.note,
      weight: evForm.weight,
    })
    setEvForm({ entry_id: '', stance: 'pro', note: '', weight: 3 })
    setShowAddEvidence(false)
    await Promise.all([api.theories.evidence(selectedId).then(setEvidence), load()])
  }

  const addSubTheory = async () => {
    if (!selectedId || !subForm.name.trim()) return
    await api.theories.create({
      name: subForm.name.trim(),
      description: subForm.description,
      parent_id: selectedId,
      status: 'emerging',
    })
    setSubForm({ name: '', description: '' })
    setShowAddSub(false)
    await load()
  }

  if (loading) return <div className="text-center py-8 text-gray-500">Loading theory aggregator...</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Theory Aggregator</h2>
          <p className="text-sm text-gray-500">
            {theories.length} theories · {totalEvidence} evidence links · a theory can derive into sub-theories
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary">+ New Theory</button>
      </div>

      {/* Status legend */}
      <div className="flex flex-wrap gap-3 mb-3 px-1 text-xs text-gray-500">
        {(Object.keys(statusLabels) as TheoryStatus[]).map(s => (
          <span key={s} className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded inline-block" style={{ background: statusColors[s] }} />
            {statusLabels[s]}
          </span>
        ))}
      </div>

      {theories.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-500 mb-2">No theories yet.</p>
          <p className="text-sm text-gray-400">
            Run a research session and a theory map is built automatically — or create theories manually and attach pro/contra studies.
          </p>
          <button onClick={() => setShowCreate(true)} className="btn-primary mt-4">Create your first theory</button>
        </div>
      ) : (
        <div className="card mb-4" style={{ height: '52vh' }} ref={containerRef} />
      )}

      {/* Create root theory */}
      {showCreate && (
        <div className="card mb-4 space-y-3">
          <h3 className="font-semibold">New theory</h3>
          <input value={createForm.name} onChange={e => setCreateForm(f => ({ ...f, name: e.target.value }))}
            className="input" placeholder="Theory name" autoFocus />
          <textarea value={createForm.description} onChange={e => setCreateForm(f => ({ ...f, description: e.target.value }))}
            className="input min-h-[60px]" placeholder="What does the theory claim?" />
          <div className="grid grid-cols-2 gap-2">
            <select value={createForm.parent_id} onChange={e => setCreateForm(f => ({ ...f, parent_id: e.target.value }))} className="input">
              <option value="">Root theory</option>
              {theories.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <select value={createForm.status} onChange={e => setCreateForm(f => ({ ...f, status: e.target.value as TheoryStatus }))} className="input">
              {(Object.keys(statusLabels) as TheoryStatus[]).map(s => <option key={s} value={s}>{statusLabels[s]}</option>)}
            </select>
          </div>
          <div className="flex gap-2">
            <button
              onClick={async () => {
                if (!createForm.name.trim()) return
                await api.theories.create({
                  name: createForm.name.trim(),
                  description: createForm.description,
                  parent_id: createForm.parent_id ? Number(createForm.parent_id) : null,
                  status: createForm.status,
                })
                setCreateForm({ name: '', description: '', parent_id: '', status: 'emerging' })
                setShowCreate(false)
                await load()
              }}
              disabled={!createForm.name.trim()}
              className="btn-primary"
            >Create</button>
            <button onClick={() => setShowCreate(false)} className="btn-ghost">Cancel</button>
          </div>
        </div>
      )}

      {/* Selected theory detail */}
      {selected && (
        <div className="card space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0 space-y-2">
              <input value={editForm.name} onChange={e => setEditForm(f => ({ ...f, name: e.target.value }))}
                className="input font-semibold" />
              <textarea value={editForm.description} onChange={e => setEditForm(f => ({ ...f, description: e.target.value }))}
                className="input min-h-[60px]" />
              <div className="grid grid-cols-2 gap-2">
                <select value={editForm.status} onChange={e => setEditForm(f => ({ ...f, status: e.target.value as TheoryStatus }))} className="input">
                  {(Object.keys(statusLabels) as TheoryStatus[]).map(s => <option key={s} value={s}>{statusLabels[s]}</option>)}
                </select>
                <select value={editForm.parent_id} onChange={e => setEditForm(f => ({ ...f, parent_id: e.target.value }))} className="input">
                  <option value="">Root theory</option>
                  {theories.filter(t => t.id !== selected.id).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </div>
              <div className="flex gap-2">
                <button onClick={saveEdit} className="btn-primary text-sm">Save theory</button>
                <button
                  onClick={async () => {
                    if (confirm('Delete this theory? Evidence is removed; sub-theories become roots.')) {
                      await api.theories.delete(selected.id)
                      setSelectedId(null)
                      await load()
                    }
                  }}
                  className="btn-ghost text-sm text-red-500"
                >Delete</button>
              </div>
            </div>
            <div className="text-right shrink-0 text-sm">
              <span className="badge bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300">pro {selected.pro_count}</span>{' '}
              <span className="badge bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300">contra {selected.contra_count}</span>
            </div>
          </div>

          {/* Evidence */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="font-semibold text-sm">Evidence</h4>
              <div className="flex gap-2">
                <button onClick={() => setShowAddSub(!showAddSub)} className="btn-secondary text-xs">+ Sub-theory</button>
                <button onClick={() => setShowAddEvidence(!showAddEvidence)} className="btn-primary text-xs">+ Link study</button>
              </div>
            </div>

            {showAddSub && (
              <div className="mb-3 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-2">
                <input value={subForm.name} onChange={e => setSubForm(f => ({ ...f, name: e.target.value }))}
                  className="input" placeholder="Sub-theory name (a theory can derive into several)" />
                <textarea value={subForm.description} onChange={e => setSubForm(f => ({ ...f, description: e.target.value }))}
                  className="input min-h-[50px]" placeholder="Description" />
                <button onClick={addSubTheory} disabled={!subForm.name.trim()} className="btn-primary text-sm">Create sub-theory</button>
              </div>
            )}

            {showAddEvidence && (
              <div className="mb-3 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-2">
                <select value={evForm.entry_id} onChange={e => setEvForm(f => ({ ...f, entry_id: e.target.value }))} className="input">
                  <option value="">Select study / entry...</option>
                  {allEntries.map(e => <option key={e.id} value={e.id}>{e.title}{e.entry_date ? ` (${e.entry_date.slice(0, 4)})` : ''}</option>)}
                </select>
                <div className="grid grid-cols-2 gap-2">
                  <select value={evForm.stance} onChange={e => setEvForm(f => ({ ...f, stance: e.target.value as 'pro' | 'contra' }))} className="input">
                    <option value="pro">✅ Pro (supports)</option>
                    <option value="contra">❌ Contra (contradicts)</option>
                  </select>
                  <select value={evForm.weight} onChange={e => setEvForm(f => ({ ...f, weight: Number(e.target.value) }))} className="input">
                    {[1, 2, 3, 4, 5].map(w => <option key={w} value={w}>weight {w}</option>)}
                  </select>
                </div>
                <input value={evForm.note} onChange={e => setEvForm(f => ({ ...f, note: e.target.value }))}
                  className="input" placeholder="Note (how it supports/contradicts)" />
                <button onClick={addEvidence} disabled={!evForm.entry_id} className="btn-primary text-sm">Link evidence</button>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <p className="text-xs font-medium text-green-600 dark:text-green-400 mb-1.5">✅ PRO evidence ({proEvidence.length})</p>
                <div className="space-y-1.5">
                  {proEvidence.map(ev => (
                    <div key={ev.id} className="p-2 bg-green-50 dark:bg-green-950/30 rounded-lg text-sm flex items-start justify-between gap-2">
                      <button
                        className="text-left font-medium hover:text-chrono-600 min-w-0"
                        onClick={() => {
                          const entry = allEntries.find(e => e.id === ev.entry_id)
                          if (entry) onSelectEntry(entry)
                        }}
                      >{ev.entry_title}</button>
                      <button onClick={async () => {
                        await api.theories.removeEvidence(selected.id, ev.id)
                        await Promise.all([api.theories.evidence(selected.id).then(setEvidence), load()])
                      }} className="text-red-400 text-xs shrink-0">✕</button>
                    </div>
                  ))}
                  {proEvidence.length === 0 && <p className="text-xs text-gray-400">No supporting studies yet.</p>}
                </div>
              </div>
              <div>
                <p className="text-xs font-medium text-red-600 dark:text-red-400 mb-1.5">❌ CONTRA evidence ({contraEvidence.length})</p>
                <div className="space-y-1.5">
                  {contraEvidence.map(ev => (
                    <div key={ev.id} className="p-2 bg-red-50 dark:bg-red-950/30 rounded-lg text-sm flex items-start justify-between gap-2">
                      <button
                        className="text-left font-medium hover:text-chrono-600 min-w-0"
                        onClick={() => {
                          const entry = allEntries.find(e => e.id === ev.entry_id)
                          if (entry) onSelectEntry(entry)
                        }}
                      >{ev.entry_title}</button>
                      <button onClick={async () => {
                        await api.theories.removeEvidence(selected.id, ev.id)
                        await Promise.all([api.theories.evidence(selected.id).then(setEvidence), load()])
                      }} className="text-red-400 text-xs shrink-0">✕</button>
                    </div>
                  ))}
                  {contraEvidence.length === 0 && <p className="text-xs text-gray-400">No contradicting studies yet.</p>}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
