import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { BookOpen, FileCheck2, FileText, FileX2, Library, Plus, Copy, BookMarked, CheckCircle2 } from 'lucide-react'
import { EntryList } from './EntryList'
import { DuplicatesModal } from './DuplicatesModal'

const StudiesView = lazy(() => import('./StudiesView').then(m => ({ default: m.StudiesView })))
import {
  filterForLibraryTab,
  isLibraryFilter,
  LIBRARY_FILTER_LABELS,
  LIBRARY_FILTERS,
  tabForLibraryFilter,
  type LibraryFilter,
  type LibraryTab,
} from '../lib/navigation'
import {
  LIBRARY_FILTER_KEY,
  readStoredString,
  restoreLibraryScroll,
  writeStored,
} from '../lib/libraryState'
import type { Entry } from '../lib/types'

interface Props {
  activeTab: LibraryTab
  onTabChange: (tab: LibraryTab) => void
  onSelectEntry: (entry: Entry) => void
  onCreateEntry: () => void
}

const FILTER_ICONS: Record<LibraryFilter, typeof Library> = {
  all: Library,
  studies: BookOpen,
  notes: FileText,
  pdf: FileCheck2,
  nopdf: FileX2,
}

/**
 * „Bibliotecă” — one screen over everything saved. Studies, course notes and
 * ideas are facets of the same entry table, so they are filters here, not two
 * separate libraries the user has to know about.
 */
