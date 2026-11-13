import { openDB, DBSchema, IDBPDatabase } from 'idb'

/**
 * Offline-first IndexedDB schema for ChronoKG.
 * Stores: notes, highlights, cached PDFs, and sync queue.
 */

export interface OfflineNote {
  id: number                    // local ID (negative while pending)
  serverId?: number             // ID from server once synced
  entryId: number
  content: string
  courseContext: string
  pageNumber: number | null
  sourceDocumentIds: number[]
  createdAt: string             // ISO string
  updatedAt: string             // ISO string
  status: 'synced' | 'pending' | 'conflict' | 'error'
  dirty: boolean                // needs push to server
  pendingDelete?: boolean       // marked for deletion on next sync
}

export interface OfflineHighlight {
  id: number
  serverId?: number
  entryId: number
  quote: string
  note: string
  pageNumber: number | null
  documentId: number | null
  createdAt: string
  status: 'synced' | 'pending' | 'conflict' | 'error'
  dirty: boolean
  pendingDelete?: boolean       // marked for deletion on next sync
}

export interface CachedPdf {
  id: number                    // entryId
  blob: Blob
  url: string
  cachedAt: number              // timestamp
  etag?: string
  size: number
}

export interface SyncQueueItem {
  id: number
  type:
    | 'note-create'
    | 'note-update'
    | 'note-delete'
    | 'highlight-create'
    | 'highlight-update'
    | 'highlight-delete'
  payload: any
  timestamp: number
  retries: number
}

interface ChronoKGOfflineDB extends DBSchema {
  notes: {
    key: number
    value: OfflineNote
    indexes: { byEntryId: number; byStatus: string; byDirty: boolean }
  }
  highlights: {
    key: number
    value: OfflineHighlight
    indexes: { byEntryId: number; byStatus: string; byDirty: boolean }
  }
  pdfs: {
    key: number
    value: CachedPdf
  }
  syncQueue: {
    key: number
    value: SyncQueueItem
    indexes: { byTimestamp: number }
  }
  // For string index access compatibility
  [key: string]: any
}

const DB_NAME = 'chronokg-offline'
const DB_VERSION = 1

let dbPromise: Promise<IDBPDatabase<ChronoKGOfflineDB>> | null = null

export function getDb(): Promise<IDBPDatabase<ChronoKGOfflineDB>> {
  if (!dbPromise) {
    dbPromise = openDB<ChronoKGOfflineDB>(DB_NAME, DB_VERSION, {
      upgrade(db) {
        // Notes store
        const notesStore = db.createObjectStore('notes', { keyPath: 'id', autoIncrement: true })
        notesStore.createIndex('byEntryId', 'entryId')
        notesStore.createIndex('byStatus', 'status')
        notesStore.createIndex('byDirty', 'dirty')

        // Highlights store
        const highlightsStore = db.createObjectStore('highlights', { keyPath: 'id', autoIncrement: true })
        highlightsStore.createIndex('byEntryId', 'entryId')
        highlightsStore.createIndex('byStatus', 'status')
        highlightsStore.createIndex('byDirty', 'dirty')

        // PDFs store
        db.createObjectStore('pdfs', { keyPath: 'id' })

        // Sync queue store
        const syncStore = db.createObjectStore('syncQueue', { keyPath: 'id', autoIncrement: true })
        syncStore.createIndex('byTimestamp', 'timestamp')
      },
    })
  }
  return dbPromise
}

// --- Helper functions ---

export async function getNotesByEntry(entryId: number): Promise<OfflineNote[]> {
  const db = await getDb()
  return db.getAllFromIndex('notes', 'byEntryId', entryId)
}

export async function getHighlightsByEntry(entryId: number): Promise<OfflineHighlight[]> {
  const db = await getDb()
  return db.getAllFromIndex('highlights', 'byEntryId', entryId)
}

/**
 * Items waiting to be pushed. NOTE: an IDB index cannot key on booleans —
 * records with `dirty: true` are silently absent from the byDirty index, so
 * we read the stores directly and filter in JS (volumes stay tiny).
 */
export async function getPendingNotes(): Promise<OfflineNote[]> {
  const db = await getDb()
  const all = await db.getAll('notes')
  return all.filter(n => n.dirty === true)
}

export async function getPendingHighlights(): Promise<OfflineHighlight[]> {
  const db = await getDb()
  const all = await db.getAll('highlights')
  return all.filter(h => h.dirty === true)
}

export async function putNote(note: OfflineNote): Promise<number> {
  const db = await getDb()
  return db.put('notes', note)
}

export async function putHighlight(hl: OfflineHighlight): Promise<number> {
  const db = await getDb()
  return db.put('highlights', hl)
}

export async function deleteNote(id: number): Promise<void> {
  const db = await getDb()
  await db.delete('notes', id)
}

export async function deleteHighlight(id: number): Promise<void> {
  const db = await getDb()
  await db.delete('highlights', id)
}

export async function getCachedPdf(entryId: number): Promise<CachedPdf | undefined> {
  const db = await getDb()
  return db.get('pdfs', entryId)
}

export async function putCachedPdf(pdf: CachedPdf): Promise<void> {
  const db = await getDb()
  await db.put('pdfs', pdf)
}

export async function enqueueSync(item: Omit<SyncQueueItem, 'id'>): Promise<number> {
  const db = await getDb()
  return db.add('syncQueue', item as SyncQueueItem)
}

export async function getSyncQueue(): Promise<SyncQueueItem[]> {
  const db = await getDb()
  return db.getAllFromIndex('syncQueue', 'byTimestamp')
}

export async function deleteSyncItem(id: number): Promise<void> {
  const db = await getDb()
  await db.delete('syncQueue', id)
}

// Negative ID generator for local-only records
let localIdCounter = -1
export function nextLocalId(): number {
  return localIdCounter--
}