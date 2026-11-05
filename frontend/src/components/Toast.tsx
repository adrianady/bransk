import { createContext, useCallback, useContext, useRef, useState } from 'react'

type ToastKind = 'success' | 'error' | 'info' | 'warning'

interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

interface ToastApi {
  success: (message: string) => void
  error: (message: string) => void
  info: (message: string) => void
  warning: (message: string) => void
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
    warning: message => push('warning', message),
  }

  const tone: Record<ToastKind, string> = {
    success: 'border-verdigris/30 bg-verdigris-soft text-verdigris',
    error: 'border-madder/30 bg-madder-soft text-madder',
    info: 'border-accent/30 bg-accent-soft text-accent-strong',
    warning: 'border-brass/30 bg-brass-soft text-brass',
  }

  const icon: Record<ToastKind, string> = {
    success: '✓',
    error: '!',
    info: 'i',
    warning: '!',
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
            className={`rounded-xl border p-3 shadow-lift animate-rise-in flex items-start gap-2 ${tone[t.kind]}`}
          >
            <span className="mt-0.5 w-4 h-4 rounded-full border border-current flex items-center justify-center text-[10px] font-bold shrink-0" aria-hidden="true">
              {icon[t.kind]}
            </span>
            <p className="text-sm flex-1 min-w-0 break-words text-ink">{t.message}</p>
            <button
              onClick={() => dismiss(t.id)}
              aria-label="Închide notificarea"
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
  if (!ctx) throw new Error('useToast trebuie folosit în interiorul ToastProvider')
  return ctx
}
