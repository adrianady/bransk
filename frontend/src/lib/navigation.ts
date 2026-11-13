export const VIEW_NAV_ITEMS = [
  { id: 'project', label: 'Proiectul meu', shortLabel: 'Proiect' },
  { id: 'timeline', label: 'Cronologie', shortLabel: 'Timp' },
  { id: 'graph', label: 'Graf', shortLabel: 'Graf' },
  { id: 'studies', label: 'Studii', shortLabel: 'Studii' },
  { id: 'theories', label: 'Teorii', shortLabel: 'Teorii' },
  { id: 'entries', label: 'Intrări', shortLabel: 'Intrări' },
  { id: 'review', label: 'Verificare', shortLabel: 'Revizie' },
  { id: 'todos', label: 'Sarcini', shortLabel: 'Sarcini' },
  { id: 'thoughts', label: 'Gânduri', shortLabel: 'Gânduri' },
] as const

export type View = (typeof VIEW_NAV_ITEMS)[number]['id']

export const VIEW_LABELS: Record<View, string> = Object.fromEntries(
  VIEW_NAV_ITEMS.map(item => [item.id, item.label]),
) as Record<View, string>
/** The 4 core primary pillars in the modern sidebar navigation */
export const PRIMARY_NAV_PILLARS = [
  { id: 'project', label: 'Proiectul meu', shortLabel: 'Proiect', defaultView: 'project' as View, views: ['project'] as const },
  { id: 'studies', label: 'Bibliotecă', shortLabel: 'Bibliotecă', defaultView: 'studies' as View, views: ['studies', 'entries'] as const },
  { id: 'graph', label: 'Graf & Cronologie', shortLabel: 'Graf', defaultView: 'graph' as View, views: ['graph', 'timeline'] as const },
  { id: 'theories', label: 'Cercetare & Concepte', shortLabel: 'Concepte', defaultView: 'theories' as View, views: ['theories', 'thoughts', 'todos', 'review'] as const },
] as const

export type PrimaryPillarId = (typeof PRIMARY_NAV_PILLARS)[number]['id']

export function pillarForView(view: View): PrimaryPillarId {
  if (view === 'studies' || view === 'entries') return 'studies'
  if (view === 'graph' || view === 'timeline') return 'graph'
  if (view === 'theories' || view === 'thoughts' || view === 'todos' || view === 'review') return 'theories'
  return 'project'
}

/** Four fast paths remain in the mobile bottom bar; every other view is in the drawer. */
export const MOBILE_QUICK_VIEW_IDS: readonly View[] = ['project', 'studies', 'graph', 'timeline']
export function parseViewHash(hash: string): View | null {
  const candidate = hash.replace(/^#/, '')
  return VIEW_NAV_ITEMS.some(item => item.id === candidate) ? candidate as View : null
}

/** Studies and entries are two tabs of one screen („Bibliotecă”) since
 * 2026-11: both are views over the same entry rows, so the navigation shows
 * a single item while the deep links (#studies / #entries) keep working as
 * tab presets. */
export const LIBRARY_TAB_VIEWS = ['studies', 'entries'] as const
export type LibraryTab = (typeof LIBRARY_TAB_VIEWS)[number]

export function isLibraryView(view: View): view is LibraryTab {
  return view === 'studies' || view === 'entries'
}

/** The unified library's filter pills. Studies, notes and PDF state are all
 * facets of the same entry rows, so they must not live in separate screens. */
export const LIBRARY_FILTERS = ['all', 'studies', 'notes', 'pdf', 'nopdf'] as const
export type LibraryFilter = (typeof LIBRARY_FILTERS)[number]

export const LIBRARY_FILTER_LABELS: Record<LibraryFilter, string> = {
  all: 'Toate intrările',
  studies: 'Studii academice',
  notes: 'Notițe & idei',
  pdf: 'Cu PDF',
  nopdf: 'Fără PDF',
}

export function isLibraryFilter(value: string | null): value is LibraryFilter {
  return value !== null && (LIBRARY_FILTERS as readonly string[]).includes(value)
}

export function filterForLibraryTab(tab: LibraryTab): LibraryFilter {
  return tab === 'entries' ? 'notes' : 'studies'
}

export function tabForLibraryFilter(filter: LibraryFilter): LibraryTab {
  return filter === 'all' || filter === 'notes' ? 'entries' : 'studies'
}
