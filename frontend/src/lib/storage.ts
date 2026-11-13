// Why this exists: without an explicit persistent-storage grant, Chromium
// treats the Workbox PDF cache and the IndexedDB offline annotations as
// best-effort and may evict them under storage pressure — silently
// destroying offline notes/PDFs. Requesting persistence once at startup
// marks this origin's storage as persistent so the browser keeps it.

/** localStorage flag so the persist prompt is attempted at most once. */
export const STORAGE_PERSIST_FLAG_KEY = 'chronokg:storage-persist-requested'

function markPersistAttempted(): void {
  try {
    localStorage.setItem(STORAGE_PERSIST_FLAG_KEY, '1')
  } catch {
    // Private mode / disabled storage: the attempt just won't be remembered.
  }
}

function wasPersistAttempted(): boolean {
  try {
    return localStorage.getItem(STORAGE_PERSIST_FLAG_KEY) !== null
  } catch {
    return false
  }
}

/**
 * Ask the browser to treat this origin's storage (Workbox PDF cache +
 * IndexedDB annotations) as persistent. Safe to call at startup: it
 * feature-detects, never throws, never blocks rendering (fire-and-forget),
 * skips the prompt when storage is already persistent, and only attempts
 * the prompt once. Returns true when storage is persistent.
 */
export async function ensurePersistentStorage(): Promise<boolean> {
  try {
    if (typeof navigator === 'undefined') return false
    const storage = navigator.storage
    if (!storage || typeof storage.persist !== 'function') return false
    if (typeof storage.persisted === 'function') {
      try {
        if (await storage.persisted()) {
          markPersistAttempted()
          return true
        }
      } catch {
        // Fall through to the persist() attempt below.
      }
    }
    // Do not spam the user with repeated prompts.
    if (wasPersistAttempted()) return false
    const persistent = await storage.persist()
    markPersistAttempted()
    // eslint-disable-next-line no-console
    console.debug(`[storage] persistent storage ${persistent ? 'granted' : 'not granted'}`)
    return persistent
  } catch {
    return false
  }
}
