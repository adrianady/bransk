import { useCallback, useState, useEffect, lazy, Suspense } from 'react'
import {
  CalendarDays, Network, BookOpenText, Dna, FileText, RotateCcw, ListChecks, Lightbulb,
  Bot, Sun, Moon, Search, Plus, Settings2,
  GraduationCap, Menu, X, ChevronRight,
} from 'lucide-react'
import { ProjectView } from './components/ProjectView'
import { api, ApiError } from './lib/api'
import { useToast } from './components/Toast'
import type { Entry, TimelineEntry, Tag, StudySource, StudyQASource } from './lib/types'

// Every view but the project page is split into its own chunk and fetched when
// it is first opened. Before this, one 533 kB entry file carried React and all
// twelve views together, so opening the app also downloaded the graph, the
// timeline and the chat it might never use.
const TimelineView = lazy(() => import('./components/Timeline').then(m => ({ default: m.TimelineView })))
const GraphView = lazy(() => import('./components/GraphView').then(m => ({ default: m.GraphView })))
const ChatPanel = lazy(() => import('./components/ChatPanel').then(m => ({ default: m.ChatPanel })))
const EntryForm = lazy(() => import('./components/EntryForm').then(m => ({ default: m.EntryForm })))
const EntryDetail = lazy(() => import('./components/EntryDetail').then(m => ({ default: m.EntryDetail })))
const EntryList = lazy(() => import('./components/EntryList').then(m => ({ default: m.EntryList })))
const ReviewPanel = lazy(() => import('./components/ReviewPanel').then(m => ({ default: m.ReviewPanel })))
const TodoList = lazy(() => import('./components/TodoList').then(m => ({ default: m.TodoList })))
const ThoughtsView = lazy(() => import('./components/ThoughtsView').then(m => ({ default: m.ThoughtsView })))
const StudiesView = lazy(() => import('./components/StudiesView').then(m => ({ default: m.StudiesView })))
const TheoriesView = lazy(() => import('./components/TheoriesView').then(m => ({ default: m.TheoriesView })))
const AiStatusModal = lazy(() => import('./components/AiStatusModal').then(m => ({ default: m.AiStatusModal })))

/** Shown while a view's chunk arrives — brief, and in the page's own space. */
function ViewLoading() {
  return (
    <div className="flex items-center gap-2 text-sm text-muted py-10 justify-center">
      <span className="w-4 h-4 rounded-full border-2 border-accent border-t-transparent animate-spin" />
      Se încarcă…
    </div>
  )
}

/** A small instrument mark: three time lines and one recorded point. */
function BrandMark({ className = 'w-8 h-8' }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <rect width="64" height="64" rx="14" className="fill-accent" />
      <path d="M16 40h32M16 32h22M16 24h28" className="stroke-on-accent" strokeWidth="3" strokeLinecap="round" />
      <circle cx="44" cy="24" r="4" className="fill-accent-soft" />
    </svg>
  )
}

type View = 'project' | 'timeline' | 'graph' | 'studies' | 'theories' | 'entries' | 'review' | 'todos' | 'thoughts'

const VIEW_LABELS: Record<View, string> = {
  project: 'Proiect',
  timeline: 'Cronologie',
  graph: 'Graf',
  studies: 'Studii',
  theories: 'Teorii',
  entries: 'Intrări',
  review: 'Verificare',
  todos: 'Sarcini',
  thoughts: 'Gânduri',
}

