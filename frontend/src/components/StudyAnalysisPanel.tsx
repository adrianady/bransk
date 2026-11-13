import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  Edit3,
  Loader2,
  RefreshCw,
  TextQuote,
  X as XIcon,
} from 'lucide-react'
import { api } from '../lib/api'
import type {
  ClaimAggregate,
  ClaimStance,
  AnalysisCapabilities,
  VerifySummary,
  ParseSummary,
  StanceDirection,
} from '../lib/types'
import { SidePanel } from './SidePanel'

type StanceStatus = 'proposed' | 'accepted' | 'rejected' | 'insufficient'

const VERDICT_LABEL: Record<string, { label: string; color: string }> = {
  supports: { label: 'susține', color: 'text-verdigris border-verdigris/20 bg-verdigris-soft' },
  contradicts: { label: 'contrazice', color: 'text-madder border-madder/20 bg-madder-soft' },
  mixed: { label: 'mixt', color: 'text-brass border-brass/20 bg-brass-soft' },
  neutral: { label: 'neutru', color: 'text-gray-600 border-gray-200/60 bg-gray-100 dark:bg-gray-800 dark:border-gray-700/60' },
  insufficient: { label: 'insuficient', color: 'text-gray-600 border-gray-200/60 bg-gray-100 dark:bg-gray-800 dark:border-gray-700/60' },
}

const PDF_KIND_LABEL: Record<string, string> = {
  native: 'nativ',
  scanned: 'scatat',
  mixed: 'mixed',
  empty: 'gol',
  '': 'nespecificat',
}

function verdictChip(verdict: string) {
  const style = VERDICT_LABEL[verdict] ?? VERDICT_LABEL.insufficient
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-0.5 text-[10px] font-semibold border ${style.color}`}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0" />
      {style.label}
    </span>
  )
}

function statusChip(label: string, available: boolean, reason?: string) {
  return (
    <button
      type="button"
      className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-0.5 text-[10px] font-semibold border transition-colors ${
        available
          ? 'text-verdigris border-verdigris/20 bg-verdigris-soft hover:bg-verdigris-soft/80'
          : 'text-gray-500 border-gray-200/60 bg-gray-100 dark:bg-gray-800 dark:border-gray-700/60 hover:bg-gray-200 dark:hover:bg-gray-700'
      }`}
      title={available ? label : reason || 'Indisponibil'}
    >
      {available ? (
        <svg className="w-3 h-3 shrink-0 text-current" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"/></svg>
      ) : (
        <AlertTriangle className="w-3 h-3 shrink-0" />
      )}
      {label}
    </button>
  )
}

interface ClaimGroup {
  claimId: number
  claimName: string
  stances: EntryStance[]
}

/** A stance as returned by GET /entries/{entry_id}/analysis, which joins
 * theories to add claim_name / statement on each edge. */
type EntryStance = ClaimStance & { claim_name?: string; statement?: string }

