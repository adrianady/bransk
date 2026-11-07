import { useState } from 'react'
import {
  X,
  Search,
  FileText,
  Upload,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ExternalLink,
  Sparkles,
  BookmarkPlus,
  CheckSquare,
  Square,
  BookOpen,
} from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { BulkImportResponse, ExternalStudyResult, ResolvedStudyMetadata } from '../lib/types'

interface Props {
  isOpen: boolean
  onClose: () => void
  onImportComplete: () => void
}

type TabMode = 'search' | 'paste' | 'manual'

function importFeedbackMessage(res: BulkImportResponse): string {
  return `Importat ${res.total_imported} studii` +
    (res.pdfs_downloaded ? `, ${res.pdfs_downloaded} PDF-uri descărcate` : '') +
    (res.pdfs_failed ? `, PDF-uri indisponibile: ${res.pdfs_failed}` : '') +
    (res.pdfs_index_failed ? `, PDF-uri descărcate, dar neindexate: ${res.pdfs_index_failed}` : '') +
    (res.total_skipped ? `, ${res.total_skipped} sărite (existau deja)` : '')
}

export function ImportStudiesModal({ isOpen, onClose, onImportComplete }: Props) {
  const toast = useToast()
  const [tab, setTab] = useState<TabMode>('search')

  // ── Tab 1: External Search State ──
  const [searchQuery, setSearchQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResults, setSearchResults] = useState<ExternalStudyResult[]>([])
  const [selectedSearchIndices, setSelectedSearchIndices] = useState<Set<number>>(new Set())
  const [autoDownloadSearchPdfs, setAutoDownloadSearchPdfs] = useState(true)
  const [importingSearch, setImportingSearch] = useState(false)

  // ── Tab 2: Paste List State ──
  const [pasteText, setPasteText] = useState('')
  const [autoDownloadPastePdfs, setAutoDownloadPastePdfs] = useState(true)
  const [importingPaste, setImportingPaste] = useState(false)

  // ── Tab 3: Manual Single Entry State ──
  const [resolvingDoi, setResolvingDoi] = useState(false)
  const [manualDoi, setManualDoi] = useState('')
  const [manualTitle, setManualTitle] = useState('')
  const [manualAuthors, setManualAuthors] = useState('')
  const [manualYear, setManualYear] = useState<string>('')
  const [manualStudyType, setManualStudyType] = useState('peer-reviewed paper')
  const [manualQuality, setManualQuality] = useState<number>(4)
  const [manualDesign, setManualDesign] = useState('')
  const [manualEffect, setManualEffect] = useState('')
  const [manualUrl, setManualUrl] = useState('')
  const [manualPdfUrl, setManualPdfUrl] = useState('')
  const [manualTags, setManualTags] = useState('')
  const [manualAbstract, setManualAbstract] = useState('')
  const [manualNotes, setManualNotes] = useState('')
  const [manualDownloadPdf, setManualDownloadPdf] = useState(true)
  const [savingManual, setSavingManual] = useState(false)

  // ── Import summary results ──
  const [lastImportSummary, setLastImportSummary] = useState<BulkImportResponse | null>(null)

  if (!isOpen) return null

  // ── Search Handlers ──
  const handleSearch = async (queryToUse?: string) => {
    const q = (queryToUse ?? searchQuery).trim()
    if (!q) return
    setSearching(true)
    setSearchResults([])
    setSelectedSearchIndices(new Set())
    setLastImportSummary(null)
    try {
      const res = await api.studies.searchExternal(q, 15)
      setSearchResults(res.results)
      // Auto-select open access or new items that aren't already in library
      const initialSelected = new Set<number>()
      res.results.forEach((item, idx) => {
        if (!item.already_in_library) {
          initialSelected.add(idx)
        }
      })
      setSelectedSearchIndices(initialSelected)
      if (res.results.length === 0) {
        toast.info('Nu s-au găsit studii pentru termenii căutați.')
      }
    } catch (err: any) {
      toast.error('Căutarea a eșuat: ' + (err?.message || 'eroare rețea'))
    } finally {
      setSearching(false)
    }
  }

  const toggleSelectAllSearch = () => {
    const selectable = searchResults
      .map((item, idx) => ({ item, idx }))
      .filter(({ item }) => !item.already_in_library)

    if (selectedSearchIndices.size >= selectable.length && selectable.length > 0) {
      setSelectedSearchIndices(new Set())
    } else {
      setSelectedSearchIndices(new Set(selectable.map(s => s.idx)))
    }
  }

  const toggleSearchIndex = (idx: number) => {
    setSelectedSearchIndices(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  const handleImportSelectedSearch = async () => {
    const toImport = Array.from(selectedSearchIndices).map(idx => {
      const item = searchResults[idx]
      return {
        title: item.title,
        authors: item.authors,
        year: item.year,
        doi: item.doi,
        url: item.doi ? `https://doi.org/${item.doi}` : item.pdf_url,
        pdf_url: item.pdf_url,
        source: item.source,
        snippet: item.snippet,
        study_type: 'peer-reviewed paper',
        download_pdf: autoDownloadSearchPdfs,
      }
    })

    if (toImport.length === 0) {
      toast.info('Selectați cel puțin un studiu pentru import.')
      return
    }

    setImportingSearch(true)
    try {
      const res = await api.studies.importBulk({
        studies: toImport,
        auto_download_pdfs: autoDownloadSearchPdfs,
      })
      setLastImportSummary(res)
      toast.success(importFeedbackMessage(res))
      onImportComplete()
    } catch (err: any) {
      toast.error('Importul a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setImportingSearch(false)
    }
  }

  // ── Paste List Handlers ──
  const handleImportPaste = async () => {
    if (!pasteText.trim()) {
      toast.info('Introduceți cel puțin un DOI sau URL.')
      return
    }

    setImportingPaste(true)
    setLastImportSummary(null)
    try {
      const res = await api.studies.importBulk({
        raw_text: pasteText,
        auto_download_pdfs: autoDownloadPastePdfs,
      })
      setLastImportSummary(res)
      toast.success(importFeedbackMessage(res))
      setPasteText('')
      onImportComplete()
    } catch (err: any) {
      toast.error('Importul a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setImportingPaste(false)
    }
  }

  // ── Manual Entry Handlers ──
  const handleAutoFillDoi = async () => {
    const ident = manualDoi.trim()
    if (!ident) {
      toast.info('Introduceți un DOI, arXiv ID sau URL pentru preluarea automată a datelor.')
      return
    }
    setResolvingDoi(true)
    try {
      const meta: ResolvedStudyMetadata = await api.studies.resolveDoi(ident)
      setManualTitle(meta.title || manualTitle)
      setManualAuthors(meta.authors || manualAuthors)
      if (meta.year) setManualYear(String(meta.year))
      if (meta.doi) setManualDoi(meta.doi)
      if (meta.url) setManualUrl(meta.url)
      if (meta.pdf_url) setManualPdfUrl(meta.pdf_url)
      if (meta.abstract) setManualAbstract(meta.abstract)
      if (meta.study_type) setManualStudyType(meta.study_type)
      toast.success(`Metadate preluate cu succes de pe ${meta.source || 'Crossref'}!`)
    } catch (err: any) {
      toast.error('Nu s-au putut prelua datele automat: ' + (err?.message || 'identificator invalid'))
    } finally {
      setResolvingDoi(false)
    }
  }

  const handleSaveManual = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!manualTitle.trim()) {
      toast.error('Titlul studiului este obligatoriu.')
      return
    }

    setSavingManual(true)
    try {
      const studyItem = {
        title: manualTitle.trim(),
        authors: manualAuthors.trim(),
        year: manualYear ? parseInt(manualYear, 10) : null,
        doi: manualDoi.trim(),
        url: manualUrl.trim() || (manualDoi.trim() ? `https://doi.org/${manualDoi.trim()}` : ''),
        pdf_url: manualPdfUrl.trim(),
        study_type: manualStudyType,
        quality: manualQuality,
        design: manualDesign.trim(),
        effect_direction: manualEffect,
        tags: manualTags.trim(),
        abstract: manualAbstract.trim(),
        notes: manualNotes.trim(),
        download_pdf: manualDownloadPdf,
      }

      const res = await api.studies.importBulk({
        studies: [studyItem],
        auto_download_pdfs: manualDownloadPdf,
      })

      if (res.total_imported > 0) {
        toast.success(
          `Studiul "${manualTitle.slice(0, 45)}..." a fost adăugat cu succes!` +
          (res.pdfs_downloaded ? ' (PDF descărcat)' : '') +
          (res.pdfs_failed ? ` (PDF indisponibil: ${res.pdfs_failed})` : '') +
          (res.pdfs_index_failed ? ` (PDF neindexat: ${res.pdfs_index_failed})` : '')
        )
        onImportComplete()
        onClose()
      } else if (res.total_skipped > 0) {
        toast.info(res.skipped[0]?.reason || 'Studiul există deja în bibliotecă.')
      }
    } catch (err: any) {
      toast.error('Salvarea a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setSavingManual(false)
    }
  }

  const quickSearchSuggestions = [
    'Consolidarea memoriei în somn',
    'Neuroplasticitate și exercițiu fizic',
    'Dopamină și învățare prin recompensă',
    'Efectele meditației asupra atenției',
  ]
  const failedPdfCount = lastImportSummary?.pdfs_failed ?? 0
  const failedIndexCount = lastImportSummary?.pdfs_index_failed ?? 0
  const importHasPdfIssues = failedPdfCount > 0 || failedIndexCount > 0

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-2 sm:p-4 overflow-y-auto">
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl sm:rounded-2xl shadow-2xl w-full max-w-4xl max-h-[94dvh] sm:max-h-[92vh] flex flex-col my-auto animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between px-3 sm:px-6 py-3 sm:py-4 border-b border-gray-100 dark:border-gray-800 shrink-0">
          <div className="flex items-center gap-2 sm:gap-2.5 min-w-0">
            <div className="p-1.5 sm:p-2 bg-chrono-100 dark:bg-chrono-900/50 text-chrono-600 dark:text-chrono-400 rounded-lg shrink-0">
              <BookOpen className="w-4 h-4 sm:w-5 sm:h-5" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm sm:text-lg font-bold text-gray-900 dark:text-gray-100 truncate">
                Adaugă & Importă Studii Externe
              </h2>
              <p className="text-[11px] sm:text-xs text-gray-500 dark:text-gray-400 truncate">
                Căutare în baze academice (Crossref, Europe PMC, arXiv), import liste DOI sau adăugare manuală
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors shrink-0 ml-2"
            title="Închide fereastra"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-gray-100 dark:border-gray-800 px-2 sm:px-6 pt-1 sm:pt-2 bg-gray-50/50 dark:bg-gray-900/50 gap-1 sm:gap-2 shrink-0 overflow-x-auto no-scrollbar whitespace-nowrap">
          <button
            onClick={() => setTab('search')}
            className={`flex items-center gap-1.5 sm:gap-2 px-3 sm:px-4 py-2 sm:py-2.5 text-xs sm:text-sm font-medium border-b-2 transition-colors whitespace-nowrap shrink-0 ${
              tab === 'search'
                ? 'border-chrono-600 text-chrono-600 dark:border-chrono-400 dark:text-chrono-400 font-semibold'
                : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
            }`}
          >
            <Search className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            Căutare & Selectare în Masă
          </button>
          <button
            onClick={() => setTab('paste')}
            className={`flex items-center gap-1.5 sm:gap-2 px-3 sm:px-4 py-2 sm:py-2.5 text-xs sm:text-sm font-medium border-b-2 transition-colors whitespace-nowrap shrink-0 ${
              tab === 'paste'
                ? 'border-chrono-600 text-chrono-600 dark:border-chrono-400 dark:text-chrono-400 font-semibold'
                : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
            }`}
          >
            <Upload className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            Import Listă DOI / URL
          </button>
          <button
            onClick={() => setTab('manual')}
            className={`flex items-center gap-1.5 sm:gap-2 px-3 sm:px-4 py-2 sm:py-2.5 text-xs sm:text-sm font-medium border-b-2 transition-colors whitespace-nowrap shrink-0 ${
              tab === 'manual'
                ? 'border-chrono-600 text-chrono-600 dark:border-chrono-400 dark:text-chrono-400 font-semibold'
                : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
            }`}
          >
            <BookmarkPlus className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            Adăugare Manuală Formular
          </button>
        </div>

        {/* Body content */}
        <div className="flex-1 overflow-y-auto p-3 sm:p-6 space-y-4">
          {/* TAB 1: Search & Bulk Select */}
          {tab === 'search' && (
            <div className="space-y-4">
              <form
                onSubmit={e => {
                  e.preventDefault()
                  handleSearch()
                }}
                className="flex gap-2"
              >
                <div className="relative flex-1">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={e => setSearchQuery(e.target.value)}
                    placeholder="Caută subiecte academice (ex. sleep memory consolidation, fasting autophagy)..."
                    className="input pl-9 w-full text-sm"
                    autoFocus
                  />
                </div>
                <button
                  type="submit"
                  disabled={searching || !searchQuery.trim()}
                  className="btn-primary shrink-0 flex items-center gap-2"
                >
                  {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  Caută
                </button>
              </form>

              {/* Suggestions */}
              {searchResults.length === 0 && !searching && (
                <div className="pt-2">
                  <p className="text-xs text-gray-500 mb-2">Sugestii rapide de căutare:</p>
                  <div className="flex flex-wrap gap-2">
                    {quickSearchSuggestions.map(s => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => {
                          setSearchQuery(s)
                          handleSearch(s)
                        }}
                        className="badge bg-gray-100 hover:bg-chrono-50 text-gray-700 dark:bg-gray-800 dark:hover:bg-chrono-950/40 dark:text-gray-300 cursor-pointer text-xs transition-colors py-1 px-2.5"
                      >
                        ⚡ {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Results */}
              {searchResults.length > 0 && (
                <div className="space-y-3 pt-2">
                  <div className="flex flex-wrap items-center justify-between gap-3 bg-gray-50 dark:bg-gray-800/50 p-3 rounded-lg border border-gray-100 dark:border-gray-800 text-xs text-gray-600 dark:text-gray-300">
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        onClick={toggleSelectAllSearch}
                        className="font-medium hover:text-chrono-600 flex items-center gap-1.5"
                      >
                        {selectedSearchIndices.size > 0 ? (
                          <CheckSquare className="w-4 h-4 text-chrono-600" />
                        ) : (
                          <Square className="w-4 h-4 text-gray-400" />
                        )}
                        {selectedSearchIndices.size > 0
                          ? `Deselectează tot (${selectedSearchIndices.size})`
                          : `Selectează toate studiile`}
                      </button>
                    </div>

                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={autoDownloadSearchPdfs}
                        onChange={e => setAutoDownloadSearchPdfs(e.target.checked)}
                        className="rounded border-gray-300 text-chrono-600 focus:ring-chrono-500"
                      />
                      <span>Descarcă & indexează automat PDF-urile cu acces deschis</span>
                    </label>
                  </div>

                  <div className="space-y-2.5 max-h-[50vh] overflow-y-auto pr-1">
                    {searchResults.map((item, idx) => {
                      const isSelected = selectedSearchIndices.has(idx)
                      const isAlready = Boolean(item.already_in_library)

                      return (
                        <div
                          key={idx}
                          onClick={() => {
                            if (!isAlready) toggleSearchIndex(idx)
                          }}
                          className={`p-3 rounded-lg border transition-all text-left flex gap-3 ${
                            isAlready
                              ? 'bg-gray-50/70 dark:bg-gray-800/30 border-gray-200 dark:border-gray-800/60 opacity-70 cursor-default'
                              : isSelected
                              ? 'bg-chrono-50/40 dark:bg-chrono-950/20 border-chrono-300 dark:border-chrono-700 cursor-pointer shadow-sm'
                              : 'bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700 cursor-pointer'
                          }`}
                        >
                          <div className="pt-0.5 shrink-0">
                            {isAlready ? (
                              <span title="Studiu deja în bibliotecă">
                                <CheckCircle2 className="w-4 h-4 text-verdigris" />
                              </span>
                            ) : isSelected ? (
                              <CheckSquare className="w-4 h-4 text-chrono-600" />
                            ) : (
                              <Square className="w-4 h-4 text-gray-400" />
                            )}
                          </div>

                          <div className="flex-1 min-w-0 space-y-1">
                            <div className="flex items-start justify-between gap-2">
                              <h4 className="font-semibold text-sm leading-snug text-gray-900 dark:text-gray-100">
                                {item.title}
                              </h4>
                              {isAlready ? (
                                <span className="badge text-xs badge-verdigris shrink-0">
                                  ✓ În bibliotecă
                                </span>
                              ) : item.is_oa ? (
                                <span className="badge text-xs badge-verdigris shrink-0">
                                  📄 Open Access PDF
                                </span>
                              ) : null}
                            </div>

                            <p className="text-xs text-gray-500 line-clamp-1">
                              ✍️ {item.authors || 'Autori necunoscuți'} · 📅 {item.year || 'An necunoscut'} · 🏛️ {item.source}
                            </p>

                            {item.snippet && (
                              <p className="text-xs text-gray-600 dark:text-gray-400 line-clamp-2 pt-0.5">
                                {item.snippet}
                              </p>
                            )}

                            <div className="flex items-center gap-3 pt-1 text-xs text-gray-400">
                              {item.doi && (
                                <span className="font-mono text-[11px] text-gray-500">
                                  DOI: {item.doi}
                                </span>
                              )}
                              {item.pdf_url && (
                                <a
                                  href={item.pdf_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  onClick={e => e.stopPropagation()}
                                  className="text-chrono-600 hover:underline inline-flex items-center gap-1"
                                >
                                  Previzualizează PDF <ExternalLink className="w-3 h-3" />
                                </a>
                              )}
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: Paste List */}
          {tab === 'paste' && (
            <div className="space-y-4">
              <div className="bg-accent-soft border border-accent/25 p-3.5 rounded-lg text-xs text-accent-strong space-y-1">
                <p className="font-semibold flex items-center gap-1.5">
                  <Sparkles className="w-4 h-4 text-accent" />
                  Format suportat pentru import în masă:
                </p>
                <p>
                  Lipiți identificatori separați prin rând nou sau text ce conține referințe bibliografice:
                </p>
                <ul className="list-disc list-inside font-mono text-[11px] text-accent pl-1">
                  <li>10.1038/s41586-020-2012-7 (DOI Crossref)</li>
                  <li>https://doi.org/10.1016/j.neuron.2021.05.002</li>
                  <li>arXiv:2301.07067 sau https://arxiv.org/abs/2104.08653</li>
                  <li>https://pubmed.ncbi.nlm.nih.gov/32188939/ sau PMID: 32188939</li>
                  <li>PMC7144277</li>
                </ul>
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                  Lipiți lista de referințe sau textul cu articole:
                </label>
                <textarea
                  value={pasteText}
                  onChange={e => setPasteText(e.target.value)}
                  placeholder="Exemplu:&#10;10.1038/s41586-020-2012-7&#10;10.1016/j.neuron.2021.05.002&#10;arXiv:2301.07067"
                  rows={8}
                  className="input font-mono text-xs w-full leading-relaxed"
                />
              </div>

              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={autoDownloadPastePdfs}
                    onChange={e => setAutoDownloadPastePdfs(e.target.checked)}
                    className="rounded border-gray-300 text-chrono-600 focus:ring-chrono-500"
                  />
                  <span>Descarcă și indexează automat textul integral (PDF) pentru fiecare studiu</span>
                </label>
              </div>
            </div>
          )}

          {/* TAB 3: Manual Single Entry */}
          {tab === 'manual' && (
            <form onSubmit={handleSaveManual} className="space-y-4 text-xs">
              {/* Quick auto-resolve toolbar */}
              <div className="bg-chrono-50 dark:bg-chrono-950/30 border border-chrono-200 dark:border-chrono-900/60 p-3 rounded-lg space-y-2">
                <label className="block font-semibold text-chrono-900 dark:text-chrono-200">
                  ⚡ Auto-completare din DOI sau URL (opțional):
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={manualDoi}
                    onChange={e => setManualDoi(e.target.value)}
                    placeholder="ex. 10.1038/nrn.2016.44 sau https://doi.org/..."
                    className="input flex-1 text-xs"
                  />
                  <button
                    type="button"
                    onClick={handleAutoFillDoi}
                    disabled={resolvingDoi || !manualDoi.trim()}
                    className="btn-secondary shrink-0 text-xs flex items-center gap-1.5"
                  >
                    {resolvingDoi ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5 text-chrono-600" />}
                    Preia date automat
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
                <div className="md:col-span-2">
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Titlu studiu <span className="text-madder">*</span>
                  </label>
                  <input
                    type="text"
                    value={manualTitle}
                    onChange={e => setManualTitle(e.target.value)}
                    placeholder="Titlul complet al lucrării științifice"
                    required
                    className="input w-full text-xs font-medium"
                  />
                </div>

                <div>
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Autori
                  </label>
                  <input
                    type="text"
                    value={manualAuthors}
                    onChange={e => setManualAuthors(e.target.value)}
                    placeholder="ex. Smith J., Doe A., Brown R."
                    className="input w-full text-xs"
                  />
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      An publicare
                    </label>
                    <input
                      type="number"
                      value={manualYear}
                      onChange={e => setManualYear(e.target.value)}
                      placeholder="ex. 2023"
                      min={1800}
                      max={2050}
                      className="input w-full text-xs"
                    />
                  </div>

                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Calitate metodologică
                    </label>
                    <select
                      value={manualQuality}
                      onChange={e => setManualQuality(Number(e.target.value))}
                      className="input w-full text-xs"
                    >
                      <option value={5}>5★ Riguros (RCT / Meta-analiză)</option>
                      <option value={4}>4★ Bine controlat / Cohortă</option>
                      <option value={3}>3★ Observațional / Transversal</option>
                      <option value={2}>2★ Serii de cazuri</option>
                      <option value={1}>1★ Anecdotic / Părere</option>
                    </select>
                  </div>
                </div>

                <div>
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Tip studiu
                  </label>
                  <select
                    value={manualStudyType}
                    onChange={e => setManualStudyType(e.target.value)}
                    className="input w-full text-xs"
                  >
                    <option value="peer-reviewed paper">Studiu recenzat (peer-reviewed)</option>
                    <option value="systematic review">Review sistematic</option>
                    <option value="meta-analysis">Meta-analiză</option>
                    <option value="experimental study">Studiu experimental</option>
                    <option value="book">Carte / Tratat</option>
                    <option value="case study">Studiu de caz</option>
                  </select>
                </div>

                <div>
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Direcție efect
                  </label>
                  <select
                    value={manualEffect}
                    onChange={e => setManualEffect(e.target.value)}
                    className="input w-full text-xs"
                  >
                    <option value="">Nespecificat</option>
                    <option value="positive">Pozitiv (↗ sprijină ipoteza)</option>
                    <option value="negative">Negativ (↘ respinge / efect advers)</option>
                    <option value="mixed">Mixt (⇄ rezultate mixte)</option>
                    <option value="null">Nul (○ niciun efect statistic)</option>
                  </select>
                </div>

                <div>
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Link Sursă / Pagină articol
                  </label>
                  <input
                    type="url"
                    value={manualUrl}
                    onChange={e => setManualUrl(e.target.value)}
                    placeholder="https://..."
                    className="input w-full text-xs"
                  />
                </div>

                <div>
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Link direct PDF (dacă e cunoscut)
                  </label>
                  <input
                    type="url"
                    value={manualPdfUrl}
                    onChange={e => setManualPdfUrl(e.target.value)}
                    placeholder="https://.../article.pdf"
                    className="input w-full text-xs"
                  />
                </div>

                <div className="md:col-span-2">
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Etichete (separate prin virgulă)
                  </label>
                  <input
                    type="text"
                    value={manualTags}
                    onChange={e => setManualTags(e.target.value)}
                    placeholder="ex. neuroștiințe, somn, memorie, plasticitate"
                    className="input w-full text-xs"
                  />
                </div>

                <div className="md:col-span-2">
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Rezumat / Abstract
                  </label>
                  <textarea
                    value={manualAbstract}
                    onChange={e => setManualAbstract(e.target.value)}
                    placeholder="Descrierea metodei, eșantionului și constatărilor cheie..."
                    rows={4}
                    className="input w-full text-xs leading-relaxed"
                  />
                </div>

                <div className="md:col-span-2">
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Observații & Note personale (opțional)
                  </label>
                  <textarea
                    value={manualNotes}
                    onChange={e => setManualNotes(e.target.value)}
                    placeholder="Note critice, limitări identificate sau conexiuni cu alte teorii..."
                    rows={2}
                    className="input w-full text-xs leading-relaxed"
                  />
                </div>
              </div>

              <div className="flex items-center justify-between pt-2">
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={manualDownloadPdf}
                    onChange={e => setManualDownloadPdf(e.target.checked)}
                    className="rounded border-gray-300 text-chrono-600 focus:ring-chrono-500"
                  />
                  <span>Descarcă & indexează PDF-ul automat după salvare</span>
                </label>

                <button
                  type="submit"
                  disabled={savingManual || !manualTitle.trim()}
                  aria-busy={savingManual}
                  className="btn-primary flex items-center gap-2 text-xs"
                >
                  {savingManual ? <Loader2 className="w-4 h-4 animate-spin" /> : <BookmarkPlus className="w-4 h-4" />}
                  Salvează studiul în bibliotecă
                </button>
              </div>
            </form>
          )}

          {/* Import summary card if import recently completed */}
          {lastImportSummary && (
            <div
              role="status"
              aria-live="polite"
              className={`border p-4 rounded-xl text-xs space-y-2 mt-4 animate-fade-in ${
                importHasPdfIssues
                  ? 'bg-brass-soft border-brass/25'
                  : 'bg-verdigris-soft border-verdigris/25'
              }`}
            >
              <div className={`flex items-center gap-2 font-bold ${importHasPdfIssues ? 'text-brass' : 'text-verdigris'}`}>
                {importHasPdfIssues ? <AlertCircle className="w-4 h-4" /> : <CheckCircle2 className="w-4 h-4" />}
                Rezumat import: {lastImportSummary.total_imported} adăugate, {lastImportSummary.total_skipped} sărite.
                {' '}PDF-uri descărcate: {lastImportSummary.pdfs_downloaded}.
                {failedPdfCount > 0 && ` PDF-uri indisponibile: ${failedPdfCount}.`}
                {failedIndexCount > 0 && ` PDF-uri descărcate, dar neindexate: ${failedIndexCount}.`}
              </div>

              {lastImportSummary.imported.length > 0 && (
                <div className="space-y-1 pt-1">
                  <p className="font-semibold text-ink">Studii importate:</p>
                  <ul className="list-disc list-inside space-y-0.5 text-gray-700 dark:text-gray-300">
                    {lastImportSummary.imported.slice(0, 5).map(imp => (
                      <li key={imp.id} className="line-clamp-1">
                        #{imp.id}: {imp.title} {imp.pdf_downloaded
                          ? imp.pdf_index_failed ? '📄 [PDF salvat; indexarea a eșuat]' : '📄 [PDF salvat]'
                          : failedPdfCount > 0 ? '⚠️ [PDF indisponibil]' : ''}
                      </li>
                    ))}
                    {lastImportSummary.imported.length > 5 && (
                      <li className="text-gray-500 italic">...și alte {lastImportSummary.imported.length - 5} studii</li>
                    )}
                  </ul>
                </div>
              )}

              {lastImportSummary.skipped.length > 0 && (
                <div className="space-y-1 pt-1 border-t border-verdigris/20">
                  <p className="font-semibold text-brass">Sărite (există deja în bibliotecă):</p>
                  <ul className="list-disc list-inside space-y-0.5 text-gray-600 dark:text-gray-400">
                    {lastImportSummary.skipped.slice(0, 3).map((sk, idx) => (
                      <li key={idx} className="line-clamp-1">
                        {sk.title} — <span className="italic">{sk.reason}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {(lastImportSummary.unresolved_identifiers?.length ?? 0) > 0 && (
                <div className="space-y-1 pt-1 border-t border-madder/20">
                  <p className="font-semibold text-madder">Nerezolvate (nu au fost găsite în bazele academice):</p>
                  <ul className="list-disc list-inside space-y-0.5 text-gray-600 dark:text-gray-400">
                    {lastImportSummary.unresolved_identifiers!.slice(0, 5).map((u, idx) => (
                      <li key={idx} className="line-clamp-1">
                        {u.identifier} — <span className="italic">{u.reason}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="px-3 sm:px-6 py-2.5 sm:py-3.5 border-t border-gray-100 dark:border-gray-800 flex items-center justify-between gap-2 bg-gray-50/50 dark:bg-gray-900/50 shrink-0">
          <div className="text-xs text-gray-500">
            {tab === 'search' && searchResults.length > 0 && (
              <span>
                {selectedSearchIndices.size} din {searchResults.length} selectate
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="btn-secondary text-xs"
            >
              Închide
            </button>

            {tab === 'search' && searchResults.length > 0 && (
              <button
                type="button"
                onClick={handleImportSelectedSearch}
                disabled={importingSearch || selectedSearchIndices.size === 0}
                aria-busy={importingSearch}
                className="btn-primary text-xs flex items-center gap-2"
              >
                {importingSearch ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Se importă studiile...
                  </>
                ) : (
                  <>
                    <BookmarkPlus className="w-3.5 h-3.5" />
                    Importă studiile selectate ({selectedSearchIndices.size})
                  </>
                )}
              </button>
            )}

            {tab === 'paste' && (
              <button
                type="button"
                onClick={handleImportPaste}
                disabled={importingPaste || !pasteText.trim()}
                aria-busy={importingPaste}
                className="btn-primary text-xs flex items-center gap-2"
              >
                {importingPaste ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Se analizează și caută PDF-uri...
                  </>
                ) : (
                  <>
                    <Upload className="w-3.5 h-3.5" />
                    Analizează și importă lista
                  </>
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
