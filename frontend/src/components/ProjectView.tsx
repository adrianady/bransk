import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  GraduationCap, Pencil, Check, X, Loader2, Sparkles, BookOpen, BarChart3,
  MessageSquareWarning, Plus, Trash2, ChevronDown, ChevronRight, Save,
  Lightbulb, AlertTriangle, Info, ExternalLink, RefreshCw, ListTree, CheckCircle2, Compass,
  Download, BookMarked, FileDown, ClipboardList, FolderPlus, Brain,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, ApiError } from '../lib/api'
import { useToast } from './Toast'
import { ReadinessPanel } from './ReadinessPanel'
import { IdeasPanel } from './IdeasPanel'
import { BibliographyPanel } from './BibliographyPanel'
import { TitlePanel } from './TitlePanel'
import { TopicPanel } from './TopicPanel'
import { QuestionnairesPanel } from './QuestionnairesPanel'
import { NotesPanel } from './NotesPanel'
import { NoteMap } from './NoteMap'
import type {
  Project, ProjectSection, ProjectStudyRecommendation, ProjectAnalysisResponse,
  ProjectFeedbackResponse, FeedbackItem, ProjectEvidenceMap, StudySource, ProjectSummary,
  NoteProjectMap,
} from '../lib/types'

const ThesisEvidenceMap = lazy(() => import('./ThesisEvidenceMap').then(module => ({ default: module.ThesisEvidenceMap })))

// Wide markdown tables scroll inside their panel on a phone instead of widening
// the page; the table itself stays `display: table`, so desktop alignment is
// untouched. `markdown-table-wrap` lives in index.css.
const markdownComponents = {
  table: ({ node: _node, ...props }: React.ComponentProps<'table'> & { node?: unknown }) => (
    <div className="markdown-table-wrap">
      <table {...props} />
    </div>
  ),
}

interface Props {
  onSelectEntry: (entryId: number) => void
  onOpenSource?: (entryId: number, source: StudySource) => void
  onTitleChange?: (title: string) => void
  focusClaim?: { sectionId: string; claimId: number; token: number } | null
}

type Tab = 'structure' | 'map' | 'studies' | 'questionnaires' | 'readiness' | 'topic' | 'analysis' | 'feedback' | 'ideas' | 'bibliography' | 'notes'

const WORK_TYPES: { value: string; label: string }[] = [
  { value: 'licenta', label: 'Licență' },
  { value: 'disertatie', label: 'Disertație' },
  { value: 'doctorat', label: 'Doctorat' },
  { value: 'referat', label: 'Referat' },
]

/** Human labels for the research-plan fields (keys come from the server). */
const PLAN_LABELS: Record<string, string> = {
  participants: 'Participanți',
  instruments: 'Instrumente',
  hypotheses: 'Ipoteze',
  variables: 'Variabile',
  analysis: 'Analiză',
  ethics: 'Etică',
  schedule: 'Calendar',
  risks: 'Riscuri',
}

const levelStyle: Record<FeedbackItem['level'], { icon: typeof Info; cls: string }> = {
  critical: { icon: AlertTriangle, cls: 'text-madder' },
  warning: { icon: AlertTriangle, cls: 'text-brass' },
  info: { icon: Info, cls: 'text-accent' },
}

const countWords = (text: string) => (text.trim() ? text.trim().split(/\s+/).length : 0)

/**
 * The feedback payload is allowed to change shape: the cheap GET returns only
 * structural items (`reviewer: null`, `needs_generation: true`), and the
 * reviewer's text arrives from the explicit POST. Accept both shapes and never
 * blank the screen because a field is missing.
 */
function normalizeFeedback(raw: any, previous: ProjectFeedbackResponse | null): ProjectFeedbackResponse {
  const items = Array.isArray(raw?.items)
    ? raw.items.filter((item: any) => item && typeof item === 'object')
    : (previous?.items ?? [])
  const reviewer = typeof raw?.reviewer === 'string' && raw.reviewer.trim() ? raw.reviewer : null
  return {
    items,
    reviewer,
    from_llm: typeof raw?.from_llm === 'boolean' ? raw.from_llm : reviewer !== null,
    needs_generation: typeof raw?.needs_generation === 'boolean' ? raw.needs_generation : undefined,
  }
}

/** Say what the server actually said, and name a missing route as such. */
function feedbackErrorMessage(err: any): string {
  if (err instanceof ApiError) {
    if (err.status === 404 || err.status === 405) {
      return `serverul încă nu oferă ruta de review (HTTP ${err.status})`
    }
    return err.message
  }
  return err instanceof Error && err.message ? err.message : 'eroare necunoscută'
}

