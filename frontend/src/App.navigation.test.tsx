import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { VIEW_NAV_ITEMS } from './lib/navigation'

vi.mock('./lib/api', () => ({
  api: {
    project: { get: vi.fn().mockResolvedValue({ title: 'Test project' }) },
    entries: { get: vi.fn() },
  },
  ApiError: class ApiError extends Error {},
}))

vi.mock('./components/Toast', () => ({ useToast: () => ({ error: vi.fn() }) }))
vi.mock('./components/ProjectView', () => ({ ProjectView: () => <div data-testid="project-view" /> }))
vi.mock('./components/Timeline', () => ({ TimelineView: () => <div data-testid="timeline-view" /> }))
vi.mock('./components/GraphView', () => ({ GraphView: () => <div data-testid="graph-view" /> }))
vi.mock('./components/ChatPanel', () => ({ ChatPanel: () => <div data-testid="chat-panel" /> }))
vi.mock('./components/EntryForm', () => ({ EntryForm: () => <div data-testid="entry-form" /> }))
vi.mock('./components/EntryDetail', () => ({ EntryDetail: () => <div data-testid="entry-detail" /> }))
vi.mock('./components/EntryList', () => ({ EntryList: () => <div data-testid="entry-list" /> }))
vi.mock('./components/ReviewPanel', () => ({ ReviewPanel: () => <div data-testid="review-view" /> }))
vi.mock('./components/TodoList', () => ({ TodoList: () => <div data-testid="todos-view" /> }))
vi.mock('./components/ThoughtsView', () => ({ ThoughtsView: () => <div data-testid="thoughts-view" /> }))
vi.mock('./components/StudiesView', () => ({ StudiesView: () => <div data-testid="studies-view" /> }))
vi.mock('./components/TheoriesView', () => ({ TheoriesView: () => <div data-testid="theories-view" /> }))
vi.mock('./components/AiStatusModal', () => ({ AiStatusModal: () => <div data-testid="ai-settings" /> }))

beforeEach(() => {
  localStorage.clear()
  window.location.hash = ''
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  })
})

describe('App navigation', () => {
  // „Intrări” is the second tab of the unified „Bibliotecă” screen, so the
  // nav shows one button whose label replaces both item labels.
  const navItems = VIEW_NAV_ITEMS.filter(item => item.id !== 'entries')
  const navLabel = (id: string) => (id === 'studies' ? 'Bibliotecă' : VIEW_NAV_ITEMS.find(i => i.id === id)!.label)

  it('routes each desktop navigation action to its preserved hash id', () => {
    render(<App />)
    const nav = screen.getByRole('navigation', { name: 'Navigare principală' })

    for (const item of navItems) {
      const label = navLabel(item.id)
      fireEvent.click(within(nav).getByRole('button', { name: label }))
      expect(window.location.hash).toBe(`#${item.id}`)
      expect(within(nav).getByRole('button', { name: label })).toHaveAttribute('aria-current', 'page')
    }
  })

  it('keeps the library button active on both of its tabs', () => {
    window.location.hash = '#entries'
    render(<App />)
    const nav = screen.getByRole('navigation', { name: 'Navigare principală' })
    expect(within(nav).getByRole('button', { name: 'Bibliotecă' })).toHaveAttribute('aria-current', 'page')

    fireEvent.click(within(nav).getByRole('button', { name: 'Bibliotecă' }))
    expect(window.location.hash).toBe('#studies')
  })

  it('keeps all views in the mobile drawer and retains global actions in the header', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Deschide meniul principal' }))

    const navigations = screen.getAllByRole('navigation', { name: 'Navigare principală' })
    expect(navigations).toHaveLength(2)
    const drawer = navigations.find(nav => nav.id === 'mobile-navigation')!
    for (const item of navItems) {
      expect(within(drawer).getByRole('button', { name: navLabel(item.id) })).toBeInTheDocument()
    }

    expect(screen.getByRole('button', { name: 'Chat AI' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Stare și setări AI' }).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Schimbă tema' })).toBeInTheDocument()

    fireEvent.click(within(drawer).getByRole('button', { name: 'Teorii' }))
    expect(window.location.hash).toBe('#theories')
    expect(screen.getAllByRole('navigation', { name: 'Navigare principală' })).toHaveLength(1)
  })
})