export default function App() {
  const toast = useToast()
  const [view, setView] = useState<View>(() => {
    const h = window.location.hash.replace('#', '') as View
    if (['project', 'timeline', 'graph', 'studies', 'theories', 'entries', 'review', 'todos', 'thoughts'].includes(h)) {
      return h
    }
    return 'project'
  })
  const [previousView, setPreviousView] = useState<View | null>(null)

  const changeView = useCallback((next: View) => {
    setView(next)
    window.location.hash = next
  }, [])

  useEffect(() => {
    const handleHash = () => {
      const h = window.location.hash.replace('#', '') as View
      if (['project', 'timeline', 'graph', 'studies', 'theories', 'entries', 'review', 'todos', 'thoughts'].includes(h)) {
        setView(h)
      }
    }
    window.addEventListener('hashchange', handleHash)
    return () => window.removeEventListener('hashchange', handleHash)
  }, [])
  const [showEntryForm, setShowEntryForm] = useState(false)
  const [showAiStatus, setShowAiStatus] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [selectedEntry, setSelectedEntry] = useState<Entry | null>(null)
  const [projectFocus, setProjectFocus] = useState<{ sectionId: string; claimId: number; token: number } | null>(null)
  const [pendingPdfSource, setPendingPdfSource] = useState<StudyQASource | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [showChat, setShowChat] = useState(false)
  // The thesis title lives in the sidebar so the user always sees what they
  // are working on. ProjectView reports changes through onTitleChange.
  const [projectTitle, setProjectTitle] = useState('')
  const [darkMode, setDarkMode] = useState(() => {
    const stored = localStorage.getItem('chronokg-theme')
    if (stored === 'light') return false
    if (stored === 'dark') return true
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  const toggleDark = () => {
    const next = !darkMode
    setDarkMode(next)
    localStorage.setItem('chronokg-theme', next ? 'dark' : 'light')
    document.documentElement.classList.toggle('dark', next)
  }

  // Follow the OS theme only while the user hasn't made an explicit choice.
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => {
      if (!localStorage.getItem('chronokg-theme')) {
        setDarkMode(e.matches)
        document.documentElement.classList.toggle('dark', e.matches)
      }
    }
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])

  // Keep the root <html> dark class in sync on first mount too, so canvas
  // visualizations (vis-network/vis-timeline) can read the active theme.
  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
  }, [darkMode])

  // Load the thesis title for the sidebar (best-effort: never blocks the UI).
  useEffect(() => {
    api.project.get()
      .then(p => setProjectTitle(p.title || ''))
      .catch(() => { /* sidebar simply stays without a title */ })
  }, [])

  // Async handler to fetch full Entry from Timeline/Graph selection
  const handleSelectTimelineEntry = useCallback(async (item: TimelineEntry) => {
    setPreviousView(view)
    setLoadingDetail(true)
    changeView('entries')
    try {
      const fullEntry = await api.entries.get(item.id)
      setSelectedEntry(fullEntry)
    } catch (err) {
      toast.error('Detaliile intrării nu s-au putut încărca: ' + (err instanceof Error ? err.message : 'eroare necunoscută'))
    } finally {
      setLoadingDetail(false)
    }
  }, [view, changeView, toast])

  const handleSelectStudy = useCallback((entry: Entry) => {
    setPreviousView(view)
    setSelectedEntry(entry)
    changeView('entries')
  }, [view, changeView])

  // Open a study by id (the thesis project links to studies by id only).
  const handleSelectStudyById = useCallback(async (entryId: number) => {
    setPreviousView(view)
    setLoadingDetail(true)
    changeView('entries')
    try {
      setSelectedEntry(await api.entries.get(entryId))
    } catch (err) {
      toast.error('Studiul nu s-a putut deschide: ' + (err instanceof Error ? err.message : 'eroare necunoscută'))
    } finally {
      setLoadingDetail(false)
    }
  }, [view, changeView, toast])

  const handleOpenStudySource = useCallback(async (entryId: number, source: StudySource) => {
    setPreviousView(view)
    setLoadingDetail(true)
    changeView('entries')
    try {
      setPendingPdfSource({ ...source, score: 0 })
      setSelectedEntry(await api.entries.get(entryId))
    } catch (err) {
      setPendingPdfSource(null)
      toast.error('Studiul nu s-a putut deschide: ' + (err instanceof Error ? err.message : 'eroare necunoscută'))
    } finally {
      setLoadingDetail(false)
    }
  }, [view, changeView, toast])

  const handleOpenProjectClaim = useCallback((sectionId: string, claimId: number) => {
    setSelectedEntry(null)
    setPreviousView(null)
    setProjectFocus({ sectionId, claimId, token: Date.now() })
    changeView('project')
  }, [changeView])

  const handleBackFromDetail = useCallback(() => {
    setSelectedEntry(null)
    if (previousView) {
      setView(previousView)
      setPreviousView(null)
    }
  }, [previousView])

  const navItems: { id: View; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { id: 'timeline', label: 'Cronologie', icon: CalendarDays },
    { id: 'graph', label: 'Graf', icon: Network },
    { id: 'studies', label: 'Studii', icon: BookOpenText },
    { id: 'theories', label: 'Teorii', icon: Dna },
    { id: 'entries', label: 'Intrări', icon: FileText },
    { id: 'review', label: 'Verificare', icon: RotateCcw },
    { id: 'todos', label: 'Sarcini', icon: ListChecks },
    { id: 'thoughts', label: 'Gânduri', icon: Lightbulb },
  ]

  return (
    <div className={`min-h-screen bg-canvas text-ink ${darkMode ? 'dark' : ''}`}>
      {/* overflow-hidden: the chat panel must never push the page sideways on
          a phone. The main column owns its own scrolling. */}
      <div className="flex h-screen overflow-hidden">
        {/* Desktop Sidebar (hidden on mobile) */}
        <nav className="hidden md:flex flex-col shrink-0 w-16 lg:w-52 bg-surface border-r border-line">
          <div className="p-3 lg:p-4 border-b border-line">
            <button
              onClick={() => changeView('project')}
              className="w-full text-left group"
              title={projectTitle || 'Proiectul meu'}
            >
              <span className="hidden lg:flex items-center gap-2.5">
                <BrandMark className="w-7 h-7 shrink-0" />
                <span className="font-serif text-[15px] font-semibold tracking-tight text-ink">
                  Bransk
                </span>
              </span>
              <BrandMark className="lg:hidden w-7 h-7" />
              {projectTitle && (
                <span className="hidden lg:block mt-2.5">
                  <span
                    className="text-[11px] leading-snug text-muted line-clamp-3 break-words group-hover:text-accent transition-colors"
                    title={projectTitle}
                  >
                    {projectTitle}
                  </span>
                </span>
              )}
            </button>
          </div>

          <div className="p-2 pb-0">
            <button
              onClick={() => changeView('project')}
              aria-current={view === 'project' ? 'page' : undefined}
              title="Proiectul meu — lucrarea la care lucrez"
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-semibold transition-colors ${
                view === 'project'
                  ? 'bg-accent-soft text-accent-strong'
                  : 'text-ink/80 hover:bg-surface-2 hover:text-ink'
              }`}
            >
              <GraduationCap className="w-5 h-5 shrink-0" />
              <span className="hidden lg:block">Proiectul meu</span>
            </button>
            <div className="my-2 border-t border-line" />
          </div>

          <div className="flex-1 p-2 pt-0 space-y-0.5 overflow-y-auto">
            {navItems.map(item => (
              <button
                key={item.id}
                title={item.label}
                aria-current={view === item.id ? 'page' : undefined}
                onClick={() => {
                  if (item.id === 'entries') setSelectedEntry(null)
                  changeView(item.id)
                }}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  view === item.id
                    ? 'bg-accent-soft text-accent-strong font-semibold'
                    : 'text-muted hover:bg-surface-2 hover:text-ink'
                }`}
              >
                <item.icon className="w-5 h-5 shrink-0" />
                <span className="hidden lg:block">{item.label}</span>
              </button>
            ))}
          </div>

          <div className="p-2 border-t border-line space-y-0.5">
            <button onClick={() => setShowChat(!showChat)} title="Chat AI"
              aria-expanded={showChat}
              aria-label="Deschide/închide panoul de chat"
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                showChat ? 'bg-accent-soft text-accent-strong font-semibold' : 'text-muted hover:bg-surface-2 hover:text-ink'
              }`}
            >
              <Bot className="w-5 h-5 shrink-0" />
              <span className="hidden lg:block">Chat AI</span>
            </button>
            <button onClick={toggleDark}
              title={darkMode ? 'Treci pe tema luminoasă' : 'Treci pe tema întunecată'}
              aria-label={darkMode ? 'Treci pe tema luminoasă' : 'Treci pe tema întunecată'}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-muted hover:bg-surface-2 hover:text-ink transition-colors"
            >
              {darkMode ? <Sun className="w-5 h-5 shrink-0" /> : <Moon className="w-5 h-5 shrink-0" />}
              <span className="hidden lg:block">{darkMode ? 'Luminos' : 'Întunecat'}</span>
            </button>
            <button onClick={() => setShowAiStatus(true)}
              title="Stare și setări AI"
              aria-label="Stare și setări AI"
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-muted hover:bg-surface-2 hover:text-ink transition-colors"
            >
              <Settings2 className="w-5 h-5 shrink-0" />
              <span className="hidden lg:block">Stare AI</span>
            </button>
          </div>
        </nav>

        {/* Mobile Slide-over Navigation Drawer */}
        {mobileMenuOpen && (
          <div className="fixed inset-0 z-50 md:hidden">
            <div
              className="fixed inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
              onClick={() => setMobileMenuOpen(false)}
              aria-hidden="true"
            />
            <div className="fixed inset-y-0 left-0 w-72 max-w-[85vw] bg-surface shadow-pop flex flex-col z-50 border-r border-line">
              {/* Drawer Header */}
              <div className="p-4 border-b border-line flex items-center justify-between">
                <div className="flex items-center gap-2.5 min-w-0">
                  <BrandMark className="w-8 h-8 shrink-0" />
                  <div className="min-w-0">
                    <h2 className="font-serif font-semibold text-base text-ink leading-tight">Bransk</h2>
                    <p className="text-[11px] text-muted truncate max-w-[170px]">
                      {projectTitle || 'Cercetare științifică'}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setMobileMenuOpen(false)}
                  className="icon-btn"
                  aria-label="Închide meniul"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Drawer Navigation List */}
              <div className="flex-1 p-3 space-y-0.5 overflow-y-auto">
                <button
                  onClick={() => { changeView('project'); setMobileMenuOpen(false); }}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-semibold transition-colors ${
                    view === 'project'
                      ? 'bg-accent-soft text-accent-strong'
                      : 'text-ink/80 hover:bg-surface-2 hover:text-ink'
                  }`}
                >
                  <GraduationCap className="w-5 h-5 shrink-0" />
                  <span>Proiectul meu (teză)</span>
                </button>

                <div className="my-2 border-t border-line" />

                {navItems.map(item => (
                  <button
                    key={item.id}
                    onClick={() => {
                      if (item.id === 'entries') setSelectedEntry(null)
                      changeView(item.id)
                      setMobileMenuOpen(false)
                    }}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                      view === item.id
                        ? 'bg-accent-soft text-accent-strong font-semibold'
                        : 'text-muted hover:bg-surface-2 hover:text-ink'
                    }`}
                  >
                    <item.icon className="w-5 h-5 shrink-0" />
                    <span>{item.label}</span>
                  </button>
                ))}
              </div>

              {/* Drawer Footer */}
              <div className="p-3 border-t border-line space-y-0.5 bg-surface-2/40">
                <button
                  onClick={() => { setShowChat(true); setMobileMenuOpen(false); }}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-muted hover:bg-surface-2 hover:text-ink"
                >
                  <Bot className="w-5 h-5 text-accent shrink-0" />
                  <span>Chat AI</span>
                </button>
                <button
                  onClick={() => { setShowAiStatus(true); setMobileMenuOpen(false); }}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-muted hover:bg-surface-2 hover:text-ink"
                >
                  <Settings2 className="w-5 h-5 shrink-0" />
                  <span>Stare și setări AI</span>
                </button>
                <button
                  onClick={toggleDark}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-muted hover:bg-surface-2 hover:text-ink"
                >
                  {darkMode ? <Sun className="w-5 h-5 text-brass shrink-0" /> : <Moon className="w-5 h-5 text-accent shrink-0" />}
                  <span>{darkMode ? 'Treci pe mod luminos' : 'Treci pe mod întunecat'}</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Main content column */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden relative">
          {/* Mobile Top Header */}
          <header className="md:hidden sticky top-0 z-20 flex items-center justify-between px-3 h-14 bg-surface/90 backdrop-blur border-b border-line shrink-0">
            <div className="flex items-center gap-2 min-w-0">
              <button
                onClick={() => setMobileMenuOpen(true)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-accent-soft border border-accent/20 text-accent-strong font-semibold text-xs active:scale-95 transition-transform"
                aria-label="Deschide meniul principal"
              >
                <Menu className="w-4 h-4 shrink-0" />
                <span>Meniu</span>
              </button>
              <div className="flex items-center gap-1.5 min-w-0">
                <BrandMark className="w-4 h-4 shrink-0" />
                <span className="font-serif text-[13px] font-semibold text-ink shrink-0">Bransk</span>
                <span className="text-muted/60">·</span>
                <span className="text-xs font-medium text-muted truncate">{VIEW_LABELS[view]}</span>
              </div>
            </div>
            <div className="flex items-center gap-0.5 shrink-0">
              <button
                onClick={() => setShowChat(!showChat)}
                className={`p-2 rounded-lg text-xs font-medium transition-colors ${
                  showChat ? 'bg-accent-soft text-accent-strong' : 'text-muted hover:bg-surface-2 hover:text-ink'
                }`}
                title="Chat AI"
                aria-label="Chat AI"
              >
                <Bot className="w-5 h-5" />
              </button>
              <button
                onClick={toggleDark}
                className="p-2 rounded-lg text-muted hover:bg-surface-2 hover:text-ink transition-colors"
                title={darkMode ? 'Tema luminoasă' : 'Tema întunecată'}
                aria-label="Schimbă tema"
              >
                {darkMode ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
              </button>
            </div>
          </header>

          {/* Main content scroll area */}
          <main className="flex-1 min-w-0 overflow-auto relative bg-canvas pb-16 md:pb-0">
            <div className="mx-auto w-full max-w-[1500px] p-3 sm:p-5 lg:p-7">
              <Suspense fallback={<ViewLoading />}>
              {view === 'project' && (
                <ProjectView
                  onSelectEntry={handleSelectStudyById}
                  onOpenSource={handleOpenStudySource}
                  onTitleChange={setProjectTitle}
                  focusClaim={projectFocus}
                />
              )}
              {view === 'timeline' && (
                <TimelineView
                  onSelectEntry={handleSelectTimelineEntry}
                  onCreateEntry={() => setShowEntryForm(true)}
                />
              )}
              {view === 'graph' && (
                <GraphView
                  onSelectEntry={handleSelectTimelineEntry}
                />
              )}
              {view === 'studies' && (
                <StudiesView onSelectEntry={handleSelectStudy} />
              )}
              {view === 'theories' && (
                <TheoriesView onSelectEntry={handleSelectStudy} />
              )}
              {view === 'entries' && (
                <div className="space-y-4">
                  {!selectedEntry && (
                    <div className="flex items-center justify-between">
                      <h2 className="text-xl font-bold">Intrări</h2>
                      <button onClick={() => setShowEntryForm(true)} className="btn-primary">
                        <Plus className="w-4 h-4" /> Intrare nouă
                      </button>
                    </div>
                  )}
                  {loadingDetail ? (
                    <div className="text-center py-12 text-gray-500">Se încarcă detaliile intrării…</div>
                  ) : selectedEntry ? (
                    <EntryDetail
                      entry={selectedEntry}
                      onBack={handleBackFromDetail}
                      backLabel={previousView ? `← Înapoi la ${VIEW_LABELS[previousView] || 'vederea anterioară'}` : '← Înapoi la listă'}
                      onUpdate={(e) => setSelectedEntry(e)}
                      onSelectEntry={handleSelectStudy}
                      onOpenProjectClaim={handleOpenProjectClaim}
                      initialPdfSource={pendingPdfSource}
                      onPdfSourceConsumed={() => setPendingPdfSource(null)}
                    />
                  ) : (
                    <EntryList onSelect={setSelectedEntry} />
                  )}
                </div>
              )}
              {view === 'review' && <ReviewPanel />}
              {view === 'todos' && <TodoList onSelectEntry={handleSelectStudy} />}
              {view === 'thoughts' && <ThoughtsView onSelectEntry={handleSelectStudy} />}
              </Suspense>
            </div>
          </main>

          {/* Mobile Bottom Navigation Bar */}
          <nav className="md:hidden fixed bottom-0 left-0 right-0 z-20 h-14 bg-surface/95 backdrop-blur border-t border-line flex items-center justify-around px-1">
            <button
              onClick={() => changeView('project')}
              className={`flex flex-col items-center justify-center flex-1 py-1 text-[10px] font-medium transition-colors ${
                view === 'project' ? 'text-accent-strong font-bold' : 'text-muted'
              }`}
            >
              <GraduationCap className="w-5 h-5 mb-0.5" />
              <span>Proiect</span>
            </button>
            <button
              onClick={() => changeView('studies')}
              className={`flex flex-col items-center justify-center flex-1 py-1 text-[10px] font-medium transition-colors ${
                view === 'studies' ? 'text-accent-strong font-bold' : 'text-muted'
              }`}
            >
              <BookOpenText className="w-5 h-5 mb-0.5" />
              <span>Studii</span>
            </button>
            <button
              onClick={() => changeView('graph')}
              className={`flex flex-col items-center justify-center flex-1 py-1 text-[10px] font-medium transition-colors ${
                view === 'graph' ? 'text-accent-strong font-bold' : 'text-muted'
              }`}
            >
              <Network className="w-5 h-5 mb-0.5" />
              <span>Graf</span>
            </button>
            <button
              onClick={() => changeView('timeline')}
              className={`flex flex-col items-center justify-center flex-1 py-1 text-[10px] font-medium transition-colors ${
                view === 'timeline' ? 'text-accent-strong font-bold' : 'text-muted'
              }`}
            >
              <CalendarDays className="w-5 h-5 mb-0.5" />
              <span>Timp</span>
            </button>
            <button
              onClick={() => setMobileMenuOpen(true)}
              className="flex flex-col items-center justify-center flex-1 py-1 text-[10px] font-medium text-muted active:text-accent"
            >
              <Menu className="w-5 h-5 mb-0.5" />
              <span>Meniu</span>
            </button>
          </nav>
        </div>

        {/* AI Chat Panel. Below `sm` it is a full-screen overlay with a tap
            backdrop; from `sm` up it is the same right-hand column as before. */}
        {showChat && (
          <>
            <div
              className="fixed inset-0 z-30 bg-black/50 sm:hidden"
              onClick={() => setShowChat(false)}
              aria-hidden="true"
            />
            <aside className="fixed inset-0 z-40 w-full pt-12 sm:pt-0 border-l border-line bg-surface flex flex-col sm:static sm:z-auto sm:w-[22rem] lg:w-[30rem] xl:w-[34rem] sm:shrink-0">
              <Suspense fallback={<div className="p-4"><ViewLoading /></div>}>
                <ChatPanel onClose={() => setShowChat(false)} />
              </Suspense>
            </aside>
          </>
        )}
      </div>

      {/* Entry Form Modal */}
      <Suspense fallback={null}>
        {showEntryForm && (
          <EntryForm
            onClose={() => setShowEntryForm(false)}
            onSaved={(entry) => {
              setShowEntryForm(false)
              setSelectedEntry(entry)
              setView('entries')
            }}
          />
        )}

        {showAiStatus && <AiStatusModal onClose={() => setShowAiStatus(false)} />}
      </Suspense>
    </div>
  )
}
