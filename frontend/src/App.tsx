import { useCallback, useState, useEffect, lazy, Suspense } from 'react'
import {
  CalendarDays, Network, BookOpenText, Dna, FileText, RotateCcw, ListChecks, Lightbulb,
  Bot, Sun, Moon, Plus, Settings2,
  GraduationCap, Menu, X, LogOut,
} from 'lucide-react'
import { ProjectView } from './components/ProjectView'
import { CommandPalette } from './components/CommandPalette'
import { useAuthStatus } from './components/AuthGate'
import { isLibraryView } from './lib/navigation'
import { api, ApiError } from './lib/api'
import { useToast } from './components/Toast'
import type { Entry, TimelineEntry, Tag, StudySource, StudyQASource } from './lib/types'
import {
  MOBILE_QUICK_VIEW_IDS,
  parseViewHash,
  VIEW_LABELS,
  VIEW_NAV_ITEMS,
  type View,
} from './lib/navigation'

// Every view but the project page is split into its own chunk and fetched when
// it is first opened. Before this, one 533 kB entry file carried React and all
// twelve views together, so opening the app also downloaded the graph, the
// timeline and the chat it might never use.
const TimelineView = lazy(() => import('./components/Timeline').then(m => ({ default: m.TimelineView })))
const LibraryView = lazy(() => import('./components/LibraryView').then(m => ({ default: m.LibraryView })))
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

const VIEW_ICONS: Record<View, React.ComponentType<{ className?: string }>> = {
  project: GraduationCap,
  timeline: CalendarDays,
  graph: Network,
  studies: BookOpenText,
  theories: Dna,
  entries: FileText,
  review: RotateCcw,
  todos: ListChecks,
  thoughts: Lightbulb,
}

/** Mobile-only: logout lives in the drawer, so the session button can't
 *  cover the header controls the way the old fixed one did. */
function LogoutDrawerItem({ onNavigate }: { onNavigate: () => void }) {
  const auth = useAuthStatus()
  const [leaving, setLeaving] = useState(false)
  if (!auth.sessionAuthenticated) return null
  return (
    <button
      type="button"
      disabled={leaving}
      onClick={async () => {
        setLeaving(true)
        await auth.logout()
        onNavigate()
        setLeaving(false)
      }}
      className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-madder hover:bg-surface-2 transition-colors border-t border-line mt-2 pt-3"
      aria-label="Ieși din cont"
    >
      <LogOut className="w-5 h-5 shrink-0" />
      <span>Ieșire din cont</span>
    </button>
  )
}