export function StudyAnalysisPanel({ entryId, title, open, onClose }: Props) {
  const [parseSummary, setParseSummary] = useState<ParseSummary | null>(null)
  const [verifySummary, setVerifySummary] = useState<VerifySummary | null>(null)
  const [claimGroups, setClaimGroups] = useState<ClaimGroup[] | null>(null)
  const [aggregates, setAggregates] = useState<Record<number, ClaimAggregate>>({})
  const [capabilities, setCapabilities] = useState<Record<number, AnalysisCapabilities>>({})
  const [expandedClaim, setExpandedClaim] = useState<number | null>(null)
  const [editingStance, setEditingStance] = useState<{
    stanceId: number
    draftDirection: StanceDirection
    draftQuote: string
    draftNote: string
  } | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [decideError, setDecideError] = useState<{ stanceId: number; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [parsed, verified, forEntry] = await Promise.all([
        api.parse.summary(entryId),
        api.notes.verifySummary(entryId),
        api.claims.forEntry(entryId),
      ])
      setParseSummary(parsed)
      setVerifySummary(verified)

      // Group this entry's stances by claim.
      const byClaim = new Map<number, EntryStance[]>()
      for (const c of forEntry.claims) {
        const list = byClaim.get(c.claim_id) ?? []
        list.push(c)
        byClaim.set(c.claim_id, list)
      }
      const groups: ClaimGroup[] = []
      for (const [claimId, stances] of byClaim) {
        groups.push({
          claimId,
          claimName: stances[0].claim_name ?? `Claim ${claimId}`,
          stances: stances.sort((a, b) => a.id - b.id),
        })
      }
      setClaimGroups(groups)

      // Aggregate verdict + NLI capability per claim.
      const aggs: Record<number, ClaimAggregate> = {}
      const caps: Record<number, AnalysisCapabilities> = {}
      await Promise.all(
        groups.map(async (g) => {
          try {
            aggs[g.claimId] = await api.claims.aggregate(g.claimId)
          } catch {
            aggs[g.claimId] = {
              claim_id: g.claimId,
              verdict: 'insufficient',
              studies_accepted: 0,
            } as ClaimAggregate
          }
          try {
            caps[g.claimId] = await api.analysis.capabilities(g.claimId)
          } catch {
            caps[g.claimId] = { nli: { available: false }, llm_rationale: { available: false, reason: '' } }
          }
        }),
      )
      setAggregates(aggs)
      setCapabilities(caps)
    } catch (err: any) {
      setLoadError(err?.message || 'Analiza nu s-a putut încărca.')
    } finally {
      setLoading(false)
    }
  }, [entryId])

  useEffect(() => {
    if (open) void load()
  }, [open, entryId, load])

  const decideStance = async (stanceId: number, status: 'accepted' | 'rejected') => {
    setDecideError(null)
    try {
      await api.claims.decideStance(stanceId, { status })
      void load()
    } catch (err: any) {
      setDecideError({ stanceId, message: err?.message || 'Decizia nu s-a putut salva.' })
    }
  }

  const saveEdit = async () => {
    if (!editingStance) return
    setDecideError(null)
    try {
      await api.claims.decideStance(editingStance.stanceId, {
        status: 'accepted',
        direction: editingStance.draftDirection,
        evidence_quote: editingStance.draftQuote || undefined,
        correction: editingStance.draftNote || undefined,
      })
      setEditingStance(null)
      void load()
    } catch (err: any) {
      setDecideError({ stanceId: editingStance.stanceId, message: err?.message || 'Corecția nu s-a putut salva.' })
    }
  }

  const aggregateFor = (claimId: number): ClaimAggregate => aggregates[claimId]
  const capabilityFor = (claimId: number): AnalysisCapabilities | null => capabilities[claimId] ?? null
  const stancesFor = (claimId: number): ClaimStance[] => {
    const g = claimGroups?.find((cg) => cg.claimId === claimId)
    return g ? g.stances : []
  }

  if (!open) return null

  const isLoading = loading || parseSummary === null || verifySummary === null || claimGroups === null

  if (isLoading) {
    return (
      <SidePanel open={open} onClose={onClose} title={title} subtitle="Se analizează studiul…">
        <div className="space-y-3">
          <div className="h-4 bg-surface-2 rounded w-1/3 animate-pulse" />
          <div className="h-4 bg-surface-2 rounded w-2/3 animate-pulse" />
          <div className="h-4 bg-surface-2 rounded w-full animate-pulse" />
          <div className="h-4 bg-surface-2 rounded w-1/2 animate-pulse" />
          <div className="h-4 bg-surface-2 rounded w-full animate-pulse" />
          <div className="h-4 bg-surface-2 rounded w-3/4 animate-pulse" />
        </div>
      </SidePanel>
    )
  }

  if (loadError) {
    return (
      <SidePanel open={open} onClose={onClose} title={title}>
        <div className="card border-madder/25 bg-madder-soft/40 p-4 space-y-3">
          <p className="text-sm text-madder">
            <AlertTriangle className="w-4 h-4 shrink-0 mr-2" /> {loadError}
          </p>
          <button onClick={load} className="btn-secondary text-xs px-3 py-1.5">
            <RefreshCw className="w-3.5 h-3.5 shrink-0" /> Reîncearcă
          </button>
        </div>
      </SidePanel>
    )
  }

  const pdfLabel = PDF_KIND_LABEL[parseSummary.kind] ?? parseSummary.kind
  const verified = verifySummary
  // NLI chip: available when every claim has NLI; otherwise show the first reason.
  const firstCap = claimGroups[0] ? capabilityFor(claimGroups[0].claimId) : null
  const nliAvailable = claimGroups.length > 0 && claimGroups.every((g) => capabilityFor(g.claimId)?.nli.available ?? false)
  const nliReason = firstCap?.nli.reason

  return (
    <SidePanel open={open} onClose={onClose} title={title} width="96">
      {/* Header chips — study-level info, always shown even with no claims. */}
      <div className="mb-4 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-sm leading-snug">{title}</h3>
            <p className="text-[11px] text-muted leading-snug">
              Studiu #{entryId} · poziția față de claim-uri
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="badge text-xs badge-muted">PDF: {pdfLabel}</span>
          {verified ? (
            statusChip(
              `Verificare note: ${verified.notes_fully_verified}/${verified.notes_total}`,
              verified.notes_total > 0,
              'Încă nu există notițe de verificat pentru acest studiu.',
            )
          ) : (
            <span className="badge text-xs badge-muted">Verificare note: 0/0</span>
          )}
          {statusChip(
            `NLI: ${nliAvailable ? 'activ' : 'indisponibil'}`,
            nliAvailable,
            nliReason || 'Serviciul NLI nu este disponibil pentru aceste claim-uri.',
          )}
        </div>
      </div>

      {/* Claims list */}
      <div className="space-y-2">
        {claimGroups.length === 0 ? (
          <div className="card text-center py-10 space-y-3">
            <TextQuote className="w-8 h-8 text-muted mx-auto" />
            <p className="text-sm text-muted">Niciun claim încă — creează unul din Teorii.</p>
            <p className="text-[11px] text-gray-400">
              Când adaugi un claim în secțiunea Teorii, acesta apare aici cu propunerea de poziție a sistemului.
            </p>
          </div>
        ) : (
          claimGroups.map((g) => {
            const agg = aggregateFor(g.claimId)
            const isOpen = expandedClaim === g.claimId

            return (
              <div key={g.claimId} className="card border-line p-3 hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors">
                <div className="flex items-center justify-between gap-2 mb-2">
                  <button
                    type="button"
                    onClick={() => setExpandedClaim(isOpen ? null : g.claimId)}
                    className="flex items-start gap-2 text-left w-full min-w-0"
                  >
                    <ChevronRight className={`w-3.5 h-3.5 shrink-0 text-muted transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`} />
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-ink leading-snug truncate">{g.claimName}</p>
                      <p className="text-[10px] text-muted mt-0.5">
                        {agg.studies_accepted} studii · veredict {agg.verdict}
                      </p>
                    </div>
                    <div className="shrink-0">
                      {verdictChip(agg.verdict)}
                      {agg.studies_accepted < 2 && (
                        <span className="ml-1 inline-flex items-center gap-1 text-[10px] text-muted">
                          <span className="w-1.5 h-1.5 rounded-full bg-brass-soft text-brass shrink-0" />
                          sub pragul de 2 studii
                        </span>
                      )}
                    </div>
                  </button>
                  {decideError?.stanceId && stancesFor(g.claimId).some((s) => s.id === decideError.stanceId) && (
                    <span className="text-[10px] text-madder bg-madder-soft/40 border border-madder/20 rounded-md px-2 py-1 leading-snug">
                      {decideError.message}
                    </span>
                  )}
                </div>

                {isOpen && (
                  <div className="space-y-3 pt-2 border-t border-line/50">
                    {g.stances.length === 0 ? (
                      <p className="text-[11px] text-muted italic">Nicio poziție propusă încă pentru acest claim.</p>
                    ) : (
                      g.stances.map((stance) => (
                        <div key={stance.id} className="space-y-2">
                          {/* Quote block */}
                          <div className="card p-3 border-line bg-surface-2/50 space-y-2">
                            <div className="flex items-start gap-2">
                              <TextQuote className="w-3 h-3 mt-0.5 text-muted shrink-0" />
                              <div className="flex-1 min-w-0">
                                <p className="text-sm text-ink leading-snug break-words">{stance.evidence_quote}</p>
                                <span className="text-[10px] text-muted block mt-1">
                                  {stance.evidence_page != null ? `p. ${stance.evidence_page}` : 'p. ?'}
                                  {stance.evidence_page == null ? (
                                    <span className="ml-1" title="Numerotarea tipăritei nu a fost detectată">
                                      (numerotarea tipăritei nu a fost detectată)
                                    </span>
                                  ) : ''}
                                </span>
                              </div>
                            </div>
                            <p className="text-[11px] text-muted leading-snug">
                              {stance.reasoning || 'Fără motivare disponibilă.'}
                            </p>
                            {stance.confidence != null && (
                              <span className="text-[10px] text-muted">Încrezere: {(stance.confidence * 100).toFixed(0)}%</span>
                            )}
                            {stance.proposed_by === 'ai' && stance.status === 'proposed' && (
                              <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-brass bg-brass-soft/40 border border-brass/20 rounded-md px-1.5 py-0.5">
                                <Bot className="w-3 h-3 shrink-0" /> propus de AI · verifică
                              </span>
                            )}
                          </div>

                          {/* Verdict + actions */}
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex items-center gap-2">
                              {verdictChip(stance.direction)}
                              {stance.status === 'proposed' && <span className="text-[10px] text-muted">în așteptare</span>}
                              {stance.status === 'accepted' && <span className="text-[10px] text-verdigris font-medium">acceptat</span>}
                              {stance.status === 'rejected' && <span className="text-[10px] text-madder font-medium">respins</span>}
                            </div>
                            <div className="flex gap-1">
                              <button
                                type="button"
                                onClick={() => {
                                  setEditingStance({
                                    stanceId: stance.id,
                                    draftDirection: stance.direction,
                                    draftQuote: stance.evidence_quote,
                                    draftNote: '',
                                  })
                                }}
                                disabled={!!decideError?.stanceId && decideError.stanceId === stance.id}
                                className="btn-ghost text-[10px] px-2 py-1 disabled:opacity-50"
                                title="Editează poziția"
                              >
                                <Edit3 className="w-3 h-3 shrink-0" />
                              </button>
                              <button
                                type="button"
                                onClick={() => void decideStance(stance.id, 'accepted')}
                                disabled={!!decideError?.stanceId && decideError.stanceId === stance.id || stance.status === 'accepted'}
                                className="btn-secondary text-[10px] px-2.5 py-1 disabled:opacity-50"
                                aria-pressed={stance.status === 'accepted'}
                              >
                                Accept
                              </button>
                              <button
                                type="button"
                                onClick={() => void decideStance(stance.id, 'rejected')}
                                disabled={!!decideError?.stanceId && decideError.stanceId === stance.id || stance.status === 'rejected'}
                                className="btn-ghost text-[10px] px-2.5 py-1 text-madder hover:bg-madder-soft disabled:opacity-50"
                                aria-pressed={stance.status === 'rejected'}
                              >
                                Reject
                              </button>
                            </div>
                          </div>

                          {/* Inline edit form */}
                          {editingStance?.stanceId === stance.id && (
                            <div className="card p-3 border-accent-soft bg-accent-soft/30 space-y-2">
                              <div className="flex items-center gap-2">
                                <label className="text-[10px] font-semibold text-muted shrink-0">Direcție</label>
                                <select
                                  value={editingStance.draftDirection}
                                  onChange={(e) =>
                                    setEditingStance((prev) =>
                                      prev ? { ...prev, draftDirection: e.target.value as StanceDirection } : null
                                    )
                                  }
                                  className="input text-[11px] py-1 px-2 w-auto shrink-0"
                                >
                                  <option value="supports">Susține</option>
                                  <option value="contradicts">Contrazice</option>
                                  <option value="mixed">Mixt</option>
                                  <option value="neutral">Neutru</option>
                                </select>
                                <button
                                  type="button"
                                  onClick={() => setEditingStance(null)}
                                  className="text-[10px] text-muted hover:text-ink"
                                >
                                  <XIcon className="w-3 h-3 shrink-0" />
                                </button>
                              </div>
                              <div>
                                <label className="text-[10px] font-semibold text-muted block mb-1">Citat corectat</label>
                                <textarea
                                  value={editingStance.draftQuote}
                                  onChange={(e) =>
                                    setEditingStance((prev) => (prev ? { ...prev, draftQuote: e.target.value } : null))
                                  }
                                  rows={2}
                                  className="input text-[11px] min-h-[48px]"
                                  placeholder="Citatul corectat, cuvânt cu cuvânt"
                                />
                              </div>
                              <div>
                                <label className="text-[10px] font-semibold text-muted block mb-1">
                                  Ce am înțeles greșit / cum este de fapt
                                </label>
                                <textarea
                                  value={editingStance.draftNote}
                                  onChange={(e) =>
                                    setEditingStance((prev) => (prev ? { ...prev, draftNote: e.target.value } : null))
                                  }
                                  rows={2}
                                  className="input text-[11px] min-h-[48px]"
                                  placeholder="Notează corecția ta alături de citat…"
                                />
                              </div>
                              <div className="flex gap-2">
                                <button
                                  type="button"
                                  onClick={saveEdit}
                                  className="btn-secondary text-[10px] px-3 py-1.5"
                                >
                                  Salvează corecția
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setEditingStance(null)}
                                  className="btn-ghost text-[10px] px-3 py-1.5"
                                >
                                  Anulează
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </SidePanel>
  )
}

interface Props {
  entryId: number
  title: string
  open: boolean
  onClose: () => void
}
