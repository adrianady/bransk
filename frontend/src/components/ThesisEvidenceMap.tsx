import { useEffect, useState } from 'react'
import { BookOpen, ExternalLink, Loader2, Plus, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { ProjectEvidenceMap, ProjectStudyRecommendation, StudyNote, StudySource } from '../lib/types'

interface Props {
  map: ProjectEvidenceMap | null
  studies: ProjectStudyRecommendation[]
  loading: boolean
  onRefresh: () => Promise<void>
  onSelectEntry: (entryId: number) => void
  onOpenSource?: (entryId: number, source: StudySource) => void
  focusClaimId?: number | null
}

const RELATIONS = [
  { value: 'supports', label: 'Susține' },
  { value: 'contradicts', label: 'Contrazice' },
  { value: 'contextualizes', label: 'Contextualizează' },
] as const

export function ThesisEvidenceMap({
  map, studies, loading, onRefresh, onSelectEntry, onOpenSource, focusClaimId,
}: Props) {
  const toast = useToast()
  const [claimDrafts, setClaimDrafts] = useState<Record<string, string>>({})
  const [creatingSection, setCreatingSection] = useState<string | null>(null)
  const [activeClaim, setActiveClaim] = useState<number | null>(null)
  const [selectedStudy, setSelectedStudy] = useState<number | null>(null)
  const [selectedDocument, setSelectedDocument] = useState<number | null>(null)
  const [selectedNote, setSelectedNote] = useState<number | null>(null)
  const [relation, setRelation] = useState<'supports' | 'contradicts' | 'contextualizes'>('supports')
  const [sources, setSources] = useState<StudySource[]>([])
  const [notes, setNotes] = useState<StudyNote[]>([])
  const [loadingStudySources, setLoadingStudySources] = useState(false)
  const [savingEvidence, setSavingEvidence] = useState(false)

  useEffect(() => {
    if (!focusClaimId) return
    document.getElementById(`thesis-claim-${focusClaimId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [map, focusClaimId])

  const createClaim = async (sectionId: string) => {
    const text = (claimDrafts[sectionId] || '').trim()
    if (!text || creatingSection) return
    setCreatingSection(sectionId)
    try {
      await api.project.createClaim(sectionId, text)
      setClaimDrafts(prev => ({ ...prev, [sectionId]: '' }))
      await onRefresh()
      toast.success('Afirmația a fost legată de capitol.')
    } catch (error) {
      toast.error('Afirmația nu s-a putut salva: ' + (error instanceof Error ? error.message : 'eroare'))
    } finally {
      setCreatingSection(null)
    }
  }

  const chooseStudy = async (entryId: number | null) => {
    setSelectedStudy(entryId)
    setSelectedDocument(null)
    setSelectedNote(null)
    setSources([])
    setNotes([])
    if (!entryId) return
    setLoadingStudySources(true)
    try {
      const [sourceResponse, noteResponse] = await Promise.all([
        api.studyQA.sources(entryId),
        api.studies.notes.list(entryId),
      ])
      setSources(sourceResponse.sources)
      setNotes(noteResponse)
    } catch (error) {
      toast.error('Fragmentele sau notițele nu s-au putut încărca: ' + (error instanceof Error ? error.message : 'eroare'))
    } finally {
      setLoadingStudySources(false)
    }
  }

  const saveEvidence = async () => {
    if (activeClaim == null || selectedStudy == null || savingEvidence) return
    setSavingEvidence(true)
    try {
      await api.project.addEvidence(activeClaim, {
        entry_id: selectedStudy,
        document_id: selectedDocument,
        note_id: selectedNote,
        relation,
      })
      setActiveClaim(null)
      setSelectedStudy(null)
      await onRefresh()
      toast.success('Dovada a fost salvată în hartă.')
    } catch (error) {
      toast.error('Dovada nu s-a putut salva: ' + (error instanceof Error ? error.message : 'eroare'))
    } finally {
      setSavingEvidence(false)
    }
  }

  const removeEvidence = async (claimId: number, evidenceId: number) => {
    try {
      await api.project.removeEvidence(claimId, evidenceId)
      await onRefresh()
      toast.success('Legătura a fost eliminată.')
    } catch (error) {
      toast.error('Legătura nu s-a putut elimina: ' + (error instanceof Error ? error.message : 'eroare'))
    }
  }

  const removeClaim = async (claimId: number) => {
    if (!window.confirm('Ștergi afirmația și legăturile ei cu dovezi?')) return
    try {
      await api.project.deleteClaim(claimId)
      await onRefresh()
      toast.success('Afirmația și legăturile sale au fost șterse.')
    } catch (error) {
      toast.error('Afirmația nu s-a putut șterge: ' + (error instanceof Error ? error.message : 'eroare'))
    }
  }

  const openEvidence = (entryId: number, document: StudySource | null, noteSources: StudySource[]) => {
    const source = document || noteSources.find(item => item.page_verified) || noteSources[0]
    if (source && onOpenSource) onOpenSource(entryId, source)
    else onSelectEntry(entryId)
  }

  if (loading || !map) {
    return <div className="card flex items-center justify-center gap-2 py-10 text-sm text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Se construiește harta…</div>
  }

  const progress = map.progress
  return (
    <div className="space-y-4">
      <div className="card space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="font-semibold text-sm">Harta capitolelor și dovezilor</h3>
            <p className="text-xs text-gray-500 mt-1">Afirmațiile, fragmentele și progresul sunt calculate din datele salvate.</p>
          </div>
          <button type="button" onClick={() => void onRefresh()} className="btn-secondary text-xs py-1 px-2">Reîmprospătează</button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
          <div className="rounded-lg bg-gray-50 dark:bg-gray-800/70 p-2"><span className="text-gray-500">Text</span><div className="font-semibold mt-0.5">{progress.written_words.toLocaleString('ro-RO')} / {progress.target_words.toLocaleString('ro-RO')} cuvinte</div></div>
          <div className="rounded-lg bg-gray-50 dark:bg-gray-800/70 p-2"><span className="text-gray-500">Redactare</span><div className="font-semibold mt-0.5">{progress.writing_percent}%</div></div>
          <div className="rounded-lg bg-gray-50 dark:bg-gray-800/70 p-2"><span className="text-gray-500">Afirmații cu dovezi</span><div className="font-semibold mt-0.5">{progress.claims_with_evidence} / {progress.total_claims}{progress.evidence_percent == null ? '' : ` · ${progress.evidence_percent}%`}</div></div>
          <div className="rounded-lg bg-gray-50 dark:bg-gray-800/70 p-2"><span className="text-gray-500">Fragmente PDF legate</span><div className="font-semibold mt-0.5">{progress.claims_with_pdf_fragment}</div></div>
        </div>
        <div className="h-2 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden" aria-label={`Progres redactare ${progress.writing_percent}%`}>
          <div className="h-full bg-chrono-600 transition-all" style={{ width: `${progress.writing_percent}%` }} />
        </div>
      </div>

      {map.sections.length === 0 ? (
        <div className="card border-dashed text-center py-8 text-sm text-gray-500">
          Nu există capitole în structura tezei. Adaugă sau generează o structură înainte de a lega afirmații.
        </div>
      ) : map.sections.map(section => (
        <section key={section.id} className="card space-y-3" aria-labelledby={`thesis-section-${section.id}`}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 id={`thesis-section-${section.id}`} className="font-semibold text-sm">{section.title}</h3>
              <p className="text-[11px] text-gray-500 mt-0.5">{section.written_words.toLocaleString('ro-RO')} / {section.target_words.toLocaleString('ro-RO')} cuvinte · {section.claims.length} afirmații</p>
            </div>
          </div>

          {section.claims.map(claim => (
            <article
              id={`thesis-claim-${claim.id}`}
              key={claim.id}
              className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-2 scroll-mt-24"
            >
              <div className="flex items-start gap-2">
                <p className="flex-1 text-sm leading-relaxed">{claim.text}</p>
                <button type="button" onClick={() => void removeClaim(claim.id)} className="icon-btn text-gray-400 hover:text-madder" aria-label="Șterge afirmația"><Trash2 className="w-3.5 h-3.5" /></button>
              </div>

              {claim.evidence.length ? (
                <div className="space-y-2">
                  {claim.evidence.map(evidence => {
                    const noteSources = evidence.note?.sources || []
                    return (
                      <div key={evidence.id} className="rounded-md bg-gray-50 dark:bg-gray-800/60 p-2.5 text-xs">
                        <div className="flex items-start gap-2">
                          <div className="flex-1 min-w-0">
                            <span className={`badge text-[10px] mr-1 ${evidence.relation === 'supports' ? 'badge-verdigris' : evidence.relation === 'contradicts' ? 'badge-brass' : 'badge-accent'}`}>
                              {RELATIONS.find(item => item.value === evidence.relation)?.label || evidence.relation}
                            </span>
                            <button type="button" onClick={() => onSelectEntry(evidence.entry_id)} className="font-semibold hover:underline text-left">{evidence.study_title}</button>
                            {evidence.study_authors && <span className="text-gray-500"> · {evidence.study_authors}</span>}
                            {(evidence.document || evidence.note) && (
                              <p className="mt-1 text-gray-600 dark:text-gray-300 leading-relaxed">
                                {evidence.document
                                  ? `Fragment #${evidence.document.chunk_index}${evidence.document.page_verified && evidence.document.page_number != null ? ` · pagina PDF ${evidence.document.page_number}` : ' · pagina neverificată'} — ${evidence.document.preview}`
                                  : evidence.note?.content}
                              </p>
                            )}
                            {evidence.note && noteSources.length > 0 && (
                              <p className="mt-1 text-[10px] text-gray-500">Notiță #{evidence.note.note_id} · {noteSources.length} fragmente-sursă</p>
                            )}
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            {(evidence.document || evidence.note) && (
                              <button type="button" onClick={() => openEvidence(evidence.entry_id, evidence.document, noteSources)} className="text-chrono-700 dark:text-chrono-300 hover:underline" title="Deschide studiul și fragmentul PDF"><ExternalLink className="w-3.5 h-3.5" /></button>
                            )}
                            <button type="button" onClick={() => void removeEvidence(claim.id, evidence.id)} className="text-gray-400 hover:text-madder" aria-label="Elimină dovada"><Trash2 className="w-3.5 h-3.5" /></button>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              ) : (
                <p className="text-[11px] text-gray-500">Încă nu există studii legate de această afirmație.</p>
              )}

              {activeClaim === claim.id ? (
                <div className="rounded-lg border border-chrono-200 dark:border-chrono-900/50 p-2.5 space-y-2">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <label className="text-[11px] text-gray-500">Studiu
                      <select className="input w-full text-xs mt-1" value={selectedStudy ?? ''} onChange={event => void chooseStudy(event.target.value ? Number(event.target.value) : null)}>
                        <option value="">Alege un studiu…</option>
                        {studies.map(study => <option key={study.entry_id} value={study.entry_id}>{study.title}</option>)}
                      </select>
                    </label>
                    <label className="text-[11px] text-gray-500">Relație
                      <select className="input w-full text-xs mt-1" value={relation} onChange={event => setRelation(event.target.value as typeof relation)}>
                        {RELATIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                      </select>
                    </label>
                  </div>
                  {selectedStudy != null && (loadingStudySources ? (
                    <p className="text-[11px] text-gray-500 flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> Se încarcă fragmentele…</p>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      <label className="text-[11px] text-gray-500">Fragment PDF (opțional)
                        <select className="input w-full text-xs mt-1" value={selectedDocument ?? ''} onChange={event => setSelectedDocument(event.target.value ? Number(event.target.value) : null)}>
                          <option value="">Fără fragment specific</option>
                          {sources.map(source => <option key={source.document_id} value={source.document_id}>#{source.chunk_index}{source.page_verified && source.page_number != null ? ` · pag. ${source.page_number}` : ' · pagină necunoscută'} — {source.preview.slice(0, 70)}</option>)}
                        </select>
                      </label>
                      <label className="text-[11px] text-gray-500">Notiță (opțional)
                        <select className="input w-full text-xs mt-1" value={selectedNote ?? ''} onChange={event => setSelectedNote(event.target.value ? Number(event.target.value) : null)}>
                          <option value="">Fără notiță</option>
                          {notes.map(note => <option key={note.id} value={note.id}>#{note.id} · {note.content.slice(0, 70)}</option>)}
                        </select>
                      </label>
                    </div>
                  ))}
                  <div className="flex justify-end gap-2">
                    <button type="button" onClick={() => { setActiveClaim(null); setSelectedStudy(null) }} className="btn-secondary text-xs py-1 px-2">Anulează</button>
                    <button type="button" onClick={() => void saveEvidence()} disabled={selectedStudy == null || loadingStudySources || savingEvidence} className="btn-primary text-xs py-1 px-2.5">
                      {savingEvidence ? 'Salvez…' : 'Leagă dovada'}
                    </button>
                  </div>
                </div>
              ) : (
                <button type="button" onClick={() => { setActiveClaim(claim.id); setRelation('supports') }} className="text-[11px] text-chrono-700 dark:text-chrono-300 hover:underline">
                  <BookOpen className="w-3 h-3" /> Leagă studiu sau fragment
                </button>
              )}
            </article>
          ))}

          <div className="flex gap-2">
            <input
              value={claimDrafts[section.id] || ''}
              onChange={event => setClaimDrafts(prev => ({ ...prev, [section.id]: event.target.value }))}
              onKeyDown={event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) void createClaim(section.id) }}
              className="input flex-1 min-w-0 text-xs"
              placeholder="Afirmație sau concept de susținut în capitol…"
              aria-label={`Afirmație nouă pentru ${section.title}`}
            />
            <button type="button" onClick={() => void createClaim(section.id)} disabled={!claimDrafts[section.id]?.trim() || creatingSection === section.id} className="btn-secondary text-xs px-2">
              {creatingSection === section.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />} Adaugă
            </button>
          </div>
        </section>
      ))}

      {map.orphan_claims.length > 0 && (
        <div className="card border-brass/40 bg-brass-soft/30 space-y-2">
          <h3 className="font-semibold text-sm">Afirmații fără capitol ({map.orphan_claims.length})</h3>
          <p className="text-xs text-gray-500">Acestea au rămas după schimbarea structurii. Dovezile se păstrează, dar nu mai au un capitol țintă.</p>
          {map.orphan_claims.map(claim => <p key={claim.id} className="text-sm">{claim.text}</p>)}
        </div>
      )}
    </div>
  )
}
