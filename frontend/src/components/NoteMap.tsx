import {
  useCallback, useMemo, useRef, useState,
} from 'react';
import { RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { useToast } from './Toast';
import type {
  NoteConcept, NoteDossierMapPayload, NoteEdgeProposal,
  NoteLink, NoteProjectMap, NoteProjectMapDoc,
} from '../lib/types';

type MapScope = 'dossier' | 'project';

/* ── helpers ────────────────────────────────────────────────────────────── */

function shortLabel(text: string, max = 40): string {
  const t = (text || '').trim();
  if (t.length <= max) return t;
  return t.slice(0, max).trimEnd() + '…';
}

function titleOf(doc: NoteProjectMapDoc): string {
  return (doc.title || `Doc #${doc.entry_id ?? '?'}`).trim() || 'Fără titlu';
}

/** Claim/dossier status → chip colour (same mapping as NotesPanel). */
function statusColor(status: string): { bg: string; border: string; text: string; dot: string } {
  const map: Record<string, { bg: string; border: string; text: string; dot: string }> = {
    verificat: { bg: 'bg-verdigris-soft', border: 'border-verdigris/20', text: 'text-verdigris', dot: 'bg-verdigris' },
    verificat_cu_nuanta: { bg: 'bg-chrono-50 dark:bg-chrono-950/20', border: 'border-chrono-300/40', text: 'text-chrono-600', dot: 'bg-chrono-600' },
    incomplet: { bg: 'bg-brass-soft', border: 'border-brass/20', text: 'text-brass', dot: 'bg-brass' },
    subestimat: { bg: 'bg-brass-soft', border: 'border-brass/20', text: 'text-brass', dot: 'bg-brass' },
    corectat: { bg: 'bg-madder-soft', border: 'border-madder/20', text: 'text-madder', dot: 'bg-madder' },
    depasit: { bg: 'bg-madder-soft', border: 'border-madder/20', text: 'text-madder', dot: 'bg-madder' },
    neverificat: { bg: 'bg-gray-100 dark:bg-gray-800', border: 'border-gray-200/60 dark:border-gray-700/60', text: 'text-gray-500', dot: 'bg-gray-500' },
  };
  return map[status] ?? map.neverificat;
}

function edgePage(link: { page?: number | null | string; page_printed?: string }): string {
  const p = link?.page
  if (p != null) return String(p)
  if (link?.page_printed) return link.page_printed
  return '—'
}

/* ── deterministic two-band layered layout ─────────────────────────────── */

interface LayoutNode {
  id: string;
  kind: 'doc' | 'claim' | 'concept';
  label: string;
  x: number;
  y: number;
  status: string;
  bridged?: boolean;
}

interface LayoutEdge {
  id: string;
  from: string;
  to: string;
  points: { x: number; y: number }[];
  motive: string;
  page: string;
  labelVisible: boolean;
  /** Original backend edge id, for dossier-scope deletion. */
  originalId?: number;
}

/**
 * Deterministic layered layout (no force simulation, no random).
 *
 * Dossier scope: claims on one horizontal band ordered by code; edges are
 * orthogonal right-angle polylines.
 * Project scope: documents on the left band, concepts on the right band,
 * each doc→concept link an orthogonal polyline. Concepts linked to ≥2
 * documents are bridged (emphasised).
 * Same input ⇒ same picture every time.
 */
function layout(scope: MapScope,
                docs: NoteProjectMapDoc[],
                claims: { code: string; statement: string; status: string }[],
                edges: NoteLink[],
                concepts: NoteConcept[],
                width: number,
                height: number): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const nodes: LayoutNode[] = [];
  const edgesOut: LayoutEdge[] = [];
  const pad = 40;
  const innerW = Math.max(200, width - pad * 2);
  const bandY = height / 2;

  if (scope === 'dossier') {
    const ordered = [...claims].sort((a, b) => a.code.localeCompare(b.code, 'ro'));
    const n = Math.max(ordered.length, 1);
    const step = innerW / n;
    ordered.forEach((c, i) => {
      nodes.push({
        id: c.code, kind: 'claim', label: shortLabel(c.statement),
        x: pad + step * (i + 0.5), y: bandY, status: c.status,
      });
    });
    edges.forEach((e, ei) => {
      const a = nodes.find(n => n.id === e.from_code);
      const b = nodes.find(n => n.id === e.to_code);
      if (!a || !b) return;
      const midY = bandY - 40;
      const points = [
        { x: a.x, y: bandY },
        { x: a.x, y: midY },
        { x: b.x, y: midY },
        { x: b.x, y: bandY },
      ];
      const motive = (e.motive || '').trim();
      edgesOut.push({
        id: `e${ei}`, from: e.from_code, to: e.to_code, points,
        motive, page: edgePage(e), labelVisible: motive.length > 0 && motive.length <= 40,
        originalId: e.id,
      });
    });
    return { nodes, edges: edgesOut };
  }

  // project scope: docs left band, concepts right band
  const docCount = docs.length;
  const conceptCount = concepts.length;
  const total = Math.max(docCount + conceptCount, 1);
  const step = innerW / (total + 1);
  docs.forEach((d, i) => {
    nodes.push({ id: `d${d.dossier_id}`, kind: 'doc', label: titleOf(d),
      x: pad + step * (i + 1), y: bandY, status: d.status });
  });
  concepts.forEach((c, i) => {
    const docIds = new Set(c.links.map(l => l.dossier_id));
    const bridged = docIds.size >= 2;
    nodes.push({
      id: `c${c.code}`, kind: 'concept', label: c.label,
      x: pad + step * (docCount + 1 + i), y: bandY,
      status: bridged ? 'bridged' : 'verificat_cu_nuanta', bridged,
    });
  });
  let ei = 0;
  concepts.forEach(c => {
    c.links.forEach(link => {
      const a = nodes.find(n => n.id === `d${link.dossier_id}`);
      const b = nodes.find(n => n.id === `c${c.code}`);
      if (!a || !b) return;
      const midY = bandY - 50;
      edgesOut.push({
        id: `e${ei++}`, from: `d${link.dossier_id}`, to: `c${c.code}`, points: [
          { x: a.x, y: bandY }, { x: a.x, y: midY }, { x: b.x, y: midY }, { x: b.x, y: bandY },
        ],
        motive: (link.note || '').trim(), page: edgePage(link),
        labelVisible: (link.note || '').trim().length > 0 && (link.note || '').trim().length <= 40,
      });
    });
  });
  return { nodes, edges: edgesOut };
}

