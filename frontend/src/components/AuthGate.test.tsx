import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AUTH_EVENT, clearAuthToken, setAuthToken } from '../lib/api'
import { AuthGate } from './AuthGate'

function stubStatus(required: boolean) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ required }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
}

beforeEach(() => {
  clearAuthToken()
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AuthGate', () => {
  it('gets out of the way when the instance is local-only', async () => {
    stubStatus(false)
    render(<AuthGate><div>app ready</div></AuthGate>)
    expect(await screen.findByText('app ready')).toBeInTheDocument()
  })

  it('asks for the token and lets the app through once entered', async () => {
    stubStatus(true)
    render(<AuthGate><div>app ready</div></AuthGate>)

    expect(await screen.findByText('Acces protejat')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('token'), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Intră/ }))

    expect(await screen.findByText('app ready')).toBeInTheDocument()
    expect(localStorage.getItem('chronokg-token')).toBe('secret')
  })

  it('asks again when the saved token contains invalid header characters', async () => {
    stubStatus(true)
    setAuthToken(`${'a'.repeat(165)}\uFFFD`)

    render(<AuthGate><div>app ready</div></AuthGate>)

    expect(await screen.findByText('Acces protejat')).toBeInTheDocument()
    expect(localStorage.getItem('chronokg-token')).toBeNull()
  })

  it('comes back when the server rejects the stored token', async () => {
    stubStatus(false)
    render(<AuthGate><div>app ready</div></AuthGate>)
    expect(await screen.findByText('app ready')).toBeInTheDocument()

    fireEvent(window, new Event(AUTH_EVENT))

    expect(await screen.findByText('Acces protejat')).toBeInTheDocument()
    expect(screen.getByText(/nu mai este acceptat/)).toBeInTheDocument()
  })
})
