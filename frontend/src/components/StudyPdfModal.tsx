import { useState, useEffect, useRef, useCallback } from 'react'
import {
  X,
  BookOpen,
  Plus,
  Trash2,
  Edit2,
  Check,
  Loader2,
  Calendar,
  GraduationCap,
  Sparkles,
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  Maximize2,
  FileText,
  Highlighter,
  Brain,
  AlertTriangle,
  TextQuote,
} from 'lucide-react'
import * as pdfjsLib from 'pdfjs-dist'
import 'pdfjs-dist/web/pdf_viewer.css'
import { api } from '../lib/api'
import { getStudyDoi } from '../lib/doi'
import { setReadingContext, getReadingContext, useReadingContext } from '../lib/readingContext'
import { useToast } from './Toast'
import type { Entry, ExtractedConcept, PdfHighlight, StudyNote, StudyQASource, NoteDossier } from '../lib/types'
import { useOfflineNotes, useOfflineHighlights, usePdfCache } from '../lib/idb/hooks'
import { ConflictMergeDialog } from './ConflictMergeDialog'
import { registerBackgroundSync, subscribeServerStatus, isServerReachable } from '../lib/idb/sync'

// Configure PDF.js worker - local bundle for offline
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

interface Props {
  study: Entry | null
  isOpen: boolean
  onClose: () => void
  onNotesUpdated?: () => void
  sourceToOpen?: StudyQASource | null
}

// Status chips reused for capture previews (same vocabulary as StudyAnalysisPanel / NotesPanel).
const CAPTURE_STATUS_COLOR: Record<string, string> = {
  verificat: 'text-verdigris border-verdigris/20 bg-verdigris-soft',
  verificat_cu_nuanta: 'text-chrono-600 border-chrono-300/40 bg-chrono-50 dark:bg-chrono-950/20',
  incomplet: 'text-brass border-brass/20 bg-brass-soft',
  subestimat: 'text-brass border-brass/20 bg-brass-soft',
  corectat: 'text-madder border-madder/20 bg-madder-soft',
  depasit: 'text-madder border-madder/20 bg-madder-soft',
  neverificat: 'text-gray-500 border-gray-200/60 bg-gray-100 dark:bg-gray-800 dark:border-gray-700/60',
}

