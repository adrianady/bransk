import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import React from 'react'
import { NotesPanel } from './NotesPanel'
import { ToastProvider } from './Toast'

const hoisted = vi.hoisted(() => ({
  mockList: vi.fn(),
  mockGet: vi.fn(),
  mockVerify: vi.fn(),
  mockPatchClaim: vi.fn(),
  mockRemove: vi.fn(),
}))

vi.mock('../lib/api', () => ({
  api: {
    noteDossiers: {
      list: hoisted.mockList,
      get: hoisted.mockGet,
      verify: hoisted.mockVerify,
      patchClaim: hoisted.mockPatchClaim,
      remove: hoisted.mockRemove,
    },
  },
}))

const mockList = hoisted.mockList
const mockGet = hoisted.mockGet
const mockVerify = hoisted.mockVerify
const mockPatchClaim = hoisted.mockPatchClaim
const mockRemove = hoisted.mockRemove

const baseSummary = {
  id: 1,
  project_id: 1,
  entry_id: 10,
  status: 'partial' as const,
  title: 'Dosar pentru „Impactul opțiunilor de viață”',
  summary: 'Rezumat',
  created_at: '2026-01-10T10:00:00Z',
  updated_at: '2026-01-12T09:00:00Z',
  entry_title: 'Studiu de exemplu',
  entry_authors: 'Popescu, I.',
  entry_year: '2020',
  claims_total: 3,
  claims_verificat: 1,
  by_status: { verificat: 1, neverificat: 2 },
}

const baseDetail = {
  dossier: { ...baseSummary, source_sheet_json: 'Autor: Popescu, I.\nAn: 2020\nTitlu: Impactul opțiunilor de viață\n' },
  claims: [
    {
      id: 1,
      dossier_id: 1,
      code: 'A1',
      raw_note: 'Opțiunile de viață influență sănătatea.',
      statement: 'Opțiunile de viață influențează sănătatea.',
      quote: '„Opțiunile de viață sunt un predictor semnificativ.”',
      page_pdf: 4,
      page_printed: '4',
      engine: 'native',
      visual_check: true,
      status: 'verificat' as const,
      correction: '',
      method: 'exact_match',
      coverage: 1,
      created_at: '2026-01-10T10:00:00Z',
      verified_at: '2026-01-10T10:05:00Z',
    },
    {
      id: 2,
      dossier_id: 1,
      code: 'A2',
      raw_note: 'Stresul e mic.',
      statement: 'Stresul este redus la adulți.',
      quote: '„Nicio reducere semnificativă a stresului nu a fost notată.”',
      page_pdf: 7,
      page_printed: '7',
      engine: 'ocr',
      visual_check: false,
      status: 'corectat' as const,
      correction: 'Textul nu spune că stresul se reduce.',
      method: 'semantic',
      coverage: 0.4,
      created_at: '2026-01-10T10:10:00Z',
      verified_at: null,
    },
  ],
  sections: [
    { kind: 'ce_spune', title: 'Ce spune sursa', position: 1, body_md: 'Sursa argumentă că opțiunile de viață sunt importante.\n\nA doua paragraf.' },
    { kind: 'glosar', title: 'Glosar', position: 2, body_md: 'Opțiune de viață: un factor contextual.' },
  ],
  edges: [],
  rounds: [
    { id: 1, summary: 'Prima verificare automată.', changed_json: {}, open_json: {}, created_at: '2026-01-10T10:00:00Z' },
  ],
  summary: { verificat: 1, neverificat: 2, total: 3 },
  by_status: { verificat: 1, neverificat: 2 },
}

function renderPanel(props = {}) {
  return render(
    <ToastProvider>
      <NotesPanel {...props} />
    </ToastProvider>,
  )
}

