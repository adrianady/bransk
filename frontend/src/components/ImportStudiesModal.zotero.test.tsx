import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ImportStudiesModal } from './ImportStudiesModal'

const { apiMock, toastMock } = vi.hoisted(() => ({
  apiMock: {
    zotero: {
      status: vi.fn(),
      importLocal: vi.fn(),
      importFile: vi.fn(),
      syncApi: vi.fn(),
      export: vi.fn(),
    },
    studies: {
      searchExternal: vi.fn(),
      importBulk: vi.fn(),
    },
  },
  toastMock: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../lib/api', () => ({ api: apiMock }))
vi.mock('./Toast', () => ({ useToast: () => toastMock }))

beforeEach(() => vi.clearAllMocks())

describe('ImportStudiesModal Zotero integration', () => {
  it('switches to Zotero tab, detects local library, and imports local collection', async () => {
    apiMock.zotero.status.mockResolvedValue({
      installed: true,
      has_database: true,
      path: '/home/user/Zotero',
      storage_path: '/home/user/Zotero/storage',
      item_count: 42,
      pdf_count: 35,
    })
    apiMock.zotero.importLocal.mockResolvedValue({
      imported_count: 40,
      skipped_count: 2,
      pdfs_copied: 35,
    })

    const onImportComplete = vi.fn()
    render(<ImportStudiesModal isOpen onClose={vi.fn()} onImportComplete={onImportComplete} />)

    const zoteroTabBtn = screen.getByRole('button', { name: /Integrare Zotero/ })
    await act(async () => {
      fireEvent.click(zoteroTabBtn)
    })

    expect(
      await screen.findByText(/Zotero detectat: 42 articole în baza de date, 35 PDF-uri atașate/)
    ).toBeInTheDocument()

    const importLocalBtn = screen.getByRole('button', { name: /Importă din Zotero \/ Folder/ })
    await act(async () => {
      fireEvent.click(importLocalBtn)
    })

    expect(apiMock.zotero.importLocal).toHaveBeenCalledWith({
      collection: undefined,
      data_dir: '/home/user/Zotero',
      storage_dir: '/home/user/Zotero/storage',
      dedup_by_doi: true,
      dedup_by_title: true,
    })
    expect(toastMock.success).toHaveBeenCalledWith(
      expect.stringContaining('Import Zotero finalizat: 40 adăugate și 2 dubluri evitate după Titlu/DOI')
    )
    expect(onImportComplete).toHaveBeenCalled()
    expect(await screen.findByRole('status')).toHaveTextContent('40 studii adăugate și 2 dubluri evitate după Titlu/DOI')
  })

  it('allows syncing via Web API with API key and User ID', async () => {
    apiMock.zotero.status.mockResolvedValue({
      installed: false,
      message: 'Not found',
    })
    apiMock.zotero.syncApi.mockResolvedValue({
      imported_count: 10,
      skipped_count: 1,
      pdfs_copied: 8,
    })

    render(<ImportStudiesModal isOpen onClose={vi.fn()} onImportComplete={vi.fn()} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Integrare Zotero/ }))
    })

    expect(await screen.findByText(/Zotero local nu a fost găsit/)).toBeInTheDocument()

    const userIdInput = screen.getByPlaceholderText(/Ex: 12345678/)
    const apiKeyInput = screen.getByPlaceholderText(/Cheia API generată pe zotero.org/)
    fireEvent.change(userIdInput, { target: { value: '987654' } })
    fireEvent.change(apiKeyInput, { target: { value: 'my-secret-key' } })

    const syncBtn = screen.getByRole('button', { name: /Sincronizează via Zotero Web API/ })
    await act(async () => {
      fireEvent.click(syncBtn)
    })

    expect(apiMock.zotero.syncApi).toHaveBeenCalledWith('my-secret-key', '987654')
    expect(toastMock.success).toHaveBeenCalledWith(expect.stringContaining('Sincronizare Zotero Web: 10 importate'))
  })

  it('allows customizing PDF storage directory and deduplication flags', async () => {
    apiMock.zotero.status.mockResolvedValue({
      installed: true,
      has_database: true,
      path: '/home/user/Zotero',
      storage_path: '/home/user/Zotero/storage',
      item_count: 10,
      pdf_count: 10,
    })
    apiMock.zotero.importLocal.mockResolvedValue({
      imported_count: 5,
      skipped_count: 0,
      pdfs_copied: 5,
    })

    render(<ImportStudiesModal isOpen onClose={vi.fn()} onImportComplete={vi.fn()} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Integrare Zotero/ }))
    })

    expect(await screen.findByText(/Zotero detectat/)).toBeInTheDocument()

    const storageInput = screen.getByPlaceholderText(/\/home\/user\/Zotero\/storage sau alt folder cu PDF-uri/)
    expect(storageInput).toHaveValue('/home/user/Zotero/storage')
    fireEvent.change(storageInput, { target: { value: '/custom/zotfile/storage' } })

    const doiCheckbox = screen.getByLabelText(/Verifică și evită dublurile după DOI/)
    const titleCheckbox = screen.getByLabelText(/Verifică și evită dublurile după Titlu/)
    expect(doiCheckbox).toBeChecked()
    expect(titleCheckbox).toBeChecked()

    fireEvent.click(titleCheckbox)

    const importBtn = screen.getByRole('button', { name: /Importă din Zotero \/ Folder/ })
    await act(async () => {
      fireEvent.click(importBtn)
    })

    expect(apiMock.zotero.importLocal).toHaveBeenCalledWith({
      collection: undefined,
      data_dir: '/home/user/Zotero',
      storage_dir: '/custom/zotfile/storage',
      dedup_by_doi: true,
      dedup_by_title: false,
    })
  })

  it('probes a custom folder path and detects a PDF-only folder', async () => {
    apiMock.zotero.status.mockImplementation(async (customPath?: string) => {
      if (customPath === '/media/disk/PDFs') {
        return {
          installed: false,
          has_database: false,
          has_pdfs: true,
          path: '/media/disk/PDFs',
          storage_path: '/media/disk/PDFs',
          item_count: 0,
          pdf_count: 17,
        }
      }
      return { installed: false, message: 'Not found' }
    })
    apiMock.zotero.importLocal.mockResolvedValue({
      imported_count: 15,
      skipped_count: 2,
      pdfs_copied: 17,
    })

    render(<ImportStudiesModal isOpen onClose={vi.fn()} onImportComplete={vi.fn()} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Integrare Zotero/ }))
    })

    const pathInput = screen.getByLabelText(/Cale folder Zotero/)
    fireEvent.change(pathInput, { target: { value: '/media/disk/PDFs' } })

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Verifică folder/ }))
    })

    expect(apiMock.zotero.status).toHaveBeenCalledWith('/media/disk/PDFs')
    expect(await screen.findByText(/Folder de PDF-uri detectat: 17 fișiere PDF găsite/)).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Importă din Zotero \/ Folder/ }))
    })

    expect(apiMock.zotero.importLocal).toHaveBeenCalledWith(
      expect.objectContaining({
        data_dir: '/media/disk/PDFs',
        storage_dir: '/media/disk/PDFs',
      })
    )
    expect(toastMock.success).toHaveBeenCalledWith(
      expect.stringContaining('Import Zotero finalizat: 15 adăugate și 2 dubluri evitate după Titlu/DOI')
    )
  })

  it('probes a custom database folder and imports from it', async () => {
    apiMock.zotero.status.mockImplementation(async (customPath?: string) => {
      if (customPath === '/media/disk/Zotero') {
        return {
          installed: true,
          has_database: true,
          has_pdfs: true,
          path: '/media/disk/Zotero',
          storage_path: '/media/disk/Zotero/storage',
          item_count: 128,
          pdf_count: 96,
        }
      }
      return { installed: false, message: 'Not found' }
    })
    apiMock.zotero.importLocal.mockResolvedValue({
      imported_count: 120,
      skipped_count: 8,
      pdfs_copied: 96,
    })

    render(<ImportStudiesModal isOpen onClose={vi.fn()} onImportComplete={vi.fn()} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Integrare Zotero/ }))
    })

    const pathInput = screen.getByLabelText(/Cale folder Zotero/)
    fireEvent.change(pathInput, { target: { value: '/media/disk/Zotero' } })
    fireEvent.blur(pathInput)

    expect(
      await screen.findByText(/Zotero detectat: 128 articole în baza de date, 96 PDF-uri atașate/)
    ).toBeInTheDocument()

    const collectionInput = screen.getByLabelText(/Colecție specifică/)
    fireEvent.change(collectionInput, { target: { value: 'Teză' } })

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Importă din Zotero \/ Folder/ }))
    })

    expect(apiMock.zotero.importLocal).toHaveBeenCalledWith(
      expect.objectContaining({
        collection: 'Teză',
        data_dir: '/media/disk/Zotero',
        storage_dir: '/media/disk/Zotero/storage',
        dedup_by_doi: true,
        dedup_by_title: true,
      })
    )
  })

  it('strips a leading dash and quotes from a pasted folder path', async () => {
    apiMock.zotero.status.mockResolvedValue({
      installed: true,
      has_database: true,
      has_pdfs: true,
      path: '/media/disk/Zotero',
      storage_path: '/media/disk/Zotero/storage',
      item_count: 128,
      pdf_count: 96,
    })
    apiMock.zotero.importLocal.mockResolvedValue({
      imported_count: 120,
      skipped_count: 8,
      pdfs_copied: 96,
    })

    render(<ImportStudiesModal isOpen onClose={vi.fn()} onImportComplete={vi.fn()} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Integrare Zotero/ }))
    })

    const pathInput = screen.getByLabelText(/Cale folder Zotero/)
    fireEvent.change(pathInput, {
      target: { value: '-"/media/disk/Zotero"' },
    })
    expect(pathInput).toHaveValue('/media/disk/Zotero')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Verifică folder/ }))
    })
    expect(apiMock.zotero.status).toHaveBeenLastCalledWith('/media/disk/Zotero')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Importă din Zotero \/ Folder/ }))
    })
    expect(apiMock.zotero.importLocal).toHaveBeenCalledWith(
      expect.objectContaining({
        data_dir: '/media/disk/Zotero',
        storage_dir: '/media/disk/Zotero/storage',
      })
    )
  })
})
