import { createContext, useContext, useEffect, useState } from 'react'
import { KeyRound, Loader2, LockKeyhole, LogOut, ShieldCheck } from 'lucide-react'
import {
  AUTH_EVENT,
  changeSharedPassword,
  clearAuthToken,
  fetchAuthStatus,
  getAuthToken,
  loginSharedAccount,
  logoutSharedAccount,
  setAuthToken,
  setupSharedAccount,
  type AuthStatus,
} from '../lib/api'

type GateMode = 'checking' | 'ready' | 'locked' | 'setup'

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

/** Enforces network auth while keeping the local-first instance frictionless. */
/** Lets the app surfaces (e.g. the mobile drawer) offer logout without a
 *  fixed button covering the header controls. */
export const AuthContext = createContext<{
  sessionAuthenticated: boolean
  logout: () => Promise<void>
  /** True in network mode when a password can be configured/managed. */
  canManagePassword: boolean
  /** Opens the setup or manage-password dialog (used by the mobile drawer). */
  openPasswordManager: () => void
}>({
  sessionAuthenticated: false,
  logout: async () => {},
  canManagePassword: false,
  openPasswordManager: () => {},
})

export function useAuthStatus() {
  return useContext(AuthContext)
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<GateMode>('checking')
  const [authStatus, setAuthStatus] = useState<AuthStatus>({ required: false })
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [showTokenEntry, setShowTokenEntry] = useState(false)
  const [managePassword, setManagePassword] = useState(false)

  const applyStatus = (incoming: AuthStatus) => {
    const tokenAuthenticated = incoming.token_authenticated ?? !!getAuthToken()
    const resolved: AuthStatus = {
      ...incoming,
      token_authenticated: tokenAuthenticated,
      authenticated: incoming.authenticated ?? (tokenAuthenticated || !!incoming.session_authenticated),
    }
    setAuthStatus(resolved)

    if (!resolved.required) {
      setMode('ready')
    } else if (resolved.password_configured === false && tokenAuthenticated) {
      setMode('setup')
    } else if (resolved.authenticated || tokenAuthenticated) {
      setMode('ready')
    } else {
      setMode('locked')
    }
  }

  useEffect(() => {
    let alive = true
    fetchAuthStatus()
      .then(status => { if (alive) applyStatus(status) })
      // An unreachable/older backend must not freeze the app behind a gate.
      .catch(() => { if (alive) setMode('ready') })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    const onUnauthorized = () => {
      clearAuthToken()
      setToken('')
      setPassword('')
      setConfirmation('')
      setError('Sesiunea a expirat sau tokenul nu mai este acceptat. Autentifică-te din nou.')
      setShowTokenEntry(true)
      setMode('locked')
      setAuthStatus(current => ({
        ...current,
        authenticated: false,
        token_authenticated: false,
        session_authenticated: false,
      }))
    }
    window.addEventListener(AUTH_EVENT, onUnauthorized)
    return () => window.removeEventListener(AUTH_EVENT, onUnauthorized)
  }, [])

  const submitToken = async (event: React.FormEvent) => {
    event.preventDefault()
    const value = token.trim()
    if (!value) {
      setError('Scrie tokenul existent, disponibil în browserul deja autorizat.')
      return
    }
    setSubmitting(true)
    setError('')
    setAuthToken(value)
    try {
      const status = await fetchAuthStatus()
      if (status.token_authenticated === false) {
        clearAuthToken()
        setError('Tokenul nu este acceptat de server.')
        return
      }
      setToken('')
      applyStatus({ ...status, token_authenticated: status.token_authenticated ?? true })
      setShowTokenEntry(false)
    } catch (err) {
      setError(messageOf(err, 'Nu am putut verifica tokenul.'))
    } finally {
      setSubmitting(false)
    }
  }

  const submitSetup = async (event: React.FormEvent) => {
    event.preventDefault()
    if (password.length < 12) {
      setError('Alege o parolă de cel puțin 12 caractere.')
      return
    }
    if (password !== confirmation) {
      setError('Parolele nu coincid.')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const result = await setupSharedAccount(username.trim(), password)
      setPassword('')
      setConfirmation('')
      setUsername('')
      setNotice(`Contul „${result.username}” este configurat. Folosește userul și parola pe telefon.`)
      setAuthStatus(current => ({
        ...current,
        password_configured: true,
        authenticated: true,
        token_authenticated: true,
      }))
      setMode('ready')
    } catch (err) {
      setError(messageOf(err, 'Nu am putut configura contul.'))
    } finally {
      setSubmitting(false)
    }
  }

  const submitLogin = async (event: React.FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      await loginSharedAccount(username, password)
      setPassword('')
      const status = await fetchAuthStatus()
      applyStatus(status)
      setUsername('')
      if (!status.session_authenticated && !status.token_authenticated) {
        setError('Autentificarea nu a putut fi confirmată. Încearcă din nou.')
        setMode('locked')
      }
    } catch (err) {
      setPassword('')
      setError(messageOf(err, 'Autentificarea a eșuat.'))
    } finally {
      setSubmitting(false)
    }
  }

  const submitPasswordChange = async (event: React.FormEvent) => {
    event.preventDefault()
    if (password.length < 12) {
      setError('Alege o parolă de cel puțin 12 caractere.')
      return
    }
    if (password !== confirmation) {
      setError('Parolele nu coincid.')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      await changeSharedPassword(password)
      setPassword('')
      setConfirmation('')
      setManagePassword(false)
      setNotice('Parola a fost schimbată. Sesiunile de pe celelalte dispozitive au fost închise.')
      setAuthStatus(current => ({ ...current, session_authenticated: false }))
    } catch (err) {
      setError(messageOf(err, 'Nu am putut schimba parola.'))
    } finally {
      setSubmitting(false)
    }
  }

  const logout = async () => {
    setSubmitting(true)
    setError('')
    try {
      await logoutSharedAccount()
      clearAuthToken()
      setPassword('')
      setConfirmation('')
      setAuthStatus(current => ({ ...current, authenticated: false, session_authenticated: false, token_authenticated: false }))
      setNotice('Ai ieșit din cont.')
      setMode('locked')
    } catch {
      // An HttpOnly cookie cannot be cleared by JavaScript; claim logout only
      // after the server confirms that it revoked the session.
      setError('Nu am putut confirma ieșirea. Verifică conexiunea și încearcă din nou.')
    } finally {
      setSubmitting(false)
    }
  }

  if (mode === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Verific accesul…
        </div>
      </div>
    )
  }

  if (mode === 'setup') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
        <form onSubmit={submitSetup} className="card max-w-md w-full space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-chrono-100 dark:bg-chrono-900/40 flex items-center justify-center">
              <KeyRound className="w-5 h-5 text-chrono-600 dark:text-chrono-400" />
            </div>
            <div>
              <h1 className="font-bold text-lg">Configurează user și parolă</h1>
              <p className="text-sm text-gray-500">Sesiunea aceasta este autorizată cu tokenul existent.</p>
            </div>
          </div>

          <label className="block">
            <span className="text-sm font-medium">Nume de utilizator</span>
            <input
              type="text"
              value={username}
              onChange={event => setUsername(event.target.value)}
              className="input w-full mt-1"
              autoComplete="username"
              minLength={3}
              maxLength={32}
              required
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium">Parolă nouă</span>
            <input
              type="password"
              value={password}
              onChange={event => setPassword(event.target.value)}
              className="input w-full mt-1"
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              required
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium">Confirmă parola</span>
            <input
              type="password"
              value={confirmation}
              onChange={event => setConfirmation(event.target.value)}
              className="input w-full mt-1"
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              required
            />
          </label>

          {error && <p role="alert" className="text-sm text-madder">{error}</p>}
          <button type="submit" disabled={submitting} className="btn-primary w-full">
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
            Configurează contul
          </button>
          <button
            type="button"
            onClick={() => { setError(''); setNotice('Poți configura parola oricând din această sesiune autorizată.'); setMode('ready') }}
            className="btn-secondary w-full"
          >
            Continuă cu tokenul actual
          </button>
          <p className="text-xs text-gray-500">
            Parola este salvată doar ca hash pe server. Introdu-o pe telefon pentru acces; nu o trimite prin chat.
          </p>
        </form>
      </div>
    )
  }

  if (mode === 'locked') {
    const passwordConfigured = authStatus.password_configured === true
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
        <div className="card max-w-md w-full space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-chrono-100 dark:bg-chrono-900/40 flex items-center justify-center">
              <LockKeyhole className="w-5 h-5 text-chrono-600 dark:text-chrono-400" />
            </div>
            <div>
              <h1 className="font-bold text-lg">Acces protejat</h1>
              <p className="text-sm text-gray-500">
                {passwordConfigured ? 'Autentifică-te cu userul și parola partajată.' : 'Această instanță cere acces autorizat.'}
              </p>
            </div>
          </div>

          {notice && <p role="status" className="text-sm text-green-700 dark:text-green-300">{notice}</p>}

          {passwordConfigured && (
            <form onSubmit={submitLogin} className="space-y-3">
              <label className="block">
                <span className="text-sm font-medium">Nume de utilizator</span>
                <input
                  type="text"
                  value={username}
                  onChange={event => setUsername(event.target.value)}
                  className="input w-full mt-1"
                  autoComplete="username"
                  autoFocus
                  required
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium">Parolă</span>
                <input
                  type="password"
                  value={password}
                  onChange={event => setPassword(event.target.value)}
                  className="input w-full mt-1"
                  autoComplete="current-password"
                  required
                />
              </label>
              <button type="submit" disabled={submitting} className="btn-primary w-full">
                {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
                Intră
              </button>
            </form>
          )}

          {!passwordConfigured && (
            <p className="text-sm text-gray-600 dark:text-gray-300">
              Configurează userul și parola o singură dată dintr-un browser care are deja acces autorizat.
            </p>
          )}

          {error && <p role="alert" className="text-sm text-madder">{error}</p>}

          {(showTokenEntry || !passwordConfigured) ? (
            <form onSubmit={submitToken} className="space-y-3 border-t border-gray-200 dark:border-gray-800 pt-4">
              <label className="block">
                <span className="text-sm font-medium">Token de acces existent</span>
                <span className="block text-xs text-gray-500 mb-1">
                  Folosește-l doar dacă îl ai deja într-un dispozitiv autorizat.
                </span>
                <input
                  type="password"
                  value={token}
                  onChange={event => setToken(event.target.value)}
                  className="input w-full"
                  autoComplete="current-password"
                  placeholder="token"
                />
              </label>
              <button type="submit" disabled={submitting} className="btn-secondary w-full">
                Verifică tokenul
              </button>
            </form>
          ) : (
            <button type="button" onClick={() => setShowTokenEntry(true)} className="btn-secondary w-full">
              Folosește tokenul existent
            </button>
          )}

          <p className="text-[11px] text-gray-400">
            Parola nu este păstrată în browser. Sesiunea este un cookie HttpOnly; pe ruta HTTP actuală, folosește doar adresa privată Tailscale.
          </p>
        </div>
      </div>
    )
  }

  return (
    <AuthContext.Provider value={{
      sessionAuthenticated: Boolean(authStatus.session_authenticated) && !authStatus.token_authenticated,
      logout,
      canManagePassword: Boolean(authStatus.required && authStatus.token_authenticated),
      openPasswordManager: () => {
        setError('')
        if (authStatus.password_configured === false) {
          setMode('setup')
        } else {
          setManagePassword(true)
          setPassword('')
          setConfirmation('')
        }
      },
    }}>
      {children}
      {authStatus.session_authenticated && !authStatus.token_authenticated && (
        <button
          type="button"
          onClick={logout}
          disabled={submitting}
          className="fixed z-40 top-3 right-3 btn-secondary shadow-lg hidden md:inline-flex"
          aria-label="Ieși din cont"
        >
          {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogOut className="w-4 h-4" />}
          Ieșire
        </button>
      )}
      {error && !managePassword && (
        <div role="alert" className="fixed z-40 top-14 right-3 max-w-sm rounded-lg bg-red-50 dark:bg-red-950 text-red-800 dark:text-red-200 px-3 py-2 text-sm shadow-lg">
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="fixed z-40 right-3 bottom-[calc(8rem+env(safe-area-inset-bottom,0px))] md:bottom-14 max-w-sm rounded-lg bg-green-50 dark:bg-green-950 text-green-800 dark:text-green-200 px-3 py-2 text-sm shadow-lg">
          {notice}
        </div>
      )}
      {managePassword && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={submitPasswordChange} className="card max-w-md w-full space-y-4 shadow-xl">
            <div>
              <h2 className="font-bold text-lg">Schimbă parola comună</h2>
              <p className="text-sm text-gray-500">Este necesar tokenul existent din acest browser. Celelalte sesiuni vor fi revocate.</p>
            </div>
            <label className="block">
              <span className="text-sm font-medium">Parolă nouă</span>
              <input
                type="password"
                value={password}
                onChange={event => setPassword(event.target.value)}
                className="input w-full mt-1"
                autoComplete="new-password"
                minLength={12}
                maxLength={256}
                required
                autoFocus
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium">Confirmă parola</span>
              <input
                type="password"
                value={confirmation}
                onChange={event => setConfirmation(event.target.value)}
                className="input w-full mt-1"
                autoComplete="new-password"
                minLength={12}
                maxLength={256}
                required
              />
            </label>
            {error && <p role="alert" className="text-sm text-madder">{error}</p>}
            <div className="flex gap-2">
              <button type="button" onClick={() => setManagePassword(false)} className="btn-secondary flex-1">Anulează</button>
              <button type="submit" disabled={submitting} className="btn-primary flex-1">
                {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
                Salvează
              </button>
            </div>
          </form>
        </div>
      )}
    </AuthContext.Provider>
  )
}
