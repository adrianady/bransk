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
