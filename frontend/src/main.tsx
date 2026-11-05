import React from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource-variable/source-sans-3'
import '@fontsource-variable/source-serif-4'
import App from './App'
import { AuthGate } from './components/AuthGate'
import { ToastProvider } from './components/Toast'
import './index.css'

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
          <div className="card max-w-md w-full text-center space-y-4">
            <p className="text-3xl" aria-hidden="true">🧠</p>
            <h1 className="text-lg font-bold text-gray-900 dark:text-gray-100">Bransk hit an unexpected error</h1>
            <p className="text-sm text-gray-500 break-words">{String(this.state.error.message || this.state.error)}</p>
            <button onClick={() => window.location.reload()} className="btn-primary">
              Reload app
            </button>
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
      <AuthGate>
        <ToastProvider>
          <App />
        </ToastProvider>
      </AuthGate>
    </ErrorBoundary>
  </React.StrictMode>,
)
