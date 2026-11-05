import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import type { ProjectEvidenceMap, ProjectStudyRecommendation, StudySource } from '../lib/types'
import { ThesisEvidenceMap } from './ThesisEvidenceMap'

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const apiMocks = vi.hoisted(() => ({
  createClaim: vi.fn(),
  addEvidence: vi.fn(),
  sources: vi.fn(),
  listNotes: vi.fn(),
}))

vi.mock('./Toast', () => ({ useToast: () => toastMocks }))
vi.mock('../lib/api', () => ({
  api: {
    project: {
      createClaim: apiMocks.createClaim,
      addEvidence: apiMocks.addEvidence,
    },
    studyQA: { sources: apiMocks.sources },
    studies: { notes: { list: apiMocks.listNotes } },
  },
}))

const study: ProjectStudyRecommendation = {
  entry_id: 7,
  title: 'Studiu despre alfabetizare digitală',
  year: '2024',
  study_type: 'paper',
  design: 'cohort',
  quality: null,
  effect_direction: '',
  authors: 'Autor',
  has_pdf: true,
  score: 1,
  matched_terms: [],
  reason: '',
}

const source: StudySource = {
  document_id: 81,
  entry_id: 7,
  chunk_index: 3,
  page_number: 12,
  page_verified: true,
  preview: 'Fragment verificat din PDF.',
}

function makeMap(withEvidence = false): ProjectEvidenceMap {
  return {
    sections: [{
      id: 's1',
      title: 'Introducere',
      description: '',
      target_words: 1000,
      written_words: 250,
      claims: [{
        id: 13,
        section_id: 's1',
        text: 'Alfabetizarea digitală influențează procesul educațional.',
        created_at: '',
        updated_at: '',
        evidence: withEvidence ? [{
          id: 21,
          entry_id: 7,
          study_title: study.title,
          study_authors: study.authors,
          relation: 'supports',
          document: source,
          note: null,
          created_at: '',
        }] : [],
      }],
    }],
    orphan_claims: [],
    progress: {
      written_words: 250,
      target_words: 1000,
      writing_percent: 25,
      sections_with_claims: 1,
      sections_with_evidence: withEvidence ? 1 : 0,
      total_sections: 1,
      claims_with_evidence: withEvidence ? 1 : 0,
      claims_with_pdf_fragment: withEvidence ? 1 : 0,
      total_claims: 1,
      evidence_percent: withEvidence ? 100 : 0,
    },
  }
}

const refresh = vi.fn(async () => {})

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.createClaim.mockResolvedValue({})
  apiMocks.addEvidence.mockResolvedValue({})
  apiMocks.sources.mockResolvedValue({ entry_id: 7, sources: [source] })
  apiMocks.listNotes.mockResolvedValue([])
})

describe('ThesisEvidenceMap', () => {
  it('explains the empty thesis structure and uses persisted progress', () => {
    const emptyMap = makeMap()
    emptyMap.sections = []
    emptyMap.progress = {
      ...emptyMap.progress,
      total_claims: 0,
      claims_with_evidence: 0,
      claims_with_pdf_fragment: 0,
      evidence_percent: null,
    }
    render(
      <ThesisEvidenceMap
        map={emptyMap}
        studies={[]}
        loading={false}
        onRefresh={refresh}
        onSelectEntry={vi.fn()}
      />,
    )

    expect(screen.getByText(/Nu există capitole în structura tezei/)).toBeInTheDocument()
    expect(screen.getByText('25%')).toBeInTheDocument()
    expect(screen.getByText('0 / 0')).toBeInTheDocument()
  })

  it('links a selected study fragment to a claim and opens persisted evidence', async () => {
    const onOpenSource = vi.fn()
    render(
      <ThesisEvidenceMap
        map={makeMap(true)}
        studies={[study]}
        loading={false}
        onRefresh={refresh}
        onSelectEntry={vi.fn()}
        onOpenSource={onOpenSource}
      />,
    )

    fireEvent.click(screen.getByTitle('Deschide studiul și fragmentul PDF'))
    expect(onOpenSource).toHaveBeenCalledWith(7, source)

    fireEvent.click(screen.getByRole('button', { name: /Leagă studiu sau fragment/ }))
    fireEvent.change(screen.getByLabelText('Studiu'), { target: { value: '7' } })
    await waitFor(() => expect(api.studyQA.sources).toHaveBeenCalledWith(7))
    fireEvent.change(screen.getByLabelText('Fragment PDF (opțional)'), { target: { value: '81' } })
    fireEvent.click(screen.getByRole('button', { name: 'Leagă dovada' }))

    await waitFor(() => expect(api.project.addEvidence).toHaveBeenCalledWith(13, {
      entry_id: 7,
      document_id: 81,
      note_id: null,
      relation: 'supports',
    }))
    expect(refresh).toHaveBeenCalled()
  })
})
