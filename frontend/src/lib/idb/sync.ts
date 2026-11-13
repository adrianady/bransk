import { getDb, getPendingNotes, getPendingHighlights, enqueueSync, deleteSyncItem, putNote, putHighlight, deleteNote, deleteHighlight, type OfflineNote, type OfflineHighlight } from './schema'
import { api, setNetworkFailureListener } from '../api'

const STATUS_SYNCED = 'synced' as const
const STATUS_PENDING = 'pending' as const
const STATUS_CONFLICT = 'conflict' as const
const STATUS_ERROR = 'error' as const

const MAX_RETRIES = 3
const RETRY_DELAY_MS = 5000

let syncInProgress = false

export function isSyncInProgress(): boolean {
  return syncInProgress
}

// --- Server reachability ---------------------------------------------------
// The device can be "online" per navigator.onLine (it has internet) while our
// server is still unreachable — e.g. the phone's Tailscale is turned off. The
// sync layer therefore tracks *server* reachability with a cheap probe of the
// unauthenticated /api/health endpoint and skips pushes while it is down.

const REACH_TTL_MS = 30_000
const REACH_RETRY_MS = 5_000  // probe aggressively while "down" to detect recovery fast
const HEALTH_TIMEOUT_MS = 4_000

let reachState: boolean | null = null // null = not probed yet
let reachCheckedAt = 0
let reachListeners: Array<(reachable: boolean | null) => void> = []

function reachTtl(): number {
  // When we believe the server is down, re-probe soon so a reconnect (e.g.
  // Tailscale back on) is detected within seconds, not after a full 30s cache.
  return reachState === false ? REACH_RETRY_MS : REACH_TTL_MS
}

function notifyReachability(): void {
  for (const cb of reachListeners) {
    try { cb(reachState) } catch { /* listener errors never break sync */ }
  }
}

export function getServerStatus(): { reachable: boolean | null; checkedAt: number } {
  return { reachable: reachState, checkedAt: reachCheckedAt }
}

export function subscribeServerStatus(cb: (reachable: boolean | null) => void): () => void {
  reachListeners.push(cb)
  return () => { reachListeners = reachListeners.filter(l => l !== cb) }
}

/** Cached check; fires the probe only when the last result is stale. */
export async function isServerReachable(): Promise<boolean> {
  if (reachState !== null && Date.now() - reachCheckedAt < reachTtl()) {
    // Late subscribers (e.g. the PDF modal just mounted) must still learn the
    // current state — otherwise the badge stays "checking" until the TTL lapses.
    notifyReachability()
    return reachState
  }
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS)
    const res = await fetch('/api/health', { cache: 'no-store', signal: controller.signal })
    clearTimeout(timer)
    reachState = res.ok
  } catch {
    reachState = false
  }
  reachCheckedAt = Date.now()
  notifyReachability()
  return reachState
}

/** Called by the API layer when a request fails at the network level. */
export function noteServerUnreachable(): void {
  if (reachState === false && Date.now() - reachCheckedAt < reachTtl()) return
  reachState = false
  reachCheckedAt = Date.now()
  notifyReachability()
}

// --- Push scheduling ---------------------------------------------------------
// Every local write schedules a push a moment later. While the server is
// unreachable the push is a no-op and items stay dirty for the periodic sweep.
let pushTimer: ReturnType<typeof setTimeout> | null = null

export function schedulePush(delayMs = 1500): void {
  if (pushTimer) clearTimeout(pushTimer)
  pushTimer = setTimeout(() => {
    pushTimer = null
    void pushPending()
  }, delayMs)
}

async function pushPending(): Promise<void> {
  // navigator.onLine is unreliable for our case (can stay false after network
  // toggles, or stay true while the server is unreachable). Reachability of
  // *our* server is the only gate that matters.
  if (!(await isServerReachable())) return
  const result = await processSyncQueue().catch(() => ({ synced: 0, failed: 0, conflicts: 0 }))
  if (result.synced > 0) {
    markLastSync()
    try { window.dispatchEvent(new CustomEvent('chronokg-synced', { detail: result })) } catch {}
  }
}

/**
 * Register background sync for notes and highlights.
 * Called when service worker is ready.
 */
export async function registerBackgroundSync(): Promise<void> {
  if ('serviceWorker' in navigator && 'SyncManager' in window) {
    try {
      const registration = await navigator.serviceWorker.ready
      await registration.sync.register('sync-notes')
      await registration.sync.register('sync-highlights')
    } catch (err) {
      console.warn('Background sync registration failed:', err)
    }
  }
}

let offlineSyncInitialized = false

/**
 * Device-level sync triggers — the mechanism that makes "offline then
 * reconnect → automatic push" work no matter which screen is open:
 *  - 'online' event          → push everything that piled up offline
 *  - periodic sweep (30 s)   → catches our server becoming reachable again
 *                              while the browser still reports online —
 *                              e.g. the phone re-enables Tailscale
 *  - tab refocus (visible)   → catch-up push (covers flaky Wi-Fi where
 *                              'online' fires late or not at all)
 *  - app startup, if online  → push items saved during a previous offline run
 */
