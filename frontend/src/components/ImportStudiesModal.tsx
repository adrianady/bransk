import { useState, useEffect } from 'react'
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
  BookMarked,
  RefreshCw,
  FolderSync,
  FileUp,
} from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { BulkImportResponse, ExternalStudyResult, ResolvedStudyMetadata, ZoteroStatus, ZoteroImportResult } from '../lib/types'
interface Props {
  isOpen: boolean
  onClose: () => void
  onImportComplete: () => void
}

type TabMode = 'search' | 'pdf' | 'identifier' | 'zotero'

/** Auto-detecție tip identificator pentru lista rapidă (DOI vs ISBN vs URL …). */
function detectIdentifierType(line: string): string {
  const t = line.trim()
  if (!t) return ''
  if (/10\.\d{4,}\//.test(t)) return 'DOI'
  if (/\bISBN\b/i.test(t) || /97[89][- ]?\d{1,5}[- ]?\d+[- ]?[\dX]/i.test(t.replace(/[- ]/g, ''))) return 'ISBN'
  if (/arxiv/i.test(t) || /\d{4}\.\d{4,}/.test(t)) return 'arXiv'
  if (/PMID:?/i.test(t) || /pubmed/i.test(t)) return 'PMID'
  if (/PMC\d+/i.test(t)) return 'PMC'
  if (/^https?:\/\//i.test(t)) return 'URL'
  return 'necunoscut'
}

function importFeedbackMessage(res: BulkImportResponse): string {
  return `Importat ${res.total_imported} studii` +
    (res.pdfs_downloaded ? `, ${res.pdfs_downloaded} PDF-uri descărcate` : '') +
    (res.pdfs_failed ? `, PDF-uri indisponibile: ${res.pdfs_failed}` : '') +
    (res.pdfs_index_failed ? `, PDF-uri descărcate, dar neindexate: ${res.pdfs_index_failed}` : '') +
    (res.total_skipped ? `, ${res.total_skipped} sărite (existau deja)` : '')
}

/**
 * Normalize a folder path pasted by the user: drop a leading ``-`` copied from
 * ``ls`` output, surrounding quotes/backticks, bullets/prompts, and unwrap
 * ``file://`` URLs. Without this, ``-/media/disk/Zotero`` looked nonexistent.
 */
function cleanLocalPath(raw: string): string {
  let text = (raw ?? '').trim()
  if (/^file:\/\//i.test(text)) {
    try {
      text = decodeURIComponent(text.slice(7))
    } catch {
      text = text.slice(7)
    }
  }
  return text
    .replace(/^[\s\-–—−•·*>`"'“”‘’(\[]+/, '')
    .replace(/[\s>`"'“”‘’`)\]]+$/, '')
    .trim()
}

export function ImportStudiesModal({ isOpen, onClose, onImportComplete }: Props) {
  const toast = useToast()
  const [tab, setTab] = useState<TabMode>('pdf')

  // ── Tab 1: External Search State ──
  const [searchQuery, setSearchQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResults, setSearchResults] = useState<ExternalStudyResult[]>([])
  const [selectedSearchIndices, setSelectedSearchIndices] = useState<Set<number>>(new Set())
  const [autoDownloadSearchPdfs, setAutoDownloadSearchPdfs] = useState(true)
  const [importingSearch, setImportingSearch] = useState(false)

  // ── Tab 2: Direct PDF upload State ──
  const [pdfFile, setPdfFile] = useState<File | null>(null)
  const [pdfTitle, setPdfTitle] = useState('')
  const [pdfStudyType, setPdfStudyType] = useState('peer-reviewed paper')
  const [uploadingDirectPdf, setUploadingDirectPdf] = useState(false)
  const [isDraggingPdf, setIsDraggingPdf] = useState(false)
  const [pdfMetaLoading, setPdfMetaLoading] = useState(false)
  const [pdfMetaHint, setPdfMetaHint] = useState('')
  const [pdfAuthors, setPdfAuthors] = useState('')
  const [pdfDoi, setPdfDoi] = useState('')
  const [pdfIsbn, setPdfIsbn] = useState('')

  // ── Tab 3: Identifier List State ──
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


  // ── Tab 4: Zotero Integration State ──
  const [zoteroStatus, setZoteroStatus] = useState<ZoteroStatus | null>(null)
  const [checkingZotero, setCheckingZotero] = useState(false)
  const [importingZoteroLocal, setImportingZoteroLocal] = useState(false)
  const [importingZoteroFile, setImportingZoteroFile] = useState(false)
  const [syncingZoteroApi, setSyncingZoteroApi] = useState(false)
  const [zoteroApiKey, setZoteroApiKey] = useState('')
  const [zoteroUserId, setZoteroUserId] = useState('')
  const [zoteroCollection, setZoteroCollection] = useState('')
  const [customZoteroPath, setCustomZoteroPath] = useState('')
  const [probedZoteroPath, setProbedZoteroPath] = useState('')
  const [customStorageDir, setCustomStorageDir] = useState('')
  const [dedupByDoi, setDedupByDoi] = useState(true)
  const [dedupByTitle, setDedupByTitle] = useState(true)
  const [zoteroImportSummary, setZoteroImportSummary] = useState<ZoteroImportResult | null>(null)
  const [isDraggingZoteroFile, setIsDraggingZoteroFile] = useState(false)

  const probeZoteroPath = async (pathArg?: string) => {
    const probe = cleanLocalPath(pathArg ?? customZoteroPath)
    setCheckingZotero(true)
    try {
      if (api.zotero?.status) {
        const res = await api.zotero.status(probe || undefined)
        setZoteroStatus(res)
        if (res.has_database || res.has_pdfs) {
          // ``res.path`` is the actual Zotero data directory when the user
          // selected its ``storage/`` child or a parent library folder.
          const resolved = cleanLocalPath(res.path || '')
          if (resolved) {
            setProbedZoteroPath(resolved)
            setCustomZoteroPath(prev => (probe ? resolved : prev || resolved))
          }
        } else {
          setProbedZoteroPath(probe || res.path || '')
          if (!customZoteroPath.trim() && res.path) {
            setCustomZoteroPath(cleanLocalPath(res.path))
          }
        }
        if (res.storage_path !== undefined) {
          setCustomStorageDir(prev => prev || cleanLocalPath(res.storage_path ?? ''))
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Eroare conectare Zotero'
      setZoteroStatus({ installed: false, message: msg })
      setProbedZoteroPath(probe)
    } finally {
      setCheckingZotero(false)
    }
  }

  useEffect(() => {
    if (!isOpen || tab !== 'zotero') return
    void probeZoteroPath('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, tab])

  // Auto-probe a custom folder path shortly after the user stops typing.
  useEffect(() => {
    if (!isOpen || tab !== 'zotero') return
    const value = cleanLocalPath(customZoteroPath)
    if (!value || value === probedZoteroPath) return
    const timer = window.setTimeout(() => { void probeZoteroPath(value) }, 600)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customZoteroPath, probedZoteroPath, isOpen, tab])

  const handleImportZoteroLocal = async () => {
    setImportingZoteroLocal(true)
    setZoteroImportSummary(null)
    try {
      const options: {
        collection?: string
        data_dir?: string
        storage_dir?: string
        dedup_by_doi?: boolean
        dedup_by_title?: boolean
      } = {
        collection: zoteroCollection.trim() || undefined,
        storage_dir: cleanLocalPath(customStorageDir),
        dedup_by_doi: dedupByDoi,
        dedup_by_title: dedupByTitle,
      }
      const chosenPath = cleanLocalPath(customZoteroPath)
      if (chosenPath) options.data_dir = chosenPath
      const res = await api.zotero.importLocal(options)
      setZoteroImportSummary(res)
      const skippedMsg = res.skipped_count > 0 ? ` și ${res.skipped_count} dubluri evitate după Titlu/DOI` : ''
      toast.success(`Import Zotero finalizat: ${res.imported_count} adăugate${skippedMsg}, ${res.pdfs_copied} PDF-uri copiate.`)
      onImportComplete()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Importul local din Zotero a eșuat'
      toast.error(msg)
    } finally {
      setImportingZoteroLocal(false)
    }
  }

  const handleUploadZoteroFile = async (file: File) => {
    if (!file) return
    setImportingZoteroFile(true)
    setZoteroImportSummary(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await api.zotero.importFile(formData)
      setZoteroImportSummary(res)
      toast.success(`Import fișier Zotero: ${res.imported_count} importate, ${res.skipped_count} sărite.`)
      onImportComplete()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Importul fișierului a eșuat'
      toast.error(msg)
    } finally {
      setImportingZoteroFile(false)
    }
  }

  const handleSyncZoteroApi = async () => {
    if (!zoteroApiKey.trim() || !zoteroUserId.trim()) {
      toast.error('Introdu API Key și User ID pentru sincronizare Zotero Web.')
      return
    }
    setSyncingZoteroApi(true)
    setZoteroImportSummary(null)
    try {
      const res = await api.zotero.syncApi(zoteroApiKey.trim(), zoteroUserId.trim())
      setZoteroImportSummary(res)
      toast.success(`Sincronizare Zotero Web: ${res.imported_count} importate, ${res.pdfs_copied} PDF-uri sincronizate.`)
      onImportComplete()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Sincronizarea API Zotero a eșuat'
      toast.error(msg)
    } finally {
      setSyncingZoteroApi(false)
    }
  }
  // Formularul manual (studiu / carte) — randat în <details> în tab-ul
  // „Identificator rapid”, ca alternativă la lista de identificatori.
  const ManualEntryForm = () => (
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
  )

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

  // ── Direct PDF Upload Handlers (Tab: Încarcă PDF direct) ──
  const handlePdfFileSelected = async (f: File) => {
    setPdfFile(f)
    setPdfMetaLoading(true)
    setPdfMetaHint('')
    try {
      const meta = await api.studies.extractPdfMetadata(f)
      if (meta.title && !pdfTitle.trim()) setPdfTitle(meta.title)
      if (meta.authors) setPdfAuthors(meta.authors)
      if (meta.doi) setPdfDoi(meta.doi)
      if (meta.isbn) setPdfIsbn(meta.isbn)
      if (meta.study_type === 'book' || meta.study_type === 'peer-reviewed paper') {
        setPdfStudyType(meta.study_type)
      }
      const bits = [
        meta.page_count ? `${meta.page_count} pagini` : '',
        meta.doi ? `DOI ${meta.doi}` : '',
        meta.isbn ? `ISBN ${meta.isbn}` : '',
        meta.study_type === 'book' ? 'sugestie: Carte / Tratat' : 'sugestie: Studiu recenzat',
      ].filter(Boolean)
      setPdfMetaHint(bits.join(' · '))
    } catch {
      setPdfMetaHint('Nu am putut citi metadatele PDF-ului; completează titlul manual.')
    } finally {
      setPdfMetaLoading(false)
    }
  }

  const handleUploadDirectPdf = async () => {
    if (!pdfFile) {
      toast.info('Alege mai întâi un fișier PDF de încărcat.')
      return
    }
    if (!pdfTitle.trim()) {
      toast.error('Titlul lucrării este obligatoriu.')
      return
    }
    setUploadingDirectPdf(true)
    try {
      const created = await api.entries.create({
        title: pdfTitle.trim(),
        study_type: pdfStudyType,
        authors: pdfAuthors.trim(),
        doi: pdfDoi.trim(),
        isbn: pdfIsbn.trim(),
        tags: 'study',
      } as any)
      const updated = await api.entries.uploadPdf(created.id, pdfFile) as any
      if (updated?.duplicate_entry_id) {
        toast.warning(updated.duplicate_warning || `Același PDF există deja la intrarea #${updated.duplicate_entry_id}.`)
      } else {
        toast.success(
          `"${pdfTitle.slice(0, 45)}..." a fost adăugat${pdfStudyType === 'book' ? ' ca și carte' : ' ca studiu'} cu PDF indexat!`
        )
      }
      setPdfFile(null)
      setPdfTitle('')
      setPdfAuthors('')
      setPdfDoi('')
      setPdfIsbn('')
      setPdfMetaHint('')
      onImportComplete()
      onClose()
    } catch (err: any) {
      toast.error('Încărcarea PDF-ului a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setUploadingDirectPdf(false)
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
                PDF direct · Identificator rapid (DOI / ISBN / URL) · Căutare academică · Zotero & fișiere
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

        {/* Tab Navigation: 4 opțiuni mari și clare */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 px-2 sm:px-6 pt-2 sm:pt-3 pb-1 shrink-0">
          <button
            onClick={() => setTab('pdf')}
            className={`flex flex-col items-start gap-1 p-3 rounded-xl border-2 text-left transition-colors ${
              tab === 'pdf'
                ? 'border-chrono-600 bg-chrono-50 dark:bg-chrono-950/40 shadow-sm'
                : 'border-gray-200 dark:border-gray-800 hover:border-chrono-300 dark:hover:border-chrono-800 bg-white dark:bg-gray-900'
            }`}
            title="Încarcă un PDF de pe disc, cu clasificare Studiu vs Carte"
          >
            <span className="flex items-center gap-1.5 text-xs sm:text-sm font-bold text-gray-900 dark:text-gray-100">
              <FileUp className="w-4 h-4 text-chrono-600" />
              1) Încarcă PDF direct
            </span>
            <span className="text-[11px] text-gray-500 dark:text-gray-400 leading-snug">
              Dropzone evident + clasificare Studiu vs Carte
            </span>
          </button>
          <button
            onClick={() => setTab('identifier')}
            className={`flex flex-col items-start gap-1 p-3 rounded-xl border-2 text-left transition-colors ${
              tab === 'identifier'
                ? 'border-chrono-600 bg-chrono-50 dark:bg-chrono-950/40 shadow-sm'
                : 'border-gray-200 dark:border-gray-800 hover:border-chrono-300 dark:hover:border-chrono-800 bg-white dark:bg-gray-900'
            }`}
            title="Lipește un DOI, ISBN sau URL — tipul se detectează automat"
          >
            <span className="flex items-center gap-1.5 text-xs sm:text-sm font-bold text-gray-900 dark:text-gray-100">
              <Upload className="w-4 h-4 text-chrono-600" />
              2) Identificator rapid
            </span>
            <span className="text-[11px] text-gray-500 dark:text-gray-400 leading-snug">
              DOI / ISBN / URL — cu auto-detecție tip
            </span>
          </button>
          <button
            onClick={() => setTab('search')}
            className={`flex flex-col items-start gap-1 p-3 rounded-xl border-2 text-left transition-colors ${
              tab === 'search'
                ? 'border-chrono-600 bg-chrono-50 dark:bg-chrono-950/40 shadow-sm'
                : 'border-gray-200 dark:border-gray-800 hover:border-chrono-300 dark:hover:border-chrono-800 bg-white dark:bg-gray-900'
            }`}
            title="Caută în Crossref, Europe PMC și arXiv și importă în masă"
          >
            <span className="flex items-center gap-1.5 text-xs sm:text-sm font-bold text-gray-900 dark:text-gray-100">
              <Search className="w-4 h-4 text-chrono-600" />
              3) Căutare Academică
            </span>
            <span className="text-[11px] text-gray-500 dark:text-gray-400 leading-snug">
              Crossref / arXiv — caută și importă în masă
            </span>
          </button>
          <button
            onClick={() => setTab('zotero')}
            className={`flex flex-col items-start gap-1 p-3 rounded-xl border-2 text-left transition-colors ${
              tab === 'zotero'
                ? 'border-chrono-600 bg-chrono-50 dark:bg-chrono-950/40 shadow-sm'
                : 'border-gray-200 dark:border-gray-800 hover:border-chrono-300 dark:hover:border-chrono-800 bg-white dark:bg-gray-900'
            }`}
            title="Importă din Zotero local / web sau din fișiere .bib / .ris / .json"
          >
            <span className="flex items-center gap-1.5 text-xs sm:text-sm font-bold text-gray-900 dark:text-gray-100">
              <BookMarked className="w-4 h-4 text-chrono-600" />
              4) Integrare Zotero & Fișiere
            </span>
            <span className="text-[11px] text-gray-500 dark:text-gray-400 leading-snug">
              Zotero local / web sau .bib / .ris / .json
            </span>
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
          {/* TAB: 1) Încarcă PDF direct — dropzone + Studiu vs Carte */}
          {tab === 'pdf' && (
            <div className="space-y-4">
              <div className="bg-chrono-50 dark:bg-chrono-950/30 border border-chrono-200 dark:border-chrono-900/60 p-3.5 rounded-xl text-xs text-chrono-900 dark:text-chrono-200 space-y-1">
                <p className="font-semibold flex items-center gap-1.5">
                  <FileUp className="w-4 h-4 text-chrono-600" />
                  Încarcă un PDF de pe disc — titlul, autorii, DOI/ISBN și tipul se citesc automat din fișier.
                </p>
                <p className="opacity-80">
                  Poți corecta orice câmp înainte de salvare. Clasificarea Studiu vs. Carte este doar o sugestie.
                </p>
              </div>

              <div>
                <span className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                  Fișier PDF
                </span>
                <label
                  onDragOver={e => { e.preventDefault(); setIsDraggingPdf(true) }}
                  onDragLeave={() => setIsDraggingPdf(false)}
                  onDrop={e => {
                    e.preventDefault()
                    setIsDraggingPdf(false)
                    const f = e.dataTransfer.files?.[0]
                    if (f && /\.pdf$/i.test(f.name)) void handlePdfFileSelected(f)
                    else if (f) toast.error('Doar fișiere .pdf sunt acceptate.')
                  }}
                  className={`flex flex-col items-center justify-center gap-2 p-6 rounded-xl border-2 border-dashed cursor-pointer transition-colors ${
                    isDraggingPdf
                      ? 'border-chrono-600 bg-chrono-50 dark:bg-chrono-950/40'
                      : 'border-gray-300 dark:border-gray-700 hover:border-chrono-400 bg-gray-50/50 dark:bg-gray-900/50'
                  }`}
                  title="Trage un PDF aici sau apasă pentru a alege fișierul"
                >
                  {pdfMetaLoading ? (
                    <Loader2 className="w-8 h-8 text-chrono-600 animate-spin" />
                  ) : (
                    <Upload className="w-8 h-8 text-chrono-600" />
                  )}
                  <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    {pdfFile ? `📄 ${pdfFile.name}` : 'Trage PDF-ul aici sau apasă pentru a-l alege'}
                  </span>
                  <span className="text-[11px] text-gray-500">
                    {pdfMetaLoading
                      ? 'Citesc metadatele PDF-ului…'
                      : 'doar .pdf · metadate preluate automat · se indexează după încărcare'}
                  </span>
                  <input
                    type="file"
                    accept=".pdf,application/pdf"
                    className="hidden"
                    onChange={e => {
                      const f = e.target.files?.[0]
                      if (f) void handlePdfFileSelected(f)
                      e.target.value = ''
                    }}
                  />
                </label>
                {pdfMetaHint && (
                  <p className="mt-2 text-[11px] text-chrono-700 dark:text-chrono-300 bg-chrono-50 dark:bg-chrono-950/30 border border-chrono-200 dark:border-chrono-900/60 rounded-lg px-2.5 py-1.5">
                    {pdfMetaHint}
                  </p>
                )}
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                  Titlul lucrării <span className="text-madder">*</span>
                </label>
                <input
                  type="text"
                  value={pdfTitle}
                  onChange={e => setPdfTitle(e.target.value)}
                  placeholder="ex. Thinking, Fast and Slow — sau titlul articolului"
                  className="input w-full text-sm"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div>
                  <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                    Autori
                  </label>
                  <input
                    type="text"
                    value={pdfAuthors}
                    onChange={e => setPdfAuthors(e.target.value)}
                    placeholder="ex. Kahneman, D."
                    className="input w-full text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                    DOI / ISBN
                  </label>
                  <input
                    type="text"
                    value={pdfDoi || pdfIsbn}
                    onChange={e => {
                      const value = e.target.value.trim()
                      if (value.toUpperCase().startsWith('978') || value.toUpperCase().startsWith('979')) {
                        setPdfIsbn(value)
                        setPdfDoi('')
                      } else {
                        setPdfDoi(value)
                        setPdfIsbn('')
                      }
                    }}
                    placeholder="10.xxxx/... sau 978-..."
                    className="input w-full text-sm font-mono"
                  />
                </div>
              </div>

              <div>
                <span className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                  Clasificare: Studiu vs Carte
                </span>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { value: 'peer-reviewed paper', icon: '📄', title: 'Studiu / Articol', sub: 'articol recenzat, review, teză' },
                    { value: 'book', icon: '📚', title: 'Carte / Tratat', sub: 'monografie, manual, volum' },
                  ].map(opt => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setPdfStudyType(opt.value)}
                      aria-pressed={pdfStudyType === opt.value}
                      className={`flex items-start gap-2 p-3 rounded-xl border-2 text-left transition-colors ${
                        pdfStudyType === opt.value
                          ? 'border-chrono-600 bg-chrono-50 dark:bg-chrono-950/40'
                          : 'border-gray-200 dark:border-gray-800 hover:border-chrono-300'
                      }`}
                    >
                      <span className="text-xl">{opt.icon}</span>
                      <span>
                        <span className="block text-xs font-bold text-gray-900 dark:text-gray-100">{opt.title}</span>
                        <span className="block text-[11px] text-gray-500">{opt.sub}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB: 2) Identificator rapid — DOI / ISBN / URL cu auto-detecție + formular manual */}
          {tab === 'identifier' && (
            <div className="space-y-4">
              <div className="bg-accent-soft border border-accent/25 p-3.5 rounded-lg text-xs text-accent-strong space-y-1">
                <p className="font-semibold flex items-center gap-1.5">
                  <Sparkles className="w-4 h-4 text-accent" />
                  Identificator rapid — tipul se detectează automat (DOI vs ISBN vs URL):
                </p>
                <ul className="list-disc list-inside font-mono text-[11px] text-accent pl-1">
                  <li>10.1038/s41586-020-2012-7 (DOI Crossref)</li>
                  <li>ISBN 978-0-123456-78-9 (carte — se completează automat ISBN)</li>
                  <li>https://doi.org/10.1016/j.neuron.2021.05.002</li>
                  <li>arXiv:2301.07067 sau https://arxiv.org/abs/2104.08653</li>
                  <li>https://pubmed.ncbi.nlm.nih.gov/32188939/ sau PMID: 32188939</li>
                  <li>PMC7144277</li>
                </ul>
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1.5">
                  Lipește lista de identificatori (unul pe rând) sau un text cu referințe:
                </label>
                <textarea
                  value={pasteText}
                  onChange={e => setPasteText(e.target.value)}
                  placeholder="Exemplu:&#10;10.1038/s41586-020-2012-7&#10;ISBN 978-0-123456-78-9&#10;arXiv:2301.07067"
                  rows={6}
                  className="input font-mono text-xs w-full leading-relaxed"
                />
                {pasteText.trim() && (
                  <div className="flex flex-wrap gap-1.5 mt-2" aria-live="polite">
                    {pasteText.split('\n').map(l => l.trim()).filter(Boolean).slice(0, 6).map((line, i) => (
                      <span key={i} className="badge badge-muted text-[11px] font-mono" title={line}>
                        {detectIdentifierType(line)}: {line.slice(0, 42)}{line.length > 42 ? '…' : ''}
                      </span>
                    ))}
                  </div>
                )}
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

              <details className="rounded-xl border border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/50 p-3">
                <summary className="text-xs font-semibold cursor-pointer text-gray-700 dark:text-gray-300">
                  ✍️ Sau adaugă manual un singur studiu / o singură carte (formular)
                </summary>
                <div className="pt-3">
                  <ManualEntryForm />
                </div>
              </details>
            </div>
          )}

          {/* TAB 4: Zotero Integration */}
          {tab === 'zotero' && (
            <div className="space-y-6">
              {/* 1. Local Zotero Detection Card */}
              <div className="card p-4 sm:p-5 border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 rounded-xl space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <BookMarked className="w-5 h-5 text-chrono-600 dark:text-chrono-400" />
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      Zotero local (Desktop / SQLite / PDF)
                    </h3>
                  </div>
                  <button
                    type="button"
                    onClick={() => void probeZoteroPath()}
                    disabled={checkingZotero}
                    className="text-xs text-gray-500 hover:text-chrono-600 flex items-center gap-1"
                    title="Reverifică detectarea Zotero"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${checkingZotero ? 'animate-spin' : ''}`} />
                    Reverifică
                  </button>
                </div>

                {/* Folder path input — always visible so any Zotero install can be used */}
                <div className="space-y-2">
                  <label htmlFor="zotero-folder-path" className="block text-xs font-medium text-gray-700 dark:text-gray-300">
                    Cale folder Zotero (bază de date zotero.sqlite sau folder de PDF-uri)
                  </label>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <input
                      id="zotero-folder-path"
                      type="text"
                      placeholder="/home/user/Zotero sau /media/disc/Zotero/storage"
                      value={customZoteroPath}
                      onChange={e => setCustomZoteroPath(cleanLocalPath(e.target.value))}
                      onBlur={() => {
                        const value = cleanLocalPath(customZoteroPath)
                        setCustomZoteroPath(value)
                        if (value && value !== probedZoteroPath) void probeZoteroPath(value)
                      }}
                      className="input-text text-xs flex-1"
                    />
                    <button
                      type="button"
                      onClick={() => void probeZoteroPath(customZoteroPath)}
                      disabled={checkingZotero}
                      aria-busy={checkingZotero}
                      className="btn-secondary text-xs flex items-center justify-center gap-2 shrink-0 py-2 px-4"
                    >
                      {checkingZotero ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Search className="w-4 h-4" />
                      )}
                      Verifică folder
                    </button>
                  </div>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">
                    Acceptă folderul care conține <code>zotero.sqlite</code>, folderul Zotero sau orice folder cu fișiere PDF.
                  </p>
                </div>

                {checkingZotero ? (
                  <div className="flex items-center gap-2 text-xs text-gray-500 py-2">
                    <Loader2 className="w-4 h-4 animate-spin text-chrono-600" />
                    Se verifică folderul Zotero...
                  </div>
                ) : zoteroStatus?.has_database || zoteroStatus?.installed ? (
                  <div className="p-3 bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 rounded-lg text-xs space-y-1">
                    <div className="flex items-center gap-2 text-emerald-800 dark:text-emerald-300 font-semibold">
                      <CheckCircle2 className="w-4 h-4" />
                      Zotero detectat: {zoteroStatus.item_count ?? 0} articole în baza de date
                      {typeof zoteroStatus.pdf_count === 'number' ? `, ${zoteroStatus.pdf_count} PDF-uri atașate` : ''}
                    </div>
                    {(zoteroStatus.path || probedZoteroPath) && (
                      <p className="text-emerald-700 dark:text-emerald-400 text-[11px] font-mono truncate">
                        Cale: {zoteroStatus.path || probedZoteroPath}
                      </p>
                    )}
                  </div>
                ) : zoteroStatus?.has_pdfs ? (
                  <div className="p-3 bg-sky-50 dark:bg-sky-950/30 border border-sky-200 dark:border-sky-800 rounded-lg text-xs space-y-1">
                    <div className="flex items-center gap-2 text-sky-800 dark:text-sky-300 font-semibold">
                      <CheckCircle2 className="w-4 h-4" />
                      Folder de PDF-uri detectat: {zoteroStatus.pdf_count ?? 0} fișiere PDF găsite
                    </div>
                    {(zoteroStatus.path || probedZoteroPath) && (
                      <p className="text-sky-700 dark:text-sky-400 text-[11px] font-mono truncate">
                        Cale: {zoteroStatus.path || probedZoteroPath}
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="p-3 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-lg text-xs text-amber-800 dark:text-amber-300">
                    {probedZoteroPath ? (
                      <>
                        Nu am găsit <code>zotero.sqlite</code> sau fișiere PDF în folderul{' '}
                        <span className="font-mono">{probedZoteroPath}</span>. Verifică calea sau alege alt folder.
                      </>
                    ) : (
                      <>
                        Zotero local nu a fost găsit automat în căile standard. Introdu mai sus calea către folderul
                        Zotero (inclusiv de pe alt disc) sau către folderul cu PDF-uri, apoi apasă „Verifică folder”.
                      </>
                    )}
                  </div>
                )}

                {/* Configurare folder PDF-uri, colecție & deduplicare */}
                <div className="p-3 bg-gray-50 dark:bg-gray-800/40 border border-gray-200 dark:border-gray-800 rounded-lg space-y-3">
                  <div>
                    <label htmlFor="zotero-storage-dir" className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Folder de stocare PDF-uri (personalizat / ZotFile / Google Drive)
                    </label>
                    <input
                      id="zotero-storage-dir"
                      type="text"
                      placeholder="/home/user/Zotero/storage sau alt folder cu PDF-uri"
                      value={customStorageDir}
                      onChange={e => setCustomStorageDir(cleanLocalPath(e.target.value))}
                      className="input-text text-xs w-full"
                    />
                  </div>
                  <div>
                    <label htmlFor="zotero-collection" className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Colecție specifică (opțional)
                    </label>
                    <input
                      id="zotero-collection"
                      type="text"
                      placeholder="Colecție specifică (opțional, lasă gol pentru întreaga bibliotecă)"
                      value={zoteroCollection}
                      onChange={e => setZoteroCollection(e.target.value)}
                      className="input-text text-xs w-full"
                    />
                  </div>
                  <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-6 pt-1 border-t border-gray-200 dark:border-gray-700/60">
                    <label className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={dedupByDoi}
                        onChange={e => setDedupByDoi(e.target.checked)}
                        className="rounded border-gray-300 text-chrono-600 focus:ring-chrono-500"
                      />
                      <span>Verifică și evită dublurile după DOI</span>
                    </label>
                    <label className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={dedupByTitle}
                        onChange={e => setDedupByTitle(e.target.checked)}
                        className="rounded border-gray-300 text-chrono-600 focus:ring-chrono-500"
                      />
                      <span>Verifică și evită dublurile după Titlu</span>
                    </label>
                  </div>
                </div>

                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={handleImportZoteroLocal}
                    disabled={importingZoteroLocal}
                    aria-busy={importingZoteroLocal}
                    className="btn-primary text-xs flex items-center justify-center gap-2 py-2 px-4"
                  >
                    {importingZoteroLocal ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Se importă din Zotero / Folder...
                      </>
                    ) : (
                      <>
                        <BookmarkPlus className="w-4 h-4" />
                        Importă din Zotero / Folder
                      </>
                    )}
                  </button>
                </div>
              </div>

              {/* 2. Drag-and-Drop File Upload Area */}
              <div className="card p-4 sm:p-5 border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 rounded-xl space-y-3">
                <div className="flex items-center gap-2">
                  <FileUp className="w-5 h-5 text-chrono-600 dark:text-chrono-400" />
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                    Import fișier exportat din Zotero (.bib, .ris, .json)
                  </h3>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Exportă din Zotero (File → Export Library) în format BibTeX (.bib), RIS (.ris) sau CSL JSON (.json) și trage fișierul aici.
                </p>

                <div
                  onDragOver={e => {
                    e.preventDefault()
                    setIsDraggingZoteroFile(true)
                  }}
                  onDragLeave={() => setIsDraggingZoteroFile(false)}
                  onDrop={e => {
                    e.preventDefault()
                    setIsDraggingZoteroFile(false)
                    const file = e.dataTransfer.files?.[0]
                    if (file) handleUploadZoteroFile(file)
                  }}
                  className={`border-2 border-dashed rounded-xl p-6 sm:p-8 text-center transition-colors cursor-pointer flex flex-col items-center justify-center gap-2 ${
                    isDraggingZoteroFile
                      ? 'border-chrono-500 bg-chrono-50/50 dark:bg-chrono-950/30'
                      : 'border-gray-300 dark:border-gray-700 hover:border-chrono-400 bg-gray-50/50 dark:bg-gray-800/30'
                  }`}
                  onClick={() => {
                    const input = document.getElementById('zotero-file-input') as HTMLInputElement | null
                    input?.click()
                  }}
                >
                  <input
                    id="zotero-file-input"
                    type="file"
                    accept=".bib,.ris,.json,application/json,application/x-bibtex"
                    className="hidden"
                    onChange={e => {
                      const file = e.target.files?.[0]
                      if (file) handleUploadZoteroFile(file)
                      e.target.value = ''
                    }}
                  />
                  {importingZoteroFile ? (
                    <div className="flex flex-col items-center gap-2">
                      <Loader2 className="w-8 h-8 animate-spin text-chrono-600" />
                      <p className="text-xs font-medium text-gray-700 dark:text-gray-300">
                        Se procesează și se importă referințele din fișier...
                      </p>
                    </div>
                  ) : (
                    <>
                      <Upload className="w-8 h-8 text-gray-400 dark:text-gray-500" />
                      <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                        Trage fișierul aici sau apasă pentru a alege
                      </p>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400">
                        Acceptă fișiere .bib (BibTeX), .ris (Research Information Systems) sau .json (CSL)
                      </p>
                    </>
                  )}
                </div>
              </div>

              {/* 3. Optional Web API Sync Fields */}
              <div className="card p-4 sm:p-5 border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 rounded-xl space-y-3">
                <div className="flex items-center gap-2">
                  <FolderSync className="w-5 h-5 text-chrono-600 dark:text-chrono-400" />
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                    Sincronizare Zotero Web API (Opțional)
                  </h3>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Dacă dorești sincronizare directă din cloud-ul Zotero, introdu User ID-ul tău Zotero și un API Key creat în contul zotero.org (Settings → Feeds/API).
                </p>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[11px] font-semibold text-gray-700 dark:text-gray-300 mb-1">
                      Zotero User ID / Library ID
                    </label>
                    <input
                      type="text"
                      placeholder="Ex: 12345678"
                      value={zoteroUserId}
                      onChange={e => setZoteroUserId(e.target.value)}
                      className="input-text text-xs w-full"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] font-semibold text-gray-700 dark:text-gray-300 mb-1">
                      Zotero API Key
                    </label>
                    <input
                      type="password"
                      placeholder="Cheia API generată pe zotero.org"
                      value={zoteroApiKey}
                      onChange={e => setZoteroApiKey(e.target.value)}
                      className="input-text text-xs w-full"
                    />
                  </div>
                </div>

                <div className="flex justify-end pt-1">
                  <button
                    type="button"
                    onClick={handleSyncZoteroApi}
                    disabled={syncingZoteroApi || !zoteroApiKey.trim() || !zoteroUserId.trim()}
                    aria-busy={syncingZoteroApi}
                    className="btn-primary text-xs flex items-center gap-2 py-2 px-4"
                  >
                    {syncingZoteroApi ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Se sincronizează cu Zotero Web...
                      </>
                    ) : (
                      <>
                        <FolderSync className="w-4 h-4" />
                        Sincronizează via Zotero Web API
                      </>
                    )}
                  </button>
                </div>
              </div>

              {/* Zotero Import Summary Results */}
              {zoteroImportSummary && (
                <div
                  role="status"
                  aria-live="polite"
                  className="p-4 rounded-xl text-xs space-y-2 border bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800"
                >
                  <div className="flex items-center gap-2 font-bold text-emerald-800 dark:text-emerald-300">
                    <CheckCircle2 className="w-4 h-4 shrink-0" />
                    <span>
                      Rezultat import Zotero: {zoteroImportSummary.imported_count} studii adăugate
                      {zoteroImportSummary.skipped_count > 0 && (
                        <> și {zoteroImportSummary.skipped_count} dubluri evitate după Titlu/DOI</>
                      )}
                      , {zoteroImportSummary.pdfs_copied} PDF-uri copiate/atașate.
                    </span>
                  </div>
                  {zoteroImportSummary.skipped && zoteroImportSummary.skipped.length > 0 && (
                    <div className="pt-1 border-t border-emerald-200 dark:border-emerald-800 text-[11px] text-gray-600 dark:text-gray-400">
                      <p className="font-semibold text-gray-700 dark:text-gray-300 mb-1">
                        Dubluri evitate ({zoteroImportSummary.skipped.length}):
                      </p>
                      <ul className="list-disc list-inside space-y-0.5 max-h-28 overflow-y-auto">
                        {zoteroImportSummary.skipped.slice(0, 10).map((s, idx) => (
                          <li key={idx} className="truncate">
                            <span className="font-medium text-gray-800 dark:text-gray-200">{s.title || 'Fără titlu'}</span>
                            {s.reason ? ` (${s.reason})` : ''}
                          </li>
                        ))}
                        {zoteroImportSummary.skipped.length > 10 && (
                          <li className="italic text-gray-500">...și încă {zoteroImportSummary.skipped.length - 10} dubluri.</li>
                        )}
                      </ul>
                    </div>
                  )}
                  {zoteroImportSummary.errors && zoteroImportSummary.errors.length > 0 && (
                    <div className="pt-1 border-t border-emerald-200 dark:border-emerald-800 space-y-1">
                      <p className="font-semibold text-amber-700 dark:text-amber-400">Atenționări la import:</p>
                      <ul className="list-disc list-inside space-y-0.5 text-gray-700 dark:text-gray-300">
                        {zoteroImportSummary.errors.slice(0, 5).map((err, idx) => (
                          <li key={idx}>{err}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
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

            {tab === 'pdf' && (
              <button
                type="button"
                onClick={handleUploadDirectPdf}
                disabled={uploadingDirectPdf || pdfMetaLoading || !pdfFile || !pdfTitle.trim()}
                aria-busy={uploadingDirectPdf}
                className="btn-primary text-xs flex items-center gap-2"
                title="Creează intrarea și încarcă PDF-ul cu indexare automată"
              >
                {uploadingDirectPdf ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Se încarcă și indexează…
                  </>
                ) : (
                  <>
                    <FileUp className="w-3.5 h-3.5" />
                    Încarcă PDF în bibliotecă
                  </>
                )}
              </button>
            )}

            {tab === 'identifier' && (
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
