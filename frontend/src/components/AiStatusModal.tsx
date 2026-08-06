import { useEffect, useState } from 'react'
import { X, Cpu, CheckCircle2, XCircle, HelpCircle, Loader2 } from 'lucide-react'

interface AiStatus {
  provider_mode: string
  deepseek_model: string
  ollama_model: string
  configured_embed_model: string
  active_embed_model: string | null
  semantic_retrieval: boolean
  embedding_model_installed: boolean
  embedding_dimension: number | null
  available_embed_models: string[]
  pdf_chunks_indexed: number
}

export function AiStatusModal({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<AiStatus | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let isMounted = true
    fetch('/api/ai/status')
      .then(r => r.json())
      .then(s => { if (isMounted) setStatus(s) })
      .catch((e) => { if (isMounted) setError(e?.message || 'AI status unavailable') })
    return () => { isMounted = false }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex items-start justify-between gap-4 text-sm py-1.5 border-b border-gray-100 dark:border-gray-800 last:border-0">
      <span className="text-gray-500">{label}</span>
      <span className="text-right font-medium min-w-0 break-words">{value}</span>
    </div>
  )

  const yesNo = (ok: boolean) =>
    ok
      ? <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400"><CheckCircle2 className="w-4 h-4" /> yes</span>
      : <span className="inline-flex items-center gap-1 text-red-500 dark:text-red-400"><XCircle className="w-4 h-4" /> no</span>

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Cpu className="w-5 h-5 text-chrono-700" /> AI Status
          </h2>
          <button onClick={onClose} className="icon-btn" aria-label="Close dialog" title="Close (Esc)">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4">
          {error && (
            <p className="text-sm text-red-600 dark:text-red-400 mb-3">⚠️ {error}</p>
          )}
          {!status && !error && (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading status...
            </div>
          )}
          {status && (
            <div>
              <Row label="Provider mode" value={status.provider_mode} />
              <Row label="DeepSeek model" value={status.deepseek_model} />
              <Row label="Ollama model" value={status.ollama_model} />
              <Row label="Embedding model (configured)" value={status.configured_embed_model} />
              <Row
                label="Embedding model (active)"
                value={status.active_embed_model ?? '— none —'}
              />
              <Row label="Semantic retrieval" value={yesNo(status.semantic_retrieval)} />
              <Row label="Embedding model installed" value={yesNo(status.embedding_model_installed)} />
              <Row
                label="Embedding dimension"
                value={status.embedding_dimension ?? '—'}
              />
              <Row label="PDF chunks indexed" value={status.pdf_chunks_indexed} />

              {!status.embedding_model_installed && (
                <div className="mt-4 p-3 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 text-sm space-y-2">
                  <p className="flex items-start gap-2 text-amber-700 dark:text-amber-400">
                    <HelpCircle className="w-4 h-4 shrink-0 mt-0.5" />
                    Semantic search is disabled until an embedding model is available.
                  </p>
                  <code className="block text-xs bg-white dark:bg-gray-800 rounded px-2 py-1">
                    ollama pull nomic-embed-text
                  </code>
                  <p className="text-xs text-gray-600 dark:text-gray-400">
                    Then restart the backend. The app auto-detects the model; the
                    configured name can be changed with <code>OLLAMA_EMBED_MODEL</code> in
                    <code> backend/.env</code>.
                  </p>
                </div>
              )}

              {status.available_embed_models.length > 0 && (
                <div className="mt-4">
                  <p className="text-xs font-medium text-gray-500 mb-1.5">Installed embedding candidates</p>
                  <div className="flex flex-wrap gap-1.5">
                    {status.available_embed_models.map(m => (
                      <span key={m} className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">{m}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
