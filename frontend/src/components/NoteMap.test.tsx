import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { NoteMap } from './NoteMap'
import { ToastProvider } from './Toast'

const hoisted = vi.hoisted(() => ({
  projectMap: vi.fn(),
  regenerateProjectMap: vi.fn(),
  regenerateMap: vi.fn(),
  addEdge: vi.fn(),
  removeEdge: vi.fn(),
  removeConcept: vi.fn(),
}))

vi.mock('../lib/api', () => ({
  api: {
    noteDossiers: {
      projectMap: hoisted.projectMap,
      regenerateProjectMap: hoisted.regenerateProjectMap,
      regenerateMap: hoisted.regenerateMap,
      addEdge: hoisted.addEdge,
      removeEdge: hoisted.removeEdge,
      removeConcept: hoisted.removeConcept,
    },
  },
}))

const mockRegenerateProjectMap = hoisted.regenerateProjectMap

const baseClaims = [
  { code: 'A1', statement: 'Prima afirmație verificată cu citat pe pagină.', status: 'verificat' },
  { code: 'A2', statement: 'A doua afirmație nu este delușie.', status: 'neverificat' },
]

const baseEdges = [
  { id: 1, from_code: 'A1', to_code: 'A2', motive: 'contradică prima', page: '3', claim_code: 'A2' },
]

const baseDocs = [
  { dossier_id: 1, entry_id: 1, title: 'Studiu uno', authors: 'Autor A', year: '2020', status: 'verificat', claims_total: 2, claims_verificat: 1 },
  { dossier_id: 2, entry_id: 2, title: 'Studiu doi', authors: 'Autor B', year: '2021', status: 'partial', claims_total: 2, claims_verificat: 0 },
]

const baseConcepts = [
  {
    id: 10, code: 'C1', label: 'Motivație', description: 'Descriere scurtă.',
    links: [
      { dossier_id: 1, claim_code: 'A1', note: 'sprijină', page: '2', statement: 's1', page_printed: '2' },
      { dossier_id: 2, claim_code: 'A3', note: 'completează', page: '5', statement: 's2', page_printed: '5' },
    ],
  },
]

function renderMap(props: Parameters<typeof NoteMap>[0]) {
  return render(
    <ToastProvider>
      <NoteMap {...props} />
    </ToastProvider>,
  )
}

describe('NoteMap dossier scope', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders claim nodes and exactly one edge polyline; small print has motive and p.', () => {
    const payload = {
      dossier_id: 1,
      entry_id: 1,
      title: 'Dosar de test',
      claims: baseClaims,
      edges: baseEdges,
    }
    renderMap({ scope: 'dossier', payload, dossierId: 1 })
    expect(screen.getAllByText(/Prima afirmație/).length).toBeGreaterThanOrEqual(1)
    expect(document.querySelectorAll('polyline').length).toBe(1)
    expect(document.body.innerHTML).toContain('contradică prima')
    expect(document.body.innerHTML).toContain('(p. 3)')
  })

  it('shows the Romanian empty-edges explanation and still renders the claim nodes', () => {
    const payload = { dossier_id: 1, entry_id: 1, title: 'Dosar', claims: baseClaims, edges: [] }
    renderMap({ scope: 'dossier', payload, dossierId: 1 })
    expect(screen.getByText(/Încă nicio muchie/)).toBeInTheDocument()
    expect(document.querySelectorAll('svg circle').length).toBeGreaterThanOrEqual(2)
  })

  it('selecting a node reveals its details panel', () => {
    const payload = { dossier_id: 1, entry_id: 1, title: 'Dosar', claims: baseClaims, edges: [] }
    renderMap({ scope: 'dossier', payload, dossierId: 1 })
    fireEvent.click(screen.getByRole('button', { name: /Prima afirmație/ }))
    expect(screen.getByText('Detalii selecție')).toBeInTheDocument()
    expect(screen.getByText('Prima afirmație verificată cu citat pe pagină.')).toBeInTheDocument()
  })
})

describe('NoteMap project scope', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('marks a concept linking ≥2 documents as bridged', () => {
    const payload: any = {
      documents: baseDocs,
      concepts: baseConcepts,
      bridges: [{ concept_code: 'C1', dossier_ids: [1, 2] }],
      stats: { documents: 2, concepts: 1, bridged_concepts: 1, links: 2 },
    }
    renderMap({ scope: 'project', payload })
    expect(document.body.textContent).toContain('apare în: 2 documente')
    expect(screen.getByRole('button', { name: 'Motivație' })).toBeInTheDocument()
  })

  it('regenerates the project map on button click', async () => {
    mockRegenerateProjectMap.mockResolvedValue({
      documents: [], concepts: [], bridges: [],
      stats: { documents: 0, concepts: 0, bridged_concepts: 0, links: 0 },
    })
    renderMap({ scope: 'project', payload: undefined })
    fireEvent.click(screen.getAllByRole('button', { name: /Regenerează harta/ })[0])
    await waitFor(() => expect(mockRegenerateProjectMap).toHaveBeenCalled())
  })
})
