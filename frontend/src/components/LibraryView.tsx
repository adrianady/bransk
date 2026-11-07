import { useEffect, useState, type ReactNode } from 'react'
import { BookOpen, FileText } from 'lucide-react'
import type { LibraryTab } from '../lib/navigation'

interface Props {
  activeTab: LibraryTab
  onTabChange: (tab: LibraryTab) => void
  studiesPane: ReactNode
  entriesPane: ReactNode
}

/**
 * „Bibliotecă” — the unified screen over everything saved: studies (the
 * research workbench: import, screening, PDFs) and entries (the knowledge
 * notes). One navigation item, two tabs; the deep links stay per-tab.
 */
export function LibraryView({ activeTab, onTabChange, studiesPane, entriesPane }: Props) {
  const [tab, setTab] = useState<LibraryTab>(activeTab)

  // Follow external navigation (hash links, back button, palette).
  useEffect(() => { setTab(activeTab) }, [activeTab])

  const switchTab = (next: LibraryTab) => {
    if (next === tab) return
    setTab(next)
    onTabChange?.(next)
  }

  const tabButton = (id: LibraryTab, label: string, Icon: typeof BookOpen) => (
    <button
      role="tab"
      aria-selected={tab === id}
      onClick={() => switchTab(id)}
      className={`flex-1 sm:flex-none px-4 py-1.5 rounded-lg text-sm font-medium inline-flex items-center justify-center gap-1.5 transition-colors ${
        tab === id
          ? 'bg-white dark:bg-gray-800 text-chrono-700 dark:text-chrono-300 shadow-sm font-semibold'
          : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
      }`}
    >
      <Icon className="w-4 h-4" />
      {label}
    </button>
  )

  return (
    <div className="space-y-3">
      <div
        className="inline-flex w-full sm:w-auto rounded-xl border border-gray-200 dark:border-gray-800 bg-gray-100 dark:bg-gray-900 p-1 gap-1 sticky top-14 z-10 sm:static sm:bg-gray-100/70 sm:dark:bg-gray-900/60"
        role="tablist"
        aria-label="Bibliotecă"
      >
        {tabButton('studies', 'Studii', BookOpen)}
        {tabButton('entries', 'Intrări', FileText)}
      </div>
      {tab === 'studies' ? studiesPane : entriesPane}
    </div>
  )
}
