import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../lib/api', () => ({
  api: {
    parse: { summary: vi.fn() },
    notes: { verifySummary: vi.fn() },
    analysis: { capabilities: vi.fn(), run: vi.fn() },
    claims: {
      forEntry: vi.fn(),
      aggregate: vi.fn(),
      decideStance: vi.fn(),
    },
  },
}))

import { api } from '../lib/api'
import { StudyAnalysisPanel } from './StudyAnalysisPanel'

const parseSummary = {
  entry_id: 1,
  pdf_path: 'studies/1.pdf',
  kind: 'native' as const,
  pages: 5,
  pages_native: 5,
  pages_scanned: 0,
  pages_empty: 0,
  parsed: { native: 5, vision_ok: 0, vision_uncertain: 0, manual: 0, error: 0, missing: 0 },
  per_page: [],
}

const verifySummary = {
  statements: 7,
  by_status: { verificat: 3 },
  notes_total: 7,
  notes_fully_verified: 3,
  pages_with_printed_numbers: 5,
  pages_total: 5,
}

const claimStance = {
  id: 1,
  claim_id: 10,
  entry_id: 1,
  claim_name: 'Claimul A',
  statement: 'Claimul A',
  direction: 'supports' as const,
  aspect: '',
  evidence_page: 46,
  evidence_quote: 'Citatul verbatim de pe pagina 46.',
  reasoning: 'Motivarea sistemului.',
  confidence: 0.85,
  status: 'proposed' as const,
  proposed_by: 'ai' as const,
  provider: 'deepseek',
  model: 'model',
  created_at: '2026-01-01',
  decided_at: null,
}

const aggregate = {
  claim_id: 10,
  verdict: 'supports' as const,
  studies_accepted: 1,
  per_entry: [],
  per_study: [],
}

const nliUnavailable = {
  nli: { available: false, reason: 'NLI nedisponibil local' },
  llm_rationale: { available: false, reason: '' },
}

const renderPanel = (open = true) =>
  render(<StudyAnalysisPanel entryId={1} title='Studiu de test' open={open} onClose={vi.fn()} />)

afterEach(() => {
  vi.resetAllMocks()
})

