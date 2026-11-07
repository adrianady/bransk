import { useState, useEffect } from 'react'
import {
  Download,
  RefreshCw,
  FileText,
  AlertTriangle,
  Loader2,
  Search,
  Plus,
  Trash2,
  CheckSquare,
  Square,
  BookMarked,
} from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { subjectTags } from '../lib/tags'
import { useToast } from './Toast'
import { ImportStudiesModal } from './ImportStudiesModal'
import { StudyPdfModal } from './StudyPdfModal'
import type { Entry, ScreeningDecisionRow, ScreeningSummary } from '../lib/types'

interface Props {
  onSelectEntry: (entry: Entry) => void
}

const typeColors: Record<string, string> = {
  'peer-reviewed paper': 'bg-accent-soft text-accent-strong',
  'systematic review': 'bg-verdigris-soft text-verdigris',
  'meta-analysis': 'bg-verdigris-soft text-verdigris',
  book: 'bg-brass-soft text-brass',
  'case study': 'bg-madder-soft text-madder',
  study: 'bg-accent-soft text-accent-strong',
}

// Display-only translations: the stored values stay canonical (they are sent to
// the API and keyed in typeColors above), only the visible label changes.
const studyTypeLabels: Record<string, string> = {
  study: 'studiu',
  'peer-reviewed paper': 'studiu recenzat',
  book: 'carte',
  'systematic review': 'review sistematic',
  'meta-analysis': 'meta-analiză',
  'case study': 'studiu de caz',
}
const designLabels: Record<string, string> = {
  review: 'review',
  'experimental study': 'studiu experimental',
  'systematic review': 'review sistematic',
  'meta-analysis': 'meta-analiză',
  'cross-sectional': 'transversal',
  book: 'carte',
}
const effectLabels: Record<string, string> = {
  positive: 'pozitiv', negative: 'negativ', mixed: 'mixt',
}
const labelOf = (map: Record<string, string>, value: string) => map[value] || value

