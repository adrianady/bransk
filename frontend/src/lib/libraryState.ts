/**
 * Keeps the „Bibliotecă” screen's place while the user opens an entry (or a
 * PDF) and comes back. The app is a hash-routed single page, so navigating to
 * a detail used to remount the list and silently reset its page, search and
 * scroll. The state below lives in `sessionStorage`, which is exactly the
 * lifetime we want: one browser tab, cleared when it is closed.
 */
export const LIBRARY_FILTER_KEY = 'chronokg-library-filter'
export const LIBRARY_SEARCH_KEY = 'chronokg-library-search'
export const LIBRARY_PAGE_KEY = 'chronokg-library-page'
export const LIBRARY_TYPE_KEY = 'chronokg-library-type'
export const LIBRARY_QUALITY_KEY = 'chronokg-library-quality'
export const LIBRARY_YEAR_KEY = 'chronokg-library-year'
export const LIBRARY_PDF_KEY = 'chronokg-library-pdf'
export const LIBRARY_DOI_KEY = 'chronokg-library-doi'
export const LIBRARY_SORT_KEY = 'chronokg-library-sort'
export const LIBRARY_SCROLL_KEY = 'chronokg-library-scroll'

export function readStoredString(key: string, fallback = ''): string {
  try {
    return window.sessionStorage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

export function readStoredNumber(key: string, fallback = 0): number {
  const value = Number(readStoredString(key))
  return Number.isFinite(value) && value > 0 ? value : fallback
}

export function writeStored(key: string, value: string | number | boolean): void {
  try {
    window.sessionStorage.setItem(key, String(value))
  } catch {
    // Private windows can refuse storage; the app must keep working.
  }
}

/** The scroll container is the app's <main>, not the window. */
function scrollRoot(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-scroll-root]')
}

/** Set while the list is being replaced by the entry detail (or another view).
 *  The replacement resets <main>.scrollTop to 0 before the unmount cleanup
 *  runs; without this guard that synthetic scroll would overwrite the real
 *  offset and Back would always land at the top of the list. */
let suspended = false

/** Saves the list's current offset immediately. Called by App before it swaps
 *  the list out. */
export function suspendLibraryScroll(): void {
  const root = scrollRoot()
  if (root) writeStored(LIBRARY_SCROLL_KEY, root.scrollTop)
  suspended = true
}

/**
 * Re-applies the saved scroll offset once the (async) list has rendered.
 * A single requestAnimationFrame would clamp to the still-empty list, so the
 * offset is re-applied for a short window until the content is tall enough.
 */
export function restoreLibraryScroll(): () => void {
  suspended = false
  const root = scrollRoot()
  if (!root) return () => {}
  const saved = Number(readStoredString(LIBRARY_SCROLL_KEY, '0'))
  const onScroll = () => {
    if (suspended) return
    writeStored(LIBRARY_SCROLL_KEY, root.scrollTop)
  }
  root.addEventListener('scroll', onScroll, { passive: true })

  if (!Number.isFinite(saved) || saved <= 0) {
    return () => root.removeEventListener('scroll', onScroll)
  }

  let attempts = 0
  const apply = () => {
    // Only move the viewport ourselves while it is still at the place the
    // remount started from; a user scroll wins immediately.
    if (root.scrollTop === 0 && attempts < 40) root.scrollTop = saved
    attempts += 1
    if (attempts >= 40) window.clearInterval(timer)
  }
  apply()
  const timer = window.setInterval(apply, 50)

  return () => {
    window.clearInterval(timer)
    root.removeEventListener('scroll', onScroll)
    if (!suspended) writeStored(LIBRARY_SCROLL_KEY, root.scrollTop)
  }
}