export default function App() {
  const toast = useToast()
  const [view, setView] = useState<View>(() => parseViewHash(window.location.hash) ?? 'project')
  const [previousView, setPreviousView] = useState<View | null>(null)

  const changeView = useCallback((next: View) => {
    setView(next)
    window.location.hash = next
  }, [])

  useEffect(() => {
    const handleHash = () => {
      const next = parseViewHash(window.location.hash)
      if (next) setView(next)
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

  const selectNavView = (next: View) => {
    if (next === 'entries') setSelectedEntry(null)
    changeView(next)
  }

  // Ctrl/Cmd+K global search palette — reachable from every view.
  const [paletteOpen, setPaletteOpen] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen(open => !open)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className={`min-h-screen bg-canvas text-ink ${darkMode ? 'dark' : ''}`}>
      {/* overflow-hidden: the chat panel must never push the page sideways on
          a phone. The main column owns its own scrolling. */}
      <div className="flex h-screen overflow-hidden">
        {/* Desktop Sidebar (hidden on mobile) */}
        <nav aria-label="Navigare principală" className="hidden md:flex flex-col shrink-0 w-16 lg:w-52 bg-surface border-r border-line">
          <div className="p-3 lg:p-4 border-b border-line">
            <button
              onClick={() => changeView('project')}
              aria-label="Deschide pagina proiectului"
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

          <div className="flex-1 p-2 space-y-0.5 overflow-y-auto">
            {VIEW_NAV_ITEMS.map(item => {
              if (item.id === 'entries') return null // „Intrări” e tabul al doilea al Bibliotecii
              const label = item.id === 'studies' ? 'Bibliotecă' : item.label
              const active = item.id === 'studies' ? isLibraryView(view) : view === item.id
              const Icon = VIEW_ICONS[item.id]
              return (
                <button
                  key={item.id}
                  title={label}
                  aria-label={label}
                  aria-current={active ? 'page' : undefined}
                  onClick={() => selectNavView(item.id)}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                    active
                      ? 'bg-accent-soft text-accent-strong font-semibold'
                      : 'text-muted hover:bg-surface-2 hover:text-ink'
                  }`}
                >
                  <Icon className="w-5 h-5 shrink-0" />
                  <span className="hidden lg:block">{label}</span>
                </button>
              )
            })}
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
            <nav id="mobile-navigation" aria-label="Navigare principală" className="fixed inset-y-0 left-0 w-72 max-w-[85vw] bg-surface shadow-pop flex flex-col z-50 border-r border-line">
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
                {VIEW_NAV_ITEMS.map(item => {
                  if (item.id === 'entries') return null // „Intrări” e tabul al doilea al Bibliotecii
                  const label = item.id === 'studies' ? 'Bibliotecă' : item.label
                  const active = item.id === 'studies' ? isLibraryView(view) : view === item.id
                  const Icon = VIEW_ICONS[item.id]
                  return (
                    <button
                      key={item.id}
                      aria-label={label}
                      aria-current={active ? 'page' : undefined}
                      onClick={() => {
                        selectNavView(item.id)
                        setMobileMenuOpen(false)
                      }}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                        active
                          ? 'bg-accent-soft text-accent-strong font-semibold'
                          : 'text-muted hover:bg-surface-2 hover:text-ink'
                      }`}
                    >
                      <Icon className="w-5 h-5 shrink-0" />
                      <span>{label}</span>
                    </button>
                  )
                })}
                <LogoutDrawerItem onNavigate={() => setMobileMenuOpen(false)} />
              </div>
            </nav>
          </div>
        )}

        {/* Main content column */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden relative">
          {/* Mobile Top Header */}
          <header className="md:hidden sticky top-0 z-20 flex items-center justify-between px-3 h-14 bg-surface/90 backdrop-blur border-b border-line shrink-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <BrandMark className="w-4 h-4 shrink-0" />
              <span className="font-serif text-[13px] font-semibold text-ink shrink-0">Bransk</span>
              <span className="text-muted/60">·</span>
              <span className="text-xs font-medium text-muted truncate">{VIEW_LABELS[view]}</span>
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
                onClick={() => setShowAiStatus(true)}
                className="p-2 rounded-lg text-muted hover:bg-surface-2 hover:text-ink transition-colors"
                title="Stare și setări AI"
                aria-label="Stare și setări AI"
              >
                <Settings2 className="w-5 h-5" />
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
              {view === 'theories' && (
                <TheoriesView onSelectEntry={handleSelectStudy} />
              )}
              {isLibraryView(view) && (
                <LibraryView
                  activeTab={view}
                  onTabChange={changeView}
                  studiesPane={<StudiesView onSelectEntry={handleSelectStudy} />}
                  entriesPane={
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
                  }
                />
              )}
              {view === 'review' && <ReviewPanel />}
              {view === 'todos' && <TodoList onSelectEntry={handleSelectStudy} />}
              {view === 'thoughts' && <ThoughtsView onSelectEntry={handleSelectStudy} />}
              </Suspense>
            </div>
          </main>

          {/* Mobile Bottom Navigation Bar */}
          <nav aria-label="Navigare rapidă" className="md:hidden fixed bottom-0 left-0 right-0 z-20 h-14 bg-surface/95 backdrop-blur border-t border-line flex items-center justify-around px-1">
            {MOBILE_QUICK_VIEW_IDS.map(id => {
              const item = VIEW_NAV_ITEMS.find(navItem => navItem.id === id)!
              const Icon = VIEW_ICONS[id]
              const label = id === 'studies' ? 'Bibliotecă' : item.shortLabel
              const active = id === 'studies' ? isLibraryView(view) : view === id
              return (
                <button
                  key={id}
                  onClick={() => selectNavView(id)}
                  aria-current={active ? 'page' : undefined}
                  className={`flex flex-col items-center justify-center flex-1 py-1 text-[10px] font-medium transition-colors ${
                    active ? 'text-accent-strong font-bold' : 'text-muted'
                  }`}
                >
                  <Icon className="w-5 h-5 mb-0.5" />
                  <span>{label}</span>
                </button>
              )
            })}
            <button
              onClick={() => setMobileMenuOpen(true)}
              aria-expanded={mobileMenuOpen}
              aria-controls={mobileMenuOpen ? 'mobile-navigation' : undefined}
              aria-label="Deschide meniul principal"
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

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onSelectEntry={handleSelectStudyById}
      />
    </div>
  )
}
