import { useCallback, useEffect, useState } from 'react'
import { X, Copy, Loader2, CheckCircle2, FileText, AlertTriangle } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { DuplicateGroup, DuplicatesResponse } from '../lib/types'

interface Props {
  isOpen: boolean
  onClose: () => void
  onMerged?: () => void
}

const REASON_LABELS: Record<string, string> = {
  doi: 'Același DOI',
  isbn: 'Același ISBN',
  pdf_hash: 'Același PDF (SHA256)',
  title: 'Titlu similar',
}

const READ_STATUS_LABELS: Record<string, string> = {
  unread: 'Necitit',
  reading: 'În lectură',
  read: 'Citit',
  annotated: 'Adnotat',
}

export function DuplicatesModal({ isOpen, onClose, onMerged }: Props) {
  const toast = useToast()
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<DuplicatesResponse | null>(null)
  const [error, setError] = useState('')
  const [mergingId, setMergingId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await api.studies.getDuplicates()
      setData(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Nu s-au putut încărca duplicatele')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isOpen) void load()
  }, [isOpen, load])

  // Închidere pe Escape.
  useEffect(() => {
    if (!isOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isOpen, onClose])

  const handleMerge = async (keepId: number, duplicateId: number) => {
    if (!window.confirm(`Fuzionezi intrarea #${duplicateId} în #${keepId}? Datele duplicatului se mută în intrarea păstrată.`)) return
    setMergingId(duplicateId)
    try {
      await api.studies.merge({ keep_id: keepId, duplicate_id: duplicateId })
      toast.success('Intrări fuzionate cu succes!')
      onMerged?.()
      await load()
    } catch (err) {
      toast.error('Fuziunea a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setMergingId(null)
    }
  }

  if (!isOpen) return null

  const groups: DuplicateGroup[] = data?.duplicate_groups ?? []

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-black/70 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Audit duplicate"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-800 w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <div className="p-1.5 bg-brass/15 text-brass rounded-lg shrink-0">
              <Copy className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm sm:text-base font-bold text-gray-900 dark:text-gray-100 truncate">
                Audit duplicate în bibliotecă
              </h2>
              <p className="text-[11px] sm:text-xs text-gray-500">
                {data ? `${data.total_groups} grupuri · ${data.total_duplicates} duplicate` : 'Se scanează biblioteca după DOI / ISBN / hash PDF / titlu…'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800"
            title="Închide"
            aria-label="Închide"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 sm:p-4 space-y-4">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
              <Loader2 className="w-5 h-5 animate-spin" /> Se caută duplicate…
            </div>
          )}
          {!loading && error && (
            <div className="card border-madder/25 text-sm text-madder flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0" /> {error}
              <button onClick={load} className="btn-secondary text-xs ml-auto">Reîncearcă</button>
            </div>
          )}
          {!loading && !error && groups.length === 0 && (
            <div className="card text-center py-10 space-y-2">
              <CheckCircle2 className="w-8 h-8 mx-auto text-verdigris" />
              <p className="font-semibold">Nu au fost detectate duplicate în bibliotecă!</p>
              <p className="text-xs text-muted">Biblioteca este curată: niciun DOI, ISBN, hash PDF sau titlu duplicat.</p>
            </div>
          )}
          {!loading && !error && groups.map((group, gi) => (
            <div key={`${group.reason}-${gi}`} className="card space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="badge badge-brass text-xs font-semibold">
                  {REASON_LABELS[group.reason] || group.reason}
                </span>
                {group.value && (
                  <span className="text-[11px] font-mono text-muted break-all">{group.value}</span>
                )}
                <span className="text-[11px] text-muted ml-auto">{group.entries.length} intrări</span>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                {group.entries.map((entry) => (
                  <div
                    key={entry.id}
                    className="rounded-lg border border-line bg-surface-2/40 p-3 space-y-1.5"
                  >
                    <p className="font-semibold text-sm leading-snug break-words">{entry.title}</p>
                    {entry.authors && (
                      <p className="text-xs text-muted line-clamp-1">✍️ {entry.authors}</p>
                    )}
                    <div className="flex flex-wrap gap-1.5 text-[11px]">
                      {entry.doi && <span className="badge badge-muted font-mono">DOI: {entry.doi}</span>}
                      {entry.isbn && <span className="badge badge-muted font-mono">ISBN: {entry.isbn}</span>}
                      {entry.study_type && (
                        <span className="badge bg-accent-soft text-accent-strong">{entry.study_type}</span>
                      )}
                      {entry.read_status && (
                        <span className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                          {READ_STATUS_LABELS[entry.read_status] || entry.read_status}
                        </span>
                      )}
                      {entry.has_pdf ? (
                        <span className="badge badge-verdigris"><FileText className="w-3 h-3 inline" /> PDF</span>
                      ) : (
                        <span className="badge badge-muted">fără PDF</span>
                      )}
                    </div>
                    <p className="text-[11px] text-muted">
                      #{entry.id} · adăugat la {entry.created_at?.slice(0, 10)}
                    </p>
                    <button
                      onClick={() => {
                        const other = group.entries.find((e) => e.id !== entry.id)
                        if (other) void handleMerge(entry.id, other.id)
                      }}
                      disabled={mergingId !== null || group.entries.length < 2}
                      className="btn-secondary text-xs w-full flex items-center justify-center gap-1.5"
                      title={`Păstrează #${entry.id} și fuzionează cealaltă intrare în aceasta`}
                    >
                      {mergingId === entry.id
                        ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Se fuzionează…</>
                        : <>Păstrează aceasta (fuzionează celălalt în acesta)</>}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
