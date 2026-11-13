import { useCallback, useRef, useState, useEffect } from 'react'
import {
  Copy,
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
  ChevronDown,
  BookOpen,
  MoreHorizontal,
  TextQuote,
} from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { subjectTags } from '../lib/tags'
import { getStudyDoi } from '../lib/doi'
import { useToast } from './Toast'

// Offline library cache: written on every successful load so a cold start
// with the server unreachable (e.g. the phone's Tailscale is off) still shows
// the library and opens the PDFs already cached on the device.
const STUDIES_CACHE_KEY = 'chronokg-studies-cache'

interface StudiesCache {
  at: number
  items: Entry[]
  total: number
  evidence: unknown[]
}

function readStudiesCache(): StudiesCache | null {
  try {
    const raw = localStorage.getItem(STUDIES_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as StudiesCache
    if (!parsed || !Array.isArray(parsed.items)) return null
    return parsed
  } catch {
    return null
  }
}

function buildTheoryMap(evidence: unknown[]): Record<number, { theory_name: string; stance: string }[]> {
  const map: Record<number, { theory_name: string; stance: string }[]> = {}
  for (const item of evidence) {
    const e = item as { entry_id?: number; theory_name?: string; stance?: string }
    if (typeof e.entry_id !== 'number' || !e.theory_name || !e.stance) continue
    if (!map[e.entry_id]) map[e.entry_id] = []
    map[e.entry_id].push({ theory_name: e.theory_name, stance: e.stance })
  }
  return map
}
import { ImportStudiesModal } from './ImportStudiesModal'
import { DuplicatesModal } from './DuplicatesModal'
import { StudyPdfModal } from './StudyPdfModal'
import { StudyAnalysisPanel } from './StudyAnalysisPanel'
import {
  LIBRARY_DOI_KEY,
  LIBRARY_PAGE_KEY,
  LIBRARY_PDF_KEY,
  LIBRARY_QUALITY_KEY,
  LIBRARY_SEARCH_KEY,
  LIBRARY_TYPE_KEY,
  LIBRARY_YEAR_KEY,
  readStoredNumber,
  readStoredString,
  writeStored,
} from '../lib/libraryState'
import type { Entry, ScreeningDecisionRow, ScreeningSummary } from '../lib/types'

type PdfFilter = 'all' | 'with' | 'without'

interface Props {
  onSelectEntry: (entry: Entry) => void
  /** Pill-driven PDF facet from the unified library. */
  pdfFilter?: PdfFilter
  /** Quick facet from LibraryView: only books (study_type = book). */
  typePreset?: string
  /** Quick facet from LibraryView: 'read' = citite + adnotate. */
  readPreset?: string
  /** Mobile sheet: create a new entry (LibraryView's „Intrare nouă”). */
  onCreateEntry?: () => void
  /** Mobile sheet: check for duplicates (LibraryView's „Verifică duplicate”). */
  onCheckDuplicates?: () => void
}

const typeColors: Record<string, string> = {
  'peer-reviewed paper': 'bg-accent-soft text-accent-strong',
  'systematic review': 'bg-verdigris-soft text-verdigris',
  'meta-analysis': 'bg-verdigris-soft text-verdigris',
  book: 'bg-brass-soft text-brass',
  'case study': 'bg-madder-soft text-madder',
  study: 'bg-accent-soft text-accent-strong',
}

/** Heuristic: does the saved source actually look like a direct PDF link?
 *  A Crossref DOI landing page promised "PDF disponibil" for years and the
 *  download button then failed with 502 — the badge now tells the truth. */
function hasDirectPdfHint(study: Entry): boolean {
  if (study.content?.includes('**PDF:**')) return true
  const src = (study.source || '').toLowerCase()
  if (!src) return false
  return /\.pdf([?#].*)?$/.test(src) || src.includes('/pdf/') || src.includes('download')
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

export const READ_STATUS_STYLES: Record<string, string> = {
  unread: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
  reading: 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-200',
  read: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-200',
  annotated: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950/60 dark:text-indigo-200',
}

export const READ_STATUS_LABELS: Record<string, string> = {
  unread: 'Necitit',
  reading: 'În lectură',
  read: 'Citit',
  annotated: 'Adnotat',
}

/* Mobile-only dismissible sheet that gathers the secondary actions removed
   from the header / filters row on screens below `sm`. Each entry calls the
   exact handler it calls today — only its location changes. */
function MobileActionsSheet({
  open,
  onClose,
  onCheckDuplicates,
  onCreateEntry,
  onDownloadAll,
  onExportBibtex,
  onToggleScreening,
  onToggleQuality,
  onToggleAdvanced,
  onRefresh,
}: {
  open: boolean
  onClose: () => void
  onCheckDuplicates: () => void
  onCreateEntry: () => void
  onDownloadAll: () => void
  onExportBibtex: () => void
  onToggleScreening: () => void
  onToggleQuality: () => void
  onToggleAdvanced: () => void
  onRefresh: () => void
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 sm:hidden" onClick={onClose}>
      <div className="absolute inset-0 bg-ink/40 backdrop-blur-sm" />
      <div
        className="fixed bottom-4 left-4 right-4 z-50 w-full max-w-sm mx-auto rounded-xl border border-line bg-surface shadow-pop p-3"
        onClick={e => e.stopPropagation()}
      >
        <div className="font-semibold text-ink mb-3">Mai multe acțiuni</div>
        <button
          onClick={() => { onClose(); onCheckDuplicates() }}
          className="w-full inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-left min-h-[40px] hover:bg-surface-2"
        >
          <Copy className="w-4 h-4 shrink-0" />
          <span>Verifică duplicate</span>
        </button>
        <button
          onClick={() => { onClose(); onCreateEntry() }}
          className="w-full inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-left min-h-[40px] hover:bg-surface-2"
        >
          <Plus className="w-4 h-4 shrink-0" />
          <span>Intrare nouă</span>
        </button>
        <button
          onClick={() => { onClose(); onDownloadAll() }}
          className="w-full inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-left min-h-[40px] hover:bg-surface-2"
        >
          <Download className="w-4 h-4 shrink-0" />
          <span>Descarcă toate PDF-urile</span>
        </button>
        <button
          onClick={() => { onClose(); onExportBibtex() }}
          className="w-full inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-left min-h-[40px] hover:bg-surface-2"
        >
          <BookMarked className="w-4 h-4 shrink-0" />
          <span>Export BibTeX</span>
        </button>
        <button
          onClick={() => { onClose(); onToggleScreening() }}
          className="w-full inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-left min-h-[40px] hover:bg-surface-2"
        >
          <span className="shrink-0">⚖️</span>
          <span>Screening PRISMA</span>
        </button>
        <button
          onClick={() => { onClose(); onToggleQuality() }}
          className="w-full inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-left min-h-[40px] hover:bg-surface-2"
        >
          <span className="shrink-0">⭐</span>
          <span>Calitate 4★+</span>
        </button>
        <button
          onClick={() => { onClose(); onToggleAdvanced() }}
          className="w-full inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-left min-h-[40px] hover:bg-surface-2"
        >
          <ChevronDown className="w-4 h-4 shrink-0" />
          <span>Filtre avansate</span>
        </button>
        <button
          onClick={() => { onClose(); onRefresh() }}
          className="w-full inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-left min-h-[40px] hover:bg-surface-2"
        >
          <RefreshCw className="w-4 h-4 shrink-0" />
          <span>Reîmprospătează</span>
        </button>
      </div>
    </div>
  )
}

export function StudiesView({ onSelectEntry, pdfFilter, typePreset, readPreset, onCreateEntry, onCheckDuplicates }: Props) {
  const toast = useToast()
  const [studies, setStudies] = useState<Entry[]>([])
  const [total, setTotal] = useState(0)
  const [theoryLinks, setTheoryLinks] = useState<Record<number, { theory_name: string; stance: string }[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [offlineNotice, setOfflineNotice] = useState('')
  const [filterType, setFilterType] = useState(() => readStoredString(LIBRARY_TYPE_KEY))
  const [filterQuality, setFilterQuality] = useState(() => readStoredNumber(LIBRARY_QUALITY_KEY))
  // PRISMA screening: record include/exclude/maybe per study, with a filter.
  const [screeningMode, setScreeningMode] = useState(false)
  const [screeningDecisions, setScreeningDecisions] = useState<Record<string, ScreeningDecisionRow>>({})
  const [screeningSummary, setScreeningSummary] = useState<ScreeningSummary | null>(null)
  const [filterScreening, setFilterScreening] = useState('')
  // Draft reasons per study id; saved on Enter from the inline input.
  const [reasonDrafts, setReasonDrafts] = useState<Record<number, string>>({})
  // Library filters the audit asked for: only readable / identified studies.
  // The unified library's „Cu PDF” / „Fără PDF” pills drive this too.
  const [filterPdf, setFilterPdf] = useState<PdfFilter>(() => {
    if (pdfFilter) return pdfFilter
    const stored = readStoredString(LIBRARY_PDF_KEY)
    return stored === 'with' || stored === 'without' ? stored : 'all'
  })
  const [filterDoi, setFilterDoi] = useState(() => readStoredString(LIBRARY_DOI_KEY) === '1')
  const [filterYear, setFilterYear] = useState(() => readStoredString(LIBRARY_YEAR_KEY))
  const [searchQuery, setSearchQuery] = useState(() => readStoredString(LIBRARY_SEARCH_KEY))
  const [page, setPage] = useState(() => {
    const stored = readStoredNumber(LIBRARY_PAGE_KEY)
    return stored > 0 ? stored : 1
  })
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)
  // Mobile: one dismissible sheet holding the secondary actions.
  const [sheetOpen, setSheetOpen] = useState(false)
  const [downloadingAll, setDownloadingAll] = useState(false)
  const [downloadingPdf, setDownloadingPdf] = useState<Record<number, boolean>>({})
  const [uploadingPdf, setUploadingPdf] = useState<Record<number, boolean>>({})
  // Study ids whose full text is being hunted right now (one request each).
  const [fetchingText, setFetchingText] = useState<Record<number, boolean>>({})
  const [importModalOpen, setImportModalOpen] = useState(false)
  const [duplicatesOpen, setDuplicatesOpen] = useState(false)
  const [pdfModalStudy, setPdfModalStudy] = useState<Entry | null>(null)
  // One open PDF action menu at a time (per-study ⋯ menu).
  const [pdfMenuId, setPdfMenuId] = useState<number | null>(null)
  // The study whose analysis panel is open on the right.
  const [analysisEntry, setAnalysisEntry] = useState<Entry | null>(null)
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
      const downloaded = new Set(res.downloaded)
      setStudies(prev => prev.map(s => downloaded.has(s.id) ? { ...s, pdf_path: s.pdf_path || 'local' } : s))
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

  // Monotonic request id + in-flight flag (same pattern family as
  // EntryList's loadRef guard): rapid tab switching fires overlapping
  // load() calls whose responses can land out of order, so an older
  // snapshot must never overwrite a newer one.
  const loadSeq = useRef(0)
  const loadInFlight = useRef(false)

  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    loadInFlight.current = true
    setLoading(true)
    setError('')
    setOfflineNotice('')
    try {
      const [library, evidence] = await Promise.all([api.studies.all(), api.theories.allEvidence()])
      // A newer load() started while this one was in flight: drop the stale
      // snapshot instead of overwriting the fresh list.
      if (seq !== loadSeq.current) return
      setStudies(library.items)
      setTotal(library.total)
      const map: Record<number, { theory_name: string; stance: string }[]> = {}
      evidence.forEach(e => {
        if (!map[e.entry_id]) map[e.entry_id] = []
        map[e.entry_id].push({ theory_name: e.theory_name, stance: e.stance })
      })
      setTheoryLinks(map)
      // Persist for offline cold starts (best-effort; quota failures ignored).
      try {
        localStorage.setItem(STUDIES_CACHE_KEY, JSON.stringify({ at: Date.now(), items: library.items, total: library.total, evidence }))
      } catch { /* localStorage full — offline hydration simply won't apply */ }
    } catch (err) {
      if (seq !== loadSeq.current) return
      // Without this branch the failure vanished: the user saw "0 din 0
      // studii" with no error and no way to retry.
      const cached = readStudiesCache()
      if (cached && cached.items.length > 0) {
        // Server unreachable but the device has data: open from the local
        // cache so reading cached PDFs and writing notes still works.
        setStudies(cached.items)
        setTotal(cached.total)
        setTheoryLinks(buildTheoryMap(cached.evidence))
        setOfflineNotice(
          `Serverul nu răspunde — biblioteca este din cache local (${cached.items.length} studii, din ${new Date(cached.at).toLocaleString('ro-RO')}). Citirea PDF-urilor din cache funcționează.`,
        )
        return
      }
      setStudies([])
      setTotal(0)
      setTheoryLinks({})
      setError(err instanceof Error && err.message ? err.message : 'Studiile nu s-au putut încărca.')
    } finally {
      // Only the latest request may clear the busy state; a stale response
      // must leave the newer load's spinner and in-flight flag alone.
      if (seq === loadSeq.current) {
        loadInFlight.current = false
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // Chat actions, imports and PDF downloads happen outside this component;
  // refresh when the tab regains focus so the list reflects the server
  // without a manual reload or a backend restart.
  // loadRef keeps the listener on the latest load without re-subscribing
  // (EntryList.tsx uses the same shape); the auto-refresh is skipped while
  // a load is already in flight, since that fetch is at least as fresh.
  const loadRef = useRef(load)
  loadRef.current = load
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === 'visible' && !loadInFlight.current) void loadRef.current()
    }
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [])

  // Remember the library's place: opening an entry remounts this screen, and
  // the user must come back to the same search, filters and PDF facet.
  useEffect(() => { writeStored(LIBRARY_SEARCH_KEY, searchQuery) }, [searchQuery])
  useEffect(() => { writeStored(LIBRARY_TYPE_KEY, filterType) }, [filterType])
  useEffect(() => { writeStored(LIBRARY_QUALITY_KEY, filterQuality) }, [filterQuality])
  useEffect(() => { writeStored(LIBRARY_YEAR_KEY, filterYear) }, [filterYear])
  useEffect(() => { writeStored(LIBRARY_DOI_KEY, filterDoi ? '1' : '0') }, [filterDoi])
  useEffect(() => { writeStored(LIBRARY_PDF_KEY, filterPdf) }, [filterPdf])
  useEffect(() => {
    if (pdfFilter) setFilterPdf(pdfFilter)
  }, [pdfFilter])

  // Persist the page and restart at page 1 whenever the visible set changes.
  useEffect(() => { writeStored(LIBRARY_PAGE_KEY, page) }, [page])
  useEffect(() => {
    setPage(1)
  }, [searchQuery, filterType, filterQuality, filterPdf, filterDoi, filterYear, filterScreening, typePreset, readPreset])

  // Close the per-study PDF menu when tapping anywhere outside it.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (pdfMenuId !== null && !target.closest('[data-pdf-menu]')) setPdfMenuId(null)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [pdfMenuId])

  const downloadAll = async () => {
    setDownloadingAll(true)
    try {
      const res = await api.studies.downloadAll()
      const downloaded = new Set(res.downloaded)
      setStudies(prev => prev.map(s => downloaded.has(s.id) ? { ...s, pdf_path: s.pdf_path || 'local' } : s))
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
    const key = String(studyId)
    // Optimistic update: remember the exact previous row so a failed request
    // can restore only this study (never the whole list).
    const prevDecision = screeningDecisions[key]
    const prevDraft = reasonDrafts[studyId]
    setScreeningDecisions(prev => ({
      ...prev,
      [key]: {
        entry_id: studyId,
        decision,
        reason: reason ?? prevDecision?.reason ?? '',
        updated_at: prevDecision?.updated_at ?? new Date().toISOString(),
      },
    }))
    if (reason !== undefined) {
      setReasonDrafts(prev => {
        const next = { ...prev }
        delete next[studyId]
        return next
      })
    }
    try {
      const updated = await api.studies.setScreening(studyId, { decision, reason: reason ?? '' })
      setScreeningDecisions(prev => ({ ...prev, [key]: updated }))
      setReasonDrafts(prev => {
        const next = { ...prev }
        delete next[studyId]
        return next
      })
      setScreeningSummary(await api.studies.screeningSummary())
    } catch (err) {
      // Roll back only the affected study to its exact previous value.
      setScreeningDecisions(prev => {
        const next = { ...prev }
        if (prevDecision === undefined) delete next[key]
        else next[key] = prevDecision
        return next
      })
      if (reason !== undefined) {
        setReasonDrafts(prev => (prevDraft === undefined ? prev : { ...prev, [studyId]: prevDraft }))
      }
      toast.error('Decizia nu s-a putut salva — modificarea nu a fost păstrată: ' + (err instanceof Error ? err.message : 'eroare'))
    }
  }

  const updateReadStatus = async (studyId: number, next: string) => {
    // Optimistic update: remember the exact previous status so a failed
    // request can restore only this study (never the whole list).
    const prevStatus = studies.find(s => s.id === studyId)?.read_status ?? 'unread'
    setStudies(prev => prev.map(s => s.id === studyId ? { ...s, read_status: next as Entry['read_status'] } : s))
    try {
      const updated = await api.entries.updateStatus(studyId, next)
      setStudies(prev => prev.map(s => s.id === studyId ? { ...s, read_status: updated.read_status } : s))
    } catch (err) {
      // Roll back only the affected study to its exact previous value.
      setStudies(prev => prev.map(s => s.id === studyId ? { ...s, read_status: prevStatus } : s))
      toast.error('Starea de lectură nu s-a putut salva — modificarea nu a fost păstrată: ' + (err instanceof Error ? err.message : 'eroare'))
    }
  }

  const toggleStudyType = async (study: Entry) => {
    const isBook = study.study_type === 'book'
    const next = isBook ? 'peer-reviewed paper' : 'book'
    // Optimistic update: remember the exact previous type so a failed
    // request can restore only this study (never the whole list).
    const prevType = study.study_type
    setStudies(prev => prev.map(s => s.id === study.id ? { ...s, study_type: next } : s))
    try {
      const updated = await api.entries.update(study.id, { study_type: next })
      setStudies(prev => prev.map(s => s.id === study.id ? { ...s, study_type: updated.study_type } : s))
      toast.success(isBook ? 'Marcat ca studiu (articol).' : 'Marcat ca și carte.')
    } catch (err) {
      // Roll back only the affected study to its exact previous value.
      setStudies(prev => prev.map(s => s.id === study.id ? { ...s, study_type: prevType } : s))
      toast.error('Tipul nu s-a putut schimba — modificarea nu a fost păstrată: ' + (err instanceof Error ? err.message : 'eroare'))
    }
  }

  const types = [...new Set(studies.map(s => s.study_type).filter(Boolean))].sort()
  const filtered = studies.filter(s => {
    if (typePreset && s.study_type !== typePreset) return false
    if (readPreset === 'read' && !['read', 'annotated'].includes(s.read_status || 'unread')) return false
    if (filterType && s.study_type !== filterType) return false
    if (filterQuality > 0 && (s.quality ?? 0) < filterQuality) return false
    if (filterScreening) {
      const dec = screeningDecisions[String(s.id)]?.decision
      if (filterScreening === 'undecided' ? !!dec : dec !== filterScreening) return false
    }
    if (filterPdf === 'with' && !s.pdf_path) return false
    if (filterPdf === 'without' && s.pdf_path) return false
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

  const PAGE_SIZE = 30
  // Cumulative window: "Încarcă mai multe" appends the next chunk to the
  // rows already on screen. A per-page slice((page-1)*SIZE, page*SIZE) would
  // replace the visible rows instead, and a reload that shrinks the list
  // would strand a high stored page on an empty view.
  const pageStudies = filtered.slice(0, page * PAGE_SIZE)
  const hasMore = page * PAGE_SIZE < filtered.length

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
          <h2 className="text-xl font-bold">Studii academice</h2>
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
          <button onClick={downloadAll} disabled={downloadingAll} aria-busy={downloadingAll} className="btn-secondary hidden sm:inline-flex flex-1 sm:flex-none text-xs sm:text-sm whitespace-nowrap items-center justify-center gap-1">
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
            className="btn-secondary hidden sm:inline-flex flex-1 sm:flex-none text-xs sm:text-sm whitespace-nowrap items-center justify-center gap-1"
            title="Exportă bibliografia pentru Zotero / Mendeley / Word"
          >
            <BookMarked className="w-4 h-4" />
            Export BibTeX
          </button>
          <button onClick={load} className="btn-secondary p-2 min-w-[40px] min-h-[40px] hidden sm:inline-flex" aria-label="Reîmprospătează studiile">
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setSheetOpen(true)}
            className="btn-secondary inline-flex sm:hidden items-center justify-center gap-1.5 px-3 text-xs sm:text-sm whitespace-nowrap min-h-[40px]"
            aria-label="Mai multe acțiuni"
            title="Mai multe acțiuni"
          >
            <MoreHorizontal className="w-4 h-4" />
            <span>Mai multe acțiuni</span>
          </button>
        </div>
      </div>

      {/* Sleek Filters & Search */}
      <div className="space-y-2.5 mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[220px]">
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

          <button
            onClick={() => { const next = !screeningMode; setScreeningMode(next); if (next) loadScreening() }}
            className={`hidden sm:inline-flex btn-secondary text-xs sm:text-sm items-center gap-1.5 transition-colors ${
              screeningMode ? '!bg-amber-100 dark:!bg-amber-950/60 !border-amber-400 font-semibold text-amber-900 dark:text-amber-200' : ''
            }`}
            title="Mod PRISMA: decide include/exclude pentru fiecare studiu"
          >
            <span>⚖️ Screening PRISMA</span>
          </button>

          <button
            onClick={() => setFilterQuality(prev => prev >= 4 ? 0 : 4)}
            className={`hidden sm:inline-flex btn-secondary text-xs sm:text-sm items-center gap-1.5 transition-colors ${
              filterQuality >= 4 ? '!bg-chrono-100 dark:!bg-chrono-950/60 !border-chrono-400 font-semibold text-chrono-700 dark:text-chrono-300' : ''
            }`}
            title="Filtrează studiile de calitate înaltă (4★ și 5★)"
          >
            <span>⭐ Calitate 4★+</span>
          </button>

          <button
            onClick={() => setShowAdvancedFilters(prev => !prev)}
            className={`hidden sm:inline-flex btn-secondary text-xs sm:text-sm items-center gap-1 transition-colors ${
              (filterType || filterYear || filterDoi || showAdvancedFilters) ? 'border-line font-medium text-ink' : 'text-muted'
            }`}
          >
            <span>Filtre avansate</span>
            <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAdvancedFilters ? 'rotate-180' : ''}`} />
          </button>

          {(filterType || filterQuality > 0 || searchQuery || filterScreening || filterDoi || filterYear) && (
            <button
              onClick={() => {
                setFilterType('')
                setFilterQuality(0)
                setSearchQuery('')
                setFilterScreening('')
                setFilterDoi(false)
                setFilterYear('')
              }}
              className="btn-ghost text-xs text-madder hover:underline"
            >
              ✕ Resetează filtrele
            </button>
          )}
        </div>

        {/* Collapsible Advanced Filters */}
        {showAdvancedFilters && (
          <div className="flex flex-wrap items-center gap-3 p-3 bg-surface-2 rounded-xl border border-line text-xs">
            <div className="flex items-center gap-1.5">
              <span className="text-muted font-medium">Tip studiu:</span>
              <select value={filterType} onChange={e => setFilterType(e.target.value)} className="input text-xs py-1 px-2">
                <option value="">Toate tipurile</option>
                {types.map(t => <option key={t} value={t}>{labelOf(studyTypeLabels, t)}</option>)}
              </select>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-muted font-medium">An publicare:</span>
              <input
                type="number"
                inputMode="numeric"
                value={filterYear}
                onChange={e => setFilterYear(e.target.value)}
                placeholder="ex. 2024"
                className="input w-24 text-xs py-1 px-2"
              />
            </div>

            <label className="flex items-center gap-1.5 cursor-pointer text-muted hover:text-ink font-medium">
              <input
                type="checkbox"
                checked={filterDoi}
                onChange={e => setFilterDoi(e.target.checked)}
                className="rounded border-gray-300 text-chrono-600"
              />
              <span>Doar cu DOI</span>
            </label>
          </div>
        )}

        {screeningMode && (
          <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 bg-amber-500/10 rounded-xl border border-amber-500/25 text-xs">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-amber-900 dark:text-amber-200">Decizii PRISMA:</span>
              <select value={filterScreening} onChange={e => setFilterScreening(e.target.value)} className="input text-xs py-1 px-2">
                <option value="">Toate deciziile</option>
                <option value="include">Doar incluse</option>
                <option value="exclude">Doar excluse</option>
                <option value="maybe">Doar „poate”</option>
                <option value="undecided">Doar nerecenzate</option>
              </select>
            </div>
            {screeningSummary && (
              <span className="text-muted text-[11px]">
                <b className="text-emerald-600">{screeningSummary.included}</b> incluse ·{' '}
                <b className="text-madder">{screeningSummary.excluded}</b> excluse ·{' '}
                <b className="text-brass">{screeningSummary.maybe}</b> poate ·{' '}
                {screeningSummary.undecided} în așteptare din {screeningSummary.studies_total}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Server unreachable but the device kept the library: an explicit,
          friendly banner instead of a silent cached list. */}
      {offlineNotice && (
        <div className="card mb-3 border-amber-300/40 bg-amber-50/70 dark:bg-amber-900/20 text-sm text-amber-900 dark:text-amber-200 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" /> {offlineNotice}
        </div>
      )}

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
        <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
          {pageStudies.map(study => {
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
                    // 40px tap area; the negative margin keeps the row's layout
                    // unchanged so the icon still reads as a small checkbox.
                    className="touch-40-neg text-gray-400 hover:text-chrono-600 focus:outline-none shrink-0 rounded inline-flex items-center justify-center"
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
                ) : hasDirectPdfHint(study) ? (
                  <span className="badge text-xs badge-brass border border-brass/25">PDF disponibil</span>
                ) : (study.source || study.content) ? (
                  <span className="badge text-xs badge-muted" title="Sursa este o pagină web, nu un PDF direct — „Descarcă PDF” poate eșua; folosește „Text integral”">
                    Sursă web
                  </span>
                ) : null}
                <span className={`badge text-xs ${READ_STATUS_STYLES[study.read_status || 'unread']}`}>
                  {READ_STATUS_LABELS[study.read_status || 'unread']}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 mb-2" onClick={e => e.stopPropagation()}>
                <select
                  value={study.read_status || 'unread'}
                  onChange={e => void updateReadStatus(study.id, e.target.value)}
                  className="input text-[11px] py-0.5 px-1.5 w-auto"
                  title="Starea de lectură"
                  aria-label={`Starea de lectură pentru ${study.title}`}
                >
                  <option value="unread">Necitit</option>
                  <option value="reading">În lectură</option>
                  <option value="read">Citit</option>
                  <option value="annotated">Adnotat</option>
                </select>
                <button
                  type="button"
                  onClick={() => void toggleStudyType(study)}
                  className="btn-ghost text-[11px] px-2 py-0.5 inline-flex items-center gap-1"
                  title={study.study_type === 'book' ? 'Comută la studiu (articol recenzat)' : 'Comută la carte'}
                >
                  <BookOpen className="w-3 h-3" />
                  {study.study_type === 'book' ? 'Carte → Studiu' : 'Studiu → Carte'}
                </button>
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
              <div className="flex items-center flex-wrap gap-2 mb-2">
                {/* Primary PDF action */}
                {study.pdf_path ? (
                  <button
                    type="button"
                    onClick={e => {
                      e.stopPropagation()
                      e.preventDefault()
                      setPdfModalStudy(study)
                    }}
                    className="btn-secondary text-xs px-2.5 py-1 inline-flex items-center gap-1 font-semibold text-chrono-700 dark:text-chrono-300 bg-chrono-50 dark:bg-chrono-950/60 border border-chrono-200 dark:border-chrono-800 hover:bg-chrono-100 touch-40-neg"
                    title="Deschide PDF-ul nativ în aplicație și adaugă notițe"
                  >
                    📄 Citește PDF & Notițe
                  </button>
                ) : (
                  <button
                    onClick={async e => {
                      e.stopPropagation()
                      setDownloadingPdf(prev => ({ ...prev, [study.id]: true }))
                      try {
                        const res = await api.studies.download(study.id)
                        const pdfPath = res.pdf_path || 'local'
                        setStudies(prev => prev.map(s => s.id === study.id ? { ...s, pdf_path: pdfPath } : s))
                        setPdfModalStudy({ ...study, pdf_path: pdfPath })
                        toast.success('PDF descărcat cu succes!')
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
                    className="btn-ghost text-xs px-2 py-1 disabled:opacity-50 touch-40-neg"
                  >
                    {downloadingPdf[study.id]
                      ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Se descarcă PDF-ul…</>
                      : <><FileText className="w-3.5 h-3.5" /> Descarcă PDF</>}
                  </button>
                )}
                {/* Compact ⋯ menu with the remaining PDF actions */}
                <div data-pdf-menu className="relative inline-block" onClick={e => e.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => setPdfMenuId(pdfMenuId === study.id ? null : study.id)}
                    className="btn-secondary text-xs px-2 py-1 touch-40-neg inline-flex items-center justify-center"
                    aria-label="Mai multe acțiuni PDF"
                    title="Mai multe acțiuni PDF"
                    aria-expanded={pdfMenuId === study.id}
                  >
                    <MoreHorizontal className="w-4 h-4" />
                  </button>
                  {pdfMenuId === study.id && (
                    <div className="absolute right-0 top-full mt-1 w-48 bg-surface border border-line rounded-lg shadow-lg p-1 z-20 dark:bg-surface-2">
                      {(() => {
                        const doi = getStudyDoi(study)
                        const browserUrl = doi
                          ? `https://sci-hub.se/${encodeURIComponent(doi)}`
                          : (study.source && study.source.startsWith('http') ? study.source : null)
                        if (!browserUrl) return null
                        return (
                          <a
                            href={browserUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={e => { e.stopPropagation(); setPdfMenuId(null) }}
                            className="block w-full text-left text-xs px-3 py-2 text-chrono-600 dark:text-chrono-400 hover:bg-surface-2 hover:text-ink inline-flex items-center gap-1"
                            title={doi ? `Deschide pe Sci-Hub (DOI: ${doi})` : 'Deschide sursa în browser'}
                          >
                            🌐 Deschide în browser (Sci-Hub)
                          </a>
                        )
                      })()}
                      {!study.pdf_path && (
                        <label
                          onClick={e => { e.stopPropagation(); setPdfMenuId(null) }}
                          className="block w-full text-left text-xs px-3 py-2 cursor-pointer text-gray-700 dark:text-gray-300 hover:bg-surface-2 hover:text-chrono-600 disabled:opacity-50"
                          title="Încarcă fișier PDF descârcat din browser"
                        >
                          {uploadingPdf[study.id] ? (
                            <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Se încarcă…</>
                          ) : (
                            <>📤 Încarcă PDF descârcat</>
                          )}
                          <input
                            type="file"
                            accept=".pdf,application/pdf"
                            className="hidden"
                            disabled={uploadingPdf[study.id]}
                            onChange={async e => {
                              const file = e.target.files?.[0]
                              if (!file) return
                              setUploadingPdf(prev => ({ ...prev, [study.id]: true }))
                              try {
                                const updated = await api.entries.uploadPdf(study.id, file)
                                setStudies(prev => prev.map(s => s.id === study.id
                                  ? { ...s, ...updated, pdf_path: updated.pdf_path || 'local' }
                                  : s))
                                toast.success(`PDF încărcat și indexat cu succes pentru: ${study.title}`)
                              } catch (err: unknown) {
                                const msg = err instanceof Error ? err.message : 'Încărcarea PDF-ului a eșuat'
                                toast.error(msg)
                              } finally {
                                setUploadingPdf(prev => ({ ...prev, [study.id]: false }))
                                e.target.value = ''
                              }
                            }}
                          />
                        </label>
                      )}
                      <button
                        onClick={async e => {
                          e.stopPropagation()
                          setPdfMenuId(null)
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
                        className="block w-full text-left text-xs px-3 py-2 text-muted hover:bg-surface-2 hover:text-ink"
                        title="Caută articolul în sursele deschise (OpenAlex, Unpaywall, Semantic Scholar, PMC, arXiv)"
                      >
                        {fetchingText[study.id]
                          ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Caut…</>
                          : <><Search className="w-3.5 h-3.5" /> Text integral</>}
                      </button>
                      <button
                        type="button"
                        onClick={async e => {
                          e.stopPropagation()
                          setPdfMenuId(null)
                          try {
                            const snippet = await api.citations.cite(study.id)
                            await navigator.clipboard.writeText(snippet)
                            toast.success(`Citare copiată: ${snippet}`)
                          } catch {
                            toast.error('Copierea citării a eșuat')
                          }
                        }}
                        className="block w-full text-left text-xs px-3 py-2 font-mono text-[11px] text-muted hover:bg-surface-2 hover:text-ink"
                        title="Copiază citarea în text (Autor, An)"
                      >
                        Citează
                      </button>
                      <button
                        type="button"
                        onClick={e => {
                          e.stopPropagation()
                          setPdfMenuId(null)
                          setAnalysisEntry(study)
                        }}
                        className="block w-full text-left text-xs px-3 py-2 text-muted hover:bg-surface-2 hover:text-ink"
                        title="Deschide panoul de analiză a poziției pentru acest studiu"
                      >
                        <TextQuote className="w-3.5 h-3.5 shrink-0 mr-2" />
                        Analizează poziția
                      </button>
                    </div>
                  )}
                </div>
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
        {hasMore && (
          <button
            onClick={() => setPage(p => p + 1)}
            className="w-full btn-secondary text-xs sm:text-sm py-2"
          >
            Încarcă mai multe
          </button>
        )}
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

      {/* Mobile secondary-actions sheet (below `sm` only). */}
      <MobileActionsSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        onCheckDuplicates={onCheckDuplicates!}
        onCreateEntry={onCreateEntry!}
        onDownloadAll={downloadAll}
        onExportBibtex={async () => {
          try {
            await api.citations.export('bibtex')
            toast.success('BibTeX descărcat (.bib)')
          } catch (err) {
            toast.error('Exportul a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
          }
        }}
        onToggleScreening={() => {
          const next = !screeningMode
          setScreeningMode(next)
          if (next) void loadScreening()
        }}
        onToggleQuality={() => setFilterQuality(prev => (prev >= 4 ? 0 : 4))}
        onToggleAdvanced={() => setShowAdvancedFilters(prev => !prev)}
        onRefresh={load}
      />

      {/* Import / Manual Add Studies Modal */}
      <ImportStudiesModal
        isOpen={importModalOpen}
        onClose={() => setImportModalOpen(false)}
        onImportComplete={load}
      />

      <DuplicatesModal
        isOpen={duplicatesOpen}
        onClose={() => setDuplicatesOpen(false)}
        onMerged={load}
      />

      {/* Native In-App PDF Reader & Study Notes Modal */}
      <StudyPdfModal
        study={pdfModalStudy}
        isOpen={Boolean(pdfModalStudy)}
        onClose={() => setPdfModalStudy(null)}
        onNotesUpdated={load}
      />

      {/* Study analysis side panel: which claims this study touches, with
          Accept / Edit / Reject per proposed stance. */}
      <StudyAnalysisPanel
        entryId={analysisEntry?.id ?? 0}
        title={analysisEntry?.title || 'Analiză studiu'}
        open={Boolean(analysisEntry)}
        onClose={() => setAnalysisEntry(null)}
      />
    </div>
  )
}
