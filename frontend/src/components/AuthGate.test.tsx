import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AUTH_EVENT, clearAuthToken, setAuthToken } from '../lib/api'
import { AuthGate } from './AuthGate'

function stubStatus(required: boolean, extra: Record<string, unknown> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ required, ...extra }), {
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
    fireEvent.click(screen.getByRole('button', { name: /Verifică tokenul/ }))

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

  it('lets an authorized browser create the shared account without persisting its password', async () => {
    setAuthToken('existing-authorized-token')
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith('/auth/status')) {
        return new Response(JSON.stringify({
          required: true,
          password_configured: false,
          authenticated: true,
          token_authenticated: true,
          session_authenticated: false,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (String(url).endsWith('/auth/setup')) {
        expect(JSON.parse(String(init?.body))).toEqual({ username: 'owner', password: 'private-password-123' })
        return new Response(JSON.stringify({ configured: true, username: 'owner' }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<AuthGate><div>app ready</div></AuthGate>)
    expect(await screen.findByText('Configurează user și parolă')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Nume de utilizator'), { target: { value: 'owner' } })
    fireEvent.change(screen.getByLabelText('Parolă nouă'), { target: { value: 'private-password-123' } })
    fireEvent.change(screen.getByLabelText('Confirmă parola'), { target: { value: 'private-password-123' } })
    fireEvent.click(screen.getByRole('button', { name: /Configurează contul/ }))

    expect(await screen.findByText('app ready')).toBeInTheDocument()
    expect(localStorage.getItem('chronokg-token')).toBe('existing-authorized-token')
    expect(JSON.stringify(localStorage)).not.toContain('private-password-123')
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/setup',
      expect.objectContaining({ credentials: 'include' }),
    ))
  })

  it('logs in with a password using a cookie and does not save the credential in browser storage', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith('/auth/status')) {
        const session = Boolean(localStorage.getItem('test-session-state'))
        return new Response(JSON.stringify({
          required: true,
          password_configured: true,
          authenticated: session,
          token_authenticated: false,
          session_authenticated: session,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (String(url).endsWith('/auth/login')) {
        expect((init?.credentials)).toBe('include')
        localStorage.setItem('test-session-state', 'true')
        return new Response(JSON.stringify({ authenticated: true, username: 'owner' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<AuthGate><div>app ready</div></AuthGate>)
    expect(await screen.findByText('Acces protejat')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Nume de utilizator'), { target: { value: 'owner' } })
    fireEvent.change(screen.getByLabelText('Parolă'), { target: { value: 'private-password-123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Intră' }))

    expect(await screen.findByText('app ready')).toBeInTheDocument()
    expect(localStorage.getItem('chronokg-token')).toBeNull()
    expect(localStorage.getItem('private-password-123')).toBeNull()
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', expect.any(Object))
  })

  it('revokes the current session on logout and returns to the gate', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith('/auth/status')) {
        return new Response(JSON.stringify({
          required: true,
          password_configured: true,
          authenticated: true,
          token_authenticated: false,
          session_authenticated: true,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (String(url).endsWith('/auth/logout')) {
        expect(init?.method).toBe('POST')
        return new Response(null, { status: 204 })
      }
      throw new Error(`Unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<AuthGate><div>app ready</div></AuthGate>)
    expect(await screen.findByText('app ready')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Ieși din cont' }))

    expect(await screen.findByText('Acces protejat')).toBeInTheDocument()
    expect(screen.getByText('Ai ieșit din cont.')).toBeInTheDocument()
    expect(localStorage.getItem('chronokg-token')).toBeNull()
  })
})