export function ProjectView({ onSelectEntry, onOpenSource, onTitleChange, focusClaim }: Props) {
  const toast = useToast()
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<Tab>('structure')

  // Title editing
  const [editingTitle, setEditingTitle] = useState(false)
  const [expandedTitle, setExpandedTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const titleRef = useRef<HTMLInputElement>(null)

  // Structure editing
  const [openSection, setOpenSection] = useState<string | null>(null)
  const [dirtySections, setDirtySections] = useState<Record<string, string>>({})
  const [savingSection, setSavingSection] = useState<string | null>(null)
  const [lastSaved, setLastSaved] = useState<Record<string, string>>({})
  const autoSaveTimersRef = useRef<Record<string, number>>({})
  const [generatingStructure, setGeneratingStructure] = useState(false)
  const [exportingDocx, setExportingDocx] = useState(false)
  const [evidenceMap, setEvidenceMap] = useState<ProjectEvidenceMap | null>(null)
  const [mapStudies, setMapStudies] = useState<ProjectStudyRecommendation[]>([])
  const [loadingEvidenceMap, setLoadingEvidenceMap] = useState(false)
  const [synthesizingConcepts, setSynthesizingConcepts] = useState(false)
  // Project map (note dossiers)
  const [projectMap, setProjectMap] = useState<NoteProjectMap | null>(null)
  const [loadingProjectMap, setLoadingProjectMap] = useState(false)
  const [projectMapOpen, setProjectMapOpen] = useState(true)
  // Recommendations / analysis / feedback
  const [studies, setStudies] = useState<ProjectStudyRecommendation[]>([])
  const [libraryTotal, setLibraryTotal] = useState(0)
  const [terms, setTerms] = useState<string[]>([])
  const [loadingStudies, setLoadingStudies] = useState(false)
  const [analysis, setAnalysis] = useState<ProjectAnalysisResponse | null>(null)
  const [loadingAnalysis, setLoadingAnalysis] = useState(false)
  const [feedback, setFeedback] = useState<ProjectFeedbackResponse | null>(null)
  const [loadingFeedback, setLoadingFeedback] = useState(false)
  // Two failures, two messages: the cheap GET can fail on its own, and the
  // reviewer's POST can fail while the structural items stay useful.
  const [feedbackError, setFeedbackError] = useState('')
  const [reviewError, setReviewError] = useState('')
  const [requestingReview, setRequestingReview] = useState(false)

  // Multi-Project Isolation
  const [projectList, setProjectList] = useState<ProjectSummary[]>([])
  const [showProjectModal, setShowProjectModal] = useState(false)
  const [newProjectTitle, setNewProjectTitle] = useState('')
  const [creatingProject, setCreatingProject] = useState(false)

  const loadProjects = useCallback(async () => {
    try {
      const res = await api.projects.list()
      setProjectList(res.projects || [])
    } catch {
      // best-effort
    }
  }, [])

  useEffect(() => {
    loadProjects()
  }, [loadProjects])

  const handleSwitchProject = async (pid: number) => {
    try {
      const res = await api.projects.activate(pid)
      setProject(res.project)
      setTitleDraft(res.project.title || '')
      onTitleChange?.(res.project.title || '')
      toast.success(`Comutat la proiectul: ${res.project.title || 'Fără titlu'}`)
      await loadProjects()
    } catch (err) {
      toast.error('Comutarea proiectului a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
    }
  }

  const handleCreateNewProject = async () => {
    if (!newProjectTitle.trim()) {
      toast.error('Introdu un titlu pentru noul proiect.')
      return
    }
    setCreatingProject(true)
    try {
      const created = await api.projects.create({ title: newProjectTitle.trim() })
      setProject(created)
      setTitleDraft(created.title || '')
      onTitleChange?.(created.title || '')
      setShowProjectModal(false)
      setNewProjectTitle('')
      toast.success('Noul proiect a fost creat și activat izolat!')
      await loadProjects()
    } catch (err) {
      toast.error('Eroare la crearea proiectului: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setCreatingProject(false)
    }
  }
  const loadProject = useCallback(async () => {
    try {
      const p = await api.project.get()
      setProject(p)
      setTitleDraft(p.title)
      onTitleChange?.(p.title || '')
      if (p.structure) {
        const restored: Record<string, string> = {}
        for (const sec of p.structure) {
          try {
            const savedDraft = localStorage.getItem(`chronokg-draft-${sec.id}`)
            if (savedDraft && savedDraft !== sec.content && savedDraft.trim().length > (sec.content || '').trim().length) {
              restored[sec.id] = savedDraft
            }
          } catch {}
        }
        if (Object.keys(restored).length > 0) {
          setDirtySections(prev => ({ ...prev, ...restored }))
          toast.info('Am recuperat ciorna nesalvată a capitolelor din browser.')
        }
      }
    } catch (err: unknown) {
      toast.error('Proiectul nu s-a putut încărca: ' + (err instanceof Error ? err.message : 'necunoscut'))
    } finally {
      setLoading(false)
    }
  }, [toast, onTitleChange])

  useEffect(() => { loadProject() }, [loadProject])

  const loadEvidenceMap = useCallback(async () => {
    setLoadingEvidenceMap(true)
    try {
      const [mapResponse, studyResponse] = await Promise.all([
        api.project.evidenceMap(),
        api.project.studies(50),
      ])
      setEvidenceMap(mapResponse)
      setMapStudies(studyResponse.studies)
    } catch (err: any) {
      toast.error('Harta tezei nu s-a putut încărca: ' + (err?.message || 'necunoscut'))
    } finally {
      setLoadingEvidenceMap(false)
    }
  }, [toast])

  const loadProjectMap = useCallback(async () => {
    setLoadingProjectMap(true)
    try {
      setProjectMap(await api.noteDossiers.projectMap())
    } catch (err: any) {
      toast.error('Harta proiectului nu s-a putut încărca: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setLoadingProjectMap(false)
    }
  }, [toast])

  const regenerateProjectMap = useCallback(async () => {
    const ok = window.confirm(
      'Regenerează harta proiectului folosind doar afirmațiile verificate. ' +
      'Concepte și muchile ve vor fi propuse de nou de către model. Continuă?',
    )
    if (!ok) return
    setLoadingProjectMap(true)
    try {
      setProjectMap(await api.noteDossiers.regenerateProjectMap({ replace: true }))
      toast.success('Harta proiectului a fost regenerată.')
    } catch (err: any) {
      toast.error('Regenerarea hărții proiectului a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setLoadingProjectMap(false)
    }
  }, [toast])

  // Build a concept/factor map from what was actually read: notes and PDF
  // highlights are the evidence the extractor grounds every concept in.
  const synthesizeConcepts = useCallback(async () => {
    if (synthesizingConcepts) return
    setSynthesizingConcepts(true)
    try {
      const { items } = await api.studies.all()
      const read = items.filter(s => ['read', 'annotated'].includes(s.read_status || ''))
      const source = read.length > 0 ? read : items
      if (source.length === 0) {
        toast.info('Nu există studii sau cărți în bibliotecă pentru sinteză.')
        return
      }
      const res = await api.aiConcepts.extract({
        entry_ids: source.slice(0, 30).map(s => s.id),
        project_id: project?.id,
      })
      const nConcepts = res.concepts?.length ?? 0
      const nRelations = res.relations?.length ?? 0
      if (nConcepts === 0) {
        toast.info('AI-ul nu a găsit concepte noi. Adaugă evidențieri sau notițe pe studii.')
      } else {
        toast.success(
          `${nConcepts} concepte și ${nRelations} relații create din ${read.length > 0 ? 'studiile citite' : 'bibliotecă'}. Deschide Graful → „Concepte”.`
        )
      }
    } catch (err: any) {
      toast.error('Sinteza conceptelor a eșuat: ' + (err?.message || 'eroare'))
    } finally {
      setSynthesizingConcepts(false)
    }
  }, [project?.id, synthesizingConcepts, toast])

  const patch = useCallback(async (body: Partial<Project>, quiet = false) => {
    try {
      const updated = await api.project.update(body)
      setProject(updated)
      if (body.structure !== undefined) setEvidenceMap(null)
      setTitleDraft(updated.title)
      onTitleChange?.(updated.title || '')
      if (!quiet) toast.success('Salvat')
      return updated
    } catch (err: any) {
      toast.error('Salvarea a eșuat: ' + (err?.message || 'necunoscut'))
      return null
    }
  }, [toast])

  const loadStudies = useCallback(async () => {
    setLoadingStudies(true)
    try {
      const res = await api.project.studies(12)
      setStudies(res.studies)
      setLibraryTotal(res.total_library)
      setTerms(res.terms)
    } catch (err: any) {
      toast.error('Recomandările nu s-au putut încărca: ' + (err?.message || 'necunoscut'))
    } finally {
      setLoadingStudies(false)
    }
  }, [toast])

  const loadAnalysis = useCallback(async () => {
    setLoadingAnalysis(true)
    try {
      // This reads what is stored; it never costs a model call. Generation is
      // a separate button, so opening the tab stays instant and free.
      setAnalysis(await api.project.analysis())
    } catch (err: any) {
      toast.error('Analiza a eșuat: ' + (err?.message || 'necunoscut'))
    } finally {
      setLoadingAnalysis(false)
    }
  }, [toast])

  const regenerateAnalysis = useCallback(async () => {
    setLoadingAnalysis(true)
    try {
      setAnalysis(await api.project.regenerateAnalysis())
    } catch (err: any) {
      toast.error('Generarea a eșuat: ' + (err?.message || 'necunoscut'))
    } finally {
      setLoadingAnalysis(false)
    }
  }, [toast])

  const loadFeedback = useCallback(async () => {
    setLoadingFeedback(true)
    setFeedbackError('')
    try {
      const raw = await api.project.feedback()
      setFeedback(prev => normalizeFeedback(raw, prev))
    } catch (err: any) {
      const msg = feedbackErrorMessage(err)
      setFeedbackError(msg)
      toast.error('Feedback-ul a eșuat: ' + msg)
    } finally {
      setLoadingFeedback(false)
    }
  }, [toast])

  // One LLM call, 5–39 s, paid only when the user asks. Never fired from the
  // tab effect: opening the tab must stay free.
  const requestReview = useCallback(async () => {
    setRequestingReview(true)
    setReviewError('')
    try {
      const raw = await api.project.feedbackReview()
      setFeedback(prev => normalizeFeedback(raw, prev))
      // Read the reply directly: a state updater runs later, so a flag set
      // inside it could not be trusted here.
      const reviewer = typeof raw?.reviewer === 'string' ? raw.reviewer.trim() : ''
      if (!reviewer) {
        toast.info('Modelul a răspuns, dar fără observații scrise (proiect gol sau mod mock).')
      }
    } catch (err: any) {
      const msg = feedbackErrorMessage(err)
      setReviewError(msg)
      toast.error('Comentariul modelului a eșuat: ' + msg)
    } finally {
      setRequestingReview(false)
    }
  }, [toast])

  // Load the data of a tab the first time it is opened.
  useEffect(() => {
    if (tab === 'studies' && studies.length === 0 && !loadingStudies) loadStudies()
    if (tab === 'map' && !evidenceMap && !loadingEvidenceMap) loadEvidenceMap()
    if (tab === 'analysis' && !analysis && !loadingAnalysis) loadAnalysis()
    if (tab === 'feedback' && !feedback && !loadingFeedback) loadFeedback()
    if (tab === 'notes' && !projectMap && !loadingProjectMap) loadProjectMap()
  }, [tab]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (focusClaim) setTab('map')
  }, [focusClaim?.token])

  const saveTitle = async () => {
    const value = titleDraft.trim()
    setEditingTitle(false)
    if (!project || value === project.title) return
    await patch({ title: value })
    // The topic changed: recommendations and analysis are stale now.
    setStudies([]); setAnalysis(null); setFeedback(null)
  }

  const addSection = async () => {
    if (!project) return
    const next: ProjectSection[] = [
      ...project.structure,
      { id: `s${Date.now()}`, title: 'Capitol nou', target_words: 1000, description: '', content: '' },
    ]
    await patch({ structure: next })
    setOpenSection(next[next.length - 1].id)
  }

  const removeSection = async (id: string) => {
    if (!project) return
    if (!confirm('Ștergi acest capitol?')) return
    await patch({ structure: project.structure.filter(s => s.id !== id) })
  }

  const saveSectionContent = async (section: ProjectSection, silent = false) => {
    if (!project) return
    const draft = dirtySections[section.id]
    if (draft === undefined) return
    setSavingSection(section.id)
    const next = project.structure.map(s => (s.id === section.id ? { ...s, content: draft } : s))
    const updated = await patch({ structure: next }, true)
    setSavingSection(null)
    if (updated) {
      setDirtySections(prev => {
        const copy = { ...prev }
        delete copy[section.id]
        return copy
      })
      try {
        localStorage.removeItem(`chronokg-draft-${section.id}`)
      } catch {}
      const timeStr = new Date().toLocaleTimeString('ro-RO', { hour: '2-digit', minute: '2-digit' })
      setLastSaved(prev => ({ ...prev, [section.id]: timeStr }))
      if (!silent) {
        toast.success(`Salvat: ${section.title}`)
      }
    }
  }

  // Debounced auto-save: 1.5s after user stops typing
  useEffect(() => {
    if (!project) return
    for (const section of project.structure) {
      const draft = dirtySections[section.id]
      if (draft !== undefined && draft !== section.content) {
        try {
          localStorage.setItem(`chronokg-draft-${section.id}`, draft)
        } catch {}

        clearTimeout(autoSaveTimersRef.current[section.id])
        autoSaveTimersRef.current[section.id] = setTimeout(() => {
          saveSectionContent(section, true)
        }, 1500)
      }
    }
    return () => {
      Object.values(autoSaveTimersRef.current).forEach(clearTimeout)
    }
  }, [dirtySections, project])

  // Warn before leaving if there are unsaved chapter edits
  useEffect(() => {
    const hasUnsaved = project?.structure.some(
      s => dirtySections[s.id] !== undefined && dirtySections[s.id] !== s.content
    )
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasUnsaved) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirtySections, project])

  const generateStructure = async () => {
    if (!project) return
    if (project.structure.some(s => countWords(s.content) > 0) &&
        !confirm('Structura nouă înlocuiește capitolele actuale. Textul scris în ele se pierde. Continui?')) {
      return
    }
    setGeneratingStructure(true)
    try {
      const res = await api.project.structureExample({
        title: project.title, work_type: project.work_type, field: project.field,
        description: project.description, research_q: project.research_q,
        methodology: project.methodology,
      })
      await patch({ structure: res.structure })
      setDirtySections({})
      toast.success(
        res.from_llm
          ? `Structură generată: ${res.structure.length} capitole`
          : 'Structură standard aplicată'
      )
    } catch (err: any) {
      toast.error('Generarea structurii a eșuat: ' + (err?.message || 'necunoscut'))
    } finally {
      setGeneratingStructure(false)
    }
  }

  const totals = useMemo(() => {
    if (!project) return { written: 0, target: 0, done: 0, sections: 0 }
    let written = 0, target = 0, done = 0
    for (const s of project.structure) {
      const words = countWords(dirtySections[s.id] ?? s.content ?? '')
      written += words
      target += s.target_words || 0
      if (words > 0) done++
    }
    return { written, target, done, sections: project.structure.length }
  }, [project, dirtySections])

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Se încarcă proiectul…</div>
  }
  if (!project) {
    return (
      <div className="card text-center py-10">
        <p className="text-gray-500 mb-3">Proiectul nu s-a putut încărca.</p>
        <button onClick={loadProject} className="btn-secondary">Reîncearcă</button>
      </div>
    )
  }

  const exportThesisDocx = async () => {
    setExportingDocx(true)
    try {
      await api.project.downloadDocx()
      toast.success('Teza a fost descărcată în format Word (.docx)!')
    } catch (err) {
      toast.error('Exportul DOCX a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setExportingDocx(false)
    }
  }

  const exportThesisMarkdown = async () => {
    if (!project) return
    const lines: string[] = []
    lines.push(`# ${project.title || 'Lucrare de cercetare'}`)
    lines.push(`\n**Tip lucrare:** ${WORK_TYPES.find(w => w.value === project.work_type)?.label || project.work_type || 'Nespecificat'}`)
    if (project.field) lines.push(`**Domeniu:** ${project.field}`)
    if (project.deadline) lines.push(`**Termen limită:** ${project.deadline}`)
    lines.push(`\n---\n`)

    if (project.description) {
      lines.push(`## Rezumat / Descriere\n\n${project.description}\n`)
    }
    if (project.research_q) {
      lines.push(`## Întrebarea de cercetare (Research Question)\n\n${project.research_q}\n`)
    }
    if (project.methodology) {
      lines.push(`## Metodologie\n\n${project.methodology}\n`)
    }

    const plan = project.plan || {}
    const planFor = (sectionId: string) =>
      (project.plan_map?.sections?.[sectionId] || []).filter(key => (plan[key] || '').trim())

    if (project.structure && project.structure.length > 0) {
      lines.push(`\n## Capitole și Conținut\n`)
      project.structure.forEach((sec, idx) => {
        lines.push(`\n### ${idx + 1}. ${sec.title}`)
        if (sec.target_words) lines.push(`*Obiectiv: ~${sec.target_words} cuvinte*`)
        if (sec.description) lines.push(`*${sec.description}*\n`)
        // The plan belongs to the chapter that uses it, not to an appendix.
        const planKeys = planFor(sec.id)
        if (planKeys.length > 0) {
          lines.push(`**Din planul de cercetare:**`)
          for (const key of planKeys) {
            lines.push(`- **${PLAN_LABELS[key] || key}:** ${plan[key]}`)
          }
          lines.push('')
        }
        const content = dirtySections[sec.id] !== undefined ? dirtySections[sec.id] : sec.content
        if (content && content.trim()) {
          lines.push(`\n${content.trim()}\n`)
        } else {
          lines.push(`\n*[Capitol în lucru]*\n`)
        }
      })
    }

    const unmapped = (project.plan_map?.unmapped || []).filter(key => (plan[key] || '').trim())
    if (unmapped.length > 0) {
      lines.push(`\n## Plan de cercetare fără capitol propriu\n`)
      for (const key of unmapped) {
        lines.push(`- **${PLAN_LABELS[key] || key}:** ${plan[key]}`)
      }
      lines.push('')
    }

    // Complete bibliography matching Word (.docx) export
    try {
      const bib = await api.citations.bibliography()
      if (bib.references && bib.references.length > 0) {
        const cited = bib.references.filter(r => r.cited)
        const consulted = bib.references.filter(r => !r.cited)

        lines.push(`\n## Bibliografie\n`)
        if (cited.length > 0) {
          lines.push(`### Lucrări citate în text\n`)
          cited.forEach((ref, idx) => {
            lines.push(`${idx + 1}. ${ref.apa}`)
          })
          lines.push('')
        }
        if (consulted.length > 0) {
          lines.push(`### Lucrări consultate\n`)
          consulted.forEach((ref, idx) => {
            lines.push(`${idx + 1}. ${ref.apa}`)
          })
          lines.push('')
        }
      }
    } catch {
      // If bibliography fetch fails, still export the text
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const safeTitle = (project.title || 'teza').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40)
    a.download = `${safeTitle}-${new Date().toISOString().slice(0, 10)}.md`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('Teza a fost descărcată în format Markdown cu bibliografie!')
  }

  const progress = totals.target > 0 ? Math.min(100, Math.round((totals.written / totals.target) * 100)) : 0
  const plan = project.plan || {}
  const unmappedPlanFields = (project.plan_map?.unmapped || []).filter(key => (plan[key] || '').trim())

  return (
    <div className="space-y-4">
      {/* ── Header: the project itself ── */}
      <div className="card">
        <div className="flex items-start gap-3">
          <div className="shrink-0 w-10 h-10 rounded-xl bg-chrono-100 dark:bg-chrono-900/40 flex items-center justify-center">
            <GraduationCap className="w-5 h-5 text-chrono-600 dark:text-chrono-400" />
          </div>
          <div className="flex-1 min-w-0">
            {/* Multi-Project Isolation Switcher */}
            <div className="flex flex-wrap items-center gap-2 mb-2 pb-2 border-b border-line">
              <span className="text-[11px] font-bold uppercase tracking-wider text-chrono-600 dark:text-chrono-400 bg-chrono-100 dark:bg-chrono-950 px-2 py-0.5 rounded">
                Proiect Izolat
              </span>
              {projectList.length > 1 && (
                <select
                  value={project.id}
                  onChange={e => handleSwitchProject(Number(e.target.value))}
                  className="max-w-[calc(100vw-9rem)] truncate text-xs bg-surface border border-line rounded px-2 py-0.5 font-medium text-ink cursor-pointer"
                  aria-label="Comută proiectul de cercetare"
                >
                  {projectList.map(p => (
                    <option key={p.id} value={p.id} title={p.title || `Proiect #${p.id}`}>
                      {p.title || `Proiect #${p.id}`}
                    </option>
                  ))}
                </select>
              )}
              <button
                onClick={() => setShowProjectModal(true)}
                className="btn-ghost text-xs py-0.5 px-2 flex items-center gap-1 text-gray-500 hover:text-ink ml-auto"
                title="Creează o lucrare / un proiect izolat nou"
              >
                <FolderPlus className="w-3.5 h-3.5" />
                <span>+ Proiect Nou</span>
              </button>
            </div>
            {editingTitle ? (
              <div className="flex items-start gap-2">
                <input
                  ref={titleRef}
                  value={titleDraft}
                  onChange={e => setTitleDraft(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') saveTitle()
                    if (e.key === 'Escape') { setTitleDraft(project.title); setEditingTitle(false) }
                  }}
                  className="input flex-1 min-w-0 text-base font-semibold"
                  placeholder="Titlul lucrării"
                  autoFocus
                />
                <button onClick={saveTitle} className="icon-btn text-verdigris" aria-label="Salvează titlul">
                  <Check className="w-5 h-5" />
                </button>
                <button
                  onClick={() => { setTitleDraft(project.title); setEditingTitle(false) }}
                  className="icon-btn" aria-label="Renunță"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            ) : (
              <button
                onClick={() => setEditingTitle(true)}
                className="group text-left w-full"
                aria-label="Editează titlul lucrării"
              >
                {/* A long thesis title must not push the outline off screen.
                    The clamp lives on the inner span: line-clamp needs
                    display:-webkit-box, which a flex container would override. */}
                <h2 className="font-bold leading-snug break-words flex items-start gap-2 text-base sm:text-xl">
                  <span className={`min-w-0 ${expandedTitle ? '' : 'line-clamp-3'}`}>
                    {project.title || 'Adaugă titlul lucrării — apasă aici'}
                  </span>
                  <Pencil className="w-4 h-4 mt-1 shrink-0 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                </h2>
              </button>
            )}

            {(project.title || '').length > 90 && (
              <button
                onClick={() => setExpandedTitle(v => !v)}
                className="text-[11px] text-chrono-600 dark:text-chrono-400 hover:underline mt-1 py-2 touch-40"
              >
                {expandedTitle ? 'Restrânge titlul' : 'Vezi titlul întreg'}
              </button>
            )}

            <div className="flex flex-wrap items-center gap-2 mt-2">
              <select
                value={project.work_type}
                onChange={e => patch({ work_type: e.target.value }, true)}
                className="badge badge-accent border-0 cursor-pointer text-xs py-1"
                aria-label="Tipul lucrării"
              >
                {WORK_TYPES.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
              </select>
              {project.field && (
                <span className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300 text-xs">
                  {project.field}
                </span>
              )}
              {project.deadline && (
                <span className="badge badge-brass text-xs">
                  termen {project.deadline}
                </span>
              )}
              <button
                onClick={exportThesisMarkdown}
                className="btn-secondary text-xs py-1 px-2.5 ml-auto flex items-center gap-1.5"
                title="Descarcă toată teza asamblată în format Markdown (.md)"
              >
                <Download className="w-3.5 h-3.5" />
                Descarcă teza (.md)
              </button>
              <button
                onClick={exportThesisDocx}
                disabled={exportingDocx}
                className="btn-secondary text-xs py-1 px-2.5 flex items-center gap-1.5"
                title="Descarcă teza în format Word (.docx), cu bibliografie generată"
              >
                {exportingDocx ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
                Word (.docx)
              </button>
            </div>
          </div>
        </div>

        {/* Progress */}
        <div className="mt-4">
          <div className="flex items-center justify-between text-xs text-gray-500 mb-1.5">
            <span>{totals.written.toLocaleString('ro-RO')} din ~{totals.target.toLocaleString('ro-RO')} cuvinte</span>
            <span>{totals.done}/{totals.sections} capitole începute · {progress}%</span>
          </div>
          <div className="h-2 rounded-full bg-gray-200 dark:bg-gray-800 overflow-hidden">
            <div
              className="h-full bg-chrono-600 dark:bg-chrono-500 transition-all"
              style={{ width: `${Math.max(progress, totals.written > 0 ? 1 : 0)}%` }}
            />
          </div>
        </div>
      </div>

      {/* ── Tabs (Horizontally scrollable on mobile) ── */}
      {/* ── Modern Streamlined Project Workspaces ── */}
      <div className="space-y-2">
        <div className="flex flex-wrap gap-1.5 border-b border-gray-200 dark:border-gray-700 pb-px" role="tablist">
          {([
            { id: 'writing', label: '✍️ Structură & Redactare', defaultTab: 'structure', tabs: ['structure'] },
            { id: 'methodology', label: '📋 Chestionare & Scale', defaultTab: 'questionnaires', tabs: ['questionnaires'] },
            { id: 'advisor', label: '🧭 Diagnostic & Literatură', defaultTab: 'readiness', tabs: ['readiness', 'analysis', 'feedback', 'studies', 'topic'] },
            { id: 'evidence', label: '🗺️ Hartă Teză & Bibliografie', defaultTab: 'map', tabs: ['map', 'bibliography', 'ideas', 'notes'] },
          ] as const).map(pillar => {
            const isActive = (pillar.tabs as readonly string[]).includes(tab)
            return (
              <button
                key={pillar.id}
                role="tab"
                aria-selected={isActive}
                onClick={() => {
                  if (!isActive) {
                    setTab(pillar.defaultTab as Tab)
                  }
                }}
                className={`px-3 sm:px-4 py-2 min-h-[40px] text-xs sm:text-sm font-medium border-b-2 transition-all whitespace-nowrap shrink-0 ${
                  isActive
                    ? 'border-chrono-600 text-chrono-600 dark:text-chrono-400 font-bold bg-chrono-50/50 dark:bg-chrono-950/20'
                    : 'border-transparent text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'
                }`}
              >
                {pillar.label}
              </button>
            )
          })}
        </div>

        {/* Sub-navigation for Diagnostic & Literatură */}
        {(tab === 'readiness' || tab === 'analysis' || tab === 'feedback' || tab === 'studies' || tab === 'topic') && (
          <div className="flex flex-wrap items-center gap-1.5 p-1 bg-gray-100 dark:bg-gray-800/60 rounded-lg border border-gray-200 dark:border-gray-700 text-xs">
            {([
              { id: 'readiness', label: '✅ Pregătire & Scor' },
              { id: 'analysis', label: '📊 Analiză Temă' },
              { id: 'feedback', label: '💬 Feedback' },
              { id: 'studies', label: '📚 Studii Recomandate' },
              { id: 'topic', label: '🧭 Idei de Temă' },
            ] as const).map(st => (
              <button
                key={st.id}
                onClick={() => setTab(st.id as Tab)}
                className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                  tab === st.id
                    ? 'bg-white dark:bg-gray-900 text-chrono-600 dark:text-chrono-400 shadow-sm font-semibold'
                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100'
                }`}
              >
                {st.label}
              </button>
            ))}
          </div>
        )}

        {/* Sub-navigation for Hartă Teză & Bibliografie */}
        {(tab === 'map' || tab === 'bibliography' || tab === 'ideas') && (
          <div className="flex flex-wrap items-center gap-1.5 p-1 bg-gray-100 dark:bg-gray-800/60 rounded-lg border border-gray-200 dark:border-gray-700 text-xs">
            {([
              { id: 'map', label: '🗺️ Hartă Afirmații & Dovezi' },
              { id: 'bibliography', label: '📖 Bibliografie APA' },
              { id: 'ideas', label: '💡 Idei de Cercetare' },
              { id: 'notes', label: '🗒️ Notițe' },
            ] as const).map(st => (
              <button
                key={st.id}
                onClick={() => setTab(st.id as Tab)}
                className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                  tab === st.id
                    ? 'bg-white dark:bg-gray-900 text-chrono-600 dark:text-chrono-400 shadow-sm font-semibold'
                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100'
                }`}
              >
                {st.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Structure tab ── */}
      {tab === 'structure' && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <button onClick={generateStructure} disabled={generatingStructure} className="btn-secondary text-sm">
              {generatingStructure
                ? <><Loader2 className="w-4 h-4 animate-spin" /> Generez…</>
                : <><Sparkles className="w-4 h-4" /> Exemplu de structură</>}
            </button>
            <button onClick={addSection} className="btn-secondary text-sm">
              <Plus className="w-4 h-4" /> Adaugă capitol
            </button>
          </div>

          {/* The plan has content but no chapter carries it: say where it is
              edited instead of leaving it invisible until the export. */}
          {unmappedPlanFields.length > 0 && (
            <div className="card !p-3 text-xs text-brass bg-brass-soft border-brass/25">
              <p className="flex items-start gap-2">
                <Lightbulb className="w-4 h-4 mt-0.5 shrink-0" />
                <span>
                  {unmappedPlanFields.length} părți din planul de cercetare nu se leagă de niciun capitol:{' '}
                  {unmappedPlanFields.map(key => PLAN_LABELS[key] || key).join(', ')}.{' '}
                  <button onClick={() => setTab('readiness')} className="underline hover:no-underline py-1">
                    Vezi planul în Pregătire
                  </button>
                </span>
              </p>
            </div>
          )}

          {/* The title read critically, above the outline it should announce. */}
          <TitlePanel />

          <div className="card !p-2 sm:!p-3">
            <p className="text-xs text-gray-500 px-2 mb-2">
              Scrie aici proiectul, capitol cu capitol. Textul se salvează pe server.
            </p>
            <div className="space-y-1">
              {project.structure.map((section, index) => {
                const isOpen = openSection === section.id
                const draft = dirtySections[section.id] ?? section.content ?? ''
                const words = countWords(draft)
                const targetWords = section.target_words || 0
                const sectionProgress = targetWords > 0 ? Math.min(100, Math.round((words / targetWords) * 100)) : 0
                const isDirty = dirtySections[section.id] !== undefined && dirtySections[section.id] !== section.content
                const planKeys = (project.plan_map?.sections?.[section.id] || [])
                  .filter(key => (plan[key] || '').trim())

                return (
                  <div key={section.id} className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
                    <div className="flex items-center gap-2 p-2 sm:p-3">
                      <button
                        onClick={() => setOpenSection(isOpen ? null : section.id)}
                        className="flex items-center gap-2 flex-1 min-w-0 text-left py-2 touch-40"
                        aria-expanded={isOpen}
                      >
                        {isOpen ? <ChevronDown className="w-4 h-4 shrink-0 text-gray-400" />
                                : <ChevronRight className="w-4 h-4 shrink-0 text-gray-400" />}
                        <span className="text-xs text-gray-400 shrink-0 w-5">{index + 1}.</span>
                        <span className="font-medium text-sm truncate">{section.title}</span>
                      </button>
                      <span className={`text-[11px] shrink-0 tabular-nums ${
                        words === 0 ? 'text-gray-400'
                          : sectionProgress >= 100 ? 'text-verdigris'
                          : 'text-chrono-600 dark:text-chrono-400'
                      }`}>
                        {words}{targetWords ? `/${targetWords}` : ''}
                      </span>
                      {words > 0 && (
                        <span className="w-10 h-1.5 rounded-full bg-gray-200 dark:bg-gray-800 overflow-hidden shrink-0 hidden sm:block">
                          <span className="block h-full bg-chrono-600 dark:bg-chrono-500" style={{ width: `${Math.max(sectionProgress, 3)}%` }} />
                        </span>
                      )}
                      <button
                        onClick={() => removeSection(section.id)}
                        className="icon-btn text-muted hover:text-madder shrink-0 touch-40"
                        aria-label={`Șterge capitolul ${section.title}`}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    {isOpen && (
                      <div className="px-2 sm:px-3 pb-3 space-y-2 border-t border-gray-100 dark:border-gray-800 pt-2">
                        {section.description && (
                          <p className="text-xs text-gray-500 dark:text-gray-400 flex items-start gap-1.5">
                            <Lightbulb className="w-3.5 h-3.5 mt-0.5 shrink-0 text-brass" />
                            <span>{section.description}</span>
                          </p>
                        )}
                        {planKeys.length > 0 && (
                          <div className="rounded-lg bg-accent-soft p-2 space-y-1">
                            <p className="text-[11px] font-semibold text-accent-strong">
                              Din planul de cercetare
                            </p>
                            {planKeys.map(key => (
                              <p key={key} className="text-xs text-gray-600 dark:text-gray-400 break-words">
                                <span className="font-medium">{PLAN_LABELS[key] || key}:</span> {plan[key]}
                              </p>
                            ))}
                          </div>
                        )}
                        <textarea
                          value={draft}
                          onChange={e => setDirtySections(prev => ({ ...prev, [section.id]: e.target.value }))}
                          placeholder="Scrie textul acestui capitol…"
                          className="input w-full min-h-[220px] sm:min-h-[280px] text-sm sm:text-base leading-relaxed resize-y p-3"
                          spellCheck
                        />
                        <div className="flex items-center justify-between gap-2 flex-wrap pt-1">
                          <div className="flex items-center gap-2">
                            {savingSection === section.id ? (
                              <span className="text-[11px] text-chrono-600 dark:text-chrono-400 flex items-center gap-1 font-medium">
                                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Se salvează automat…
                              </span>
                            ) : isDirty ? (
                              <span className="text-[11px] text-brass font-medium">
                                Modificări nesalvate
                              </span>
                            ) : lastSaved[section.id] ? (
                              <span className="text-[11px] text-verdigris flex items-center gap-1 font-medium">
                                <Check className="w-3.5 h-3.5" /> Salvat automat la {lastSaved[section.id]}
                              </span>
                            ) : (
                              <span className="text-[11px] text-gray-400">
                                Salvat pe server
                              </span>
                            )}
                            <span className="text-[11px] text-gray-400 hidden sm:inline">
                              · Ctrl+K pentru a căuta studii
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => saveSectionContent(section)}
                            disabled={!isDirty || savingSection === section.id}
                            className="btn-primary text-xs px-3 py-1.5"
                          >
                            {savingSection === section.id
                              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Salvez…</>
                              : <><Save className="w-3.5 h-3.5" /> Salvează acum</>}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* ── Thesis map: chapters → claims → source evidence ── */}
      {tab === 'map' && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted max-w-xl">
              Sinteza citește notițele și evidențierile din studiile marcate „citit/adnotat” și le transformă în concepte și relații (factori → subfactori), pe care le vezi apoi în Graful „Concepte”.
            </p>
            <button
              type="button"
              onClick={() => void synthesizeConcepts()}
              disabled={synthesizingConcepts}
              className="btn-secondary text-xs"
              title="Extrage concepte și relații din notele și evidențierile studiilor citite"
            >
              {synthesizingConcepts
                ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Sintetizez…</>
                : <><Brain className="w-3.5 h-3.5" /> Sinteză concepte din studiile citite</>}
            </button>
          </div>
          <Suspense fallback={<div className="card flex items-center justify-center gap-2 py-10 text-sm text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Se încarcă harta…</div>}>
            <ThesisEvidenceMap
              map={evidenceMap}
              studies={mapStudies}
              loading={loadingEvidenceMap}
              onRefresh={loadEvidenceMap}
              onSelectEntry={onSelectEntry}
              onOpenSource={onOpenSource}
              focusClaimId={focusClaim?.claimId}
            />
          </Suspense>
        </div>
      )}

      {/* ── Topic tab: choosing what to study ── */}
      {tab === 'topic' && (
        <TopicPanel
          onProjectChange={p => setProject(p)}
          onOpenTitle={() => setTab('structure')}
        />
      )}

      {/* ── Readiness tab ── */}
      {tab === 'readiness' && (
        <ReadinessPanel project={project} onProjectChange={p => setProject(p)} />
      )}

      {/* ── Ideas tab ── */}
      {tab === 'ideas' && <IdeasPanel />}
      {tab === 'bibliography' && <BibliographyPanel />}
      {/* ── Notițe tab: harta proiectului + dosare ── */}
      {tab === 'notes' && (
        <div className="space-y-3">
          <section
            className="card p-3 sm:p-4 space-y-3"
            onClick={() => setProjectMapOpen(!projectMapOpen)}
            role="button" tabIndex={0} onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setProjectMapOpen(!projectMapOpen) }
            }}
            style={{ cursor: 'pointer' }}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <ChevronDown className={`w-4 h-4 shrink-0 ${projectMapOpen ? '' : '-rotate-90'} text-muted`} />
                <h4 className="font-semibold text-sm text-ink">Harta proiectului</h4>
              </div>
              {loadingProjectMap && (
                <Loader2 className="w-3 h-3 animate-spin text-chrono-500" />
              )}
            </div>
            <p className="text-[11px] text-muted">
              Documente și concepte transversale legate prin afirmațiile verificate. Apasă pentru a deschide/închide.
            </p>
            {projectMapOpen && (
              <div className="space-y-2 pt-2 border-t border-line/40">
                {loadingProjectMap && !projectMap ? (
                  <div className="card text-center py-8 text-gray-500">
                    <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" /> Se încarcă harta…
                  </div>
                ) : (
                  <>
                    <NoteMap scope="project" payload={projectMap ?? undefined} />
                    {!projectMap && !loadingProjectMap && (
                      <p className="text-[11px] text-muted">Fără date de hartă. Așteaptă verificarea afirmațiilor în dosare.</p>
                    )}
                    <button onClick={regenerateProjectMap} disabled={loadingProjectMap}
                            className="btn-secondary text-xs sm:text-sm min-w-[40px] min-h-[40px] touch-40">
                      {loadingProjectMap ? <><RefreshCw className="w-3 h-3 animate-spin" /> Regenerez…</> : <><RefreshCw className="w-3 h-3" /> Regenerează harta proiectului</>}
                    </button>
                  </>
                )}
              </div>
            )}
          </section>
          <NotesPanel />
        </div>
      )}

      {/* ── Studies tab ── */}
      {tab === 'studies' && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-gray-500">
              {loadingStudies
                ? 'Caut în bibliotecă…'
                : `${studies.length} studii potrivite din ${libraryTotal.toLocaleString('ro-RO')} în bibliotecă`}
            </p>
            <button onClick={loadStudies} disabled={loadingStudies} className="btn-ghost text-xs">
              <RefreshCw className={`w-3.5 h-3.5 ${loadingStudies ? 'animate-spin' : ''}`} /> Reîmprospătează
            </button>
          </div>
          {terms.length > 0 && (
            <div className="flex flex-wrap gap-1">
              <span className="text-[11px] text-gray-400 mr-1">după:</span>
              {terms.slice(0, 10).map(t => (
                <span key={t} className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400 text-[10px]">{t}</span>
              ))}
            </div>
          )}
          {loadingStudies && studies.length === 0 ? (
            <div className="card text-center py-8 text-gray-500">
              <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" /> Se caută în bibliotecă…
            </div>
          ) : studies.length === 0 ? (
            <div className="card text-center py-8">
              <p className="text-gray-500 mb-1">Niciun studiu nu se potrivește încă.</p>
              <p className="text-sm text-gray-400">Scrie titlul și descrierea proiectului, apoi reîmprospătează.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {studies.map(study => (
                <button
                  key={study.entry_id}
                  onClick={() => onSelectEntry(study.entry_id)}
                  className="card w-full text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors min-w-0"
                >
                  <div className="flex items-start justify-between gap-2 mb-1">
                    <h3 className="font-semibold text-sm leading-snug break-words min-w-0">{study.title}</h3>
                    <span className="text-xs shrink-0 text-gray-400 tabular-nums" title="Scor de potrivire">
                      {study.score.toFixed(1)}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5 mb-1.5">
                    {study.year && <span className="badge badge-muted text-[10px]">{study.year}</span>}
                    {study.study_type && <span className="badge badge-accent text-[10px]">{study.study_type}</span>}
                    {study.design && <span className="badge badge-accent text-[10px]">{study.design}</span>}
                    {study.quality != null && <span className="text-[10px] text-brass">{'★'.repeat(study.quality)}</span>}
                    {study.has_pdf && <span className="badge badge-verdigris text-[10px]">PDF</span>}
                  </div>
                  <p className="text-[11px] text-gray-500 break-words">{study.reason}</p>
                  <span className="text-[11px] text-chrono-600 dark:text-chrono-400 inline-flex items-center gap-1 mt-1">
                    Deschide studiul <ExternalLink className="w-3 h-3" />
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {/* ── Questionnaires tab ── */}
      {tab === 'questionnaires' && (
        <QuestionnairesPanel projectId={project.id} />
      )}

      {/* ── Analysis tab ── */}
      {tab === 'analysis' && (
        <div className="space-y-3">
          {loadingAnalysis && !analysis ? (
            <div className="card text-center py-10">
              <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2 text-chrono-500" />
              <p className="text-gray-500 text-sm">Citesc analiza salvată…</p>
            </div>
          ) : analysis ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                {[
                  ['Studii în bibliotecă', analysis.stats.library_studies],
                  ['Potrivite temei', analysis.stats.matching_studies],
                  ['Cu PDF local', analysis.stats.studies_with_pdf],
                  ['Cu DOI verificat', analysis.stats.studies_with_doi ?? 0],
                  ['Direcții de efect', Object.keys(analysis.stats.effect_directions).length],
                ].map(([label, value]) => (
                  <div key={label as string} className="card !p-3 text-center">
                    <div className="text-xl font-bold tabular-nums">{value}</div>
                    <div className="text-[10px] text-gray-500 leading-tight mt-0.5">{label}</div>
                  </div>
                ))}
              </div>
              <div className="card">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <h3 className="font-semibold text-sm">Analiza temei</h3>
                  <div className="flex items-center gap-2">
                    {analysis.generated_at && (
                      <span className="text-[11px] text-gray-400">
                        generată {analysis.generated_at}
                      </span>
                    )}
                    <button onClick={regenerateAnalysis} disabled={loadingAnalysis} className="btn-secondary text-xs">
                      {loadingAnalysis ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                      {analysis.needs_generation ? 'Generează' : 'Refă'}
                    </button>
                  </div>
                </div>

                {/* Storage answers instantly and free; the model costs an
                    explicit click. When the project changed since, say so. */}
                {analysis.needs_generation && (
                  <p className="text-[11px] text-brass mb-2">
                    {analysis.stale
                      ? 'Tema s-a schimbat după ce a fost scrisă analiza de mai jos — poți genera una nouă.'
                      : 'Analiza încă nu a fost generată. „Generează” costă un apel către model și durează câteva secunde.'}
                  </p>
                )}

                <div className="prose prose-sm dark:prose-invert max-w-none text-sm break-words">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{analysis.analysis}</ReactMarkdown>
                </div>
                {!analysis.from_llm && analysis.needs_generation && (
                  <p className="text-[11px] text-brass mt-2">
                    Textul de mai sus nu e o interpretare scrisă de model — apasă „Generează”.
                  </p>
                )}
                {!analysis.from_llm && !analysis.needs_generation && (
                  <p className="text-[11px] text-brass mt-2">
                    Textul e o sinteză simplă — fără LLM configurat nu pot scrie o interpretare.
                  </p>
                )}
              </div>
              {Object.keys(analysis.stats.designs).length > 0 && (
                <div className="card">
                  <h3 className="font-semibold text-sm mb-2">Ce tipuri de studii ai</h3>
                  <div className="space-y-1.5">
                    {Object.entries(analysis.stats.designs).map(([design, count]) => {
                      const max = Math.max(...Object.values(analysis.stats.designs))
                      return (
                        <div key={design} className="flex items-center gap-2 text-xs">
                          <span className="w-32 shrink-0 truncate text-gray-600 dark:text-gray-400">{design}</span>
                          <span className="flex-1 h-2 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
                            <span className="block h-full bg-chrono-600 dark:bg-chrono-500" style={{ width: `${(count / max) * 100}%` }} />
                          </span>
                          <span className="w-5 text-right tabular-nums text-gray-500">{count}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="card text-center py-8">
              <p className="text-gray-500 mb-3">Analiza nu s-a încărcat.</p>
              <button onClick={loadAnalysis} className="btn-secondary">Încearcă din nou</button>
            </div>
          )}
        </div>
      )}

      {/* ── Feedback tab ──
          The GET is cheap now: deterministic items only, no model call. The
          reviewer's read is one LLM call (5–39 s) and runs only on an explicit
          click, never when the tab opens. */}
      {tab === 'feedback' && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold text-sm">Ce trebuie corectat</h3>
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={requestReview}
                disabled={requestingReview}
                className="btn-primary text-xs"
              >
                {requestingReview
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <Sparkles className="w-3.5 h-3.5" />}
                {requestingReview ? 'Modelul citește…' : 'Cere feedback de la model'}
              </button>
              <button onClick={loadFeedback} disabled={loadingFeedback} className="btn-ghost text-xs">
                <RefreshCw className={`w-3.5 h-3.5 ${loadingFeedback ? 'animate-spin' : ''}`} /> Reface
              </button>
            </div>
          </div>

          {requestingReview && (
            <p className="text-xs text-gray-500">
              Modelul citește tot proiectul; răspunsul vine în 5–39 de secunde. Verificările de mai jos rămân valabile.
            </p>
          )}

          {/* GET failed while a previous result is still on screen. */}
          {feedback && feedbackError && (
            <div className="card border-madder/25 text-sm text-madder flex flex-wrap items-center justify-between gap-2">
              <span className="flex items-start gap-2 min-w-0">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                Reîmprospătarea a eșuat: {feedbackError}
              </span>
              <button onClick={loadFeedback} disabled={loadingFeedback} className="btn-ghost text-sm">
                <RefreshCw className="w-4 h-4" /> Reîncearcă
              </button>
            </div>
          )}

          {/* POST failed: the structural items stay, and the model read can be
              retried without reloading the tab. */}
          {reviewError && (
            <div className="card border-madder/25 text-sm text-madder flex flex-wrap items-center justify-between gap-2">
              <span className="flex items-start gap-2 min-w-0">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                Comentariul modelului nu a venit: {reviewError}
              </span>
              <button onClick={requestReview} disabled={requestingReview} className="btn-ghost text-sm">
                <RefreshCw className="w-4 h-4" /> Reîncearcă
              </button>
            </div>
          )}

          {loadingFeedback && !feedback ? (
            <div className="card text-center py-8">
              <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2 text-chrono-500" />
              <p className="text-gray-500 text-sm">Verific proiectul…</p>
            </div>
          ) : feedback ? (
            <>
              {feedback.items.length > 0 && (
                <div className="card space-y-2">
                  {feedback.items.map((item, i) => {
                    const { icon: Icon, cls } = levelStyle[item.level] ?? levelStyle.info
                    return (
                      <div key={i} className="flex items-start gap-2 text-sm">
                        <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${cls}`} />
                        <div className="min-w-0">
                          <p className="break-words">{item.message}</p>
                          {item.action && <p className="text-xs text-gray-500 mt-0.5 break-words">{item.action}</p>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
              {feedback.reviewer ? (
                <div className="card">
                  <h4 className="font-semibold text-sm mb-2">Observațiile îndrumătorului</h4>
                  <div className="prose prose-sm dark:prose-invert max-w-none text-sm break-words">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{feedback.reviewer}</ReactMarkdown>
                  </div>
                </div>
              ) : (
                <div className="card text-center py-6">
                  <p className="text-sm text-gray-500 mb-1">Observațiile îndrumătorului nu au fost cerute încă.</p>
                  <p className="text-xs text-gray-400 mb-3">
                    Modelul citește tot proiectul și răspunde în 5–39 de secunde. Pornește doar când apeși butonul.
                  </p>
                  <button onClick={requestReview} disabled={requestingReview} className="btn-secondary text-sm">
                    {requestingReview
                      ? <Loader2 className="w-4 h-4 animate-spin" />
                      : <Sparkles className="w-4 h-4" />}
                    {requestingReview ? 'Modelul citește…' : 'Cere feedback de la model'}
                  </button>
                </div>
              )}
            </>
          ) : (
            <div className="card text-center py-8">
              <p className="text-gray-500 mb-3">
                {feedbackError ? `Feedback-ul nu s-a încărcat: ${feedbackError}` : 'Feedback-ul nu s-a încărcat.'}
              </p>
              <button onClick={loadFeedback} className="btn-secondary">Încearcă din nou</button>
            </div>
          )}
        </div>
      )}
      {/* Multi-Project Creation Modal */}
      {showProjectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="card max-w-md w-full shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b pb-3 border-line">
              <h4 className="font-bold text-base flex items-center gap-2">
                <FolderPlus className="w-5 h-5 text-chrono-600" />
                <span>Creează Proiect Izolat Nou</span>
              </h4>
              <button onClick={() => setShowProjectModal(false)} className="icon-btn text-gray-400">✕</button>
            </div>
            <p className="text-xs text-gray-500">
              Fiecare proiect are propriul său titlu, structură de capitole, plan de cercetare și chestionare, complet izolate de alte lucrări.
            </p>
            <div>
              <label className="block text-xs font-semibold text-gray-600 dark:text-gray-300 mb-1">
                Titlul noului proiect *
              </label>
              <input
                type="text"
                value={newProjectTitle}
                onChange={e => setNewProjectTitle(e.target.value)}
                placeholder="Ex: Impactul atașamentului asupra rezilienței la adulți"
                className="input w-full text-sm"
                autoFocus
                onKeyDown={e => {
                  if (e.key === 'Enter') handleCreateNewProject()
                }}
              />
            </div>
            <div className="flex justify-end gap-2 pt-2 border-t border-line">
              <button onClick={() => setShowProjectModal(false)} className="btn-secondary text-xs">Renunță</button>
              <button onClick={handleCreateNewProject} disabled={creatingProject} className="btn-primary text-xs flex items-center gap-1.5">
                {creatingProject && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                <span>Creează Proiect</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