export function LibraryView({ activeTab, onTabChange, onSelectEntry, onCreateEntry }: Props) {
  const [filter, setFilter] = useState<LibraryFilter>(() => {
    const stored = readStoredString(LIBRARY_FILTER_KEY)
    return isLibraryFilter(stored) ? stored : filterForLibraryTab(activeTab)
  })
  // Distinguish our own tab change (the filter drives it) from external
  // navigation (nav button, deep link, browser back), which must reset it.
  const pendingTab = useRef<LibraryTab | null>(null)
  const lastTab = useRef<LibraryTab>(activeTab)

  useEffect(() => {
    writeStored(LIBRARY_FILTER_KEY, filter)
  }, [filter])

  useEffect(() => {
    if (lastTab.current === activeTab) return
    lastTab.current = activeTab
    if (pendingTab.current === activeTab) {
      pendingTab.current = null
      return
    }
    setFilter(filterForLibraryTab(activeTab))
  }, [activeTab])

  useEffect(() => restoreLibraryScroll(), [])

  // Facete rapide peste lista de studii: „Cărți” și „Citite”.
  const [quickFacet, setQuickFacet] = useState<'' | 'books' | 'read'>(() => {
    const stored = readStoredString('chronokg-library-quickfacet')
    return stored === 'books' || stored === 'read' ? stored : ''
  })
  const [duplicatesOpen, setDuplicatesOpen] = useState(false)
  useEffect(() => { writeStored('chronokg-library-quickfacet', quickFacet) }, [quickFacet])

  const showingStudies = filter === 'studies' || filter === 'pdf' || filter === 'nopdf' || quickFacet !== ''
  const pdfFilter = filter === 'pdf' ? 'with' : filter === 'nopdf' ? 'without' : 'all'
  // Mount a pane the first time its filter is opened, then keep it mounted so
  // switching filters never discards its query, page or scroll. This also
  // avoids fetching the notes list for a user who only works with studies.
  const [mounted, setMounted] = useState(() => ({
    studies: showingStudies,
    entries: !showingStudies,
  }))
  useEffect(() => {
    setMounted(prev => showingStudies
      ? (prev.studies ? prev : { ...prev, studies: true })
      : (prev.entries ? prev : { ...prev, entries: true }))
  }, [showingStudies])

  const selectFilter = (next: LibraryFilter) => {
    if (next === filter) return
    setFilter(next)
    const tab = tabForLibraryFilter(next)
    if (tab === activeTab) return
    pendingTab.current = tab
    onTabChange(tab)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-bold">Bibliotecă</h2>
          <p className="text-sm text-gray-500">
            Studii, notițe și idei — toate într-un singur loc.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 shrink-0">
          <button
            onClick={() => setDuplicatesOpen(true)}
            className="hidden sm:inline-flex btn-secondary text-xs sm:text-sm whitespace-nowrap items-center gap-1.5"
            title="Scanează biblioteca după duplicate (DOI / ISBN / hash PDF / titlu)"
          >
            <Copy className="w-4 h-4" /> Verifică duplicate
          </button>
          <button onClick={onCreateEntry} className="hidden sm:inline-flex btn-primary text-xs sm:text-sm whitespace-nowrap items-center gap-1.5">
            <Plus className="w-4 h-4" /> Intrare nouă
          </button>
        </div>
      </div>

      <div
        role="tablist"
        aria-label="Filtre bibliotecă"
        className="flex flex-nowrap gap-1.5 overflow-x-auto sm:flex-wrap sm:overflow-visible rounded-xl border border-line bg-surface-2/60 p-1"
      >
        {LIBRARY_FILTERS.map(id => {
          const Icon = FILTER_ICONS[id]
          const active = filter === id
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => selectFilter(id)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 min-h-[40px] text-sm font-medium transition-colors ${
                active
                  ? 'bg-surface text-accent-strong shadow-sm font-semibold'
                  : 'text-muted hover:bg-surface/70 hover:text-ink'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{LIBRARY_FILTER_LABELS[id]}</span>
            </button>
          )
        })}
        <button
          type="button"
          role="tab"
          aria-selected={quickFacet === 'books'}
          onClick={() => setQuickFacet(prev => prev === 'books' ? '' : 'books')}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 min-h-[40px] text-sm font-medium transition-colors ${
            quickFacet === 'books'
              ? 'bg-surface text-accent-strong shadow-sm font-semibold'
              : 'text-muted hover:bg-surface/70 hover:text-ink'
          }`}
          title="Doar cărți (study_type = book)"
        >
          <BookMarked className="w-4 h-4" />
          <span>Cărți</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={quickFacet === 'read'}
          onClick={() => setQuickFacet(prev => prev === 'read' ? '' : 'read')}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 min-h-[40px] text-sm font-medium transition-colors ${
            quickFacet === 'read'
              ? 'bg-surface text-accent-strong shadow-sm font-semibold'
              : 'text-muted hover:bg-surface/70 hover:text-ink'
          }`}
          title="Doar studii citite / adnotate"
        >
          <CheckCircle2 className="w-4 h-4" />
          <span>Citite</span>
        </button>
      </div>

      {/*
        Panes stay mounted once opened, so scroll and pane-local filters are
        not lost while switching between the study and PDF facets (the
        all↔notes switch deliberately starts again at page 1).
      */}
      {(mounted.studies || showingStudies) && (
        <div hidden={!showingStudies}>
          <Suspense fallback={<div className="py-8 text-center text-xs text-muted">Se încarcă studiile…</div>}>
            <StudiesView
              onSelectEntry={onSelectEntry}
              pdfFilter={pdfFilter}
              typePreset={quickFacet === 'books' ? 'book' : undefined}
              readPreset={quickFacet === 'read' ? 'read' : undefined}
              onCreateEntry={onCreateEntry}
              onCheckDuplicates={() => setDuplicatesOpen(true)}
            />
          </Suspense>
        </div>
      )}
      {(mounted.entries || !showingStudies) && (
        <div hidden={showingStudies}>
          <EntryList
            onSelect={onSelectEntry}
            mode={filter === 'notes' ? 'notes' : 'all'}
          />
        </div>
      )}
      <DuplicatesModal
        isOpen={duplicatesOpen}
        onClose={() => setDuplicatesOpen(false)}
      />
    </div>
  )
}