export function initOfflineSync(): void {
  if (offlineSyncInitialized || typeof window === 'undefined') return
  offlineSyncInitialized = true

  const sweep = () => { void pushPending() }

  window.addEventListener('online', sweep)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') sweep()
  })
  // API layer reports network-level failures → mark unreachable, retry soon.
  setNetworkFailureListener(() => {
    noteServerUnreachable()
    schedulePush(5_000)
  })
  // The periodic sweep is what detects server reachability changes while the
  // app stays open: browser events alone never fire for "Tailscale is back".
  setInterval(sweep, 30_000)

  void registerBackgroundSync()
  // App just (re)opened: catch up on anything saved while offline earlier.
  sweep()
}

/**
 * Process all pending notes and highlights.
 * Returns { synced: number, failed: number, conflicts: number }
 */
export async function processSyncQueue(): Promise<{ synced: number; failed: number; conflicts: number }> {
  if (syncInProgress) return { synced: 0, failed: 0, conflicts: 0 }
  // Server unreachable (e.g. the phone's Tailscale is off): leave every item
  // untouched instead of marking errors, and retry on the next sweep.
  if (!(await isServerReachable())) return { synced: 0, failed: 0, conflicts: 0 }
  syncInProgress = true

  let synced = 0
  let failed = 0
  let conflicts = 0

  try {
    // Sync notes
    const pendingNotes = await getPendingNotes()
    for (const note of pendingNotes) {
      try {
        if (note.status === STATUS_CONFLICT) {
          conflicts++
          continue // Conflicts need manual resolution
        }

        // Deleted while offline: remove on the server, then drop locally.
        if (note.pendingDelete && note.serverId) {
          await api.studies.notes.delete(note.serverId)
          await deleteNote(note.id)
          synced++
          continue
        }

        if (note.serverId) {
          // Update existing note on server
          await api.studies.notes.update(note.serverId, {
            content: note.content,
            course_context: note.courseContext,
            page_number: note.pageNumber,
            source_document_ids: note.sourceDocumentIds,
          })
        } else {
          // Create new note on server, then adopt the server ID locally so
          // the next merge matches the server list by the same key.
          const created = await api.studies.notes.create(note.entryId, {
            content: note.content,
            course_context: note.courseContext,
            page_number: note.pageNumber,
            source_document_ids: note.sourceDocumentIds,
          })
          const localId = note.id
          note.id = created.id
          note.serverId = created.id
          await deleteNote(localId)
        }

        note.status = STATUS_SYNCED
        note.dirty = false
        note.updatedAt = new Date().toISOString()
        await putNote(note)
        synced++
      } catch (err) {
        note.status = STATUS_ERROR
        await putNote(note)
        failed++
        console.error('Note sync failed:', err)
      }
    }

    // Sync highlights
    const pendingHighlights = await getPendingHighlights()
    for (const hl of pendingHighlights) {
      try {
        if (hl.status === STATUS_CONFLICT) {
          conflicts++
          continue
        }

        // Deleted while offline: remove on the server, then drop locally.
        if (hl.pendingDelete && hl.serverId) {
          await api.highlights.remove(hl.serverId)
          await deleteHighlight(hl.id)
          synced++
          continue
        }

        if (hl.serverId) {
          // Highlights are append-only; updates only for note field
          await api.highlights.update(hl.serverId, { note: hl.note })
        } else {
          // Create on the server and adopt its ID (same rationale as notes).
          const created = await api.highlights.create(hl.entryId, {
            quote: hl.quote,
            note: hl.note,
            page_number: hl.pageNumber,
            document_id: hl.documentId,
          })
          const localId = hl.id
          hl.id = created.id
          hl.serverId = created.id
          await deleteHighlight(localId)
        }

        hl.status = STATUS_SYNCED
        hl.dirty = false
        await putHighlight(hl)
        synced++
      } catch (err) {
        hl.status = STATUS_ERROR
        await putHighlight(hl)
        failed++
        console.error('Highlight sync failed:', err)
      }
    }
  } finally {
    syncInProgress = false
  }

  return { synced, failed, conflicts }
}

/**
 * Sync handler called by Service Worker background sync event.
 */
export async function handleBackgroundSync(tag: 'sync-notes' | 'sync-highlights'): Promise<void> {
  await processSyncQueue()
}

/**
 * Get sync status for UI indicators.
 */
