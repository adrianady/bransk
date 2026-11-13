import { useState } from 'react'
import { X, ChevronLeft, ChevronRight, Check, AlertTriangle } from 'lucide-react'
import { useToast } from './Toast'
import type { OfflineNote } from '../lib/idb/schema'
import type { ConflictResolution } from '../lib/idb/sync'

interface Props {
  isOpen: boolean
  onClose: () => void
  localNote: OfflineNote
  serverNote: any // StudyNote from API
  onResolve: (resolution: ConflictResolution) => Promise<void>
}

export function ConflictMergeDialog({ isOpen, onClose, localNote, serverNote, onResolve }: Props) {
  const toast = useToast()
  const [activePane, setActivePane] = useState<'local' | 'server' | 'merged'>('local')
  const [mergedContent, setMergedContent] = useState(localNote?.content ?? '')
  const [mergedCourseContext, setMergedCourseContext] = useState(localNote?.courseContext ?? '')
  const [mergedPageNumber, setMergedPageNumber] = useState<number | null>(localNote?.pageNumber ?? null)
  const [resolving, setResolving] = useState(false)

  if (!isOpen) return null

  const handleResolve = async (strategy: 'local' | 'server' | 'manual') => {
    setResolving(true)
    try {
      let resolution: ConflictResolution = { strategy }
      if (strategy === 'manual') {
        resolution = {
          strategy: 'manual',
          mergedContent,
          mergedCourseContext,
          mergedPageNumber,
          mergedSourceDocumentIds: localNote.sourceDocumentIds,
        }
      }
      await onResolve(resolution)
      toast.success(strategy === 'manual' ? 'Versiunea combinată salvată local (se va sincroniza).'
        : strategy === 'local' ? 'Versiunea ta locală va fi împinsă pe server.'
        : 'Versiunea de pe server acceptată.')
      onClose()
    } catch (err) {
      toast.error('Rezolvarea conflictului a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setResolving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
         onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="conflict-title">
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col"
           onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b dark:border-gray-700 sticky top-0 bg-white/95 dark:bg-gray-900/95 backdrop-blur rounded-t-2xl">
          <h2 id="conflict-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-amber-600" />
            Conflict de sincronizare — Notă
          </h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500"
                  aria-label="Închide">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Three-pane diff */}
        <div className="flex-1 overflow-hidden flex">
          {/* Local pane */}
          <div className={`flex-1 flex flex-col min-w-0 ${activePane === 'local' ? 'ring-2 ring-chrono-600' : ''}`}>
            <div className="p-3 bg-amber-50 dark:bg-amber-900/20 border-b dark:border-gray-700 flex items-center justify-between">
              <span className="text-sm font-semibold text-amber-800 dark:text-amber-200">Locală (tua)</span>
              <span className="text-xs text-amber-600 dark:text-amber-400">{localNote.status === 'conflict' ? 'Conflict' : localNote.dirty ? 'Nesincronizată' : 'Sincronizată'}</span>
            </div>
            <div className="flex-1 p-3 overflow-y-auto">
              <textarea
                readOnly
                value={localNote.content}
                className="w-full h-full min-h-[200px] font-mono text-sm bg-transparent border-none resize-none focus:outline-none text-gray-900 dark:text-gray-100"
                placeholder="(gole)"
              />
              <div className="mt-2 text-xs text-gray-500 dark:text-gray-400 space-y-1">
                <div>Context curs: <span className="font-mono">{localNote.courseContext || '—'}</span></div>
                <div>Pagină: <span className="font-mono">{localNote.pageNumber?.toString() || '—'}</span></div>
                <div>Actualizată: <span className="font-mono">{new Date(localNote.updatedAt).toLocaleString('ro-RO')}</span></div>
              </div>
            </div>
            <div className="p-3 border-t dark:border-gray-700">
              <button
                onClick={() => setActivePane('local')}
                disabled={activePane === 'local'}
                className="w-full btn-secondary text-sm"
              >
                Folosește această versiune
              </button>
            </div>
          </div>

          {/* Divider with navigation */}
          <div className="flex flex-col justify-center items-center px-2 bg-gray-50 dark:bg-gray-800/50 border-x dark:border-gray-700">
            <button
              onClick={() => setActivePane(activePane === 'server' ? 'merged' : 'server')}
              disabled={activePane !== 'local'}
              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400"
              aria-label="Următoarea"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
            <span className="text-xs text-gray-500 px-1">sau</span>
            <button
              onClick={() => setActivePane(activePane === 'local' ? 'merged' : 'local')}
              disabled={activePane !== 'server'}
              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400"
              aria-label="Anterioră"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
          </div>

          {/* Server pane */}
          <div className={`flex-1 flex flex-col min-w-0 ${activePane === 'server' ? 'ring-2 ring-lapis-600' : ''}`}>
            <div className="p-3 bg-blue-50 dark:bg-blue-900/20 border-b dark:border-gray-700 flex items-center justify-between">
              <span className="text-sm font-semibold text-blue-800 dark:text-blue-200">Server</span>
              <span className="text-xs text-blue-600 dark:text-blue-400">Autoritate</span>
            </div>
            <div className="flex-1 p-3 overflow-y-auto">
              <textarea
                readOnly
                value={serverNote.content}
                className="w-full h-full min-h-[200px] font-mono text-sm bg-transparent border-none resize-none focus:outline-none text-gray-900 dark:text-gray-100"
                placeholder="(gole)"
              />
              <div className="mt-2 text-xs text-gray-500 dark:text-gray-400 space-y-1">
                <div>Context curs: <span className="font-mono">{serverNote.course_context || '—'}</span></div>
                <div>Pagină: <span className="font-mono">{serverNote.page_number?.toString() || '—'}</span></div>
                <div>Actualizată: <span className="font-mono">{new Date(serverNote.updated_at).toLocaleString('ro-RO')}</span></div>
              </div>
            </div>
            <div className="p-3 border-t dark:border-gray-700">
              <button
                onClick={() => setActivePane('server')}
                disabled={activePane === 'server'}
                className="w-full btn-secondary text-sm"
              >
                Folosește versiunea server
              </button>
            </div>
          </div>

          {/* Merged pane */}
          <div className={`flex-1 flex flex-col min-w-0 ${activePane === 'merged' ? 'ring-2 ring-verdigris-600' : ''}`}>
            <div className="p-3 bg-emerald-50 dark:bg-emerald-900/20 border-b dark:border-gray-700 flex items-center justify-between">
              <span className="text-sm font-semibold text-emerald-800 dark:text-emerald-200">Combinare manuală</span>
              <span className="text-xs text-emerald-600 dark:text-emerald-400">Editează liber</span>
            </div>
            <div className="flex-1 p-3 overflow-y-auto">
              <textarea
                value={mergedContent}
                onChange={e => setMergedContent(e.target.value)}
                className="w-full min-h-[200px] font-mono text-sm input dark:bg-gray-800 resize-none"
                placeholder="Scrie versiunea combinată aici..."
              />
              <div className="mt-3 space-y-2 text-sm">
                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Context curs</label>
                  <input
                    type="text"
                    value={mergedCourseContext}
                    onChange={e => setMergedCourseContext(e.target.value)}
                    className="input w-full text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Pagină</label>
                  <input
                    type="number"
                    min="1"
                    value={mergedPageNumber ?? ''}
                    onChange={e => setMergedPageNumber(e.target.value ? parseInt(e.target.value, 10) : null)}
                    className="input w-24 text-sm"
                    placeholder="—"
                  />
                </div>
              </div>
            </div>
            <div className="p-3 border-t dark:border-gray-700">
              <button
                onClick={() => handleResolve('manual')}
                disabled={resolving || !mergedContent.trim()}
                className="w-full btn-primary text-sm"
              >
                {resolving ? 'Salvez...' : 'Salvează combinația'}
              </button>
            </div>
          </div>
        </div>

        {/* Footer actions for quick resolve */}
        <div className="px-4 py-3 border-t dark:border-gray-700 flex items-center justify-end gap-2 bg-gray-50/50 dark:bg-gray-900/50 rounded-b-2xl">
          <button onClick={onClose} className="btn-ghost text-sm" disabled={resolving}>
            Anulează
          </button>
          <button
            onClick={() => handleResolve('server')}
            disabled={resolving}
            className="btn-secondary text-sm"
          >
            Folosește server
          </button>
          <button
            onClick={() => handleResolve('local')}
            disabled={resolving}
            className="btn-secondary text-sm"
          >
            Păstrează locală
          </button>
        </div>
      </div>
    </div>
  )
}