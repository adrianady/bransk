import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ImportStudiesModal } from './ImportStudiesModal'

const { apiMock, toastMock } = vi.hoisted(() => ({
  apiMock: { studies: { importBulk: vi.fn() } },
  toastMock: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../lib/api', () => ({ api: apiMock }))
vi.mock('./Toast', () => ({ useToast: () => toastMock }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => vi.clearAllMocks())

describe('ImportStudiesModal PDF outcome feedback', () => {
  it('reports PDF download and indexing failures without masking successful study imports', async () => {
    const pending = deferred<any>()
    apiMock.studies.importBulk.mockReturnValue(pending.promise)
    render(<ImportStudiesModal isOpen onClose={vi.fn()} onImportComplete={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /Identificator rapid/ }))
    fireEvent.change(screen.getByPlaceholderText(/Exemplu:/), { target: { value: '10.1234/example' } })
    fireEvent.click(screen.getByRole('button', { name: 'Analizează și importă lista' }))

    const submit = screen.getByRole('button', { name: 'Se analizează și caută PDF-uri...' })
    expect(submit).toBeDisabled()
    expect(submit).toHaveAttribute('aria-busy', 'true')

    await act(async () => pending.resolve({
      imported: [
        { id: 4, title: 'PDF indisponibil', pdf_downloaded: false, pdf_index_failed: false },
        { id: 5, title: 'PDF salvat fără index', pdf_downloaded: true, pdf_index_failed: true },
      ],
      skipped: [],
      total_imported: 2,
      total_skipped: 0,
      pdfs_downloaded: 1,
      pdfs_failed: 1,
      pdfs_index_failed: 1,
    }))

    const summary = await screen.findByRole('status')
    expect(summary).toHaveTextContent('2 adăugate')
    expect(summary).toHaveTextContent('PDF-uri descărcate: 1')
    expect(summary).toHaveTextContent('PDF-uri indisponibile: 1')
    expect(summary).toHaveTextContent('PDF-uri descărcate, dar neindexate: 1')
    expect(summary).toHaveTextContent('[PDF indisponibil]')
    expect(summary).toHaveTextContent('[PDF salvat; indexarea a eșuat]')
    expect(screen.getByRole('button', { name: 'Analizează și importă lista' })).toHaveAttribute('aria-busy', 'false')
  })
})