export function StudyPdfModal({ study, isOpen, onClose, onNotesUpdated, sourceToOpen }: Props) {
  const toast = useToast()
  const [noteCardOpen, setNoteCardOpen] = useState(false)
  const [notesPanelOpen, setNotesPanelOpen] = useState(false)

  // Offline-first hooks
  const {
    notes,
    loading: loadingNotes,
    error: notesError,
    create: createNoteOffline,
    update: updateNoteOffline,
    remove: removeNoteOffline,
    refresh: refreshNotes,
  } = useOfflineNotes(study?.id ?? 0)

  const {
    highlights,
    loading: loadingHighlights,
    error: highlightsError,
    create: createHighlightOffline,
    remove: removeHighlightOffline,
    refresh: refreshHighlights,
  } = useOfflineHighlights(study?.id ?? 0)

  const {
    blobUrl,
    loading: loadingPdfCache,
    error: pdfCacheError,
    cached,
    refresh: refreshPdfCache,
  } = usePdfCache(study?.id ?? 0, study ? api.studies.pdfUrl(study.id) : '')

  // PDF.js State
  const [pdfDoc, setPdfDoc] = useState<any>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [numPages, setNumPages] = useState(0)
  const [scale, setScale] = useState(1.2)
  const [loadingPdf, setLoadingPdf] = useState(false)
  const [pdfProgress, setPdfProgress] = useState<number | null>(null)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const [pdfLoadAttempt, setPdfLoadAttempt] = useState(0)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [uploadingManualPdf, setUploadingManualPdf] = useState(false)
  const textLayerRef = useRef<HTMLDivElement | null>(null)
  const highlightLayerRef = useRef<HTMLDivElement | null>(null)
  const renderTaskRef = useRef<any>(null)

  // Visible highlight marks painted over the PDF text layer.
  // Matches saved quotes against the rendered text-layer spans and draws
  // absolutely positioned amber boxes in an overlay sibling.
  const drawHighlights = useCallback(() => {
    try {
      const overlay = highlightLayerRef.current
      const container = textLayerRef.current
      if (!overlay || !container) return
      overlay.replaceChildren()
      const spans = Array.from(container.querySelectorAll('span')) as HTMLSpanElement[]
      if (spans.length === 0) return

      // Concatenate span text and remember each span's range in the full string.
      const spanRanges: { span: HTMLSpanElement; text: string; start: number; end: number }[] = []
      let full = ''
      for (const span of spans) {
        const t = span.textContent ?? ''
        if (!t) continue
        const start = full.length
        full += t
        spanRanges.push({ span, text: t, start, end: full.length })
      }
      if (!full) return

      // Normalized full text (whitespace-insensitive) + norm index -> original index.
      const normFullChars: string[] = []
      const normToOrig: number[] = []
      for (let i = 0; i < full.length; i++) {
        const ch = full[i]
        if (/\s/.test(ch) || ch === ' ') continue
        normFullChars.push(ch.toLowerCase())
        normToOrig.push(i)
      }
      const normFull = normFullChars.join('')
      if (!normFull) return

      const containerRect = container.getBoundingClientRect()

      for (const hl of highlights) {
        try {
          if (hl.pageNumber != null && hl.pageNumber !== currentPage) continue
          const rawQuote = (hl.quote ?? '').replace(/[\s ]+/g, '').toLowerCase()
          if (rawQuote.length < 3) continue
          const found = normFull.indexOf(rawQuote)
          if (found < 0) continue
          const origStart = normToOrig[found]
          const origEnd = normToOrig[found + rawQuote.length - 1] + 1

          for (const r of spanRanges) {
            if (r.end <= origStart || r.start >= origEnd) continue
            const localStart = Math.max(0, origStart - r.start)
            const localEnd = Math.min(r.text.length, origEnd - r.start)
            if (localStart >= localEnd) continue
            // Map local span offsets onto its text nodes.
            const textNodes: Text[] = []
            r.span.childNodes.forEach(n => {
              if (n.nodeType === Node.TEXT_NODE) textNodes.push(n as Text)
            })
            if (textNodes.length === 0) continue
            let acc = 0
            for (const tn of textNodes) {
              const nodeText = tn.textContent ?? ''
              if (!nodeText) continue
              const nodeStart = acc
              const nodeEnd = acc + nodeText.length
              acc = nodeEnd
              const overlapStart = Math.max(localStart, nodeStart)
              const overlapEnd = Math.min(localEnd, nodeEnd)
              if (overlapStart >= overlapEnd) continue
              const range = document.createRange()
              range.setStart(tn, overlapStart - nodeStart)
              range.setEnd(tn, overlapEnd - nodeStart)
              const rects = range.getClientRects()
              for (const rect of Array.from(rects)) {
                if (rect.width < 0.5 || rect.height < 0.5) continue
                const mark = document.createElement('div')
                mark.style.position = 'absolute'
                mark.style.left = `${rect.left - containerRect.left}px`
                mark.style.top = `${rect.top - containerRect.top}px`
                mark.style.width = `${rect.width}px`
                mark.style.height = `${rect.height}px`
                mark.style.background = 'rgba(250, 204, 21, 0.35)'
                mark.style.borderRadius = '2px'
                mark.style.pointerEvents = 'none'
                overlay.appendChild(mark)
              }
            }
          }
        } catch {
          // A bad quote must never break the reader — skip it.
          continue
        }
      }
    } catch {
      // Never break the reader because of highlight painting.
    }
  }, [highlights, currentPage])

  // Highlights state — selections saved from the native reader
  const [selection, setSelection] = useState<{ text: string; x: number; y: number } | null>(null)
  const [savingHighlight, setSavingHighlight] = useState(false)

  // AI concept extraction from the current text selection
  const [extractingConcept, setExtractingConcept] = useState(false)
  const [extractedConcepts, setExtractedConcepts] = useState<ExtractedConcept[]>([])
  const [extractedRelations, setExtractedRelations] = useState<any[]>([])

  // Form state
  const [courseContext, setCourseContext] = useState('')
  const [pageNumber, setPageNumber] = useState<string>('1')
  const [noteContent, setNoteContent] = useState('')
  const [savingNote, setSavingNote] = useState(false)

  // Capture → verified dossier (TASK 4)
  const [capturing, setCapturing] = useState(false)
  const [captureResult, setCaptureResult] = useState<NoteDossier | null>(null)

  // Editing state
  const [editingNoteId, setEditingNoteId] = useState<number | null>(null)
  const [editContent, setEditContent] = useState('')
  const [editCourse, setEditCourse] = useState('')
  const [editPage, setEditPage] = useState<string>('')
  const [updatingNote, setUpdatingNote] = useState(false)

  // Conflict resolution state
  const [conflictDialogOpen, setConflictDialogOpen] = useState(false)
  const [conflictLocalNote, setConflictLocalNote] = useState<any>(null)
  const [conflictServerNote, setConflictServerNote] = useState<any>(null)

  // Register background sync on mount
  useEffect(() => {
    if (isOpen) {
      registerBackgroundSync()
      // Fresh reachability probe so the badge reflects reality right away.
      void isServerReachable()
    }
  }, [isOpen])

  // Server reachability (Tailscale on/off — navigator.onLine is not enough:
  // the phone can have internet yet be unable to reach this server).
  const [serverUp, setServerUp] = useState<boolean | null>(null)
  useEffect(() => subscribeServerStatus(setServerUp), [])

  // Load PDF document using PDF.js (with offline cache fallback)
  useEffect(() => {
    if (!isOpen || !study) return

    let isCancelled = false
    let loadingTask: ReturnType<typeof pdfjsLib.getDocument> | null = null
    setLoadingPdf(true)
    setPdfProgress(null)
    setPdfError(null)
    setPdfDoc(null)
    setNumPages(0)

    const targetPage = sourceToOpen?.page_verified && sourceToOpen.page_number
      ? sourceToOpen.page_number
      : 1
    setCurrentPage(targetPage)
    setPageNumber(String(targetPage))

    // Use blob URL if cached offline, otherwise fetch from server
    const pdfUrl = blobUrl || api.studies.pdfUrl(study.id)
    const isOfflineUrl = !!blobUrl

    try {
      loadingTask = pdfjsLib.getDocument({
        url: pdfUrl,
        httpHeaders: isOfflineUrl ? {} : api.studies.pdfHeaders(),
        withCredentials: !isOfflineUrl,
      })
    } catch (err: any) {
      if (!isCancelled) {
        setPdfError(err?.message || 'Nu s-a putut reda PDF-ul în aplicație.')
        setLoadingPdf(false)
      }
      return
    }

    loadingTask.onProgress = ({ loaded, total }: { loaded: number; total: number }) => {
      if (!isCancelled && total > 0) {
        setPdfProgress(Math.min(100, Math.max(0, Math.round((loaded / total) * 100))))
      }
    }

    loadingTask.promise
      .then((doc: any) => {
        if (!isCancelled) {
          setPdfDoc(doc)
          setNumPages(doc.numPages)
          setPdfProgress(null)
          setLoadingPdf(false)
        }
      })
      .catch((err: any) => {
        if (!isCancelled) {
          console.error('PDF.js loading error:', err)
          setPdfError(err?.message || 'Nu s-a putut reda PDF-ul în aplicație.')
          setLoadingPdf(false)
        }
      })

    return () => {
      isCancelled = true
      try {
        void loadingTask?.destroy().catch(() => {})
      } catch {}
    }
  }, [isOpen, study?.id, sourceToOpen?.document_id, pdfLoadAttempt, blobUrl])

  // Render current page onto canvas
  useEffect(() => {
    if (!pdfDoc || !canvasRef.current || currentPage < 1 || currentPage > numPages) return

    let cancelled = false
    const canvas = canvasRef.current
    const context = canvas.getContext('2d')
    if (!context) return

    if (renderTaskRef.current) {
      try { renderTaskRef.current.cancel() } catch {}
    }

    pdfDoc.getPage(currentPage).then((page: any) => {
      if (cancelled) return

      const containerWidth = canvas.parentElement?.clientWidth || window.innerWidth
      const isMobile = window.innerWidth < 640
      const unscaledViewport = page.getViewport({ scale: 1.0 })
      const responsiveScale = isMobile
        ? Math.min(scale, Math.max(0.6, (containerWidth - 32) / unscaledViewport.width))
        : scale

      const viewport = page.getViewport({ scale: responsiveScale })
      const outputScale = window.devicePixelRatio || 1
      canvas.width = Math.floor(viewport.width * outputScale)
      canvas.height = Math.floor(viewport.height * outputScale)
      canvas.style.width = Math.floor(viewport.width) + 'px'
      canvas.style.height = Math.floor(viewport.height) + 'px'

      const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined

      const renderContext = { canvasContext: context, transform, viewport }
      const task = page.render(renderContext)
      renderTaskRef.current = task
      task.promise.then(async () => {
        const container = textLayerRef.current
        if (!container) return
        // Clear stale marks as soon as a new page starts rendering.
        highlightLayerRef.current?.replaceChildren()
        try {
          container.replaceChildren()
          const textContent = await page.getTextContent()
          container.style.setProperty('--total-scale-factor', String(responsiveScale))
          const tl = new (pdfjsLib as any).TextLayer({
            textContentSource: textContent,
            container,
            viewport,
          })
          await tl.render()
          drawHighlights()
          // Text-layer spans can settle a frame late — retry briefly until they exist.
          let frames = 0
          const retry = () => {
            if (cancelled) return
            frames += 1
            drawHighlights()
            const spanCount = textLayerRef.current?.querySelectorAll('span').length ?? 0
            if (spanCount === 0 && frames < 10) requestAnimationFrame(retry)
          }
          requestAnimationFrame(retry)
        } catch (err: any) {
          if (err?.name !== 'RenderingCancelledException') {
            console.warn('Text layer error:', err)
          }
        }
      }).catch((err: any) => {
        if (err?.name !== 'RenderingCancelledException') {
          console.warn('Page rendering error:', err)
        }
      })
    })

    return () => {
      cancelled = true
      if (renderTaskRef.current) {
        try { renderTaskRef.current.cancel() } catch {}
      }
    }
  }, [pdfDoc, currentPage, scale, numPages, drawHighlights])

  // Repaint marks whenever highlight data / page / zoom / document changes.
  useEffect(() => {
    if (!pdfDoc) {
      highlightLayerRef.current?.replaceChildren()
      return
    }
    drawHighlights()
  }, [highlights, currentPage, scale, pdfDoc, drawHighlights])

  // Load notes and highlights when modal opens; set the reading context so
  // chat knows the source and page while the user reads.
  useEffect(() => {
    if (isOpen && study) {
      refreshNotes()
      refreshHighlights()
      setNoteContent('')
      setEditingNoteId(null)
      setReadingContext(study.id, currentPage)
    }
    return () => {
      // Modal unmounting / closing: clear the reading context.
      setReadingContext(null, null)
    }
  }, [isOpen, study?.id, refreshNotes, refreshHighlights, currentPage])

  if (!isOpen || !study) return null

  const handlePageChange = (newPage: number) => {
    if (newPage >= 1 && newPage <= numPages) {
      setCurrentPage(newPage)
      setPageNumber(String(newPage))
      if (study) setReadingContext(study.id, newPage)
    }
  }

  const saveSelectionHighlight = async () => {
    if (!study || !selection) return
    setSavingHighlight(true)
    try {
      await createHighlightOffline({
        quote: selection.text.slice(0, 4000),
        note: '',
        pageNumber: currentPage,
        documentId: null,
      })
      await refreshHighlights()
      toast.success('Evidențiere salvată local — se sincronizează la conectare.')
      window.getSelection()?.removeAllRanges()
      setSelection(null)
    } catch (err: any) {
      toast.error('Evidențierea nu s-a putut salva: ' + (err?.message || 'eroare'))
    } finally {
      setSavingHighlight(false)
    }
  }

  const deleteHighlight = async (id: number) => {
    try {
      await removeHighlightOffline(id)
      await refreshHighlights()
      toast.success('Evidențiere ștearsă.')
    } catch (err: any) {
      toast.error('Ștergerea a eșuat: ' + (err?.message || 'eroare'))
    }
  }

  const extractConceptFromSelection = async () => {
    if (!study || !selection) return
    setExtractingConcept(true)
    try {
      const res = await api.aiConcepts.extract({
        quote: selection.text.slice(0, 4000),
        text_selection: selection.text.slice(0, 4000),
        entry_id: study.id,
        entry_ids: [study.id],
        page_number: currentPage,
      })
      setExtractedConcepts(res.concepts || [])
      setExtractedRelations(res.relations || [])
      if ((res.concepts || []).length === 0) {
        toast.info('AI-ul nu a propus niciun concept nou din acest pasaj.')
      } else {
        toast.success(`Concepte salvate: ${res.concepts.length} (vezi mai jos).`)
      }
    } catch (err: any) {
      toast.error('Extragerea conceptelor a eșuat: ' + (err?.message || 'eroare'))
    } finally {
      setExtractingConcept(false)
    }
  }

  // Capture text selections over the rendered page
  const handleViewerMouseUp = () => {
    const sel = window.getSelection()
    const text = sel?.toString().trim() ?? ''
    if (!sel || sel.isCollapsed || text.length < 3) {
      setSelection(null)
      return
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect()
    setSelection({ text, x: rect.left + rect.width / 2, y: rect.top })
  }

  const handleCreateNote = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!noteContent.trim()) {
      toast.info('Introduceți textul notiței.')
      return
    }

    setSavingNote(true)
    try {
      const pageNum = pageNumber.trim() ? parseInt(pageNumber.trim(), 10) : currentPage
      await createNoteOffline({
        content: noteContent.trim(),
        courseContext: courseContext.trim(),
        pageNumber: isNaN(pageNum as any) ? null : pageNum,
        sourceDocumentIds: [],
      })
      toast.success('Notița salvată local — se sincronizează la conectare!')
      setNoteContent('')
      await refreshNotes()
      onNotesUpdated?.()
    } catch (err: any) {
      toast.error('Salvarea notiței a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setSavingNote(false)
    }
  }

  // TASK 4: verify the current note text against the page and keep it in a dossier.
  const handleCaptureNote = async () => {
    if (!study || !noteContent.trim()) {
      toast.info('Introduceți textul notiței înainte de verificare.')
      return
    }
    setCapturing(true)
    setCaptureResult(null)
    try {
      const pageNum = pageNumber.trim() ? parseInt(pageNumber.trim(), 10) : currentPage
      const dossier = await api.noteDossiers.capture({
        entry_id: study.id,
        raw_note: noteContent.trim(),
        page: isNaN(pageNum as any) ? null : pageNum,
      })
      setCaptureResult(dossier)
      toast.success(
        `Dosar creat: ${dossier.summary.verificat}/${dossier.summary.total} afirmații verificate.`,
      )
      // Keep the note text so the user can re-capture or save it normally.
    } catch (err: any) {
      toast.error('Verificarea notiței a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setCapturing(false)
    }
  }

  const handleDeleteNote = async (noteId: number) => {
    if (!window.confirm('Sigur doriți să ștergeți această notiță?')) return
    try {
      await removeNoteOffline(noteId)
      toast.success('Notița ștearsă.')
      await refreshNotes()
      onNotesUpdated?.()
    } catch (err: any) {
      toast.error('Ștergerea a eșuat: ' + (err?.message || 'eroare'))
    }
  }

  const startEditing = (note: any) => {
    setEditingNoteId(note.id)
    setEditContent(note.content)
    setEditCourse(note.courseContext || '')
    setEditPage(note.pageNumber ? String(note.pageNumber) : '')
  }

  const handleUpdateNote = async (noteId: number) => {
    if (!editContent.trim()) return
    setUpdatingNote(true)
    try {
      const p = editPage.trim() ? parseInt(editPage.trim(), 10) : null
      await updateNoteOffline(noteId, {
        content: editContent.trim(),
        courseContext: editCourse.trim(),
        pageNumber: isNaN(p as any) ? null : p,
      })
      toast.success('Notița actualizată local.')
      setEditingNoteId(null)
      await refreshNotes()
      onNotesUpdated?.()
    } catch (err: any) {
      toast.error('Actualizarea a eșuat: ' + (err?.message || 'eroare'))
    } finally {
      setUpdatingNote(false)
    }
  }

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-1 sm:p-4 bg-black/80 backdrop-blur-sm"
        role="dialog"
        aria-modal="true"
      >
        <div className="bg-white dark:bg-gray-900 rounded-xl sm:rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-800 w-full max-w-7xl h-[92dvh] sm:h-[94vh] max-h-[96dvh] flex flex-col overflow-hidden relative animate-in fade-in zoom-in-95 duration-150">
        {/* Slim floating top bar overlaying the PDF */}
        <div className="absolute top-0 left-0 right-0 z-20 px-3 sm:px-4 py-2 shrink-0 flex items-center justify-between gap-2 bg-gray-50/60 dark:bg-gray-900/60 backdrop-blur-sm border-b border-gray-200/50 dark:border-gray-800/50">
          <div className="flex items-center gap-2 min-w-0">
            <div className="p-1.5 bg-chrono-100 dark:bg-chrono-950/80 text-chrono-600 rounded-lg shrink-0">
              <FileText className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-bold text-gray-900 dark:text-gray-100 truncate leading-tight">
                {study.title}
              </h2>
              <p className="text-[11px] text-gray-500 truncate leading-none">
                {study.authors && `✍️ ${study.authors}`}
                {study.entry_date && ` · 📅 ${study.entry_date.slice(0, 4)}`}
                {study.doi && ` · DOI: ${study.doi}`}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            {/* Server connectivity badge (Tailscale-aware) */}
            <span
              className={`flex items-center gap-1 px-1.5 py-1 rounded-full text-[10px] font-medium ${
                serverUp === false
                  ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400'
                  : serverUp === true
                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400'
                    : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
              }`}
              title={serverUp === false
                ? 'Serverul nu răspunde (ex. Tailscale oprit). Notele și evidențierile se salvează local și se sincronizează automat la reconectare.'
                : 'Serverul răspunde — salvările merg și pe server.'}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${serverUp === false ? 'bg-amber-500' : serverUp === true ? 'bg-emerald-500' : 'bg-gray-400'}`} />
              {serverUp === false ? 'Offline' : serverUp === true ? 'Conectat' : 'Se verifică…'}
            </span>
            {cached && (
              <span className="flex items-center gap-1 px-1.5 py-1 rounded-full text-[10px] font-medium bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" title="PDF-ul este salvat în cache local — se deschide și fără server">
                PDF în cache
              </span>
            )}
            {/* Sync status */}
            {(loadingNotes || loadingHighlights) && (
              <span className="flex items-center gap-1 px-1.5 py-1 rounded-full text-[10px] bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
                <Loader2 className="w-3 h-3 animate-spin" />
                Sincronizare…
              </span>
            )}
            {/* Notes error indicator */}
            {(notesError || highlightsError) && (
              <span className="flex items-center gap-1 px-1.5 py-1 rounded-full text-[10px] bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400">
                <AlertTriangle className="w-3 h-3" />
                Eroare sincronizare
              </span>
            )}
            <button
              onClick={onClose}
              className="p-2 rounded-lg text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors min-w-[38px] min-h-[38px] flex items-center justify-center"
              title="Închide fereastra"
              aria-label="Închide fereastra"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* PDF panel — înglobează întregul modal; notițele se suprapun */}
        <div className="flex-1 h-full flex flex-col overflow-hidden bg-gray-100 dark:bg-gray-950/70 relative">
            {/* Bara de navigație compactă — jos, one-hand reachable (paginare + zoom) */}
            <div className="absolute bottom-0 left-0 right-0 z-30 px-3 sm:px-4 py-2 sm:py-2.5 bg-white/85 dark:bg-gray-900/85 backdrop-blur-md border-t border-gray-200/50 dark:border-gray-800/50">
              <div className="max-w-lg mx-auto flex flex-col sm:flex-row gap-2 items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => handlePageChange(currentPage - 1)}
                    disabled={currentPage <= 1 || loadingPdf}
                    className="btn-secondary p-1.5 text-xs sm:px-2 inline-flex items-center gap-1 disabled:opacity-40 min-w-[38px] min-h-[38px]"
                    title="Pagina anterioară"
                  >
                    <ChevronLeft className="w-4 h-4" />
                    <span className="hidden sm:inline">Anterior</span>
                  </button>
                  <div className="flex items-center gap-1 text-xs text-gray-700 dark:text-gray-300 font-medium px-1">
                    <span>Pagina</span>
                    <input
                      type="number"
                      min={1}
                      max={numPages || 1}
                      value={currentPage}
                      onChange={e => {
                        const val = parseInt(e.target.value, 10)
                        if (!isNaN(val)) handlePageChange(val)
                      }}
                      className="input w-12 text-center text-xs py-0.5 px-1 font-bold"
                    />
                    <span>din {numPages || '…'}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => handlePageChange(currentPage + 1)}
                    disabled={currentPage >= numPages || loadingPdf}
                    className="btn-secondary p-1.5 text-xs sm:px-2 inline-flex items-center gap-1 disabled:opacity-40 min-w-[38px] min-h-[38px]"
                    title="Pagina următoare"
                  >
                    <span className="hidden sm:inline">Următor</span>
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
                <div className="flex items-center gap-1.5 sm:gap-3">
                  <button
                    type="button"
                    onClick={() => setScale(s => Math.max(0.6, Number((s - 0.2).toFixed(1))))}
                    className="btn-ghost p-1.5 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded min-w-[38px] min-h-[38px]"
                    title="Micșorează (Zoom Out)"
                  >
                    <ZoomOut className="w-4 h-4" />
                  </button>
                  <span className="text-[11px] text-gray-500 font-mono w-10 text-center">
                    {Math.round(scale * 100)}%
                  </span>
                  <button
                    type="button"
                    onClick={() => setScale(s => Math.min(2.5, Number((s + 0.2).toFixed(1))))}
                    className="btn-ghost p-1.5 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded min-w-[38px] min-h-[38px]"
                    title="Mărește (Zoom In)"
                  >
                    <ZoomIn className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>

            {/* Canvas Scroll Viewport */}
            <div
              className="flex-1 overflow-auto px-2 pt-16 pb-32 sm:px-4 sm:pt-16 sm:pb-24 flex items-start justify-center relative select-none"
              onMouseUp={handleViewerMouseUp}
            >
              {loadingPdf && (
                <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/70 dark:bg-gray-900/70 z-10 gap-2">
                  <Loader2 className="w-8 h-8 animate-spin text-chrono-600" />
                  {pdfProgress == null ? (
                    <p className="text-xs text-gray-600 dark:text-gray-300 font-medium">
                      Se încarcă documentul PDF în aplicație…
                    </p>
                  ) : (
                    <>
                      <p className="text-xs text-gray-600 dark:text-gray-300 font-medium">
                        Se descarcă PDF-ul… {pdfProgress}%
                      </p>
                      <div
                        role="progressbar"
                        aria-label={`PDF descărcat ${pdfProgress}%`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={pdfProgress}
                        className="w-48 h-2 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700"
                      >
                        <div
                          className="h-full bg-chrono-600 transition-[width]"
                          style={{ width: `${pdfProgress}%` }}
                        />
                      </div>
                    </>
                  )}
                </div>
              )}

              {pdfError ? (
                <div className="m-auto max-w-md p-6 text-center card bg-madder-soft border-madder/25 space-y-4">
                  <div>
                    <p className="text-sm font-semibold text-madder mb-2">
                      Eroare la redarea documentului:
                    </p>
                    <p className="text-xs text-madder mb-4">{pdfError}</p>
                  </div>
                  <div className="flex flex-col sm:flex-row items-center justify-center gap-2">
                    <button
                      type="button"
                      onClick={() => setPdfLoadAttempt(attempt => attempt + 1)}
                      className="btn-secondary text-xs"
                    >
                      Reîncearcă
                    </button>
                    {/* A stale precache (old app version → old worker file) is
                        the most common cause of this error; a full reload picks
                        up the matching assets. */}
                    <button
                      type="button"
                      onClick={() => window.location.reload()}
                      className="btn-secondary text-xs"
                    >
                      Reîncarcă aplicația
                    </button>

                    {(() => {
                      const doi = getStudyDoi(study)
                      const browserUrl = doi
                        ? `https://sci-hub.se/${encodeURIComponent(doi)}`
                        : (study?.source && study.source.startsWith('http') ? study.source : null)
                      if (!browserUrl) return null
                      return (
                        <a
                          href={browserUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="btn-secondary text-xs inline-flex items-center gap-1 text-chrono-600 dark:text-chrono-400"
                          title={doi ? `Deschide pe Sci-Hub (${doi})` : 'Deschide în browser'}
                        >
                          🌐 Deschide în browser (Sci-Hub)
                        </a>
                      )
                    })()}

                    <label className="btn-primary text-xs cursor-pointer inline-flex items-center gap-1">
                      {uploadingManualPdf ? (
                        <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Se încarcă…</>
                      ) : (
                        <>📤 Încarcă PDF descărcat</>
                      )}
                      <input
                        type="file"
                        accept=".pdf,application/pdf"
                        className="hidden"
                        disabled={uploadingManualPdf}
                        onChange={async e => {
                          const file = e.target.files?.[0]
                          if (!file || !study) return
                          setUploadingManualPdf(true)
                          try {
                            await api.entries.uploadPdf(study.id, file)
                            toast.success('PDF încărcat și indexat cu succes!')
                            setPdfError(null)
                            setPdfLoadAttempt(attempt => attempt + 1)
                            onNotesUpdated?.()
                          } catch (err: unknown) {
                            const msg = err instanceof Error ? err.message : 'Încărcarea PDF-ului a eșuat'
                            toast.error(msg)
                          } finally {
                            setUploadingManualPdf(false)
                            e.target.value = ''
                          }
                        }}
                      />
                    </label>
                  </div>
                </div>
              ) : (
                <div className="relative rounded-xl overflow-hidden shadow-lg border border-gray-300 dark:border-gray-800 bg-white transition-all">
                  <canvas ref={canvasRef} className="block max-w-full h-auto" />
                  <div ref={textLayerRef} className="textLayer" />
                  <div ref={highlightLayerRef} className="absolute inset-0 pointer-events-none" style={{ zIndex: 2 }} />
                </div>
              )}
            </div>
            {/* Selection → highlight / concept: buttons float near the selection */}
            {selection && (
              <div
                className="fixed z-50 -translate-x-1/2 -translate-y-full flex flex-col gap-1.5 p-2 rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-xl max-w-[320px]"
                style={{ left: selection.x, top: selection.y - 8 }}
              >
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={saveSelectionHighlight}
                    disabled={savingHighlight}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-yellow-400 hover:bg-yellow-300 text-yellow-950 text-xs font-semibold shadow-sm disabled:opacity-50"
                    title="Salvează selecția ca evidențiere"
                  >
                    {savingHighlight ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Highlighter className="w-3.5 h-3.5" />}
                    Evidențiază
                  </button>
                  <button
                    onClick={extractConceptFromSelection}
                    disabled={extractingConcept}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-sm disabled:opacity-50"
                    title="Extrage concepte / factori AI din pasajul selectat și salvează-i"
                  >
                    {extractingConcept ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Brain className="w-3.5 h-3.5" />}
                    🧠 Concept / Factor
                  </button>
                  <button
                    onClick={() => { setSelection(null) }}
                    className="px-1.5 py-1.5 text-xs text-gray-400 hover:text-gray-600"
                    title="Închide"
                    aria-label="Închide selecția"
                  >
                    ✕
                  </button>
                </div>
                <p className="text-[11px] text-gray-500 dark:text-gray-400 line-clamp-2 px-0.5">
                  „{selection.text.slice(0, 140)}{selection.text.length > 140 ? '…' : ''}”
                </p>
                {extractedConcepts.length > 0 && (
                  <div className="rounded-lg bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800 p-2 space-y-1">
                    <p className="text-[11px] font-semibold text-indigo-800 dark:text-indigo-200">
                      Concepte propuse de AI (salvate):
                    </p>
                    {extractedConcepts.map(c => (
                      <p key={c.id} className="text-[11px] text-indigo-900 dark:text-indigo-100">
                        <span className="font-mono font-semibold">{c.name}</span>
                        {' '}· {c.concept_type}
                        {c.description ? ` — ${c.description.slice(0, 120)}` : ''}
                      </p>
                    ))}
                    {extractedRelations.length > 0 && (
                      <p className="text-[11px] text-indigo-800 dark:text-indigo-200">
                        Relații: {extractedRelations.map((r: any) =>
                          typeof r === 'string' ? r : [r.from || r.source, r.type || r.relation, r.to || r.target].filter(Boolean).join(' → ')
                        ).join('; ').slice(0, 200)}
                      </p>
                    )}
                    <p className="text-[10px] text-indigo-600 dark:text-indigo-300">
                      ex: Factor: mediu_social · Subfactor: familia · Relație: part_of
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>  {/* close PDF panel wrapper */}

        {/* Floating compose button — "+ Adaugă notă" */}
        <button
          onClick={() => setNoteCardOpen(true)}
          className="fixed right-3 bottom-[104px] z-40 flex items-center justify-center gap-1.5 p-3 rounded-full bg-chrono-600 text-white text-xs font-semibold shadow-xl hover:bg-chrono-700 transition-colors min-w-[48px] min-h-[48px] sm:px-3 sm:py-2 sm:right-6"
          title="Adaugă o notiță nouă"
          aria-label="Adaugă o notiță nouă"
        >
          <Plus className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Adaugă notă</span>
        </button>

        {/* Card de notă compact — zona de jos, max ~45dvh, scrollabil */}
        {noteCardOpen && (
          <div className="fixed left-4 right-4 bottom-[80px] z-50 max-w-md mx-auto rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-2xl overflow-hidden flex flex-col max-h-[45dvh]">
            <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between shrink-0 bg-gray-50/50 dark:bg-gray-900/50">
              <h3 className="font-semibold text-sm text-gray-900 dark:text-gray-100">Notiță nouă</h3>
              <button
                onClick={() => setNoteCardOpen(false)}
                className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded transition-colors min-w-[38px] min-h-[38px]"
                aria-label="Închide cardul de notă"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-4 overflow-y-auto flex-1">
              <form onSubmit={handleCreateNote} className="space-y-3">
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2">
                    <label className="block text-[10px] uppercase font-semibold text-gray-500 mb-0.5">Curs / Context</label>
                    <input type="text" value={courseContext} onChange={e => setCourseContext(e.target.value)} placeholder="ex. Cursul de Neuroștiințe" className="input w-full text-xs py-1.5 px-2.5" />
                  </div>
                  <div>
                    <label className="block text-[10px] uppercase font-semibold text-gray-500 mb-0.5">Pagina PDF</label>
                    <input type="number" value={pageNumber} onChange={e => setPageNumber(e.target.value)} placeholder="ex. 4" min={1} max={numPages || 999} className="input w-full text-xs py-1.5 px-2.5" />
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] uppercase font-semibold text-gray-500 mb-0.5">Notiță / Observație / Citat cheie *</label>
                  <textarea value={noteContent} onChange={e => setNoteContent(e.target.value)} placeholder="Notează ce este important..." rows={3} required className="input w-full text-xs leading-relaxed p-2" />
                </div>
                <div className="flex justify-end gap-2">
                  <button type="submit" disabled={savingNote || !noteContent.trim()} className="btn-primary text-xs py-1.5 px-3 flex items-center gap-1.5 shadow-sm">
                    {savingNote ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                    Salvează notița
                  </button>
                  <button
                    type="button"
                    onClick={handleCaptureNote}
                    disabled={capturing || !noteContent.trim()}
                    className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5"
                  >
                    {capturing ? <Loader2 className="w-3 h-3 animate-spin" /> : <TextQuote className="w-3.5 h-3.5" />}
                    Verifică și păstrează în dosar
                  </button>
                </div>
                {captureResult && (
                  <div className="mt-3 p-3 rounded-lg border border-accent-soft bg-accent-soft/30 space-y-2">
                    <div className="flex items-center gap-1">
                      <FileText className="w-3 h-3 text-accent-strong shrink-0" />
                      <span className="text-xs font-semibold text-accent-strong">Dosar creat</span>
                    </div>
                    <p className="text-[11px] text-muted">
                      {captureResult.summary.verificat}/{captureResult.summary.total} afirmații verificate pe pagina {captureResult.dossier.entry_title ? '' : ''}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {captureResult.claims.slice(0, 6).map(claim => (
                        <div key={claim.id} className="flex items-center gap-1">
                          <span className="font-mono text-[10px] font-semibold text-ink">{claim.code}</span>
                          <span className={`text-[10px] font-semibold rounded-md px-2 py-0.5 ${CAPTURE_STATUS_COLOR[claim.status] ?? CAPTURE_STATUS_COLOR.neverificat}`}>
                            {claim.status}
                          </span>
                          {claim.page_printed && (
                            <span className="text-[10px] text-muted">p. {claim.page_printed}</span>
                          )}
                        </div>
                      ))}
                      {captureResult.claims.length > 6 && (
                        <span className="text-[10px] text-muted">+{captureResult.claims.length - 6} alte…</span>
                      )}
                    </div>
                    <p className="text-[11px] text-muted">
                      Deschide dosarul din Proiect → 🗒️ Notițe pentru a revizui toate afirmațiile.
                    </p>
                  </div>
                )}
              </form>
            </div>
          </div>
        )}

        {/* Chip "Notițe" + panou suprapus */}
        <div className="fixed bottom-4 right-4 z-40 flex flex-col items-end gap-2">
          <button
            onClick={() => setNotesPanelOpen(true)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-xs font-medium text-gray-700 dark:text-gray-300 shadow-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors min-w-[38px] min-h-[38px]"
            title="Notițe"
            aria-label="Notițe"
          >
            <BookOpen className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Notițe</span>
            <span className="px-1.5 py-0.5 rounded-full bg-chrono-100 text-chrono-700 dark:bg-chrono-900 dark:text-chrono-300 text-[10px] font-semibold">{notes.length}</span>
          </button>
        </div>

        {/* Panou de notițe suprapus (desktop dreapta ~340px, mobile tot pantalla) */}
        {notesPanelOpen && (
          <div className="fixed inset-0 z-50">
            <button onClick={() => setNotesPanelOpen(false)} className="absolute inset-0 bg-black/30 backdrop-blur-sm" aria-label="Închide panoul de notițe" />
            <div className="absolute top-0 right-0 z-51 h-full sm:w-[340px] max-w-[92dvw] bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-800 shadow-2xl flex flex-col">
              {/* Antetul panoului de notițe */}
            {sourceToOpen && (
              <div className="m-3 sm:mx-4 sm:mt-4 sm:mb-0 p-3 rounded-lg border border-chrono-200 dark:border-chrono-900/60 bg-chrono-50/50 dark:bg-chrono-950/30">
                <div className="text-[10px] uppercase tracking-wide font-semibold text-chrono-700 dark:text-chrono-300">
                  Fragment PDF recuperat · #{sourceToOpen.chunk_index}
                  {sourceToOpen.page_verified && sourceToOpen.page_number != null
                    ? ` · pagina ${sourceToOpen.page_number}`
                    : ' · pagina neverificată'}
                </div>
                <p className="text-xs text-gray-700 dark:text-gray-300 mt-1 leading-relaxed">
                  {sourceToOpen.preview}
                </p>
              </div>
            )}
            {/* Notes Pane Header */}
            <div className="p-3 sm:p-4 border-b border-gray-100 dark:border-gray-800 shrink-0 bg-gray-50/50 dark:bg-gray-900/50">
              <div className="flex items-center justify-between mb-1">
                <h3 className="font-bold text-sm text-gray-900 dark:text-gray-100 flex items-center gap-2">
                  <BookOpen className="w-4 h-4 text-chrono-600" />
                  Notițe per PDF
                  <span className="badge text-[11px] bg-chrono-100 text-chrono-700 dark:bg-chrono-950 dark:text-chrono-300">
                    {notes.length}
                  </span>
                  {notes.some(n => n.status === 'pending' || n.status === 'conflict') && (
                    <span className="badge text-[11px] bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">
                      {notes.filter(n => n.status === 'pending' || n.status === 'conflict').length} nesincronizate
                    </span>
                  )}
                </h3>
              </div>
              <p className="text-[11px] text-gray-500 leading-normal flex items-start gap-1.5 pt-1">
                <Sparkles className="w-3.5 h-3.5 text-brass shrink-0 mt-0.5" />
                <span>
                  Modelul AI are acces direct la aceste notițe în chat și le corelează cu cursul și cu studiul.
                  Funcționează offline — se sincronizează automat la reconectare.
                </span>
              </p>
            </div>

            {/* Notes List & Add Form */}
            <div className="flex-1 overflow-y-auto p-3 sm:p-4 space-y-4">

              {/* Existing Notes List */}
              <div className="space-y-2.5">
                <h4 className="text-[11px] uppercase tracking-wider font-bold text-gray-400">
                  Notițe salvate ({notes.length})
                </h4>

                {loadingNotes ? (
                  <div className="text-center py-6 text-xs text-gray-400 flex items-center justify-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin text-chrono-600" />
                    Se încarcă notițele…
                  </div>
                ) : notes.length === 0 ? (
                  <div className="text-center py-8 text-xs text-gray-400 border border-dashed border-gray-200 dark:border-gray-800 rounded-xl p-4">
                    Nu ai adăugat încă nicio notiță pentru acest studiu. Scrie prima ta observație mai sus!
                  </div>
                ) : (
                  notes.map(note => {
                    const isEditing = editingNoteId === note.id
                    const isPending = note.dirty || note.status === 'pending'
                    const isConflict = note.status === 'conflict'
                    const isError = note.status === 'error'

                    return (
                      <div
                        key={note.id}
                        className={`p-3 rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-sm space-y-2 transition-all ${
                          isPending ? 'ring-1 ring-amber-400' : ''
                        } ${isConflict ? 'ring-1 ring-red-400' : ''} ${isError ? 'ring-1 ring-red-600' : ''}`}
                      >
                        {isEditing ? (
                          <div className="space-y-2">
                            <div className="grid grid-cols-3 gap-2">
                              <input
                                type="text"
                                value={editCourse}
                                onChange={e => setEditCourse(e.target.value)}
                                placeholder="Curs / Context"
                                className="input col-span-2 text-xs py-1 px-2"
                              />
                              <input
                                type="number"
                                value={editPage}
                                onChange={e => setEditPage(e.target.value)}
                                placeholder="Pagina"
                                className="input text-xs py-1 px-2"
                              />
                            </div>
                            <textarea
                              value={editContent}
                              onChange={e => setEditContent(e.target.value)}
                              rows={3}
                              className="input w-full text-xs p-2"
                            />
                            <div className="flex justify-end gap-1.5 pt-1">
                              <button
                                type="button"
                                onClick={() => setEditingNoteId(null)}
                                className="btn-ghost text-xs py-1 px-2"
                              >
                                Anulează
                              </button>
                              <button
                                type="button"
                                onClick={() => handleUpdateNote(note.id)}
                                disabled={updatingNote || !editContent.trim()}
                                className="btn-primary text-xs py-1 px-2.5 flex items-center gap-1"
                              >
                                {updatingNote ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                                Salvează
                              </button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="flex items-center justify-between gap-2 flex-wrap">
                              <div className="flex items-center gap-1.5 flex-wrap">
                                {note.courseContext ? (
                                  <span className="badge text-[10px] badge-verdigris font-semibold flex items-center gap-1">
                                    <GraduationCap className="w-3 h-3" />
                                    {note.courseContext}
                                  </span>
                                ) : (
                                  <span className="badge text-[10px] badge-muted">
                                    General
                                  </span>
                                )}

                                {note.pageNumber && (
                                  <button
                                    type="button"
                                    onClick={() => {
                                      handlePageChange(note.pageNumber!)
                                      
                                    }}
                                    className="badge text-[10px] badge-accent hover:brightness-95 font-medium cursor-pointer transition-colors"
                                    title={`Mergi direct la pagina ${note.pageNumber} în vizualizator`}
                                  >
                                    🔍 Pag. {note.pageNumber}
                                  </button>
                                )}

                                {isPending && (
                                  <span className="badge text-[10px] badge-amber flex items-center gap-1">
                                    <Loader2 className="w-2.5 h-2.5 animate-spin" />
                                    Nesincronizată
                                  </span>
                                )}
                                {isConflict && (
                                  <span className="badge text-[10px] badge-red flex items-center gap-1">
                                    <AlertTriangle className="w-2.5 h-2.5" />
                                    Conflict
                                  </span>
                                )}
                                {isError && (
                                  <span className="badge text-[10px] badge-red">
                                    Eroare sync
                                  </span>
                                )}
                              </div>

                              <div className="flex items-center gap-1">
                                <button
                                  type="button"
                                  onClick={() => startEditing(note)}
                                  className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded"
                                  title="Editează notița"
                                >
                                  <Edit2 className="w-3 h-3" />
                                </button>
                                <button
                                  type="button"
                                  onClick={() => handleDeleteNote(note.id)}
                                  className="p-1 text-muted hover:text-madder rounded"
                                  title="Șterge notița"
                                >
                                  <Trash2 className="w-3 h-3" />
                                </button>
                              </div>
                            </div>

                            <p className="text-xs text-gray-800 dark:text-gray-200 whitespace-pre-wrap leading-relaxed">
                              {note.content}
                            </p>

                            {note.sourceDocumentIds && note.sourceDocumentIds.length > 0 && (
                              <div className="pt-1 space-y-1">
                                {note.sourceDocumentIds.map(docId => (
                                  <button
                                    key={docId}
                                    type="button"
                                    onClick={() => {
                                      
                                    }}
                                    className="block w-full text-left text-[10px] text-chrono-700 dark:text-chrono-300 hover:underline"
                                    title="Deschide fragmentul notat"
                                  >
                                    ↗ Fragment #local · {docId}
                                  </button>
                                ))}
                              </div>
                            )}

                            <div className="flex items-center gap-1 text-[10px] text-gray-400 pt-0.5">
                              <Calendar className="w-3 h-3" />
                              <span>{note.updatedAt ? note.updatedAt.slice(0, 16).replace('T', ' ') : ''}</span>
                              {note.serverId && <span className="text-emerald-500">✓ sync</span>}
                              {note.dirty && <span className="text-amber-500">⏳ pending</span>}
                            </div>
                          </>
                        )}
                      </div>
                    )
                  })
                )}
              </div>

              {/* Highlights saved from the reader — the AI reads these as
                  user-marked evidence, ahead of raw PDF chunks. */}
              <div className="space-y-2.5">
                <h4 className="text-[11px] uppercase tracking-wider font-bold text-gray-400 flex items-center gap-1.5">
                  <Highlighter className="w-3.5 h-3.5 text-yellow-500" />
                  Evidențieri din PDF ({highlights.length})
                  {highlights.some(h => h.dirty) && (
                    <span className="badge text-[11px] bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">
                      {highlights.filter(h => h.dirty).length} nesincronizate
                    </span>
                  )}
                </h4>
                {highlights.length === 0 ? (
                  <div className="text-center py-6 text-xs text-gray-400 border border-dashed border-gray-200 dark:border-gray-800 rounded-xl p-4">
                    Selectează text în PDF și apasă „Evidențiază” — pasajul se salvează cu pagina și ajunge direct în contextul AI.
                    Funcționează offline — se sincronizează la reconectare.
                  </div>
                ) : (
                  highlights.map(hl => {
                    const isPending = hl.dirty
                    return (
                      <div
                        key={hl.id}
                        className={`p-3 rounded-xl border-l-4 border-yellow-400 bg-yellow-50/60 dark:bg-yellow-950/20 border border-gray-200 dark:border-gray-800 shadow-sm space-y-1.5 ${isPending ? 'ring-1 ring-amber-400' : ''}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-xs text-gray-800 dark:text-gray-200 whitespace-pre-wrap leading-relaxed italic">
                            „{hl.quote}”
                          </p>
                          <button
                            type="button"
                            onClick={() => deleteHighlight(hl.id)}
                            className="p-1 text-gray-400 hover:text-madder rounded shrink-0"
                            title="Șterge evidențierea"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                        {hl.note && (
                          <p className="text-[11px] text-gray-600 dark:text-gray-400">— {hl.note}</p>
                        )}
                        <div className="flex items-center gap-2">
                          {hl.pageNumber && (
                            <button
                              type="button"
                              onClick={() => {
                                handlePageChange(hl.pageNumber!)
                                
                              }}
                              className="badge text-[10px] badge-accent hover:brightness-95 font-medium cursor-pointer transition-colors"
                              title={`Mergi la pagina ${hl.pageNumber}`}
                            >
                              Pag. {hl.pageNumber}
                            </button>
                          )}
                          <span className="text-[10px] text-gray-400">
                            citată de AI în chat și la întrebări
                            {isPending && ' · nesincronizată'}
                          </span>
                        </div>
                      </div>
                    )
                  })
                )}
              {/* close Highlights section */}
              </div>
              {/* close Notes List & Add Form */}
            </div>
              {/* close notes panel */}
          </div>
              {/* close notesPanel overlay */}
        </div>
        )}  {/* close notesPanelOpen condition */}
      </div>  {/* close modal container */}
    {/* Conflict Merge Dialog */}
    <ConflictMergeDialog
      isOpen={conflictDialogOpen}
      onClose={() => setConflictDialogOpen(false)}
      localNote={conflictLocalNote}
      serverNote={conflictServerNote}
      onResolve={async (resolution) => {
        // Resolution handled in the dialog
      }}
    />
  </>
  )
}