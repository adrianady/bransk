import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { StudyNote, PdfHighlight } from '../types'
import {
  getDb,
  getNotesByEntry,
  getHighlightsByEntry,
  putNote,
  putHighlight,
  deleteNote,
  deleteHighlight,
  getPendingNotes,
  getPendingHighlights,
  nextLocalId,
  type OfflineNote,
  type OfflineHighlight,
} from './schema'
import { schedulePush } from './sync'

const STATUS_SYNCED = 'synced' as const
const STATUS_PENDING = 'pending' as const
const STATUS_CONFLICT = 'conflict' as const
const STATUS_ERROR = 'error' as const

function toOfflineNote(note: StudyNote, dirty = false): OfflineNote {
  return {
    id: note.id,
    serverId: note.id,
    entryId: note.entry_id,
    content: note.content,
    courseContext: note.course_context,
    pageNumber: note.page_number ?? null,
    sourceDocumentIds: note.sources?.map(s => s.document_id) ?? [],
    createdAt: note.created_at,
    updatedAt: note.updated_at,
    status: dirty ? STATUS_PENDING : STATUS_SYNCED,
    dirty,
  }
}

function toOfflineHighlight(hl: PdfHighlight, dirty = false): OfflineHighlight {
  return {
    id: hl.id,
    serverId: hl.id,
    entryId: hl.entry_id,
    quote: hl.quote,
    note: hl.note ?? '',
    pageNumber: hl.page_number ?? null,
    documentId: hl.document_id ?? null,
    createdAt: hl.created_at,
    status: dirty ? STATUS_PENDING : STATUS_SYNCED,
    dirty,
  }
}

function nowIso(): string {
  return new Date().toISOString()
}

function isValidEntryId(entryId: number): boolean {
  return typeof entryId === 'number' && Number.isFinite(entryId) && entryId > 0
}

export function useOfflineNotes(entryId: number) {
  const valid = isValidEntryId(entryId)
  const [notes, setNotes] = useState<OfflineNote[]>([])
  const [loading, setLoading] = useState(!valid ? false : true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!isValidEntryId(entryId)) {
      setNotes([])
      setLoading(false)
      setError(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      // Load from IndexedDB first (instant)
      const local = await getNotesByEntry(entryId)
      if (local.length > 0) {
        setNotes(local.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()))
      }
      // Then try to fetch from server (if online)
      try {
        const serverNotes = await api.studies.notes.list(entryId)
        const serverMap = new Map(serverNotes.map(n => [n.id, n]))
        const merged = new Map<number, OfflineNote>()

        // Start with local notes
        for (const n of local) {
          merged.set(n.id, n)
        }
        // Merge server notes
        for (const sn of serverNotes) {
          const existing = merged.get(sn.id)
          if (!existing || !existing.dirty) {
            // Server wins if local not dirty, or no local copy
            merged.set(sn.id, toOfflineNote(sn))
          } else if (existing.dirty) {
            // Local has pending changes - check for conflict
            const serverTime = new Date(sn.updated_at).getTime()
            const localTime = new Date(existing.updatedAt).getTime()
            if (serverTime > localTime) {
              existing.status = STATUS_CONFLICT
            }
            // Keep local version but mark conflict
          }
        }
        const final = Array.from(merged.values()).sort(
          (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
        )
        setNotes(final)
        // Persist merged state
        for (const n of final) {
          await putNote(n)
        }
      } catch {
        // Offline - keep local only
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load notes')
    } finally {
      setLoading(false)
    }
  }, [entryId])

  useEffect(() => {
    if (!isValidEntryId(entryId)) {
      setNotes([])
      setLoading(false)
      setError(null)
      return
    }
    load()
    // After a successful sync pass the pushed items adopt their server IDs —
    // reload so the UI keys match what the server returned.
    const onSynced = () => { void load() }
    window.addEventListener('chronokg-synced', onSynced)
    return () => window.removeEventListener('chronokg-synced', onSynced)
  }, [load, entryId])

  const create = useCallback(async (data: {
    content: string
    courseContext: string
    pageNumber: number | null
    sourceDocumentIds: number[]
  }) => {
    if (!isValidEntryId(entryId)) {
      return Promise.reject(new Error('Cannot create note: invalid entry id'))
    }
    const localId = nextLocalId()
    const note: OfflineNote = {
      id: localId,
      serverId: undefined,
      entryId,
      content: data.content,
      courseContext: data.courseContext,
      pageNumber: data.pageNumber,
      sourceDocumentIds: data.sourceDocumentIds,
      createdAt: nowIso(),
      updatedAt: nowIso(),
      status: STATUS_PENDING,
      dirty: true,
    }
    await putNote(note)
    setNotes(prev => [note, ...prev])
    // While online, land the note on the server within ~1.5 s; while offline
    // this is a no-op and the note waits for the next sweep.
    schedulePush()
    return note
  }, [entryId])

  const update = useCallback(async (id: number, data: Partial<Pick<OfflineNote, 'content' | 'courseContext' | 'pageNumber' | 'sourceDocumentIds'>>) => {
    if (!isValidEntryId(entryId)) return
    const existing = notes.find(n => n.id === id)
    if (!existing) return
    const updated: OfflineNote = {
      ...existing,
      ...data,
      updatedAt: nowIso(),
      status: existing.serverId ? STATUS_PENDING : STATUS_PENDING,
      dirty: true,
    }
    await putNote(updated)
    setNotes(prev => prev.map(n => (n.id === id ? updated : n)))
    schedulePush()
  }, [notes, entryId])

  const remove = useCallback(async (id: number) => {
    if (!isValidEntryId(entryId)) return
    const existing = notes.find(n => n.id === id)
    if (!existing) return
    if (existing.serverId) {
      // Mark for deletion on sync
      const deleted: OfflineNote = { ...existing, status: STATUS_PENDING, dirty: true, pendingDelete: true }
      await putNote(deleted)
      // Remove from UI immediately
      setNotes(prev => prev.filter(n => n.id !== id))
    } else {
      // Pure local - hard delete
      await deleteNote(id)
      setNotes(prev => prev.filter(n => n.id !== id))
    }
    schedulePush()
  }, [notes, entryId])

  return { notes, loading, error, create, update, remove, refresh: load }
}

