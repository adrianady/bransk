import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import type { ClaimAggregate, ClaimNode, TheoryNode } from '../lib/types'
import { ClaimRow, TheoriesView } from './TheoriesView'

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const apiMocks = vi.hoisted(() => ({
  entries: { list: vi.fn() },
  claims: { aggregate: vi.fn(), create: vi.fn() },
  getAuthToken: vi.fn(() => ''),
}))

vi.mock('./Toast', () => ({ useToast: () => toastMocks }))
vi.mock('../lib/api', () => ({
  api: {
    entries: { list: apiMocks.entries.list },
    theories: { evidence: vi.fn().mockResolvedValue([]) },
    claims: { aggregate: apiMocks.claims.aggregate, create: apiMocks.claims.create },
  },
  getAuthToken: apiMocks.getAuthToken,
}))
// vis-network needs a real canvas; stub it so the graph effect does not throw in jsdom.
class FakeNetwork {
  on() {}
  destroy() {}
  focus() {}
  fit() {}
  moveTo() {}
  getScale() { return 1 }
  selectNodes() {}
}
vi.mock('vis-network/standalone', () => ({ Network: FakeNetwork, DataSet: class {} }))

const claim: ClaimNode = {
  id: 5,
  name: 'Algoritmi',
  statement: 'Fiecare elev are un laptop.',
  parent_id: 1,
  status: 'emerging',
  pro_count: 0,
  contra_count: 0,
  pro_weight: 0,
  contra_weight: 0,
  children: [],
  node_type: 'claim',
}

const theoryNode: TheoryNode = {
  id: 1,
  name: 'Teoria A',
  description: '',
  parent_id: null,
  status: 'emerging',
  pro_count: 0,
  contra_count: 0,
  pro_weight: 0,
  contra_weight: 0,
  children: [claim],
}

const tree = [theoryNode]

const aggregate = (verdict: ClaimAggregate['verdict'], studies_accepted: number): ClaimAggregate => ({
  claim_id: 5,
  verdict,
  studies_accepted,
})

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.entries.list.mockResolvedValue([])
})

describe('Claim node colour by verdict', () => {
  it('paints supports green and contradicts red and mixed orange', () => {
    const cases: Array<[ClaimAggregate['verdict'], string]> = [
      ['supports', 'rgb(44, 111, 99)'],
      ['contradicts', 'rgb(162, 64, 63)'],
      ['mixed', 'rgb(168, 132, 60)'],
    ]
    cases.forEach(([verdict, expectedColor]) => {
      render(
        <ClaimRow
          node={claim}
          selectedId={null}
          onSelect={vi.fn()}
          depth={0}
          claimAggregates={{ 5: aggregate(verdict, 3) }}
        />,
      )
      const buttons = screen.getAllByRole('button')
      const marker = Array.from(
        buttons[buttons.length - 1].querySelectorAll('span'),
      ).find(s => s.style.backgroundColor)
      expect(marker?.style.backgroundColor).toBe(expectedColor)
      expect(buttons[buttons.length - 1].textContent).toContain('studii: 3')
    })
  })

  it('shows neutral/insufficient grey', () => {
    render(
      <ClaimRow
        node={claim}
        selectedId={null}
        onSelect={vi.fn()}
        depth={0}
        claimAggregates={{ 5: aggregate('neutral', 3) }}
      />,
    )
    const marker = Array.from(
      screen.getByRole('button').querySelectorAll('span'),
    ).find(s => s.style.backgroundColor)
    expect(marker?.style.backgroundColor).toBe('rgb(107, 114, 128)')
  })
})

describe('One study cannot colour a claim', () => {
  it('stays grey with a studii: 1 badge when fewer than 2 accepted studies', () => {
    render(
      <ClaimRow
        node={claim}
        selectedId={null}
        onSelect={vi.fn()}
        depth={0}
        claimAggregates={{ 5: aggregate('supports', 1) }}
      />,
    )
    const marker = Array.from(
      screen.getByRole('button').querySelectorAll('span'),
    ).find(s => s.style.backgroundColor)
    expect(marker?.style.backgroundColor).toBe('rgb(107, 114, 128)')
    expect(screen.getByText('studii: 1')).toBeInTheDocument()
  })

  it('shows studii: 0 when no accepted studies', () => {
    render(
      <ClaimRow
        node={claim}
        selectedId={null}
        onSelect={vi.fn()}
        depth={0}
        claimAggregates={{ 5: aggregate('insufficient', 0) }}
      />,
    )
    expect(screen.getByText('studii: 0')).toBeInTheDocument()
  })
})

describe('+ claim form', () => {
  it('calls api.claims.create with the typed statement', async () => {
    globalThis.fetch = vi.fn(async (url: string) => {
      if (String(url).includes('/theories/tree')) {
        return { ok: true, json: async () => tree } as Response
      }
      return { ok: false, status: 404 } as Response
    }) as ReturnType<typeof vi.fn>

    render(<TheoriesView onSelectEntry={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('Teoria A')).toBeInTheDocument())

    // Select the theory (its accessible name starts with the node name) so its
    // detail panel (with the + Alegere button) opens.
    fireEvent.click(screen.getByRole('button', { name: /^Teoria A/ }))

    await waitFor(() => screen.getByText('Dovezi'))
    fireEvent.click(screen.getByRole('button', { name: /\+ Alegere/ }))

    fireEvent.change(screen.getByPlaceholderText('Numele alegerei'), {
      target: { value: 'Algoritmi' },
    })
    fireEvent.change(screen.getByPlaceholderText(/ce afirmă alegerea/), {
      target: { value: 'Fiecare elev are un laptop.' },
    })

    fireEvent.click(screen.getByRole('button', { name: /Crează alegerea/ }))

    await waitFor(() =>
      expect(api.claims.create).toHaveBeenCalledWith({
        parent_id: 1,
        name: 'Algoritmi',
        statement: 'Fiecare elev are un laptop.',
      }),
    )
    expect(toastMocks.success).toHaveBeenCalledWith('Alegerea a fost creată')
  })
})
