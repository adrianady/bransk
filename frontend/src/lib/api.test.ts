import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AUTH_EVENT,
  ApiError,
  api,
  clearAuthToken,
  fetchAuthStatus,
  getAuthToken,
  loginSharedAccount,
  setAuthToken,
} from './api'

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

function stubFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl)
  vi.stubGlobal('fetch', fn)
  return fn
}

beforeEach(() => {
  clearAuthToken()
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('error handling', () => {
  it('turns a FastAPI validation list into one readable sentence', async () => {
    stubFetch(async () =>
      jsonResponse(
        {
          detail: [
            { loc: ['body', 'title'], msg: 'String should have at most 500 characters' },
          ],
        },
        422,
      ),
    )

    const err = await api.entries.update(1, { title: 'x' }).catch(e => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(422)
    expect(err.message).toBe('title: String should have at most 500 characters')
  })

  it('keeps a string detail as the message', async () => {
    stubFetch(async () => jsonResponse({ detail: 'Entry already exists' }, 409))

    const err = await api.entries.create({ title: 'dup' }).catch(e => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(409)
    expect(err.message).toBe('Entry already exists')
  })

  it('handles 204 with no body', async () => {
    stubFetch(async () => new Response(null, { status: 204 }))
    await expect(api.entries.delete(7)).resolves.toBeUndefined()
  })

  it('announces a 401 so the auth gate can ask for the token again', async () => {
    setAuthToken('stale')
    const listener = vi.fn()
    window.addEventListener(AUTH_EVENT, listener)
    stubFetch(async () => jsonResponse({ detail: 'Autentificare necesară' }, 401))

    await expect(api.entries.list()).rejects.toMatchObject({ status: 401 })
    expect(listener).toHaveBeenCalledTimes(1)

    window.removeEventListener(AUTH_EVENT, listener)
  })
})

describe('auth token', () => {
  it('sends cookie credentials with API requests while retaining bearer compatibility', async () => {
    setAuthToken('abc123')
    const fetchMock = stubFetch(async () => jsonResponse([]))

    await api.entries.list()

    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer abc123')
  })

  it('checks auth status with cookies and the stored bearer token', async () => {
    setAuthToken('existing-token')
    const fetchMock = stubFetch(async () => jsonResponse({ required: true }))

    await fetchAuthStatus()

    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer existing-token')
  })

  it('does not treat a rejected password as a rejected bearer token', async () => {
    const listener = vi.fn()
    window.addEventListener(AUTH_EVENT, listener)
    stubFetch(async () => jsonResponse({ detail: 'Nume sau parolă incorectă' }, 401))

    await expect(loginSharedAccount('owner', 'wrong-password')).rejects.toMatchObject({ status: 401 })
    expect(listener).not.toHaveBeenCalled()

    window.removeEventListener(AUTH_EVENT, listener)
  })

  it('sends the stored token as a bearer header', async () => {
    setAuthToken('abc123')
    const fetchMock = stubFetch(async () => jsonResponse([]))

    await api.entries.list()

    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer abc123')
  })

  it('sends no authorization header when no token is stored', async () => {
    const fetchMock = stubFetch(async () => jsonResponse([]))

    await api.entries.list()

    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Authorization')).toBeNull()
  })

  it('drops a malformed Unicode token before building request headers', async () => {
    setAuthToken(`${'a'.repeat(165)}\uFFFD`)
    const fetchMock = stubFetch(async (_url, init) => {
      // Simulate the browser's conversion of RequestInit.headers to ByteString.
      new Headers(init?.headers)
      return jsonResponse([])
    })

    await expect(api.entries.list()).resolves.toEqual([])

    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Authorization')).toBeNull()
    expect(getAuthToken()).toBe('')
  })

  it('trims a pasted token and removes embedded control characters', () => {
    setAuthToken(' valid-token \n')
    expect(getAuthToken()).toBe('valid-token')

    setAuthToken('valid\ntoken')
    expect(getAuthToken()).toBe('')
    expect(localStorage.getItem('chronokg-token')).toBeNull()
  })

  it('remembering and clearing the token round-trips', () => {
    expect(getAuthToken()).toBe('')
    setAuthToken('t')
    expect(getAuthToken()).toBe('t')
    clearAuthToken()
    expect(getAuthToken()).toBe('')
  })

  it('keeps the access token out of the PDF URL and exposes it as an auth header', () => {
    expect(api.studies.pdfUrl(5)).toBe('/api/studies/5/pdf')
    setAuthToken('private-token')
    expect(api.studies.pdfUrl(5)).toBe('/api/studies/5/pdf')
    expect(api.studies.pdfHeaders()).toEqual({ Authorization: 'Bearer private-token' })
  })
})

describe('studies pagination', () => {
  it('reads the real total from X-Total-Count', async () => {
    stubFetch(async () => jsonResponse([{ id: 1 }], 200, { 'X-Total-Count': '106' }))

    const page = await api.studies.list({ limit: 100, offset: 0 })
    expect(page.items).toHaveLength(1)
    expect(page.total).toBe(106)
  })

  it('falls back to the page length when the header is missing', async () => {
    stubFetch(async () => jsonResponse([{ id: 1 }, { id: 2 }]))

    const page = await api.studies.list()
    expect(page.total).toBe(2)
  })

  it('walks every page so no study stays invisible behind the page cap', async () => {
    const requested: string[] = []
    stubFetch(async (url) => {
      requested.push(url)
      const offset = Number(new URL(url, 'http://x').searchParams.get('offset') || 0)
      const items = offset === 0 ? [{ id: 1 }, { id: 2 }] : offset === 2 ? [{ id: 3 }] : []
      return jsonResponse(items, 200, { 'X-Total-Count': '3' })
    })

    const all = await api.studies.all(2)

    expect(all.items.map(i => i.id)).toEqual([1, 2, 3])
    expect(requested).toHaveLength(2)
  })
})

describe('OpenCode Console API contract', () => {
  it('posts the Console key only in the authenticated configuration body', async () => {
    const secret = 'console-test-key'
    const fetchMock = stubFetch(async () => jsonResponse({
      configured: true,
      base_url: 'https://opencode.ai/inference',
      has_key: true,
    }))

    await api.ai.saveOpenCodeConsole({
      base_url: 'https://opencode.ai/inference',
      api_key: secret,
    })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/ai/opencode-console')
    expect(url).not.toContain(secret)
    expect(JSON.parse(String(init.body))).toEqual({
      base_url: 'https://opencode.ai/inference',
      api_key: secret,
    })
    expect(localStorage.getItem('opencode-console-key')).toBeNull()
  })

  it('can save a Console model without switching providers', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ active: 'console/text-small', cleared: false }))

    await api.ai.setModel('console/text-small', 'opencode_console')

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      model_id: 'console/text-small',
      provider: 'opencode_console',
    })
  })
})
