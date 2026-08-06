import { createContext, useCallback, useContext, useRef, useState } from 'react'

type ToastKind = 'success' | 'error' | 'info'

interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

interface ToastApi {
  success: (message: string) => void
  error: (message: string) => void
  info: (message: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const push = useCallback((kind: ToastKind, message: string) => {
    const id = nextId.current++
    setToasts(prev => [...prev, { id, kind, message }])
    window.setTimeout(() => dismiss(id), 5000)
  }, [dismiss])

  const api: ToastApi = {
    success: message => push('success', message),
    error: message => push('error', message),
    info: message => push('info', message),
  }

  const borderColor: Record<ToastKind, string> = {
    success: 'border-green-400 dark:border-green-600',
    error: 'border-red-400 dark:border-red-600',
    info: 'border-chrono-400 dark:border-chrono-600',
  }

  const icon: Record<ToastKind, string> = {
    success: '✅',
    error: '⚠️',
    info: 'ℹ️',
  }

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="fixed top-4 right-4 z-[100] space-y-2 w-80 max-w-[calc(100vw-2rem)]"
        role="status"
        aria-live="polite"
      >
        {toasts.map(t => (
          <div
            key={t.id}
            className={`card border-l-4 ${borderColor[t.kind]} shadow-lg flex items-start gap-2`}
          >
            <span className="mt-0.5 shrink-0" aria-hidden="true">{icon[t.kind]}</span>
            <p className="text-sm flex-1 min-w-0 break-words">{t.message}</p>
            <button
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss notification"
              className="btn-ghost p-0.5 text-xs shrink-0"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
