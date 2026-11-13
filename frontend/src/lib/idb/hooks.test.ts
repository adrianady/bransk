import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useOfflineNotes, useOfflineHighlights, usePdfCache } from './hooks'

const { apiMock, schemaMock, syncMock } = vi.hoisted(() => ({
  apiMock: {
    studies: { notes: { list: vi.fn() } },
    highlights: { list: vi.fn() },
    getAuthToken: vi.fn(() => ''),
  },
  schemaMock: {
    getNotesByEntry: vi.fn(),
    getHighlightsByEntry: vi.fn(),
    putNote: vi.fn(),
    putHighlight: vi.fn(),
    deleteNote: vi.fn(),
    deleteHighlight: vi.fn(),
    getCachedPdf: vi.fn(),
    putCachedPdf: vi.fn(),
    nextLocalId: vi.fn(() => -1),
  },
  syncMock: { schedulePush: vi.fn() },
}))

vi.mock('../api', () => ({ api: apiMock, getAuthToken: apiMock.getAuthToken }))
vi.mock('./schema', () => schemaMock)
vi.mock('./sync', () => syncMock)

beforeEach(() => {
  vi.clearAllMocks()
  schemaMock.getNotesByEntry.mockResolvedValue([])
  schemaMock.getHighlightsByEntry.mockResolvedValue([])
  schemaMock.getCachedPdf.mockResolvedValue(undefined)
  apiMock.studies.notes.list.mockResolvedValue([])
  apiMock.highlights.list.mockResolvedValue([])
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('should not fetch') }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('offline hooks with invalid entry id', () => {
  it('useOfflineNotes(0) returns empty collections and performs no API call', async () => {
    const { result } = renderHook(() => useOfflineNotes(0))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.notes).toEqual([])
    expect(result.current.error).toBeNull()
    expect(apiMock.studies.notes.list).not.toHaveBeenCalled()
    expect(schemaMock.getNotesByEntry).not.toHaveBeenCalled()

    // Mutations are predictable no-ops: create rejects, the rest resolve.
    await expect(
      result.current.create({ content: 'x', courseContext: '', pageNumber: null, sourceDocumentIds: [] }),
    ).rejects.toThrow(/invalid entry id/i)
    await expect(result.current.update(1, { content: 'y' })).resolves.toBeUndefined()
    await expect(result.current.remove(1)).resolves.toBeUndefined()
    await expect(result.current.refresh()).resolves.toBeUndefined()
    expect(schemaMock.putNote).not.toHaveBeenCalled()
    expect(syncMock.schedulePush).not.toHaveBeenCalled()
  })

  it('useOfflineHighlights(0) returns empty collections and performs no API call', async () => {
    const { result } = renderHook(() => useOfflineHighlights(0))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.highlights).toEqual([])
    expect(result.current.error).toBeNull()
    expect(apiMock.highlights.list).not.toHaveBeenCalled()
    expect(schemaMock.getHighlightsByEntry).not.toHaveBeenCalled()

    await expect(
      result.current.create({ quote: 'q', note: '', pageNumber: null, documentId: null }),
    ).rejects.toThrow(/invalid entry id/i)
    await expect(result.current.remove(1)).resolves.toBeUndefined()
    await expect(result.current.refresh()).resolves.toBeUndefined()
    expect(schemaMock.putHighlight).not.toHaveBeenCalled()
    expect(syncMock.schedulePush).not.toHaveBeenCalled()
  })

  it('usePdfCache(0, …) returns empty state and never fetches', async () => {
    const { result } = renderHook(() => usePdfCache(0, '/api/studies/0/pdf'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.blobUrl).toBeNull()
    expect(result.current.cached).toBe(false)
    expect(result.current.error).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
    expect(schemaMock.getCachedPdf).not.toHaveBeenCalled()

    await expect(result.current.refresh()).resolves.toBeUndefined()
    expect(fetch).not.toHaveBeenCalled()
  })
})