export async function getSyncStatus(): Promise<{
  pendingNotes: number
  pendingHighlights: number
  conflicts: number
  errors: number
  lastSync?: string
}> {
  const [notes, highlights] = await Promise.all([getPendingNotes(), getPendingHighlights()])
  const pendingNotes = notes.filter(n => n.dirty && n.status !== STATUS_CONFLICT).length
  const pendingHighlights = highlights.filter(h => h.dirty && h.status !== STATUS_CONFLICT).length
  const conflicts = notes.filter(n => n.status === STATUS_CONFLICT).length +
                   highlights.filter(h => h.status === STATUS_CONFLICT).length
  const errors = notes.filter(n => n.status === STATUS_ERROR).length +
                highlights.filter(h => h.status === STATUS_ERROR).length

  // Get last successful sync from localStorage
  const lastSync = localStorage.getItem('chronokg-last-sync') || undefined

  return { pendingNotes, pendingHighlights, conflicts, errors, lastSync }
}

/**
 * Mark last successful sync timestamp.
 */
export function markLastSync(): void {
  localStorage.setItem('chronokg-last-sync', new Date().toISOString())
}

/**
 * Conflict resolution strategies.
 */
export type ConflictStrategy = 'local' | 'server' | 'manual'

export interface ConflictResolution {
  strategy: ConflictStrategy
  mergedContent?: string
  mergedCourseContext?: string
  mergedPageNumber?: number | null
  mergedSourceDocumentIds?: number[]
}

/**
 * Resolve a note conflict using Last-Write-Wins with optional manual merge.
 */
export async function resolveNoteConflict(
  localNote: OfflineNote,
  serverNote: any // StudyNote from API
): Promise<ConflictResolution> {
  const localTime = new Date(localNote.updatedAt).getTime()
  const serverTime = new Date(serverNote.updated_at).getTime()

  // If timestamps are very close (within 2 seconds), treat as concurrent
  if (Math.abs(localTime - serverTime) < 2000) {
    // Content diff check
    const sameContent =
      localNote.content === serverNote.content &&
      localNote.courseContext === serverNote.course_context &&
      localNote.pageNumber === serverNote.page_number
    if (sameContent) {
      return { strategy: 'local' } // No real conflict
    }
    return { strategy: 'manual' }
  }

  if (localTime > serverTime) {
    return { strategy: 'local' }
  }
  return { strategy: 'server' }
}

/**
 * Apply conflict resolution and update local note.
 */
export async function applyConflictResolution(
  localNote: OfflineNote,
  serverNote: any,
  resolution: ConflictResolution
): Promise<void> {
  if (resolution.strategy === 'local') {
    // Push local version to server
    await api.studies.notes.update(serverNote.id, {
      content: localNote.content,
      course_context: localNote.courseContext,
      page_number: localNote.pageNumber,
      source_document_ids: localNote.sourceDocumentIds,
    })
    localNote.status = STATUS_SYNCED
    localNote.dirty = false
    localNote.updatedAt = new Date().toISOString()
    await putNote(localNote)
  } else if (resolution.strategy === 'server') {
    // Accept server version
    localNote.content = serverNote.content
    localNote.courseContext = serverNote.course_context
    localNote.pageNumber = serverNote.page_number ?? null
    localNote.sourceDocumentIds = serverNote.sources?.map((s: any) => s.document_id) ?? []
    localNote.serverId = serverNote.id
    localNote.status = STATUS_SYNCED
    localNote.dirty = false
    localNote.updatedAt = new Date().toISOString()
    await putNote(localNote)
  } else if (resolution.strategy === 'manual' && resolution.mergedContent) {
    // Apply manual merge
    localNote.content = resolution.mergedContent
    localNote.courseContext = resolution.mergedCourseContext ?? localNote.courseContext
    localNote.pageNumber = resolution.mergedPageNumber ?? localNote.pageNumber
    localNote.sourceDocumentIds = resolution.mergedSourceDocumentIds ?? localNote.sourceDocumentIds
    localNote.status = STATUS_PENDING
    localNote.dirty = true
    localNote.updatedAt = new Date().toISOString()
    await putNote(localNote)
    // Will be pushed on next sync
  }
}

/**
 * Retry failed sync items (exponential backoff).
 */
export async function retryFailedSync(): Promise<void> {
  const db = await getDb()
  const pendingNotes = await db.getAllFromIndex('notes', 'byStatus', STATUS_ERROR)
  const pendingHighlights = await db.getAllFromIndex('highlights', 'byStatus', STATUS_ERROR)

  for (const note of pendingNotes) {
    note.status = STATUS_PENDING
    await putNote(note)
  }
  for (const hl of pendingHighlights) {
    hl.status = STATUS_PENDING
    await putHighlight(hl)
  }

  await processSyncQueue()
}

/**
 * Clear all local data (for testing or logout).
 */
export async function clearOfflineData(): Promise<void> {
  const db = await getDb()
  await db.clear('notes')
  await db.clear('highlights')
  await db.clear('pdfs')
  await db.clear('syncQueue')
  localStorage.removeItem('chronokg-last-sync')
}