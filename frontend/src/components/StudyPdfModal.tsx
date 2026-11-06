import { useState, useEffect, useRef } from 'react'
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
} from 'lucide-react'
import * as pdfjsLib from 'pdfjs-dist'
import 'pdfjs-dist/web/pdf_viewer.css'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { Entry, PdfHighlight, StudyNote, StudyQASource } from '../lib/types'

// Configure PDF.js worker
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

export function StudyPdfModal({ study, isOpen, onClose, onNotesUpdated, sourceToOpen }: Props) {
  const toast = useToast()
  const [notes, setNotes] = useState<StudyNote[]>([])
  const [loadingNotes, setLoadingNotes] = useState(false)
  const [mobileTab, setMobileTab] = useState<'pdf' | 'notes'>('pdf')

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
  const textLayerRef = useRef<HTMLDivElement | null>(null)
  const renderTaskRef = useRef<any>(null)

  // Highlights state — selections saved from the native reader. The chat and
  // study QA read them as the user's own evidence.
  const [highlights, setHighlights] = useState<PdfHighlight[]>([])
  const [selection, setSelection] = useState<{ text: string; x: number; y: number } | null>(null)
  const [savingHighlight, setSavingHighlight] = useState(false)

  // Form state
  const [courseContext, setCourseContext] = useState('')
  const [pageNumber, setPageNumber] = useState<string>('1')
  const [noteContent, setNoteContent] = useState('')
  const [savingNote, setSavingNote] = useState(false)

  // Editing state
  const [editingNoteId, setEditingNoteId] = useState<number | null>(null)
  const [editContent, setEditContent] = useState('')
  const [editCourse, setEditCourse] = useState('')
  const [editPage, setEditPage] = useState<string>('')
  const [updatingNote, setUpdatingNote] = useState(false)

  const loadNotes = async () => {
    if (!study) return
    setLoadingNotes(true)
    try {
      const data = await api.studies.notes.list(study.id)
      setNotes(data)
    } catch (err: any) {
      toast.error('Nu s-au putut încărca notițele: ' + (err?.message || 'eroare rețea'))
    } finally {
      setLoadingNotes(false)
    }
  }

  const loadHighlights = async () => {
    if (!study) return
    try {
      setHighlights(await api.highlights.list(study.id))
    } catch {
      // Highlights are an enhancement; the reader must work without them.
      setHighlights([])
    }
  }

  // Save the current PDF text selection as a highlight on the open page.
  const saveSelectionHighlight = async () => {
    if (!study || !selection) return
    setSavingHighlight(true)
    try {
      const created = await api.highlights.create(study.id, {
        quote: selection.text.slice(0, 4000),
        page_number: currentPage,
      })
      setHighlights(prev => [...prev, created].sort(
        (a, b) => (a.page_number ?? 0) - (b.page_number ?? 0) || a.id - b.id
      ))
      toast.success('Evidențiere salvată — AI-ul o primește în chat și la întrebări.')
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
      await api.highlights.remove(id)
      setHighlights(prev => prev.filter(h => h.id !== id))
    } catch (err: any) {
      toast.error('Ștergerea a eșuat: ' + (err?.message || 'eroare'))
    }
  }

  // Capture text selections over the rendered page (the PDF.js text layer).
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

  // Load PDF document using PDF.js
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

    const pdfUrl = api.studies.pdfUrl(study.id)
    try {
      loadingTask = pdfjsLib.getDocument({
        url: pdfUrl,
        httpHeaders: api.studies.pdfHeaders(),
        withCredentials: true,
      })
    } catch (err: any) {
      setPdfError(err?.message || 'Nu s-a putut reda PDF-ul în aplicație.')
      setLoadingPdf(false)
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
  }, [isOpen, study?.id, sourceToOpen?.document_id, pdfLoadAttempt])

  // Render current page onto canvas
  useEffect(() => {
    if (!pdfDoc || !canvasRef.current || currentPage < 1 || currentPage > numPages) return

    let cancelled = false
    const canvas = canvasRef.current
    const context = canvas.getContext('2d')
    if (!context) return

    // Cancel any previous in-flight render task
    if (renderTaskRef.current) {
      try {
        renderTaskRef.current.cancel()
      } catch {}
    }

    pdfDoc.getPage(currentPage).then((page: any) => {
      if (cancelled) return

      // Compute responsive viewport
      const containerWidth = canvas.parentElement?.clientWidth || window.innerWidth
      // On mobile screens (< 640px), auto-fit width nicely
      const isMobile = window.innerWidth < 640
      const unscaledViewport = page.getViewport({ scale: 1.0 })
      const responsiveScale = isMobile
        ? Math.min(scale, Math.max(0.6, (containerWidth - 32) / unscaledViewport.width))
        : scale

      const viewport = page.getViewport({ scale: responsiveScale })

      // Support high-DPI displays (retina screens)
      const outputScale = window.devicePixelRatio || 1
      canvas.width = Math.floor(viewport.width * outputScale)
      canvas.height = Math.floor(viewport.height * outputScale)
      canvas.style.width = Math.floor(viewport.width) + 'px'
      canvas.style.height = Math.floor(viewport.height) + 'px'

      const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined

      const renderContext = {
        canvasContext: context,
        transform,
        viewport,
      }

      const task = page.render(renderContext)
      renderTaskRef.current = task
      task.promise.then(async () => {
        // Render the invisible text layer on top of the canvas so passages
        // become selectable (→ highlights the AI reads as user evidence).
        const container = textLayerRef.current
        if (!container) return
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
        try {
          renderTaskRef.current.cancel()
        } catch {}
      }
    }
  }, [pdfDoc, currentPage, scale, numPages])

  useEffect(() => {
    if (isOpen && study) {
      loadNotes()
      loadHighlights()
      setNoteContent('')
      setEditingNoteId(null)
    }
  }, [isOpen, study?.id])

  if (!isOpen || !study) return null

  const handlePageChange = (newPage: number) => {
    if (newPage >= 1 && newPage <= numPages) {
      setCurrentPage(newPage)
      setPageNumber(String(newPage))
    }
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
      await api.studies.notes.create(study.id, {
        content: noteContent.trim(),
        course_context: courseContext.trim(),
        page_number: isNaN(pageNum as any) ? null : pageNum,
      })
      toast.success('Notița a fost salvată și este accesibilă modelului AI!')
      setNoteContent('')
      await loadNotes()
      onNotesUpdated?.()
    } catch (err: any) {
      toast.error('Salvarea notiței a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setSavingNote(false)
    }
  }

  const handleDeleteNote = async (noteId: number) => {
    if (!window.confirm('Sigur doriți să ștergeți această notiță?')) return
    try {
      await api.studies.notes.delete(noteId)
      toast.success('Notița a fost ștearsă.')
      await loadNotes()
      onNotesUpdated?.()
    } catch (err: any) {
      toast.error('Ștergerea a eșuat: ' + (err?.message || 'eroare'))
    }
  }

  const startEditing = (note: StudyNote) => {
    setEditingNoteId(note.id)
    setEditContent(note.content)
    setEditCourse(note.course_context || '')
    setEditPage(note.page_number ? String(note.page_number) : '')
  }

  const handleUpdateNote = async (noteId: number) => {
    if (!editContent.trim()) return
    setUpdatingNote(true)
    try {
      const p = editPage.trim() ? parseInt(editPage.trim(), 10) : null
      await api.studies.notes.update(noteId, {
        content: editContent.trim(),
        course_context: editCourse.trim(),
        page_number: isNaN(p as any) ? null : p,
      })
      toast.success('Notița a fost actualizată.')
      setEditingNoteId(null)
      await loadNotes()
      onNotesUpdated?.()
    } catch (err: any) {
      toast.error('Actualizarea a eșuat: ' + (err?.message || 'eroare'))
    } finally {
      setUpdatingNote(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-1 sm:p-4 bg-black/80 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="bg-white dark:bg-gray-900 rounded-xl sm:rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-800 w-full max-w-7xl h-[92dvh] sm:h-[94vh] max-h-[96dvh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Top Header */}
        <div className="flex items-center justify-between px-3 sm:px-4 py-2.5 sm:py-3 border-b border-gray-200 dark:border-gray-800 bg-gray-50/80 dark:bg-gray-900/80 shrink-0">
          <div className="flex items-center gap-2.5 sm:gap-3 min-w-0">
            <div className="p-1.5 sm:p-2 bg-chrono-100 dark:bg-chrono-950/80 text-chrono-600 rounded-lg sm:rounded-xl shrink-0">
              <FileText className="w-4 h-4 sm:w-5 sm:h-5" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm sm:text-base font-bold text-gray-900 dark:text-gray-100 truncate">
                {study.title}
              </h2>
              <p className="text-[11px] sm:text-xs text-gray-500 truncate">
                {study.authors && `✍️ ${study.authors}`}
                {study.entry_date && ` · 📅 ${study.entry_date.slice(0, 4)}`}
                {study.doi && ` · DOI: ${study.doi}`}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0 ml-2">
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

        {/* Mobile View Switcher (PDF vs Notes) */}
        <div className="flex sm:hidden border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 px-4 pt-1 gap-2 shrink-0">
          <button
            onClick={() => setMobileTab('pdf')}
            className={`flex-1 py-2 text-xs font-semibold border-b-2 text-center transition-colors flex items-center justify-center gap-1.5 ${
              mobileTab === 'pdf'
                ? 'border-chrono-600 text-chrono-600 dark:border-chrono-400 dark:text-chrono-400'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <FileText className="w-3.5 h-3.5" />
            <span>Document PDF</span>
            {numPages > 0 && <span className="text-[10px] opacity-75">({numPages} pag)</span>}
          </button>
          <button
            onClick={() => setMobileTab('notes')}
            className={`flex-1 py-2 text-xs font-semibold border-b-2 text-center transition-colors flex items-center justify-center gap-1.5 ${
              mobileTab === 'notes'
                ? 'border-chrono-600 text-chrono-600 dark:border-chrono-400 dark:text-chrono-400'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <BookOpen className="w-3.5 h-3.5" />
            <span>Notițe de curs</span>
            <span className="px-1.5 py-0.2 bg-chrono-100 dark:bg-chrono-900 text-chrono-700 dark:text-chrono-300 rounded-full text-[10px]">
              {notes.length}
            </span>
          </button>
        </div>

        {/* Split Container */}
        <div className="flex-1 flex flex-col sm:flex-row overflow-hidden min-h-0">
          {/* Left / Main Native Canvas PDF Viewer */}
          <div
            className={`flex-1 h-full flex flex-col bg-gray-100 dark:bg-gray-950/70 overflow-hidden ${
              mobileTab === 'notes' ? 'hidden sm:flex' : 'flex'
            }`}
          >
            {/* Native PDF Controls Toolbar */}
            <div className="p-2 sm:px-4 sm:py-2.5 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between gap-2 flex-wrap shrink-0">
              {/* Pagination Controls */}
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => handlePageChange(currentPage - 1)}
                  disabled={currentPage <= 1 || loadingPdf}
                  className="btn-secondary p-1 text-xs sm:px-2 sm:py-1 inline-flex items-center gap-1 disabled:opacity-40"
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
                  className="btn-secondary p-1 text-xs sm:px-2 sm:py-1 inline-flex items-center gap-1 disabled:opacity-40"
                  title="Pagina următoare"
                >
                  <span className="hidden sm:inline">Următor</span>
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>

              {/* Zoom & View Controls */}
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setScale(s => Math.max(0.6, Number((s - 0.2).toFixed(1))))}
                  className="btn-ghost p-1 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded"
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
                  className="btn-ghost p-1 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded"
                  title="Mărește (Zoom In)"
                >
                  <ZoomIn className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setScale(1.0)}
                  className="btn-ghost p-1 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 rounded hidden sm:inline-flex"
                  title="Dimensiune standard 100%"
                >
                  <Maximize2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* Canvas Scroll Viewport */}
            <div
              className="flex-1 overflow-auto p-2 sm:p-4 flex items-start justify-center relative select-none"
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
                <div className="m-auto max-w-md p-6 text-center card bg-madder-soft border-madder/25">
                  <p className="text-sm font-semibold text-madder mb-2">
                    Eroare la redarea documentului:
                  </p>
                  <p className="text-xs text-madder mb-4">{pdfError}</p>
                  <button
                    type="button"
                    onClick={() => setPdfLoadAttempt(attempt => attempt + 1)}
                    className="btn-secondary text-xs"
                  >
                    Reîncearcă
                  </button>
                </div>
              ) : (
                <div className="relative rounded-xl overflow-hidden shadow-lg border border-gray-300 dark:border-gray-800 bg-white transition-all">
                  <canvas ref={canvasRef} className="block max-w-full h-auto" />
                  <div ref={textLayerRef} className="textLayer" />
                </div>
              )}
            </div>
            {/* Selection → highlight: the button floats near the selection */}
            {selection && (
              <button
                onClick={saveSelectionHighlight}
                disabled={savingHighlight}
                className="fixed z-50 -translate-x-1/2 -translate-y-full flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-yellow-400 hover:bg-yellow-300 text-yellow-950 text-xs font-semibold shadow-lg"
                style={{ left: selection.x, top: selection.y - 8 }}
                title="Salvează selecția ca evidențiere"
              >
                {savingHighlight ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Highlighter className="w-3.5 h-3.5" />}
                Evidențiază
              </button>
            )}
          </div>

          {/* Right Notes Pane */}
          <div
            className={`w-full sm:w-96 lg:w-[420px] h-full flex flex-col border-t sm:border-t-0 sm:border-l border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shrink-0 ${
              mobileTab === 'pdf' ? 'hidden sm:flex' : 'flex'
            }`}
          >
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
                </h3>
              </div>
              <p className="text-[11px] text-gray-500 leading-normal flex items-start gap-1.5 pt-1">
                <Sparkles className="w-3.5 h-3.5 text-brass shrink-0 mt-0.5" />
                <span>
                  Modelul AI are acces direct la aceste notițe în chat și le corelează cu cursul și cu studiul.
                </span>
              </p>
            </div>

            {/* Notes List & Add Form */}
            <div className="flex-1 overflow-y-auto p-3 sm:p-4 space-y-4">
              {/* Add Note Form */}
              <form onSubmit={handleCreateNote} className="card p-3 border-chrono-200 dark:border-chrono-900/60 bg-chrono-50/20 dark:bg-chrono-950/20 space-y-2.5">
                <div className="flex items-center justify-between text-xs font-semibold text-chrono-900 dark:text-chrono-200">
                  <span className="flex items-center gap-1.5">
                    <Plus className="w-3.5 h-3.5" /> Adaugă notiță la Pag. {currentPage}
                  </span>
                </div>

                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2">
                    <label className="block text-[10px] uppercase font-semibold text-gray-500 mb-0.5">
                      Curs / Context
                    </label>
                    <input
                      type="text"
                      value={courseContext}
                      onChange={e => setCourseContext(e.target.value)}
                      placeholder="ex. Cursul de Neuroștiințe"
                      className="input w-full text-xs py-1 px-2"
                    />
                  </div>

                  <div>
                    <label className="block text-[10px] uppercase font-semibold text-gray-500 mb-0.5">
                      Pagina PDF
                    </label>
                    <input
                      type="number"
                      value={pageNumber}
                      onChange={e => setPageNumber(e.target.value)}
                      placeholder="ex. 4"
                      min={1}
                      max={numPages || 999}
                      className="input w-full text-xs py-1 px-2"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-[10px] uppercase font-semibold text-gray-500 mb-0.5">
                    Notiță / Observație / Citat cheie *
                  </label>
                  <textarea
                    value={noteContent}
                    onChange={e => setNoteContent(e.target.value)}
                    placeholder="Notează ce este important, ce mecanism este descris sau ce a subliniat profesorul..."
                    rows={3}
                    required
                    className="input w-full text-xs leading-relaxed p-2"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    type="submit"
                    disabled={savingNote || !noteContent.trim()}
                    className="btn-primary text-xs py-1 px-3 flex items-center gap-1.5 shadow-sm"
                  >
                    {savingNote ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                    Salvează notița
                  </button>
                </div>
              </form>

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

                    return (
                      <div
                        key={note.id}
                        className="p-3 rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-sm space-y-2 transition-all hover:border-gray-300 dark:hover:border-gray-700"
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
                                {note.course_context ? (
                                  <span className="badge text-[10px] badge-verdigris font-semibold flex items-center gap-1">
                                    <GraduationCap className="w-3 h-3" />
                                    {note.course_context}
                                  </span>
                                ) : (
                                  <span className="badge text-[10px] badge-muted">
                                    General
                                  </span>
                                )}

                                 {note.page_number && note.page_verified && (
                                   <button
                                    type="button"
                                    onClick={() => {
                                      handlePageChange(note.page_number!)
                                      if (mobileTab === 'notes') setMobileTab('pdf')
                                    }}
                                    className="badge text-[10px] badge-accent hover:brightness-95 font-medium cursor-pointer transition-colors"
                                    title={`Mergi direct la pagina ${note.page_number} în vizualizator`}
                                  >
                                     🔍 Pag. {note.page_number}
                                   </button>
                                 )}
                                {note.page_number && !note.page_verified && (
                                  <span className="badge text-[10px] badge-muted" title="Pagina a fost notată manual, dar nu este confirmată de fragmentele indexate">
                                    Pagină notată · neconfirmată
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

                            {note.sources && note.sources.length > 0 && (
                              <div className="pt-1 space-y-1">
                                {note.sources.map(source => (
                                  <button
                                    key={source.document_id}
                                    type="button"
                                    onClick={() => {
                                      if (source.page_verified && source.page_number) handlePageChange(source.page_number)
                                      if (mobileTab === 'notes') setMobileTab('pdf')
                                    }}
                                    className="block w-full text-left text-[10px] text-chrono-700 dark:text-chrono-300 hover:underline"
                                    title="Deschide fragmentul notat"
                                  >
                                    ↗ Fragment #{source.chunk_index}
                                    {source.page_verified && source.page_number != null
                                      ? ` · pagina ${source.page_number}`
                                      : ' · pagina neverificată'}
                                    {' — '}{source.preview}
                                  </button>
                                ))}
                              </div>
                            )}

                            <div className="flex items-center gap-1 text-[10px] text-gray-400 pt-0.5">
                              <Calendar className="w-3 h-3" />
                              <span>{note.created_at ? note.created_at.slice(0, 16).replace('T', ' ') : ''}</span>
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
                </h4>
                {highlights.length === 0 ? (
                  <div className="text-center py-6 text-xs text-gray-400 border border-dashed border-gray-200 dark:border-gray-800 rounded-xl p-4">
                    Selectează text în PDF și apasă „Evidențiază” — pasajul se salvează cu pagina și ajunge direct în contextul AI.
                  </div>
                ) : (
                  highlights.map(hl => (
                    <div
                      key={hl.id}
                      className="p-3 rounded-xl border-l-4 border-yellow-400 bg-yellow-50/60 dark:bg-yellow-950/20 border border-gray-200 dark:border-gray-800 shadow-sm space-y-1.5"
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
                        {hl.page_number && (
                          <button
                            type="button"
                            onClick={() => {
                              handlePageChange(hl.page_number!)
                              if (mobileTab === 'notes') setMobileTab('pdf')
                            }}
                            className="badge text-[10px] badge-accent hover:brightness-95 font-medium cursor-pointer transition-colors"
                            title={`Mergi la pagina ${hl.page_number}`}
                          >
                            Pag. {hl.page_number}
                          </button>
                        )}
                        <span className="text-[10px] text-gray-400">
                          citată de AI în chat și la întrebări
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
