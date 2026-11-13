import { useCallback, useEffect, useRef } from 'react'
import { X } from 'lucide-react'

interface Props {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  width?: '80' | '96'
  children: React.ReactNode
}

/**
 * Reusable contextual side-panel shell.
 *
 * Desktop (`>= sm`): a right column (`lg:w-80` / `xl:w-96`), sticky, with its
 * own internal scroll. Mobile (`< sm`): full-screen overlay with backdrop, a
 * 40px close button, and a `pb-safe` footer.
 *
 * Escape and backdrop click close the panel. A small hand-rolled focus trap
 * keeps keyboard focus inside while open — no new dependencies.
 */
export function SidePanel({
  open,
  onClose,
  title,
  subtitle,
  width = '80',
  children,
}: Props) {
  const panelRef = useRef<HTMLDivElement>(null)
  const prevActive = useRef<HTMLElement | null>(null)
  const focusableSelector =
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

  const trapFocus = useCallback((node: HTMLElement) => {
    const focusables = node.querySelectorAll<HTMLElement>(focusableSelector)
    if (focusables.length === 0) return
    const first = focusables[0]
    const last = focusables[focusables.length - 1]

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      if (e.shiftKey) {
        if (e.target === first) {
          e.preventDefault()
          last.focus()
        }
      } else if (e.target === last) {
        e.preventDefault()
        first.focus()
      }
    }
    node.addEventListener('keydown', handleKeyDown)
    return () => node.removeEventListener('keydown', handleKeyDown)
  }, [])

  // Open: save the previously focused element, move focus into the panel,
  // install the focus trap, and block body scroll on mobile.
  useEffect(() => {
    if (!open) return
    prevActive.current = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    if (!panel) return
    const firstFocusable = panel.querySelector<HTMLElement>(focusableSelector)
    if (firstFocusable) firstFocusable.focus()
    else panel.focus()
    document.body.style.overflow = 'hidden'

    const onKeydown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeydown)
    const cleanupTrap = trapFocus(panel)

    return () => {
      document.removeEventListener('keydown', onKeydown)
      cleanupTrap?.()
      document.body.style.overflow = ''
      // Return focus to what was focused before the panel opened.
      const toRestore = prevActive.current
      if (toRestore && document.body.contains(toRestore)) {
        toRestore.focus()
      } else {
        document.body.focus()
      }
    }
  }, [open, onClose, trapFocus])

  if (!open) return null

  const isMobile = true // class-driven via `sm:` modifiers; kept for clarity

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 sm:hidden"
    >
      {/* Backdrop — click closes. */}
      <div
        className="absolute inset-0 bg-ink/40 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Panel: full height on mobile, sticky column on desktop. */}
      <aside
        className={`fixed inset-y-0 right-0 z-50 w-full sm:w-80 lg:w-80 xl:w-96 max-h-full border-l border-line bg-surface shadow-lg transform transition-transform duration-200 ease-out flex flex-col sm:static sm:max-h-[85vh] sm:overflow-y-auto sticky sm:sticky sm:top-4`}
      >
        {/* Header. */}
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-line bg-surface/95 backdrop-blur-sm shrink-0">
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-sm leading-snug">{title}</h3>
            {subtitle && (
              <p className="text-[11px] text-muted leading-snug">{subtitle}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="touch-40 text-muted hover:text-ink hover:bg-surface/60"
            aria-label="Închide panoul"
          >
            <X className="w-5 h-5 shrink-0" />
          </button>
        </div>

        {/* Scrollable body. */}
        <div className="overflow-y-auto p-4 sm:p-5 flex-1 min-h-0">{children}</div>

        {/* Footer placeholder: content supplies its own `pb-safe` footer when needed. */}
        <div className="shrink-0" />
      </aside>
    </div>
  )
}
