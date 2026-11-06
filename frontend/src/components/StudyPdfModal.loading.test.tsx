import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StudyPdfModal } from './StudyPdfModal'

const { apiMock, pdfMock, toastMock } = vi.hoisted(() => ({
  apiMock: { studies: { pdfUrl: vi.fn(), pdfHeaders: vi.fn(), notes: { list: vi.fn() } } },
  pdfMock: { getDocument: vi.fn() },
  toastMock: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../lib/api', () => ({ api: apiMock }))
vi.mock('./Toast', () => ({ useToast: () => toastMock }))
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: pdfMock.getDocument,
}))

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
  id: 9,
  title: 'Lucrare de test',
  content: 'Rezumat',
  entry_date: null,
  created_at: '',
  updated_at: '',
  tags: 'study',
  source: '',
  importance: 3,
  archived: false,
  study_type: 'study',
  authors: '',
  quality: null,
  url_validated: false,
  design: '',
  effect_direction: '',
  pdf_path: '9-lucrare.pdf',
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.studies.pdfUrl.mockReturnValue('/api/studies/9/pdf')
  apiMock.studies.pdfHeaders.mockReturnValue({ Authorization: 'Bearer test-token' })
  apiMock.studies.notes.list.mockResolvedValue([])
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
})

afterEach(() => vi.restoreAllMocks())

describe('StudyPdfModal loading feedback', () => {
  it('shows measured progress while PDF.js downloads the document', async () => {
    const loading = deferred<{ numPages: number }>()
    const task: { promise: Promise<{ numPages: number }>; destroy: ReturnType<typeof vi.fn>; onProgress?: (event: { loaded: number; total: number }) => void } = {
      promise: loading.promise,
      destroy: vi.fn().mockResolvedValue(undefined),
    }
    pdfMock.getDocument.mockReturnValue(task)

    render(<StudyPdfModal study={study as any} isOpen onClose={vi.fn()} />)

    await waitFor(() => expect(task.onProgress).toEqual(expect.any(Function)))
    expect(pdfMock.getDocument).toHaveBeenCalledWith(expect.objectContaining({
      url: '/api/studies/9/pdf',
      httpHeaders: { Authorization: 'Bearer test-token' },
      withCredentials: true,
    }))
    act(() => task.onProgress?.({ loaded: 512, total: 1024 }))

    expect(screen.getByRole('progressbar', { name: /PDF.*50%/i })).toHaveAttribute('aria-valuenow', '50')
    await act(async () => loading.resolve({ numPages: 1 }))
    expect(await screen.findByText('din 1')).toBeInTheDocument()
  })

  it('lets the user retry after a loading failure and clears the error when retry succeeds', async () => {
    const failed = deferred<{ numPages: number }>()
    const recovered = deferred<{ numPages: number }>()
    pdfMock.getDocument
      .mockReturnValueOnce({ promise: failed.promise, destroy: vi.fn().mockResolvedValue(undefined) })
      .mockReturnValueOnce({ promise: recovered.promise, destroy: vi.fn().mockResolvedValue(undefined) })

    render(<StudyPdfModal study={study as any} isOpen onClose={vi.fn()} />)
    await act(async () => failed.reject(new Error('Conexiunea a fost întreruptă')))

    expect(await screen.findByText('Conexiunea a fost întreruptă')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Reîncearcă' }))
    await waitFor(() => expect(pdfMock.getDocument).toHaveBeenCalledTimes(2))
    await act(async () => recovered.resolve({ numPages: 3 }))

    expect(await screen.findByText('din 3')).toBeInTheDocument()
    expect(screen.queryByText('Conexiunea a fost întreruptă')).not.toBeInTheDocument()
  })

  it('destroys the active PDF.js task when the reader unmounts during loading', async () => {
    const loading = deferred<{ numPages: number }>()
    const destroy = vi.fn().mockResolvedValue(undefined)
    const task = { promise: loading.promise, destroy }
    pdfMock.getDocument.mockReturnValue(task)

    const { unmount } = render(<StudyPdfModal study={study as any} isOpen onClose={vi.fn()} />)
    await waitFor(() => expect(pdfMock.getDocument).toHaveBeenCalledOnce())
    unmount()

    expect(destroy).toHaveBeenCalledOnce()
  })
})