describe('StudyAnalysisPanel', () => {
  it('renders the three status chips', async () => {
    vi.mocked(api.parse.summary).mockResolvedValue(parseSummary)
    vi.mocked(api.notes.verifySummary).mockResolvedValue(verifySummary)
    vi.mocked(api.claims.forEntry).mockResolvedValue({
      entry_id: 1,
      entry_title: 'Studiu de test',
      claims: [],
      bias: { available: false, reason: 'not_implemented' },
    })

    renderPanel()

    await waitFor(() => expect(screen.getByText(/PDF: nativ/)).toBeInTheDocument())
    expect(screen.getByText(/Verificare note: 3\/7/)).toBeInTheDocument()
    expect(screen.getByText(/NLI: indisponibil/)).toBeInTheDocument()
  })

  it('renders a green label for a claim whose verdict is supports', async () => {
    vi.mocked(api.parse.summary).mockResolvedValue(parseSummary)
    vi.mocked(api.notes.verifySummary).mockResolvedValue(verifySummary)
    vi.mocked(api.claims.forEntry).mockResolvedValue({
      entry_id: 1,
      entry_title: 'Studiu',
      claims: [claimStance],
      bias: { available: false, reason: 'not_implemented' },
    })
    vi.mocked(api.claims.aggregate).mockResolvedValue(aggregate)
    vi.mocked(api.analysis.capabilities).mockResolvedValue(nliUnavailable)

    renderPanel()

    await waitFor(() => expect(screen.getByText('susține')).toBeInTheDocument())
  })

  it('expanding a claim shows the quote and p. 46', async () => {
    vi.mocked(api.parse.summary).mockResolvedValue(parseSummary)
    vi.mocked(api.notes.verifySummary).mockResolvedValue(verifySummary)
    vi.mocked(api.claims.forEntry).mockResolvedValue({
      entry_id: 1,
      entry_title: 'Studiu',
      claims: [claimStance],
      bias: { available: false, reason: 'not_implemented' },
    })
    vi.mocked(api.claims.aggregate).mockResolvedValue(aggregate)
    vi.mocked(api.analysis.capabilities).mockResolvedValue(nliUnavailable)

    renderPanel()

    // The claim row header is clickable to expand.
    await waitFor(() => expect(screen.getByText('Claimul A')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Claimul A').closest('button')!)

    await waitFor(() => expect(screen.getByText('Citatul verbatim de pe pagina 46.')).toBeInTheDocument())
    expect(screen.getByText('p. 46')).toBeInTheDocument()
    // AI badge for a proposed stance.
    expect(screen.getByText(/propus de AI · verifică/)).toBeInTheDocument()
  })

  it('accepting calls decideStance with { status: "accepted" }', async () => {
    vi.mocked(api.parse.summary).mockResolvedValue(parseSummary)
    vi.mocked(api.notes.verifySummary).mockResolvedValue(verifySummary)
    vi.mocked(api.claims.forEntry).mockResolvedValue({
      entry_id: 1,
      entry_title: 'Studiu',
      claims: [claimStance],
      bias: { available: false, reason: 'not_implemented' },
    })
    vi.mocked(api.claims.aggregate).mockResolvedValue(aggregate)
    vi.mocked(api.analysis.capabilities).mockResolvedValue(nliUnavailable)
    vi.mocked(api.claims.decideStance).mockResolvedValue({ ...claimStance, status: 'accepted' })

    renderPanel()

    await waitFor(() => expect(screen.getByText('Claimul A')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Claimul A').closest('button')!)
    await waitFor(() => screen.getByText('Accept'))
    fireEvent.click(screen.getByText('Accept'))

    await waitFor(() =>
      expect(api.claims.decideStance).toHaveBeenCalledWith(1, { status: 'accepted' }),
    )
  })

  it('a failed decide shows an inline error and keeps the row', async () => {
    const error = new Error('Serverul a refuzat decizia.')
    vi.mocked(api.parse.summary).mockResolvedValue(parseSummary)
    vi.mocked(api.notes.verifySummary).mockResolvedValue(verifySummary)
    vi.mocked(api.claims.forEntry).mockResolvedValue({
      entry_id: 1,
      entry_title: 'Studiu',
      claims: [claimStance],
      bias: { available: false, reason: 'not_implemented' },
    })
    vi.mocked(api.claims.aggregate).mockResolvedValue(aggregate)
    vi.mocked(api.analysis.capabilities).mockResolvedValue(nliUnavailable)
    vi.mocked(api.claims.decideStance).mockRejectedValue(error)

    renderPanel()

    await waitFor(() => expect(screen.getByText('Claimul A')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Claimul A').closest('button')!)
    await waitFor(() => screen.getByText('Accept'))
    fireEvent.click(screen.getByText('Accept'))

    await waitFor(() => expect(screen.getByText(/Serverul a refuzat decizia./)).toBeInTheDocument())
    // The claim row remains on screen (not removed on failure).
    expect(screen.getByText('Claimul A')).toBeInTheDocument()
  })

  it('a missing NLI capability renders the grey chip with the reason text', async () => {
    vi.mocked(api.parse.summary).mockResolvedValue(parseSummary)
    vi.mocked(api.notes.verifySummary).mockResolvedValue(verifySummary)
    vi.mocked(api.claims.forEntry).mockResolvedValue({
      entry_id: 1,
      entry_title: 'Studiu',
      claims: [claimStance],
      bias: { available: false, reason: 'not_implemented' },
    })
    vi.mocked(api.claims.aggregate).mockResolvedValue(aggregate)
    vi.mocked(api.analysis.capabilities).mockResolvedValue(nliUnavailable)

    renderPanel()

    await waitFor(() => expect(screen.getByText(/NLI: indisponibil/)).toBeInTheDocument())
    const chip = screen.getByText(/NLI: indisponibil/).closest('button')!
    expect(chip).toHaveAttribute('title', 'NLI nedisponibil local')
  })
})