describe('NotesPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the empty state explaining what a dossier is and a library button', async () => {
    mockList.mockResolvedValue({ dossiers: [] })
    renderPanel()
    expect(screen.getByText('Dosare de notițe verificate')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Nu există încă dosare de notițe.')).toBeInTheDocument())
    expect(screen.getByText('Deschide biblioteca')).toBeInTheDocument()
  })

  it('renders dossier list cards with status chips and counters', async () => {
    mockList.mockResolvedValue({ dossiers: [baseSummary] })
    renderPanel()
    await waitFor(() => expect(screen.getByText('Dosar pentru „Impactul opțiunilor de viață”')).toBeInTheDocument())
    expect(screen.getByText('Parțial verificat')).toBeInTheDocument()
    expect(screen.getByText(/de 3 verificate/)).toBeInTheDocument()
    expect(screen.getByText(/Popescu, I\. · 2020/)).toBeInTheDocument()
  })

  it('opens a dossier detail and renders a claim code, status chip, statement, quote and printed page', async () => {
    mockList.mockResolvedValue({ dossiers: [baseSummary] })
    mockGet.mockResolvedValue(baseDetail)
    renderPanel()
    await waitFor(() => expect(screen.getByText('Dosar pentru „Impactul opțiunilor de viață”')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Dosar pentru „Impactul opțiunilor de viață”'))
    await waitFor(() => expect(screen.getByText('A1')).toBeInTheDocument())
    expect(screen.getByText('Verificat')).toBeInTheDocument()
    expect(screen.getByText('Opțiunile de viață influențează sănătatea.')).toBeInTheDocument()
    expect(screen.getByText('„Opțiunile de viață sunt un predictor semnificativ.”')).toBeInTheDocument()
    expect(screen.getByText('Pagina tipărită 4')).toBeInTheDocument()
  })

  it('renders the corrections table for a corrected claim and handles null optional fields', async () => {
    const detailNulls = {
      ...baseDetail,
      claims: [
        {
          ...baseDetail.claims[1],
          page_printed: null as unknown as string,
          page_pdf: null,
          engine: null as unknown as string,
        },
      ],
    }
    mockList.mockResolvedValue({ dossiers: [baseSummary] })
    mockGet.mockResolvedValue(detailNulls)
    renderPanel()
    await waitFor(() => expect(screen.getByText('Dosar pentru „Impactul opțiunilor de viață”')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Dosar pentru „Impactul opțiunilor de viață”'))
    await waitFor(() => expect(screen.getByText('Corecții la notițele tale')).toBeInTheDocument())
    // Corrections table appears for the corectat claim
    expect(screen.getByText('Corecții la notițele tale')).toBeInTheDocument()
    expect(screen.getByText('Ce notasem')).toBeInTheDocument()
    expect(screen.getByText('Stresul e mic.')).toBeInTheDocument()
    expect(screen.getByText('Cum este în text')).toBeInTheDocument()
    expect(screen.getAllByText('Textul nu spune că stresul se reduce.').length).toBeGreaterThanOrEqual(1)
    // No crash when page_printed is null — the page chip is not rendered.
    expect(screen.queryByText('Pagina tipărită null')).not.toBeInTheDocument()
  })

  it('shows revert/verify actions and delete confirmation', async () => {
    mockList.mockResolvedValue({ dossiers: [baseSummary] })
    mockGet.mockResolvedValue(baseDetail)
    mockVerify.mockResolvedValue(baseDetail)
    mockRemove.mockResolvedValue(undefined)
    renderPanel()
    await waitFor(() => expect(screen.getByText('Dosar pentru „Impactul opțiunilor de viață”')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Dosar pentru „Impactul opțiunilor de viață”'))
    await waitFor(() => expect(screen.getByText('A1')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Reverifică')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Reverifică'))
    await waitFor(() => expect(mockVerify).toHaveBeenCalledWith(1, { recheck: false }))
    // Delete: open confirm then cancel
    fireEvent.click(screen.getByTitle('Șterge dosarul'))
    expect(screen.getByText('Stergi permanent acest dosar și toate afirmațiile sale?')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Anulează'))
    expect(screen.queryByText('Stergi permanent')).not.toBeInTheDocument()
    // Delete: confirm
    fireEvent.click(screen.getByTitle('Șterge dosarul'))
    fireEvent.click(screen.getByText('Șterge dosarul'))
    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith(1))
  })
})
