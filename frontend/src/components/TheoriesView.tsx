import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { Entry, TheoryEvidence, TheoryNode, TheoryStatus } from '../lib/types'

interface Props {
  onSelectEntry: (entry: Entry) => void
}

const statusColors: Record<TheoryStatus, string> = {
  supported: '#2c6f63',
  contested: '#a8843c',
  refuted: '#a2403f',
  emerging: '#2f5fa8',
}

const statusLabels: Record<TheoryStatus, string> = {
  supported: 'Susținută',
  contested: 'Contestată',
  refuted: 'Respinsă',
  emerging: 'Emergentă',
}

// Paper-toned chips that work on both themes: the tint carries the status,
// the ink stays dark enough to read on it.
const nodeStyles: Record<TheoryStatus, { background: string; border: string; text: string }> = {
  supported: { background: '#dcefe8', border: '#2c6f63', text: '#163b35' },
  contested: { background: '#f4e8cf', border: '#9a742f', text: '#4a3714' },
  refuted: { background: '#f4dedd', border: '#a2403f', text: '#4a1c1c' },
  emerging: { background: '#e2eaf8', border: '#2f5fa8', text: '#1c3457' },
}

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export function TheoriesView({ onSelectEntry }: Props) {
  const toast = useToast()
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<any>(null)
  const fitTimerRef = useRef<number | null>(null)
  const [tree, setTree] = useState<TheoryNode[]>([])
  const [theories, setTheories] = useState<TheoryNode[]>([])
  const [allEntries, setAllEntries] = useState<Entry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
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
  // Navigation for 48 theories: search filter, a "needs evidence" filter and
  // collapsible branches, remembered while the view stays mounted.
  const [search, setSearch] = useState('')
  const [onlyGaps, setOnlyGaps] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [isDark, setIsDark] = useState(() => document.documentElement.classList.contains('dark'))

  // Keep canvas text colors in sync with the active theme.
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.classList.contains('dark'))
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
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
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Arborele teoriilor nu s-a putut încărca')
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
      const hasEvidence = node.pro_count + node.contra_count > 0
      const s = nodeStyles[node.status]
      nodes.push({
        id: `t${node.id}`,
        // Evidence signal is the core datum of this view — show it on the node
        // itself instead of hiding it in the tooltip.
        label: hasEvidence ? `${node.name}\n↗ ${node.pro_count}   ↘ ${node.contra_count}` : node.name,
        shape: 'box',
        margin: { top: 8, right: 12, bottom: 8, left: 12 },
        color: { background: s.background, border: s.border },
        font: { color: s.text, size: 13, face: '"Source Sans 3 Variable", "Source Sans 3", sans-serif' },
        borderWidth: 1.5,
        shapeProperties: { borderRadius: 8 },
        // Wrap long theory names instead of truncating them.
        widthConstraint: { maximum: 260 },
        shadow: {
          enabled: true,
          color: isDark ? 'rgba(0,0,0,0.45)' : 'rgba(15,23,42,0.12)',
          size: 4,
          x: 0,
          y: 1,
        },
        title:
          `<b>${escapeHtml(node.name)}</b><br/>` +
          `${statusLabels[node.status]} — pro ${node.pro_count} / contra ${node.contra_count}<br/>` +
          `${escapeHtml((node.description || '').slice(0, 300))}`,
      })
      node.children.forEach(child => {
        edges.push({ from: `t${node.id}`, to: `t${child.id}`, label: 'derivă', arrows: 'to' })
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
          // Left-to-Right layout flows naturally with text reading direction,
          // displaying parent theories on the left and branching subtheories
          // to the right without horizontal crowding.
          hierarchical: {
            direction: 'LR',
            sortMethod: 'directed',
            levelSeparation: 260,
            nodeSpacing: 50,
            treeSpacing: 70,
            blockShifting: true,
            edgeMinimization: true,
            parentCentralization: true,
          },
        },
        physics: false,
        edges: {
          color: isDark ? '#3a4554' : '#b7c1ce',
          font: {
            size: 11,
            face: '"Source Sans 3 Variable", "Source Sans 3", sans-serif',
            color: isDark ? '#c8d0dc' : '#3a4554',
            background: isDark ? '#141b25' : '#ffffff',
          },
          smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.6 },
          arrows: { to: { enabled: true, scaleFactor: 0.65 } },
        },
        interaction: { hover: true, tooltipDelay: 150, selectable: true, multiselect: false },
      })
      networkRef.current = net
      net.on('click', (props: { nodes: string[] }) => {
        if (props.nodes.length > 0) {
          setSelectedId(Number(String(props.nodes[0]).replace('t', '')))
        }
      })
      fitTimerRef.current = window.setTimeout(() => {
        if (networkRef.current) {
          // Focus at readable zoom on the selected theory or on root t1 (the primary tree)
          if (selectedId != null && nodes.some(n => n.id === `t${selectedId}`)) {
            networkRef.current.focus(`t${selectedId}`, { scale: 0.85, animation: false })
          } else if (nodes.some(n => n.id === 't1')) {
            networkRef.current.focus('t1', { scale: 0.85, animation: false })
          } else {
            networkRef.current.fit({ animation: false })
          }
        }
      }, 80)
    }
    init()
    return () => {
      isMounted = false
      if (fitTimerRef.current) {
        clearTimeout(fitTimerRef.current)
        fitTimerRef.current = null
      }
      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }
    }
  }, [visLoaded, nodes, edges, isDark])

  // Keep the canvas selection in sync when a node is picked from the list.
  useEffect(() => {
    if (networkRef.current && selectedId != null) {
      networkRef.current.selectNodes([`t${selectedId}`])
    }
  }, [selectedId, visLoaded])

  const selected = selectedId ? theories.find(t => t.id === selectedId) : null
  const proEvidence = evidence.filter(e => e.stance === 'pro')
  const contraEvidence = evidence.filter(e => e.stance === 'contra')
  const totalEvidence = theories.reduce((acc, t) => acc + t.pro_count + t.contra_count, 0)

  const saveEdit = async () => {
    if (!selectedId) return
    try {
      await api.theories.update(selectedId, {
        name: editForm.name.trim() || selected?.name,
        description: editForm.description,
        status: editForm.status,
        parent_id: editForm.parent_id ? Number(editForm.parent_id) : null,
      })
      await load()
      toast.success('Teoria a fost actualizată')
    } catch (err: any) {
      toast.error('Teoria nu s-a putut salva: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const addEvidence = async () => {
    if (!selectedId || !evForm.entry_id) return
    try {
      await api.theories.addEvidence(selectedId, {
        entry_id: Number(evForm.entry_id),
        stance: evForm.stance,
        note: evForm.note,
        weight: evForm.weight,
      })
      setEvForm({ entry_id: '', stance: 'pro', note: '', weight: 3 })
      setShowAddEvidence(false)
      await Promise.all([api.theories.evidence(selectedId).then(setEvidence), load()])
      toast.success('Dovada a fost legată')
    } catch (err: any) {
      toast.error('Dovada nu s-a putut lega: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const addSubTheory = async () => {
    if (!selectedId || !subForm.name.trim()) return
    try {
      await api.theories.create({
        name: subForm.name.trim(),
        description: subForm.description,
        parent_id: selectedId,
        status: 'emerging',
      })
      setSubForm({ name: '', description: '' })
      setShowAddSub(false)
      await load()
      toast.success('Subteoria a fost creată')
    } catch (err: any) {
      toast.error('Subteoria nu s-a putut crea: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const fold = (t: string) => t.toLowerCase()
    .replace(/ă/g, 'a').replace(/â/g, 'a').replace(/î/g, 'i').replace(/ș/g, 's').replace(/ț/g, 't')

  const hasEvidence = (t: TheoryNode) => t.pro_count + t.contra_count > 0

  // A theory is visible when it matches the search itself, or when any of its
  // descendants does, so a match deep in the tree stays reachable.
  const matchesSearch = (t: TheoryNode): boolean => {
    if (!search.trim()) return true
    const q = fold(search.trim())
    if (fold(t.name).includes(q) || fold(t.description || '').includes(q)) return true
    return t.children.some(matchesSearch)
  }

  const matchesGap = (t: TheoryNode): boolean => {
    if (!onlyGaps) return true
    if (!hasEvidence(t) || t.parent_id === null) return true
    return t.children.some(matchesGap)
  }

  const visibleTree = tree.filter(t => matchesSearch(t) && matchesGap(t))
  const countVisible = (nodes: TheoryNode[]): number =>
    nodes.reduce((acc, n) => acc + 1 + countVisible(n.children), 0)
  const visibleCount = countVisible(visibleTree)
  const withoutEvidence = theories.filter(t => !hasEvidence(t)).length
  const rootCount = theories.filter(t => t.parent_id === null).length

  const toggleCollapse = (id: number) =>
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  if (loading) return <div className="text-center py-8 text-gray-500">Se încarcă agregatorul de teorii…</div>

  if (error && theories.length === 0) {
    return (
      <div className="card text-center py-12 border-madder/30">
        <p className="text-madder mb-2">⚠️ {error}</p>
        <button onClick={() => load()} className="btn-secondary">↻ Reîncearcă</button>
      </div>
    )
  }

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
        <div>
          <h2 className="text-xl font-bold">Agregator de teorii</h2>
          <p className="text-sm text-muted">
            {theories.length} teorii · {totalEvidence} legături de dovezi · o teorie poate deriva în subteorii
            {(withoutEvidence > 0 || rootCount > 0) && (
              <span className="block text-brass">
                {withoutEvidence} fără nicio dovadă · {rootCount} teorii rădăcină
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => {
              if (nodes.some(n => n.id === 't1')) {
                networkRef.current?.focus('t1', { scale: 0.85, animation: true })
              } else {
                networkRef.current?.fit({ animation: true })
              }
            }}
            className="btn-secondary text-xs sm:text-sm py-1.5 px-2.5"
            title="Concentrează pe arborele principal la scară lizibilă"
            aria-label="Resetează vederea arborelui"
          >⛶ Vedere lizibilă</button>
          <button
            onClick={() => networkRef.current?.fit({ animation: true })}
            className="btn-ghost text-xs sm:text-sm py-1.5 px-2.5"
            title="Încadrează toate teoriile în ecran"
            aria-label="Încadrează tot"
          >Încadrează tot</button>
          <button onClick={() => setShowCreate(true)} className="btn-primary text-xs sm:text-sm py-1.5 px-3 whitespace-nowrap">+ Teorie nouă</button>
        </div>
      </div>

      {/* Status legend */}
      <div className="flex flex-wrap gap-3 mb-3 px-1 text-xs text-muted">
      {(Object.keys(statusLabels) as TheoryStatus[]).map(s => (
          <span key={s} className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded inline-block" style={{ background: statusColors[s] }} />
            {statusLabels[s]}
          </span>
        ))}
        <span className="flex items-center gap-1.5 text-muted">
          nodurile arată numărul de dovezi <span className="font-medium text-verdigris">↗ pro</span>
          <span className="font-medium text-madder">↘ contra</span>
        </span>
      </div>

      {theories.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-500 mb-2">Nicio teorie încă.</p>
          <p className="text-sm text-gray-400">
            Pornește o sesiune de cercetare și harta teoriilor se construiește automat — sau creează teorii manual
            și leagă studii pro și contra.
          </p>
          <button onClick={() => setShowCreate(true)} className="btn-primary mt-4">Creează prima teorie</button>
        </div>
      ) : (
        <>
          {/* Search + gap filter: with 48 theories, finding one by name or
              finding the ones still unlinked is the main navigation. */}
          <div className="flex flex-wrap gap-2 mb-2 items-center">
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="input flex-1 min-w-[180px] text-sm"
              placeholder="Caută o teorie…"
              aria-label="Caută o teorie după nume sau descriere"
            />
            <button
              onClick={() => setOnlyGaps(v => !v)}
              className={`btn-ghost text-sm ${onlyGaps ? 'text-brass' : ''}`}
              aria-pressed={onlyGaps}
              aria-label="Arată doar teoriile fără dovezi și cele rădăcină"
            >
              {onlyGaps ? '✓ Doar lipsuri' : 'Doar lipsuri'}
            </button>
            <button
              onClick={() => setCollapsed(new Set())}
              className="btn-ghost text-sm"
              aria-label="Extinde toate ramurile"
            >Extinde tot</button>
            <span className="text-xs text-gray-500">
              {visibleCount} din {theories.length} în vedere
            </span>
          </div>

          {/* Interactive Theory Tree Graph */}
          <div className="card mb-2 block relative overflow-hidden p-0" style={{ height: '52vh', minHeight: '340px' }}>
            <div className="w-full h-full" ref={containerRef} />
            <div className="absolute bottom-3 right-3 z-10 flex flex-col gap-0.5 bg-surface/90 backdrop-blur-sm p-1 rounded-lg border border-line shadow-card">
              <button
                onClick={() => {
                  const s = networkRef.current?.getScale() || 1
                  networkRef.current?.moveTo({ scale: s * 1.3, animation: { duration: 250 } })
                }}
                className="w-7 h-7 flex items-center justify-center rounded text-muted hover:bg-surface-2 hover:text-ink font-bold text-base transition-colors"
                title="Mărește (Zoom in)"
              >+</button>
              <button
                onClick={() => {
                  const s = networkRef.current?.getScale() || 1
                  networkRef.current?.moveTo({ scale: s * 0.77, animation: { duration: 250 } })
                }}
                className="w-7 h-7 flex items-center justify-center rounded text-muted hover:bg-surface-2 hover:text-ink font-bold text-base transition-colors"
                title="Micșorează (Zoom out)"
              >−</button>
              <button
                onClick={() => networkRef.current?.fit({ animation: { duration: 350 } })}
                className="w-7 h-7 flex items-center justify-center rounded text-muted hover:bg-surface-2 hover:text-ink text-xs font-semibold transition-colors"
                title="Încadrează tot arborele"
              >⛶</button>
            </div>
          </div>
          {/* Keyboard-accessible fallback list (vis-network canvas is not
              keyboard-navigable) — every node is reachable and selectable. */}
          <div className="card mb-4 p-3 max-h-64 overflow-y-auto" aria-label="Lista arborelui de teorii">
            <h3 className="font-semibold text-sm mb-2">Arborele teoriilor (accesibil din tastatură)</h3>
            <ul className="space-y-0.5">
              {visibleTree.length === 0 ? (
                <li className="text-sm text-gray-500 py-2">Nicio teorie nu corespunde filtrului.</li>
              ) : visibleTree.map(root => (
                <TreeNodeRow
                  key={root.id}
                  node={root}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  depth={0}
                  collapsed={collapsed}
                  onToggle={toggleCollapse}
                  search={search}
                />
              ))}
            </ul>
          </div>
        </>
      )}

      {/* Create root theory */}
      {showCreate && (
        <div className="card mb-4 space-y-3">
          <h3 className="font-semibold">Teorie nouă</h3>
          <input value={createForm.name} onChange={e => setCreateForm(f => ({ ...f, name: e.target.value }))}
            className="input" placeholder="Numele teoriei" autoFocus />
          <textarea value={createForm.description} onChange={e => setCreateForm(f => ({ ...f, description: e.target.value }))}
            className="input min-h-[60px]" placeholder="Ce susține teoria?" />
          <div className="grid grid-cols-2 gap-2">
            <select value={createForm.parent_id} onChange={e => setCreateForm(f => ({ ...f, parent_id: e.target.value }))} className="input">
              <option value="">Teorie rădăcină</option>
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
            >Creează</button>
            <button onClick={() => setShowCreate(false)} className="btn-ghost">Anulează</button>
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
                  <option value="">Teorie rădăcină</option>
                  {theories.filter(t => t.id !== selected.id).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </div>
              <div className="flex gap-2">
                <button onClick={saveEdit} className="btn-primary text-sm">Salvează teoria</button>
                <button
                  onClick={async () => {
                    if (confirm('Ștergi această teorie? Dovezile se șterg, iar subteoriile devin teorii rădăcină.')) {
                      try {
                        await api.theories.delete(selected.id)
                        setSelectedId(null)
                        await load()
                        toast.success('Teoria a fost ștearsă')
                      } catch (err: any) {
                        toast.error('Teoria nu s-a putut șterge: ' + (err?.message || 'eroare necunoscută'))
                      }
                    }
                  }}
                  className="btn-ghost text-sm text-madder"
                >Șterge</button>
              </div>
            </div>
            <div className="text-right shrink-0 text-sm">
              <span className="badge badge-verdigris">pro {selected.pro_count}</span>{' '}
              <span className="badge badge-madder">contra {selected.contra_count}</span>
            </div>
          </div>

          {/* Evidence */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="font-semibold text-sm">Dovezi</h4>
              <div className="flex gap-2">
                <button onClick={() => setShowAddSub(!showAddSub)} className="btn-secondary text-xs">+ Subteorie</button>
                <button onClick={() => setShowAddEvidence(!showAddEvidence)} className="btn-primary text-xs">+ Leagă studiu</button>
              </div>
            </div>

            {showAddSub && (
              <div className="mb-3 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-2">
                <input value={subForm.name} onChange={e => setSubForm(f => ({ ...f, name: e.target.value }))}
                  className="input" placeholder="Numele subteoriei (o teorie poate deriva în mai multe)" />
                <textarea value={subForm.description} onChange={e => setSubForm(f => ({ ...f, description: e.target.value }))}
                  className="input min-h-[50px]" placeholder="Descriere" />
                <button onClick={addSubTheory} disabled={!subForm.name.trim()} className="btn-primary text-sm">Creează subteoria</button>
              </div>
            )}

            {showAddEvidence && (
              <div className="mb-3 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-2">
                <select value={evForm.entry_id} onChange={e => setEvForm(f => ({ ...f, entry_id: e.target.value }))} className="input">
                  <option value="">Alege studiu / intrare…</option>
                  {allEntries.map(e => <option key={e.id} value={e.id}>{e.title}{e.entry_date ? ` (${e.entry_date.slice(0, 4)})` : ''}</option>)}
                </select>
                <div className="grid grid-cols-2 gap-2">
                  <select value={evForm.stance} onChange={e => setEvForm(f => ({ ...f, stance: e.target.value as 'pro' | 'contra' }))} className="input">
                    <option value="pro">✅ Pro (susține)</option>
                    <option value="contra">❌ Contra (contrazice)</option>
                  </select>
                  <select value={evForm.weight} onChange={e => setEvForm(f => ({ ...f, weight: Number(e.target.value) }))} className="input">
                    {[1, 2, 3, 4, 5].map(w => <option key={w} value={w}>greutate {w}</option>)}
                  </select>
                </div>
                <input value={evForm.note} onChange={e => setEvForm(f => ({ ...f, note: e.target.value }))}
                  className="input" placeholder="Notă (cum susține sau contrazice)" />
                <button onClick={addEvidence} disabled={!evForm.entry_id} className="btn-primary text-sm">Leagă dovada</button>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <p className="text-xs font-medium text-verdigris mb-1.5">Dovezi pro ({proEvidence.length})</p>
                <div className="space-y-1.5">
                  {proEvidence.map(ev => (
                    <div key={ev.id} className="p-2 bg-verdigris-soft rounded-lg text-sm flex items-start justify-between gap-2">
                      <button
                        className="text-left font-medium hover:text-accent min-w-0 transition-colors"
                        onClick={() => {
                          const entry = allEntries.find(e => e.id === ev.entry_id)
                          if (entry) onSelectEntry(entry)
                        }}
                      >{ev.entry_title}</button>
                      <button onClick={async () => {
                        try {
                          await api.theories.removeEvidence(selected.id, ev.id)
                          await Promise.all([api.theories.evidence(selected.id).then(setEvidence), load()])
                        } catch (err: any) {
                          toast.error('Dovada nu s-a putut șterge: ' + (err?.message || 'eroare necunoscută'))
                        }
                      }} className="text-madder text-xs shrink-0"
                        aria-label={`Șterge dovada: ${ev.entry_title}`}
                        title="Șterge dovada"
                      >✕</button>
                    </div>
                  ))}
                  {proEvidence.length === 0 && <p className="text-xs text-gray-400">Niciun studiu care să o susțină încă.</p>}
                </div>
              </div>
              <div>
                <p className="text-xs font-medium text-madder mb-1.5">Dovezi contra ({contraEvidence.length})</p>
                <div className="space-y-1.5">
                  {contraEvidence.map(ev => (
                    <div key={ev.id} className="p-2 bg-madder-soft rounded-lg text-sm flex items-start justify-between gap-2">
                      <button
                        className="text-left font-medium hover:text-accent min-w-0 transition-colors"
                        onClick={() => {
                          const entry = allEntries.find(e => e.id === ev.entry_id)
                          if (entry) onSelectEntry(entry)
                        }}
                      >{ev.entry_title}</button>
                      <button onClick={async () => {
                        try {
                          await api.theories.removeEvidence(selected.id, ev.id)
                          await Promise.all([api.theories.evidence(selected.id).then(setEvidence), load()])
                        } catch (err: any) {
                          toast.error('Dovada nu s-a putut șterge: ' + (err?.message || 'eroare necunoscută'))
                        }
                      }} className="text-madder text-xs shrink-0"
                        aria-label={`Șterge dovada: ${ev.entry_title}`}
                        title="Șterge dovada"
                      >✕</button>
                    </div>
                  ))}
                  {contraEvidence.length === 0 && <p className="text-xs text-gray-400">Niciun studiu care să o contrazică încă.</p>}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


function TreeNodeRow({ node, selectedId, onSelect, depth, collapsed, onToggle, search }: {
  node: TheoryNode
  selectedId: number | null
  onSelect: (id: number) => void
  depth: number
  collapsed: Set<number>
  onToggle: (id: number) => void
  /** Active search: matches auto-expand so a hit deep in the tree is visible. */
  search: string
}) {
  const linked = node.pro_count + node.contra_count > 0
  const hasChildren = node.children.length > 0
  const isOpen = search.trim() ? true : !collapsed.has(node.id)
  const descendants = (t: TheoryNode): number =>
    t.children.reduce((acc, c) => acc + 1 + descendants(c), 0)
  return (
    <li>
      <div className="flex items-center gap-1">
        {hasChildren ? (
          <button
            onClick={() => onToggle(node.id)}
            className="shrink-0 w-7 h-7 flex items-center justify-center text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
            aria-expanded={isOpen}
            aria-label={`${isOpen ? 'Restrânge' : 'Extinde'} ramura ${node.name}`}
          >{isOpen ? '▾' : '▸'}</button>
        ) : (
          <span className="w-7 shrink-0" aria-hidden="true" />
        )}
        <button
          onClick={() => onSelect(node.id)}
          aria-pressed={selectedId === node.id}
          className={`flex-1 min-w-0 text-left text-sm flex flex-wrap items-center gap-x-2 gap-y-0.5 px-2 py-1 rounded hover:bg-surface-2 transition-colors ${
            selectedId === node.id ? 'bg-accent-soft text-accent-strong' : ''
          }`}
          title={node.description || node.name}
        >
          {/* Indent is halved on phones: 18px per level left the name 2px wide. */}
          <span className="hidden sm:inline-block shrink-0" style={{ width: depth * 18 }} aria-hidden="true" />
          <span className="inline-block sm:hidden shrink-0" style={{ width: depth * 8 }} aria-hidden="true" />
          <span className="w-2.5 h-2.5 rounded-full shrink-0 inline-block" style={{ background: statusColors[node.status] }} aria-hidden="true" />
          {/* The name keeps a usable width and may wrap; it used to be the only
              shrinkable cell, so on a phone it was squeezed to a sliver. */}
          <span className="min-w-[6ch] flex-1 break-words">{node.name}</span>
          {linked ? (
            <span className="text-xs text-muted shrink-0">
              <span className="text-verdigris">↗{node.pro_count}</span>{' '}
              <span className="text-madder">↘{node.contra_count}</span>
            </span>
          ) : (
            <span
              className="text-[10px] px-1.5 py-0.5 rounded shrink-0 bg-brass-soft text-brass"
              title="Teoria nu are nicio dovadă legată"
            >fără dovezi</span>
          )}
          {node.parent_id === null && (
            <span className="text-[10px] px-1.5 py-0.5 rounded shrink-0 bg-surface-2 text-muted">
              rădăcină
            </span>
          )}
          {!isOpen && hasChildren && (
            <span className="text-[10px] text-muted shrink-0">+{descendants(node)}</span>
          )}
          <span className="hidden sm:inline text-[10px] text-muted shrink-0">{statusLabels[node.status]}</span>
        </button>
      </div>
      {hasChildren && isOpen && (
        <ul>
          {node.children.map(child => (
            <TreeNodeRow
              key={child.id}
              node={child}
              selectedId={selectedId}
              onSelect={onSelect}
              depth={depth + 1}
              collapsed={collapsed}
              onToggle={onToggle}
              search={search}
            />
          ))}
        </ul>
      )}
    </li>
  )
}