export function useOfflineHighlights(entryId: number) {
  const valid = isValidEntryId(entryId)
  const [highlights, setHighlights] = useState<OfflineHighlight[]>([])
  const [loading, setLoading] = useState(!valid ? false : true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!isValidEntryId(entryId)) {
      setHighlights([])
      setLoading(false)
      setError(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const local = await getHighlightsByEntry(entryId)
      if (local.length > 0) {
        setHighlights(local.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()))
      }
      try {
        const serverHls = await api.highlights.list(entryId)
        const serverMap = new Map(serverHls.map(h => [h.id, h]))
        const merged = new Map<number, OfflineHighlight>()

        for (const h of local) {
          merged.set(h.id, h)
        }
        for (const sh of serverHls) {
          const existing = merged.get(sh.id)
          if (!existing || !existing.dirty) {
            merged.set(sh.id, toOfflineHighlight(sh))
          } else if (existing.dirty) {
            // Local pending - highlights are append-only, so conflict rare
            // If same quote/page, server wins; else keep both
            const sameQuote = existing.quote === sh.quote && existing.pageNumber === sh.page_number
            if (sameQuote) {
              existing.status = STATUS_CONFLICT
            }
          }
        }
        const final = Array.from(merged.values()).sort(
          (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
        )
        setHighlights(final)
        for (const h of final) {
          await putHighlight(h)
        }
      } catch {
        // Offline
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load highlights')
    } finally {
      setLoading(false)
    }
  }, [entryId])

  useEffect(() => {
    if (!isValidEntryId(entryId)) {
      setHighlights([])
      setLoading(false)
      setError(null)
      return
    }
    load()
    const onSynced = () => { void load() }
    window.addEventListener('chronokg-synced', onSynced)
    return () => window.removeEventListener('chronokg-synced', onSynced)
  }, [load, entryId])

  const create = useCallback(async (data: { quote: string; note: string; pageNumber: number | null; documentId: number | null }) => {
    if (!isValidEntryId(entryId)) {
      return Promise.reject(new Error('Cannot create highlight: invalid entry id'))
    }
    const localId = nextLocalId()
    const hl: OfflineHighlight = {
      id: localId,
      serverId: undefined,
      entryId,
      quote: data.quote,
      note: data.note,
      pageNumber: data.pageNumber,
      documentId: data.documentId,
      createdAt: nowIso(),
      status: STATUS_PENDING,
      dirty: true,
    }
    await putHighlight(hl)
    setHighlights(prev => [hl, ...prev])
    schedulePush()
    return hl
  }, [entryId])

  const remove = useCallback(async (id: number) => {
    if (!isValidEntryId(entryId)) return
    const existing = highlights.find(h => h.id === id)
    if (!existing) return
    if (existing.serverId) {
      const deleted: OfflineHighlight = { ...existing, status: STATUS_PENDING, dirty: true, pendingDelete: true }
      await putHighlight(deleted)
      setHighlights(prev => prev.filter(h => h.id !== id))
    } else {
      await deleteHighlight(id)
      setHighlights(prev => prev.filter(h => h.id !== id))
    }
    schedulePush()
  }, [highlights, entryId])

  return { highlights, loading, error, create, remove, refresh: load }
}

/**
 * Cache PDF in IndexedDB and return a blob URL for offline viewing.
 * Uses stale-while-revalidate: returns cached blob immediately, fetches fresh in background.
 */
export function usePdfCache(entryId: number, pdfUrl: string) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cached, setCached] = useState(false)

  const load = useCallback(async () => {
    if (!isValidEntryId(entryId) || !pdfUrl) {
      setBlobUrl(null)
      setLoading(false)
      setError(null)
      setCached(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      // Try cache first
      const cachedPdf = await (await import('./schema')).getCachedPdf(entryId)
      if (cachedPdf) {
        const url = URL.createObjectURL(cachedPdf.blob)
        setBlobUrl(url)
        setCached(true)
      }
      // Fetch fresh in background
      try {
        const token = (await import('../api')).getAuthToken?.()
        const headers: Record<string, string> = {}
        if (token) headers.Authorization = `Bearer ${token}`
        const resp = await fetch(pdfUrl, { headers })
        if (resp.ok) {
          const blob = await resp.blob()
          const etag = resp.headers.get('etag') ?? undefined
          await (await import('./schema')).putCachedPdf({
            id: entryId,
            blob,
            url: pdfUrl,
            cachedAt: Date.now(),
            etag,
            size: blob.size,
          })
          const url = URL.createObjectURL(blob)
          setBlobUrl(url)
          setCached(true)
        }
      } catch {
        // Background fetch failed - keep cached version
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load PDF')
    } finally {
      setLoading(false)
    }
  }, [entryId, pdfUrl])

  useEffect(() => {
    if (!isValidEntryId(entryId) || !pdfUrl) {
      return
    }
    load()
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [load, entryId, pdfUrl])

  return { blobUrl, loading, error, cached, refresh: load }
}