export function StudiesView({ onSelectEntry }: Props) {
  const toast = useToast()
  const [studies, setStudies] = useState<Entry[]>([])
  const [total, setTotal] = useState(0)
  const [theoryLinks, setTheoryLinks] = useState<Record<number, { theory_name: string; stance: string }[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filterType, setFilterType] = useState('')
  const [filterQuality, setFilterQuality] = useState(0)
  // PRISMA screening: record include/exclude/maybe per study, with a filter.
  const [screeningMode, setScreeningMode] = useState(false)
  const [screeningDecisions, setScreeningDecisions] = useState<Record<string, ScreeningDecisionRow>>({})
  const [screeningSummary, setScreeningSummary] = useState<ScreeningSummary | null>(null)
  const [filterScreening, setFilterScreening] = useState('')
  // Draft reasons per study id; saved on Enter from the inline input.
  const [reasonDrafts, setReasonDrafts] = useState<Record<number, string>>({})
  // Library filters the audit asked for: only readable / identified studies.
  const [filterPdf, setFilterPdf] = useState(false)
  const [filterDoi, setFilterDoi] = useState(false)
  const [filterYear, setFilterYear] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [downloadingAll, setDownloadingAll] = useState(false)
  const [downloadingPdf, setDownloadingPdf] = useState<Record<number, boolean>>({})
  // Study ids whose full text is being hunted right now (one request each).
  const [fetchingText, setFetchingText] = useState<Record<number, boolean>>({})
  const [importModalOpen, setImportModalOpen] = useState(false)
  const [pdfModalStudy, setPdfModalStudy] = useState<Entry | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [bulkDownloading, setBulkDownloading] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)

  const toggleSelectStudy = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const selectAllFiltered = () => {
    setSelectedIds(new Set(filtered.map(s => s.id)))
  }

  const clearSelection = () => {
    setSelectedIds(new Set())
  }

  const handleBulkDownload = async () => {
    if (selectedIds.size === 0) return
    setBulkDownloading(true)
    try {
      const res = await api.studies.bulkDownload(Array.from(selectedIds))
      toast.success(
        `Descărcat ${res.downloaded.length} PDF-uri` +
        (res.indexed_chunks ? `, ${res.indexed_chunks} fragmente indexate` : '') +
        (res.failed.length ? `, ${res.failed.length} eșuate (fără acces deschis sau offline)` : '')
      )
      await load()
    } catch (err: any) {
      toast.error('Descărcarea selecției a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setBulkDownloading(false)
    }
  }

  const handleBulkDelete = async () => {
    if (selectedIds.size === 0) return
    const confirmed = window.confirm(
      `Sigur doriți să ștergeți ${selectedIds.size} studii selectate din bibliotecă și graful de cunoștințe?`
    )
    if (!confirmed) return
    setBulkDeleting(true)
    try {
      const res = await api.studies.bulkDelete(Array.from(selectedIds))
      toast.success(`Au fost șterse ${res.deleted} studii.`)
      setSelectedIds(new Set())
      await load()
    } catch (err: any) {
      toast.error('Ștergerea selecției a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setBulkDeleting(false)
    }
  }

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [library, evidence] = await Promise.all([api.studies.all(), api.theories.allEvidence()])
      setStudies(library.items)
      setTotal(library.total)
      const map: Record<number, { theory_name: string; stance: string }[]> = {}
      evidence.forEach(e => {
        if (!map[e.entry_id]) map[e.entry_id] = []
        map[e.entry_id].push({ theory_name: e.theory_name, stance: e.stance })
      })
      setTheoryLinks(map)
    } catch (err) {
      // Without this branch the failure vanished: the user saw "0 din 0
      // studii" with no error and no way to retry.
      setStudies([])
      setTotal(0)
      setTheoryLinks({})
      setError(err instanceof Error && err.message ? err.message : 'Studiile nu s-au putut încărca.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const downloadAll = async () => {
    setDownloadingAll(true)
    try {
      const res = await api.studies.downloadAll()
      toast.success(
        `Descărcat ${res.downloaded.length} PDF-uri` +
        (res.indexed_chunks ? `, ${res.indexed_chunks} fragmente de text indexate` : '') +
        (res.failed.length ? `, ${res.failed.length} eșuate (offline, blocate sau nu sunt PDF-uri reale)` : '')
      )
      await load()
    } catch (err: any) {
      toast.error('Descărcarea în lot a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setDownloadingAll(false)
    }
  }

  const loadScreening = async () => {
    try {
      const [decisions, summary] = await Promise.all([
        api.studies.screeningDecisions(),
        api.studies.screeningSummary(),
      ])
      setScreeningDecisions(decisions)
      setScreeningSummary(summary)
    } catch {
      // Screening is auxiliary; the library must load without it.
    }
  }

  const applyScreening = async (studyId: number, decision: 'include' | 'exclude' | 'maybe', reason?: string) => {
    try {
      const updated = await api.studies.setScreening(studyId, { decision, reason: reason ?? '' })
      setScreeningDecisions(prev => ({ ...prev, [String(studyId)]: updated }))
      setReasonDrafts(prev => {
        const next = { ...prev }
        delete next[studyId]
        return next
      })
      setScreeningSummary(await api.studies.screeningSummary())
    } catch (err) {
      toast.error('Decizia nu s-a putut salva: ' + (err instanceof Error ? err.message : 'eroare'))
    }
  }

  const types = [...new Set(studies.map(s => s.study_type).filter(Boolean))].sort()
  const filtered = studies.filter(s => {
    if (filterType && s.study_type !== filterType) return false
    if (filterQuality > 0 && (s.quality ?? 0) < filterQuality) return false
    if (filterScreening) {
      const dec = screeningDecisions[String(s.id)]?.decision
      if (filterScreening === 'undecided' ? !!dec : dec !== filterScreening) return false
    }
    if (filterPdf && !s.pdf_path) return false
    if (filterDoi && !s.doi) return false
    if (filterYear.trim() && !(s.entry_date || '').startsWith(filterYear.trim())) return false
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      const match = (s.title || '').toLowerCase().includes(q) ||
                    (s.authors || '').toLowerCase().includes(q) ||
                    (s.tags || '').toLowerCase().includes(q) ||
                    (s.content || '').toLowerCase().includes(q) ||
                    (s.source || '').toLowerCase().includes(q)
      if (!match) return false
    }
    return true
  })

  if (loading && studies.length === 0) return <div className="text-center py-8 text-gray-500">Se încarcă studiile…</div>

  // Same pattern as GraphView: say what failed and offer a retry in place.
  if (error && studies.length === 0) {
    return (
      <div className="card text-center py-10 border-madder/25 space-y-3">
        <p className="text-sm text-madder flex items-center justify-center gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0" /> Studiile nu s-au putut încărca: {error}
        </p>
        <button onClick={load} className="btn-secondary text-sm">
          <RefreshCw className="w-4 h-4" /> Reîncearcă
        </button>
      </div>
    )
  }

  return (
    <div>
      {/* On a phone the two buttons used to sit on top of the subtitle: the row
          never wrapped and the subtitle had no room to shrink. Wrapping the
          header lets the buttons move under the title instead. */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="min-w-0">
          <h2 className="text-xl font-bold">Studii</h2>
          <p className="text-sm text-gray-500">
            Constatări din cercetarea calitativă · {filtered.length} din {total} studii
          </p>
        </div>
        <div className="flex flex-wrap gap-2 w-full sm:w-auto">
          <button
            onClick={() => setImportModalOpen(true)}
            className="btn-primary flex-1 sm:flex-none text-xs sm:text-sm whitespace-nowrap flex items-center justify-center gap-1.5 shadow-sm"
          >
            <Plus className="w-4 h-4" />
            Adaugă / Importă Studii
          </button>
          <button onClick={downloadAll} disabled={downloadingAll} aria-busy={downloadingAll} className="btn-secondary flex-1 sm:flex-none text-xs sm:text-sm whitespace-nowrap flex items-center justify-center gap-1">
            {downloadingAll ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            {downloadingAll ? 'Se descarcă PDF-urile…' : 'Descarcă toate PDF-urile'}
          </button>
          <button
            onClick={async () => {
              try {
                await api.citations.export('bibtex')
                toast.success('BibTeX descărcat (.bib)')
              } catch (err) {
                toast.error('Exportul a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
              }
            }}
            className="btn-secondary flex-1 sm:flex-none text-xs sm:text-sm whitespace-nowrap flex items-center justify-center gap-1"
            title="Exportă bibliografia pentru Zotero / Mendeley / Word"
          >
            <BookMarked className="w-4 h-4" />
            Export BibTeX
          </button>
          <button onClick={load} className="btn-secondary p-2" aria-label="Reîmprospătează studiile">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Filters & Search */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Caută în studii (titlu, autori, teme)..."
            className="input pl-9 pr-8 w-full text-sm"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xs"
              title="Șterge căutarea"
            >
              ✕
            </button>
          )}
        </div>
        <select value={filterType} onChange={e => setFilterType(e.target.value)} className="input w-auto text-sm">
          <option value="">Toate tipurile</option>
          {types.map(t => <option key={t} value={t}>{labelOf(studyTypeLabels, t)}</option>)}
        </select>
        <select value={filterQuality} onChange={e => setFilterQuality(Number(e.target.value))} className="input w-auto text-sm">
          <option value={0}>Orice calitate</option>
          <option value={3}>3★ și peste</option>
          <option value={4}>4★ și peste</option>
        </select>
        <button
          onClick={() => { const next = !screeningMode; setScreeningMode(next); if (next) loadScreening() }}
          className={`btn-secondary text-sm ${screeningMode ? '!bg-yellow-100 dark:!bg-yellow-950/40 !border-yellow-400' : ''}`}
          title="Mod PRISMA: decide include/exclude pentru fiecare studiu"
        >
          Screening PRISMA
        </button>
        <button
          onClick={() => setFilterPdf(!filterPdf)}
          className={`btn-secondary text-sm ${filterPdf ? '!bg-emerald-50 dark:!bg-emerald-950/40 !border-emerald-400' : ''}`}
          title="Doar studiile cu PDF descărcat local"
        >
          Are PDF
        </button>
        <button
          onClick={() => setFilterDoi(!filterDoi)}
          className={`btn-secondary text-sm ${filterDoi ? '!bg-emerald-50 dark:!bg-emerald-950/40 !border-emerald-400' : ''}`}
          title="Doar studiile cu DOI"
        >
          Are DOI
        </button>
        <input
          type="number"
          inputMode="numeric"
          value={filterYear}
          onChange={e => setFilterYear(e.target.value)}
          placeholder="an"
          aria-label="Filtrează după an"
          className="input w-20 text-sm"
          title="Anul publicării (prefix, ex. 2026)"
        />
        {screeningMode && (
          <select value={filterScreening} onChange={e => setFilterScreening(e.target.value)} className="input w-auto text-sm">
            <option value="">Toate deciziile</option>
            <option value="include">Doar incluse</option>
            <option value="exclude">Doar excluse</option>
            <option value="maybe">Doar „poate”</option>
            <option value="undecided">Doar nerecenzate</option>
          </select>
        )}
        {(filterType || filterQuality > 0 || searchQuery || filterScreening || filterPdf || filterDoi || filterYear) && (
          <button onClick={() => { setFilterType(''); setFilterQuality(0); setSearchQuery(''); setFilterScreening(''); setFilterPdf(false); setFilterDoi(false); setFilterYear('') }} className="btn-ghost text-sm">Golește filtrele</button>
        )}
        {screeningMode && screeningSummary && (
          <span className="text-xs text-gray-500 self-center">
            PRISMA: <b className="text-emerald-600 dark:text-emerald-400">{screeningSummary.included}</b> incluse ·
            {' '}<b className="text-madder">{screeningSummary.excluded}</b> excluse ·
            {' '}<b className="text-brass">{screeningSummary.maybe}</b> poate ·
            {' '}{screeningSummary.undecided} nerecenzate din {screeningSummary.studies_total}
          </span>
        )}
      </div>

      {/* A refresh failed but we still have the previous list: keep it on
          screen and tell the user, instead of replacing it with a spinner. */}
      {error && studies.length > 0 && (
        <div className="card mb-3 border-madder/25 text-sm text-madder flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2 min-w-0">
            <AlertTriangle className="w-4 h-4 shrink-0" /> Reîmprospătarea a eșuat: {error}
          </span>
          <button onClick={load} disabled={loading} className="btn-ghost text-sm">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Reîncearcă
          </button>
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-500 mb-2">Niciun studiu încă.</p>
          <p className="text-sm text-gray-400">
            Cere AI-ului să cerceteze un subiect — studiile se salvează aici, apar în cronologie după anul
            publicării și se leagă în graful de cunoștințe.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map(study => {
            const isSelected = selectedIds.has(study.id)
            return (
              <div
                key={study.id}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('button, a, input, select, textarea')) return
                  onSelectEntry(study)
                }}
                role="button"
                tabIndex={0}
                onKeyDown={e => {
                  // Same guard as onClick: Enter inside an inner input/button
                  // (e.g. the screening reason field) must not open the entry.
                  if ((e.target as HTMLElement).closest('button, a, input, select, textarea')) return
                  if (e.key === 'Enter') onSelectEntry(study)
                }}
                className={`card min-w-0 text-left cursor-pointer transition-all flex flex-col relative ${
                  isSelected
                    ? 'ring-2 ring-chrono-500 bg-chrono-50/20 dark:bg-chrono-950/20 border-chrono-400 dark:border-chrono-600 shadow-sm'
                    : 'hover:border-chrono-300 dark:hover:border-chrono-700'
                }`}
              >
                <div className="flex items-start gap-2 mb-1.5">
                  <button
                    type="button"
                    onClick={e => {
                      e.stopPropagation()
                      toggleSelectStudy(study.id)
                    }}
                    // 16px icon + padding = a 28px touch target; the negative
                    // margin keeps the row's layout unchanged.
                    className="p-1.5 -m-1.5 text-gray-400 hover:text-chrono-600 focus:outline-none shrink-0 rounded"
                    title={isSelected ? 'Deselectează studiul' : 'Selectează studiul'}
                    aria-label={isSelected ? 'Deselectează studiul' : 'Selectează studiul'}
                    aria-pressed={isSelected}
                  >
                    {isSelected ? (
                      <CheckSquare className="w-4 h-4 text-chrono-600" />
                    ) : (
                      <Square className="w-4 h-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200" />
                    )}
                  </button>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="font-semibold leading-snug break-words">{study.title}</h3>
                      {study.quality != null && (
                        <span className="text-xs shrink-0" title={`Calitate ${study.quality}/5`}>
                          {'★'.repeat(study.quality)}{'☆'.repeat(5 - study.quality)}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {study.study_type && (
                  <span className={`badge text-xs ${typeColors[study.study_type] || typeColors.study}`}>{labelOf(studyTypeLabels, study.study_type)}</span>
                )}
                {study.design && (
                  <span className="badge text-xs badge-accent">{labelOf(designLabels, study.design)}</span>
                )}
                {study.effect_direction && (
                  <span className={`badge text-xs ${
                    study.effect_direction === 'positive' ? 'badge-verdigris'
                    : study.effect_direction === 'negative' ? 'badge-madder'
                    : study.effect_direction === 'mixed' ? 'badge-brass'
                    : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                  }`}>
                    {study.effect_direction === 'positive' ? '↗' : study.effect_direction === 'negative' ? '↘' : study.effect_direction === 'mixed' ? '⇄' : '○'} {labelOf(effectLabels, study.effect_direction)}
                  </span>
                )}
                {study.entry_date && <span className="text-xs text-gray-500">📅 {study.entry_date.slice(0, 4)}</span>}
                {study.pdf_path ? (
                  <span className="badge text-xs badge-verdigris font-medium">PDF descărcat</span>
                ) : (study.source || study.content?.includes('**PDF:**')) ? (
                  <span className="badge text-xs badge-brass border border-brass/25">PDF disponibil</span>
                ) : null}
              </div>
              {study.authors && <p className="text-xs text-gray-500 mb-2 line-clamp-1 break-words">✍️ {study.authors}</p>}
              {screeningMode && (
                <div className="flex items-center gap-1.5 mb-2" onClick={e => { e.stopPropagation(); e.preventDefault() }}>
                  {(['include', 'exclude', 'maybe'] as const).map(dec => {
                    const current = screeningDecisions[String(study.id)]?.decision
                    const styles: Record<string, string> = {
                      include: 'bg-emerald-600 border-emerald-600',
                      exclude: 'bg-madder border-madder',
                      maybe: 'bg-brass border-brass',
                    }
                    const labels = { include: '✓ Include', exclude: '✕ Exclude', maybe: '? Poate' }
                    return (
                      <button
                        key={dec}
                        type="button"
                        onClick={() => applyScreening(study.id, dec)}
                        className={`text-[11px] px-2 py-0.5 rounded border font-semibold transition-colors ${
                          current === dec
                            ? `${styles[dec]} text-white`
                            : 'bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'
                        }`}
                      >
                        {labels[dec]}
                      </button>
                    )
                  })}
                  {screeningDecisions[String(study.id)]?.reason && !reasonDrafts[study.id] && (
                    <span className="text-[10px] text-gray-400 truncate" title={screeningDecisions[String(study.id)].reason}>
                      {screeningDecisions[String(study.id)].reason}
                    </span>
                  )}
                </div>
              )}
              {screeningMode && screeningDecisions[String(study.id)]?.decision && (
                <div className="mb-2" onClick={e => { e.stopPropagation(); e.preventDefault() }}>
                  <input
                    value={reasonDrafts[study.id] ?? ''}
                    onChange={e => setReasonDrafts(prev => ({ ...prev, [study.id]: e.target.value }))}
                    onKeyDown={e => {
                      if (e.key === 'Enter') {
                        const dec = screeningDecisions[String(study.id)].decision
                        applyScreening(study.id, dec, reasonDrafts[study.id] ?? '')
                      }
                    }}
                    placeholder="motivul deciziei (Enter salvează)"
                    className="input w-full text-[11px] py-1 px-2"
                    aria-label={`Motiv decizie pentru ${study.title}`}
                  />
                </div>
              )}
              {study.source && (
                <p className="text-xs text-gray-400 mb-2 break-all min-w-0">
                  🔗 {study.source}
                  <span className={`ml-1 ${study.url_validated ? 'text-verdigris' : 'text-brass'}`}>
                    {study.url_validated ? '✓ verificat' : '○ neverificat'}
                  </span>
                </p>
              )}
              <div className="flex items-center gap-2 mb-2">
                {study.pdf_path ? (
                  <button
                    type="button"
                    onClick={e => {
                      e.stopPropagation()
                      e.preventDefault()
                      setPdfModalStudy(study)
                    }}
                    className="btn-secondary text-xs px-2.5 py-1 inline-flex items-center gap-1 font-semibold text-chrono-700 dark:text-chrono-300 bg-chrono-50 dark:bg-chrono-950/60 border border-chrono-200 dark:border-chrono-800 hover:bg-chrono-100"
                    title="Deschide PDF-ul nativ în aplicație și adaugă notițe"
                  >
                    📄 Citește PDF & Notițe
                  </button>
                ) : (study.source || (study.content || study.content_preview || '').includes('**PDF:**')) ? (
                  <button
                    onClick={async e => {
                      e.stopPropagation()
                      setDownloadingPdf(prev => ({ ...prev, [study.id]: true }))
                      try {
                        await api.studies.download(study.id)
                        toast.success('PDF descărcat cu succes!')
                        await load()
                        setPdfModalStudy({ ...study, pdf_path: 'local' })
                      } catch (err) {
                        const timedOut = err instanceof Error && /timed out/i.test(err.message)
                        if (timedOut) {
                          toast.error('Descărcarea PDF a depășit 5 minute: sursa răspunde lent sau serverul încă procesează. Încearcă din nou.')
                        } else if (err instanceof ApiError) {
                          toast.error(`Serverul a refuzat descărcarea (HTTP ${err.status}): ${err.message}`)
                        } else {
                          toast.error('Descărcarea PDF a eșuat: ' + (err instanceof Error ? err.message : 'problemă de rețea'))
                        }
                      } finally {
                        setDownloadingPdf(prev => ({ ...prev, [study.id]: false }))
                      }
                    }}
                    disabled={downloadingPdf[study.id]}
                    aria-busy={downloadingPdf[study.id] || false}
                    className="btn-ghost text-xs px-2 py-1 disabled:opacity-50"
                  >
                    {downloadingPdf[study.id]
                      ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Se descarcă PDF-ul…</>
                      : <><FileText className="w-3.5 h-3.5" /> Descarcă PDF</>}
                  </button>
                ) : null}
                {/* Always available: the stored URL is often wrong, so let the
                    server hunt the paper in OpenAlex/Unpaywall/PMC/arXiv. */}
                <button
                  onClick={async e => {
                    e.stopPropagation()
                    setFetchingText(prev => ({ ...prev, [study.id]: true }))
                    try {
                      const res = await api.studies.fulltext(study.id)
                      if (res.status === 'pdf') {
                        toast.success(`PDF găsit (${res.source}) și indexat: ${res.chunks} fragmente.`)
                      } else if (res.status === 'html') {
                        toast.success(`Text integral găsit (${res.source}): ${Math.round(res.chars / 1000)}k caractere, ${res.chunks} fragmente.`)
                      } else {
                        toast.info(`Nicio sursă deschisă pentru acest studiu (încercate: ${res.tried.length || 0}).`)
                      }
                      await load()
                    } catch (err) {
                      toast.error('Căutarea textului integral a eșuat: ' + (err instanceof Error ? err.message : 'problemă de rețea'))
                    } finally {
                      setFetchingText(prev => ({ ...prev, [study.id]: false }))
                    }
                  }}
                  disabled={fetchingText[study.id]}
                  className="btn-ghost text-xs px-2 py-1"
                  title="Caută articolul în sursele deschise (OpenAlex, Unpaywall, Semantic Scholar, PMC, arXiv)"
                >
                  {fetchingText[study.id]
                    ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Caut…</>
                    : <><Search className="w-3.5 h-3.5" /> Text integral</>}
                </button>
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-3 flex-1 break-words">
                {(study.content_preview || study.content || '').replace(/[#*`\[\]()]/g, '').slice(0, 300)}
              </p>
              {study.tags && (
                <div className="flex gap-1 mt-2 flex-wrap">
                  {subjectTags(study.tags).filter(t => t !== 'study').slice(0, 4).map(t => (
                    <span key={t} className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">{t}</span>
                  ))}
                </div>
              )}
              {theoryLinks[study.id] && theoryLinks[study.id].length > 0 && (
                <div className="flex gap-1 mt-2 flex-wrap">
                  {theoryLinks[study.id].map((t, i) => (
                    <span key={i} title={`Dovadă pentru această teorie în agregator`}
                      className={`badge text-xs ${t.stance === 'pro' ? 'badge-verdigris' : 'badge-madder'}`}>
                      🧬 {t.theory_name} ({t.stance})
                    </span>
                  ))}
                </div>
              )}
            </div>
          )
        })}
        </div>
      )}

      {/* Floating Bulk Action Bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-gray-900/95 dark:bg-gray-800/95 text-white backdrop-blur-md px-5 py-3 rounded-2xl shadow-2xl border border-gray-700/60 flex flex-wrap items-center gap-3 animate-in fade-in slide-in-from-bottom-4 duration-200">
          <span className="text-xs font-semibold px-2.5 py-1 bg-chrono-600 rounded-full">
            {selectedIds.size} selectate
          </span>

          <div className="h-4 w-px bg-gray-700 hidden sm:block" />

          <button
            onClick={handleBulkDownload}
            disabled={bulkDownloading}
            aria-busy={bulkDownloading}
            className="btn-secondary text-xs bg-gray-800 hover:bg-gray-700 border-gray-600 text-white flex items-center gap-1.5 py-1.5 px-3"
            title="Descarcă PDF-uri pentru studiile selectate"
          >
            {bulkDownloading
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Se descarcă PDF-uri…</>
              : <><Download className="w-3.5 h-3.5" /> Descarcă PDF-uri ({selectedIds.size})</>}
          </button>

          <button
            onClick={selectAllFiltered}
            className="btn-ghost text-xs text-gray-300 hover:text-white py-1.5 px-2"
          >
            Selectează toate ({filtered.length})
          </button>

          <button
            onClick={clearSelection}
            className="btn-ghost text-xs text-gray-300 hover:text-white py-1.5 px-2"
          >
            Deselectează
          </button>

          <div className="h-4 w-px bg-gray-700 hidden sm:block" />

          <button
            onClick={handleBulkDelete}
            disabled={bulkDeleting}
            className="btn-ghost text-xs text-madder hover:bg-madder-soft flex items-center gap-1.5 py-1.5 px-2.5"
            title="Șterge studiile selectate"
          >
            {bulkDeleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
            Șterge ({selectedIds.size})
          </button>
        </div>
      )}

      {/* Import / Manual Add Studies Modal */}
      <ImportStudiesModal
        isOpen={importModalOpen}
        onClose={() => setImportModalOpen(false)}
        onImportComplete={load}
      />

      {/* Native In-App PDF Reader & Study Notes Modal */}
      <StudyPdfModal
        study={pdfModalStudy}
        isOpen={Boolean(pdfModalStudy)}
        onClose={() => setPdfModalStudy(null)}
        onNotesUpdated={load}
      />
    </div>
  )
}
