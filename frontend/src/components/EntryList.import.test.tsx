import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EntryList } from './EntryList'

const { apiMock, toastMock } = vi.hoisted(() => ({
  apiMock: {
    entries: { list: vi.fn(), listPage: vi.fn(), create: vi.fn() },
    tags: { list: vi.fn(), create: vi.fn() },
    relationships: { list: vi.fn(), create: vi.fn() },
  },
  toastMock: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../lib/api', () => ({ api: apiMock, ApiError: class ApiError extends Error {} }))
vi.mock('./Toast', () => ({ useToast: () => toastMock }))

function entry(id: number, title: string) {
  return {
    id,
    title,
    content: '',
    entry_date: null,
    created_at: '',
    updated_at: '',
    tags: '',
    source: '',
    importance: 3,
    archived: false,
    study_type: '',
    authors: '',
    quality: null,
    url_validated: false,
    design: '',
    effect_direction: '',
    pdf_path: '',
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  // Library view pages (limit 100): empty library screen.
  // Import dedup pages (limit 500): the duplicate lives on page 2.
  apiMock.entries.listPage.mockImplementation(async (params?: { limit?: number; offset?: number }) => {
    if (params?.limit === 500) {
      if ((params?.offset ?? 0) === 0) {
        return { items: [entry(1, 'First Page Paper'), entry(2, 'Second Page Paper')], total: 3 }
      }
      return { items: [entry(3, 'Deep Duplicate Paper')], total: 3 }
    }
    return { items: [], total: 0 }
  })
  apiMock.entries.list.mockResolvedValue([])
  apiMock.entries.create.mockImplementation(async (data: { title: string }) => ({ ...entry(999, data.title) }))
  apiMock.tags.list.mockResolvedValue([])
  apiMock.relationships.list.mockResolvedValue([])
})

describe('EntryList import duplicate detection', () => {
  it('detects a duplicate whose title only appears beyond the first page', async () => {
    render(<EntryList onSelect={vi.fn()} />)
    await waitFor(() => expect(apiMock.entries.listPage).toHaveBeenCalled())

    const payload = {
      entries: [
        { ...entry(101, 'Deep Duplicate Paper'), content: 'already in library' },
        { ...entry(102, 'Brand New Paper'), content: 'fresh import' },
      ],
      tags: [],
      relationships: [],
    }
    const json = JSON.stringify(payload)
    const file = new File([json], 'export.json', { type: 'application/json' })
    // jsdom File.text() may be missing: read the payload deterministically.
    Object.defineProperty(file, 'text', { value: async () => json })

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input).not.toBeNull()
    fireEvent.change(input, { target: { files: [file] } })

    // Only the genuinely new paper is created; the deep duplicate is skipped.
    await waitFor(() => expect(apiMock.entries.create).toHaveBeenCalledTimes(1))
    expect(apiMock.entries.create).toHaveBeenCalledWith(expect.objectContaining({ title: 'Brand New Paper' }))
    // Both import pages were consulted — the check is not capped at page one.
    const importCalls = apiMock.entries.listPage.mock.calls.filter(([p]) => p?.limit === 500)
    expect(importCalls.length).toBeGreaterThanOrEqual(2)
    expect(toastMock.success).toHaveBeenCalledWith(expect.stringMatching(/Importate 1 intrări, omise 1 duplicate/))
  })
})
