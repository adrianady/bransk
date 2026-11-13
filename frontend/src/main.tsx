import React from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource-variable/source-sans-3'
import '@fontsource-variable/source-serif-4'
import App from './App'
import { ToastProvider } from './components/Toast'
import { initOfflineSync } from './lib/idb/sync'
import { ensurePersistentStorage } from './lib/storage'
import './index.css'

// Device goes offline → notes/highlights stay in IndexedDB; when connectivity
// returns (or the app refocuses while online), everything is pushed to the
// server automatically — from any screen, not only the PDF reader.
initOfflineSync()

// Ask once for persistent storage so the browser does not evict the offline
// PDF cache / IndexedDB annotations under storage pressure. Fire-and-forget:
// it feature-detects, never throws, and never blocks rendering.
void ensurePersistentStorage()

const AuthGate = React.lazy(() =>
  import('./components/AuthGate').then(({ AuthGate: gate }) => ({ default: gate })),
)

/** Session flag so a stale-chunk reload happens at most once per session. */
const CHUNK_RELOAD_FLAG = 'chronokg-chunk-reload'

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error) {
    // After an app update the old route chunks no longer exist on the server, so
    // opening a view fails with "Failed to fetch dynamically imported module".
    // A single automatic reload fetches the matching build; the sessionStorage
    // guard prevents a reload loop if the failure is something else.
    const staleChunk = /dynamically imported module|Importing a module script failed|ChunkLoadError|error loading dynamically imported module/i
      .test(String(error?.message || error))
    if (!staleChunk) return
    if (sessionStorage.getItem(CHUNK_RELOAD_FLAG)) return
    sessionStorage.setItem(CHUNK_RELOAD_FLAG, '1')
    window.location.reload()
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
          <div className="card max-w-md w-full text-center space-y-4">
            <p className="text-3xl" aria-hidden="true">🧠</p>
            <h1 className="text-lg font-bold text-gray-900 dark:text-gray-100">Aplicația s-a oprit neașteptat</h1>
            <p className="text-sm text-gray-500 break-words">{String(this.state.error.message || this.state.error)}</p>
            <div className="flex flex-col sm:flex-row gap-2 justify-center">
              <button onClick={() => window.location.reload()} className="btn-primary">
                Reîncarcă aplicația
              </button>
              <button
                onClick={() => { sessionStorage.removeItem(CHUNK_RELOAD_FLAG); this.setState({ error: null }) }}
                className="btn-secondary"
              >
                Reîncearcă
              </button>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <React.Suspense fallback={
        <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
          <div className="text-sm text-gray-500">Se verifică accesul…</div>
        </div>
      }>
        <AuthGate>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthGate>
      </React.Suspense>
    </ErrorBoundary>
  </React.StrictMode>,
)