function polylinePoints(points: { x: number; y: number }[]): string {
  return points.map(p => `${p.x},${p.y}`).join(' ');
}

/* ── component ─────────────────────────────────────────────────────────── */

interface Props {
  scope: MapScope;
  payload?: NoteDossierMapPayload | NoteProjectMap;
  dossierId?: number;
  onRefresh?: () => void;
}

export function NoteMap({ scope, payload, dossierId, onRefresh }: Props) {
  const toast = useToast();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [edgeError, setEdgeError] = useState<string | null>(null);
  const [edgeForm, setEdgeForm] = useState<{ from: string; to: string; motive: string; page: string }>({
    from: '', to: '', motive: '', page: '',
  });
  const [regenerating, setRegenerating] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);

  const docs = useMemo(() => {
    if (scope === 'project' && payload && 'documents' in payload) return (payload as NoteProjectMap).documents;
    if (scope === 'dossier' && payload && 'dossier_id' in payload) {
      const p = payload as NoteDossierMapPayload;
      return [{ dossier_id: p.dossier_id, entry_id: p.entry_id, title: p.title, authors: '', year: '', status: '', claims_total: 0, claims_verificat: 0 }];
    }
    return [];
  }, [scope, payload]);

  const claims = useMemo(() => {
    if (scope === 'dossier' && payload && 'claims' in payload) return (payload as NoteDossierMapPayload).claims;
    return [];
  }, [scope, payload]);

  const edges = useMemo(() => {
    if (scope === 'dossier' && payload && 'edges' in payload) return (payload as NoteDossierMapPayload).edges;
    return [];
  }, [scope, payload]);

  const concepts = useMemo(() => {
    if (scope === 'project' && payload && 'concepts' in payload) return (payload as NoteProjectMap).concepts;
    if (scope === 'dossier' && payload && 'concepts' in payload) return (payload as NoteDossierMapPayload).concepts ?? [];
    return [];
  }, [scope, payload]);

  const stats = useMemo(() => {
    if (scope === 'project' && payload && 'stats' in payload) return (payload as NoteProjectMap).stats;
    return { documents: 0, concepts: 0, bridged_concepts: 0, links: 0 };
  }, [scope, payload]);

  const containerWidth = svgRef.current?.clientWidth || 600;
  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(() =>
    layout(scope, docs, claims, edges, concepts, containerWidth, 360),
  [scope, docs, claims, edges, concepts, containerWidth]);

  const hasEdges = layoutEdges.length > 0;

  const selectedNode = useMemo(() =>
    layoutNodes.find(n => n.id === selectedId) ?? null,
  [layoutNodes, selectedId]);

  const selectedEdge = useMemo(() =>
    layoutEdges.find(e => e.id === selectedEdgeId) ?? null,
  [layoutEdges, selectedEdgeId]);

  const handleRegenerate = useCallback(async () => {
    setRegenerating(true);
    try {
      if (scope === 'project') {
        await api.noteDossiers.regenerateProjectMap({ replace: true });
        toast.success('Harta proiectului a fost regenerată.');
      } else if (dossierId) {
        await api.noteDossiers.regenerateMap(dossierId, { replace: true });
        toast.success('Harta dosarului a fost regenerată.');
      }
      onRefresh?.();
    } catch (err: any) {
      toast.error('Regenerarea hărții a eșuat: ' + (err?.message || 'eroare necunoscută'));
    } finally {
      setRegenerating(false);
    }
  }, [scope, dossierId, toast, onRefresh]);

  const handleAddEdge = useCallback(async () => {
    if (!dossierId) return;
    setEdgeError(null);
    const body: NoteEdgeProposal = {
      from_code: edgeForm.from,
      to_code: edgeForm.to,
      motive: edgeForm.motive,
      page: edgeForm.page || null,
    };
    try {
      await api.noteDossiers.addEdge(dossierId, body);
      setEdgeForm({ from: '', to: '', motive: '', page: '' });
      setEdgeError(null);
      await api.noteDossiers.regenerateMap(dossierId, { replace: true });
      onRefresh?.();
      toast.success('Muchia a fost adăugată.');
    } catch (err: any) {
      setEdgeError(err?.message || 'Muchia nu a putut fi adăugată.');
    }
  }, [dossierId, edgeForm, toast, onRefresh]);

  const handleRemoveEdge = useCallback(async (id: number) => {
    if (!confirm('Ștergi această muchie?')) return;
    try {
      await api.noteDossiers.removeEdge(id);
      onRefresh?.();
      toast.success('Muchia a fost ștearsă.');
    } catch (err: any) {
      toast.error('Ștergerea a eșuat: ' + (err?.message || 'eroare necunoscută'));
    }
  }, [onRefresh, toast]);

  const handleRemoveConcept = useCallback(async (id: number) => {
    if (!confirm('Ștergi acest concept?')) return;
    try {
      await api.noteDossiers.removeConcept(id);
      onRefresh?.();
      toast.success('Conceptul a fost șters.');
    } catch (err: any) {
      toast.error('Ștergerea a eșuat: ' + (err?.message || 'eroare necunoscută'));
    }
  }, [onRefresh, toast]);

  const onNodeSelect = useCallback((id: string) => setSelectedId(id === selectedId ? null : id), [selectedId]);

  /* ── empty explanation (shown when there is nothing to link yet) ──────── */

  const emptyHint = scope === 'dossier'
    ? 'Încă nicio muchie. Verifică afirmațiile din dosar (status „verificat”), apoi apasă „Regenerează harta” ca să le leg.'
    : 'Încă niciun concept comun. Verifică afirmațiile din dosarele proiectului, apoi apasă „Regenerează harta proiectului” ca să apară conceptele care leagă sursele.';

  /* ── SVG ────────────────────────────────────────────────────────────── */

  const edgeElements = layoutEdges.map(edge => {
    const mid = edge.points[1];
    return (
      <g key={edge.id} role="button" tabIndex={0}
         aria-label={`Muchie ${edge.from} → ${edge.to}: ${edge.motive}, pagina ${edge.page}`}
         onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.preventDefault(); }}
         onClick={() => setSelectedEdgeId(id => edge.id === id ? null : edge.id)}
         className="touch-40-neg">
        <polyline points={polylinePoints(edge.points)} fill="none"
                  stroke="rgb(var(--muted))" strokeWidth="1.5" strokeDasharray="4 2"
                  marker-end="url(#arrow)" />
        {edge.labelVisible && (
          <text x={mid.x} y={mid.y - 8} textAnchor="middle" fill="rgb(var(--muted))"
                fontSize="10" pointerEvents="none" className="no-select">
            {edge.motive.length > 40 ? edge.motive.slice(0, 37) + '…' : edge.motive}
          </text>
        )}
      </g>
    );
  });

  const nodeElements = layoutNodes.map(node => {
    const isSelected = selectedId === node.id;
    const style = statusColor(node.status);
    const cx = node.x, cy = node.y;
    return (
      <g key={node.id} role="button" tabIndex={0} aria-label={node.label}
         className="touch-40-neg"
         onClick={() => onNodeSelect(node.id)}
         onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onNodeSelect(node.id); } }}>
        <circle cx={cx} cy={cy} r={18} fill="rgb(var(--surface))" stroke="rgb(var(--muted))" strokeWidth="2"
                className={isSelected ? 'ring-2 ring-accent' : ''} />
        <circle cx={cx} cy={cy} r={14} fill="none" stroke={style.dot} strokeWidth="2" opacity={node.bridged ? 1 : 0.7} />
        <text x={cx} y={cy + 4} textAnchor="middle" fill="rgb(var(--ink))" fontSize="11" fontWeight="600" pointerEvents="none">
          {node.label}
        </text>
        {node.bridged && (
          <text x={cx} y={cy - 24} textAnchor="middle" fill="rgb(var(--madder))" fontSize="10" fontWeight="800" pointerEvents="none">◆</text>
        )}
        {isSelected && (
          <text x={cx} y={cy + 30} textAnchor="middle" fill="rgb(var(--accent))" fontSize="10" fontWeight="600" pointerEvents="none">● selectat</text>
        )}
      </g>
    );
  });

  /* ── small print ────────────────────────────────────────────────────── */

  const edgeLines = layoutEdges.map(e => (
    <p key={e.id} className="text-[11px] text-ink break-words leading-snug">
      <span className="font-mono text-muted">{e.from} → {e.to}</span> — {e.motive} <span className="text-muted">(p. {e.page})</span>
    </p>
  ));

  const conceptLines = concepts.map(c => {
    const docIds = new Set(c.links.map(l => l.dossier_id));
    const nDocs = docIds.size;
    const bridged = nDocs >= 2;
    return (
      <p key={c.code} className="text-[11px] text-ink break-words leading-snug">
        <span className="font-mono text-muted">{c.code}</span>{' '}
        <span className={bridged ? 'font-semibold text-madder' : 'font-semibold text-ink'}>{c.label}</span> — apare în:{' '}
        <span className={bridged ? 'font-semibold text-madder' : 'text-muted'}>{nDocs} documente</span>{bridged ? ' (concept transversalinstrument)' : ''}
      </p>
    );
  });

  /* ── detail panel ───────────────────────────────────────────────────── */

  let detailPanel = null;
  if (selectedNode) {
    const isClaim = selectedNode.kind === 'claim';
    const claim = claims.find(c => c.code === selectedNode.id);
    const doc = docs.find(d => `d${d.dossier_id}` === selectedNode.id);
    const concept = concepts.find(c => `c${c.code}` === selectedNode.id);
    const style = selectedNode ? statusColor(selectedNode.status) : null;

    detailPanel = (
      <section className="card p-3 sm:p-4 space-y-3 border-t border-line pt-3">
        <h4 className="font-semibold text-sm text-ink">Detalii selecție</h4>
        {isClaim && claim && (
          <>
            <p className="text-xs font-mono text-muted">{claim.code}</p>
            <p className="text-sm text-ink break-words">{claim.statement}</p>
            <p className="text-[11px] text-muted">
              {claim.page_printed ? `Pagina tipărită ${claim.page_printed}` : 'Fără pagina tipărită'} ·{' '}
              <span className={`font-semibold ${style?.text}`}>{claim.status}</span>
            </p>
          </>
        )}
        {!isClaim && doc && (
          <>
            <p className="text-sm text-ink break-words">{titleOf(doc)}</p>
            <p className="text-[11px] text-muted">
              {doc.authors ? `${doc.authors} · ${doc.year ?? '—'}` : '—'} ·{' '}
              {doc.claims_verificat} de {doc.claims_total} afirmații verificate
            </p>
          </>
        )}
        {!isClaim && !doc && concept && (
          <>
            <p className="text-sm text-ink break-words">{concept.label}</p>
            <p className="text-[11px] text-muted break-words">{concept.description || 'Fără descriere.'}</p>
            <p className="text-[11px] text-muted">Legaturi: {concept.links.length} · documente: {new Set(concept.links.map(l => l.dossier_id)).size}</p>
          </>
        )}
        {scope === 'dossier' && isClaim && claims.length > 1 && (
          <div className="space-y-2 pt-2 border-t border-line/40">
            <label className="text-[11px] font-semibold text-muted block">Muchie nouă</label>
            <select value={edgeForm.from} onChange={e => setEdgeForm(prev => ({ ...prev, from: e.target.value }))}
                    className="input text-xs w-full min-h-[40px] touch-40" aria-label="De la cod">
              <option value="">De la…</option>
              {claims.map(c => c.code !== selectedId ? <option key={c.code} value={c.code}>{c.code}</option> : null)}
            </select>
            <select value={edgeForm.to} onChange={e => setEdgeForm(prev => ({ ...prev, to: e.target.value }))}
                    className="input text-xs w-full mt-2 min-h-[40px] touch-40" aria-label="Către cod">
              <option value="">Către…</option>
              {claims.map(c => c.code !== selectedId ? <option key={c.code} value={c.code}>{c.code}</option> : null)}
            </select>
            <textarea value={edgeForm.motive} onChange={e => setEdgeForm(prev => ({ ...prev, motive: e.target.value }))}
                      placeholder="Motivul legăturii (minim 12 caractere)"
                      className="input text-xs w-full mt-2 min-h-[40px] touch-40" rows={2} aria-label="Motiv" />
            <input value={edgeForm.page} onChange={e => setEdgeForm(prev => ({ ...prev, page: e.target.value }))}
                   type="text" placeholder="Pagina" className="input text-xs w-full mt-2 min-h-[40px] touch-40" aria-label="Pagina" />
            {edgeError && <p className="text-[11px] text-madder">{edgeError}</p>}
            <button onClick={handleAddEdge} disabled={regenerating} className="btn-secondary text-xs min-w-[40px] min-h-[40px] touch-40">
              {regenerating ? <><RefreshCw className="w-3 h-3 animate-spin" /></> : <span>Adaugă muchia</span>}
            </button>
          </div>
        )}
        {(!isClaim && concept) && (
          <button onClick={() => handleRemoveConcept(concept.id as number)}
                  className="text-[11px] text-madder hover:bg-madder-soft min-w-[40px] min-h-[40px] touch-40 rounded-lg p-1.5" title="Șterge conceptul">
            <Trash2 className="w-3.5 h-3.5" /> Șterge conceptul
          </button>
        )}
      </section>
    );
  }

  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h4 className="font-semibold text-sm text-ink">{scope === 'dossier' ? 'Harta dosarului' : 'Harta proiectului'}</h4>
        <div className="flex items-center gap-1.5">
          <button onClick={handleRegenerate} disabled={regenerating} className="btn-secondary text-xs sm:text-sm min-w-[40px] min-h-[40px] touch-40" title="Regenerează harta (doar afirmații verificate)">
            {regenerating ? <><RefreshCw className="w-3 h-3 animate-spin" /> Regenerez…</> : <><RefreshCw className="w-3 h-3" /> Regenerează harta</>}
          </button>
        </div>
      </div>

      {!hasEdges && (
        <section className="card space-y-2 border-dashed border-line rounded-xl bg-gray-50 dark:bg-gray-800/40">
          <p className="text-sm text-muted">{emptyHint}</p>
          <button onClick={handleRegenerate} disabled={regenerating} className="btn-secondary text-xs sm:text-sm min-w-[40px] min-h-[40px] touch-40">
            {regenerating ? <><RefreshCw className="w-3 h-3 animate-spin" /> Regenerez…</> : <><RefreshCw className="w-3 h-3" /> Regenerează harta</>}
          </button>
        </section>
      )}

      <div className="relative w-full min-h-[360px] overflow-hidden rounded-lg border border-line bg-surface" role="region" aria-label="Hartă vizuală">
        <svg ref={svgRef} role="img" aria-labelledby="map-title" className="w-full h-auto max-h-[360px]" tabIndex={0}
             viewBox={`0 0 ${containerWidth} 360`} preserveAspectRatio="xMidYMid meet">
          <title id="map-title">{scope === 'dossier' ? 'Harta afirmațiilor dosarului' : 'Harta proiectului: documente și concepte'}</title>
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3.5" orient="auto">
              <path d="M0,0 L0,7 L7,3.5 z" fill="rgb(var(--muted))" />
            </marker>
          </defs>
          {edgeElements}
          {nodeElements}
        </svg>
      </div>

      <section className="space-y-1 text-[11px] text-ink">
        <p className="font-semibold text-muted uppercase tracking-wider text-xs">Mici detalii (surse)</p>
        {edgeLines.length > 0 ? edgeLines : <p className="text-muted">Fără muchii de afișat.</p>}
        {conceptLines.length > 0 ? (
          <>
            <p className="font-semibold text-muted uppercase tracking-wider text-xs mt-3">Concepte</p>
            {conceptLines}
          </>
        ) : null}
        <p className="text-[11px] text-muted mt-2">
          Statistici: {stats.documents} documente · {stats.concepts} concepte · {stats.bridged_concepts} transversale · {stats.links} legaturi.
        </p>
      </section>

      {detailPanel}

      {selectedEdge && scope === 'dossier' && (
        <section className="card p-3 sm:p-4 space-y-3 border-t border-line pt-3">
          <h4 className="font-semibold text-sm text-ink">Detalii muchie</h4>
          <p className="text-xs font-mono text-muted">{selectedEdge.from} → {selectedEdge.to}</p>
          <p className="text-sm text-ink break-words">{selectedEdge.motive}</p>
          <p className="text-[11px] text-muted">Pagina: {selectedEdge.page}</p>
          <button
            onClick={() => {
              if (selectedEdge.originalId != null) handleRemoveEdge(selectedEdge.originalId);
            }}
            className="text-[11px] text-madder hover:bg-madder-soft min-w-[40px] min-h-[40px] touch-40 rounded-lg p-1.5" title="Șterge muchia">
            <Trash2 className="w-3.5 h-3.5" /> Șterge muchia
          </button>
        </section>
      )}
    </section>
  );
}
