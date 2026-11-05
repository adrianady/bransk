import { useEffect, useState } from 'react'
import { Loader2, LockKeyhole, ShieldCheck } from 'lucide-react'
import {
  AUTH_EVENT,
  clearAuthToken,
  fetchAuthStatus,
  getAuthToken,
  setAuthToken,
} from '../lib/api'

/**
 * Asks for the shared access token when the server requires one.
 *
 * Local-first instances answer `required: false` and never see this screen.
 * When Bransk is exposed on the network (`./start.sh -n`), the token printed
 * in the terminal is entered here once and kept in localStorage. A rejected
 * token (e.g. the server regenerated it) brings the prompt back instead of
 * leaving every panel showing a raw 401.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<'checking' | 'ready' | 'locked'>('checking')
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let alive = true
    fetchAuthStatus()
      .then(({ required }) => {
        if (!alive) return
        setState(required && !getAuthToken() ? 'locked' : 'ready')
      })
      // An unreachable/older backend must not freeze the app behind a gate.
      .catch(() => { if (alive) setState('ready') })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    const onUnauthorized = () => {
      clearAuthToken()
      setToken('')
      setError('Tokenul nu mai este acceptat. Introdu-l din nou.')
      setState('locked')
    }
    window.addEventListener(AUTH_EVENT, onUnauthorized)
    return () => window.removeEventListener(AUTH_EVENT, onUnauthorized)
  }, [])

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    const value = token.trim()
    if (!value) {
      setError('Scrie tokenul afișat de start.sh.')
      return
    }
    setSubmitting(true)
    setAuthToken(value)
    setToken('')
    setError('')
    setState('ready')
    setSubmitting(false)
  }

  if (state === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Verific accesul…
        </div>
      </div>
    )
  }

  if (state === 'locked') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
        <form onSubmit={submit} className="card max-w-md w-full space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-chrono-100 dark:bg-chrono-900/40 flex items-center justify-center">
              <LockKeyhole className="w-5 h-5 text-chrono-600 dark:text-chrono-400" />
            </div>
            <div>
              <h1 className="font-bold text-lg">Acces protejat</h1>
              <p className="text-sm text-gray-500">
                Această instanță rulează pe rețea și cere un token.
              </p>
            </div>
          </div>

          <label className="block">
            <span className="text-sm font-medium">Token de acces</span>
            <span className="block text-xs text-gray-500 mb-1">
              Îl găsești în terminalul unde ai rulat <code>./start.sh -n</code>.
            </span>
            <input
              type="password"
              value={token}
              onChange={e => setToken(e.target.value)}
              className="input w-full"
              autoFocus
              autoComplete="current-password"
              placeholder="token"
            />
          </label>

          {error && <p className="text-sm text-madder">{error}</p>}

          <button type="submit" disabled={submitting} className="btn-primary w-full">
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
            Intră
          </button>
          <p className="text-[11px] text-gray-400">
            Tokenul rămâne doar în acest browser. Pe același calculator, rulează fără
            <code> -n</code> ca să nu fie nevoie de el.
          </p>
        </form>
      </div>
    )
  }

  return <>{children}</>
}
