import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StudiesView } from './StudiesView'

const { apiMock, toastMock } = vi.hoisted(() => ({
  apiMock: {
    studies: {
      all: vi.fn(),
      download: vi.fn(),
      downloadAll: vi.fn(),
      bulkDownload: vi.fn(),
      bulkDelete: vi.fn(),
      fulltext: vi.fn(),
    },
    theories: { allEvidence: vi.fn() },
  },
  toastMock: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../lib/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}))

vi.mock('./Toast', () => ({ useToast: () => toastMock }))
vi.mock('./ImportStudiesModal', () => ({ ImportStudiesModal: () => null }))
vi.mock('./StudyPdfModal', () => ({ StudyPdfModal: () => null }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const study = {
  id: 7,
  title: 'Studiu de test',
  content: 'Rezumat',
  entry_date: null,
  created_at: '',
  updated_at: '',
  tags: 'study',
  source: 'https://example.org/paper',
  importance: 3,
  archived: false,
  study_type: 'study',
  authors: '',
  quality: null,
  url_validated: true,
  design: '',
  effect_direction: '',
  pdf_path: '',
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.studies.all.mockResolvedValue({ items: [study], total: 1 })
  apiMock.theories.allEvidence.mockResolvedValue([])
})

describe('StudiesView PDF loading feedback', () => {
  it('shows an accessible busy state and prevents duplicate PDF downloads', async () => {
    const download = deferred<{ pdf_path: string; indexed_chunks: number }>()
    apiMock.studies.download.mockReturnValue(download.promise)
    render(<StudiesView onSelectEntry={vi.fn()} />)

    const button = await screen.findByRole('button', { name: 'Descarcă PDF', exact: true })
    fireEvent.click(button)

    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toHaveTextContent('Se descarcă PDF-ul…')
    fireEvent.click(button)
    expect(apiMock.studies.download).toHaveBeenCalledTimes(1)

    await act(async () => download.resolve({ pdf_path: '7-studiu.pdf', indexed_chunks: 4 }))
    await waitFor(() => expect(button).toBeEnabled())
    expect(button).toHaveAttribute('aria-busy', 'false')
  })
})
