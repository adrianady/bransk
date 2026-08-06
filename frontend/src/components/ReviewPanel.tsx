import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { MaintenanceReport, SpacedRepetitionItem } from '../lib/types'

export function ReviewPanel() {
  const [mode, setMode] = useState<'integrity' | 'sr'>('integrity')

  return (
    <div>
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700 mb-4">
        {([
          { id: 'integrity' as const, label: '🔬 Data integrity', desc: 'Re-analyze and repair all data' },
          { id: 'sr' as const, label: '🔄 Spaced repetition', desc: 'Review schedule (SM-2)' },
        ]).map(tab => (
          <button key={tab.id} onClick={() => setMode(tab.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              mode === tab.id
                ? 'border-chrono-600 text-chrono-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
            title={tab.desc}
          >{tab.label}</button>
        ))}
      </div>
      {mode === 'integrity' ? <IntegrityMode /> : <SpacedRepetitionMode />}
    </div>
  )
}


// ── Data integrity & re-analysis mode ──────────────────────────────────────

function IntegrityMode() {
  const [running, setRunning] = useState(false)
  const [log, setLog] = useState<{ phase: string; message: string }[]>([])
  const [report, setReport] = useState<MaintenanceReport | null>(null)
  const [error, setError] = useState('')

  const run = async () => {
    setRunning(true)
    setReport(null)
    setError('')
    setLog([])
    try {
      const response = await api.maintenance.analyze()
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }))
        throw new Error(err.detail || `HTTP ${response.status}`)
      }
      if (!response.body) throw new Error('No response body')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6).trim()
          if (data === '[DONE]') continue
          try {
            const parsed = JSON.parse(data)
            if (parsed.type === 'progress') setLog(prev => [...prev, { phase: parsed.phase, message: parsed.message }])
            else if (parsed.type === 'done') setReport(parsed.report)
            else if (parsed.type === 'error') setError(parsed.content || 'Re-analysis failed')
          } catch { /* skip malformed frames */ }
        }
      }
    } catch (err: any) {
      setError(err?.message || 'Re-analysis failed')
    } finally {
      setRunning(false)
    }
  }

  const stats = report ? [
    { label: 'Entries scanned', value: report.entries_scanned },
    { label: 'Entries fixed', value: report.entries_fixed },
    { label: 'Dates fixed', value: report.dates_fixed },
    { label: 'Bad links removed', value: report.relationships_removed + report.self_links_removed },
    { label: 'PDFs re-read', value: report.pdfs_found, sub: `${report.pdfs_reanalyzed} re-analyzed` },
    { label: 'Theories updated', value: report.theories_updated },
  ] : []

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-xl font-bold mb-1">Data Integrity & Re-analysis</h2>
        <p className="text-sm text-gray-500 mb-4">
          Scans the whole knowledge base and repairs it so the graphs, the chronological timeline and the theory tree are correct:
          normalizes entries (dates, tags, titles), cleans broken relationships, rebuilds the search index,
          re-reads every downloaded study PDF with the AI, and recomputes theory statuses from pro/contra evidence.
        </p>
        <button onClick={run} disabled={running} className="btn-primary">
          {running ? 'Re-analyzing...' : '🔬 Run full re-analysis'}
        </button>
      </div>

      {log.length > 0 && (
        <div className="card space-y-1 max-h-56 overflow-y-auto">
          {log.map((item, i) => (
            <p key={i} className="text-sm text-gray-600 dark:text-gray-400 flex gap-2">
              <span className="text-gray-400 shrink-0">▸</span> {item.message}
            </p>
          ))}
        </div>
      )}

      {error && (
        <div className="card border-red-300 dark:border-red-900 text-sm text-red-600 dark:text-red-400">
          ⚠️ {error}
        </div>
      )}

      {report && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {stats.map(s => (
              <div key={s.label} className="card text-center">
                <p className="text-2xl font-bold text-chrono-600">{s.value}</p>
                <p className="text-xs text-gray-500">{s.label}</p>
                {s.sub && <p className="text-[10px] text-gray-400">{s.sub}</p>}
              </div>
            ))}
          </div>

          <div className="card text-sm space-y-1 text-gray-600 dark:text-gray-400">
            <p>🗂️ {report.entries_active} active entries · 🔗 {report.relationships_count} relationships · 🧬 {report.theories_count} theories</p>
            <p>🔄 FTS index rebuilt: {report.fts_rebuilt ? 'yes' : 'no'} · ⚠️ {report.pdfs_unreadable} unreadable PDFs · ❌ {report.pdf_errors} PDF errors</p>
            {report.warnings.length > 0 && (
              <div className="pt-2 space-y-1">
                {report.warnings.map((w, i) => (
                  <p key={i} className="text-amber-600 dark:text-amber-400">⚠️ {w}</p>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}


// ── Spaced repetition mode (preserved) ─────────────────────────────────────

function SpacedRepetitionMode() {
  const toast = useToast()
  const [queue, setQueue] = useState<SpacedRepetitionItem[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [showAnswer, setShowAnswer] = useState(false)
  const [loading, setLoading] = useState(true)

  const loadQueue = async () => {
    setLoading(true)
    try {
      const items = await api.spacedRepetition.queue()
      setQueue(items)
      setCurrentIndex(0)
      setShowAnswer(false)
    } catch (err: any) {
      toast.error('Could not load review queue: ' + (err?.message || 'unknown error'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadQueue() }, [])

  const submitRating = async (quality: number) => {
    const item = queue[currentIndex]
    try {
      await api.spacedRepetition.review(item.id, quality)
      if (currentIndex < queue.length - 1) {
        setCurrentIndex(prev => prev + 1)
        setShowAnswer(false)
      } else {
        await loadQueue()
      }
    } catch (err: any) {
      toast.error('Could not submit rating: ' + (err?.message || 'unknown error'))
    }
  }

  const [entryTitles, setEntryTitles] = useState<Record<number, string>>({})
  const [entryContents, setEntryContents] = useState<Record<number, string>>({})

  useEffect(() => {
    if (queue.length > 0) {
      api.entries.list({ limit: 500 }).then(allEntries => {
        const titleMap: Record<number, string> = {}
        const contentMap: Record<number, string> = {}
        allEntries.forEach(e => {
          titleMap[e.id] = e.title
          if (e.content) contentMap[e.id] = e.content
        })
        setEntryTitles(titleMap)
        setEntryContents(contentMap)
      })
    }
  }, [queue])

  if (loading) return <div className="text-center py-8 text-gray-500">Loading review queue...</div>

  if (queue.length === 0) {
    return (
      <div className="card text-center py-12">
        <h2 className="text-xl font-bold mb-2">Spaced Repetition Review</h2>
        <p className="text-gray-500 mb-4">No entries due for review!</p>
        <p className="text-sm text-gray-400">Add entries to review from their detail page. Check back later for scheduled reviews.</p>
        <button onClick={loadQueue} className="btn-secondary mt-4">Refresh</button>
      </div>
    )
  }

  const current = queue[currentIndex]

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Spaced Repetition Review</h2>
          <p className="text-sm text-gray-500">{currentIndex + 1} of {queue.length} due · SM-2 Algorithm</p>
        </div>
        <button onClick={loadQueue} className="btn-secondary">↻ Refresh</button>
      </div>

      <div className="card max-w-2xl mx-auto">
        <div className="flex items-center gap-2 mb-4">
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
            current.repetitions === 0 ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'
          }`}>
            {current.repetitions === 0 ? 'New' : `Review #${current.repetitions}`}
          </span>
          <span className="text-xs text-gray-500">EF: {current.ease_factor.toFixed(2)}</span>
        </div>

        <h3 className="text-lg font-semibold mb-4">
          {entryTitles[current.entry_id] || `Entry #${current.entry_id}`}
        </h3>

        {!showAnswer ? (
          <button onClick={() => setShowAnswer(true)}
            className="btn-primary w-full py-4 text-lg">
            Show Answer
          </button>
        ) : (
          <div>
            <div className="mb-6 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
              {entryContents[current.entry_id] ? (
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{entryContents[current.entry_id]}</ReactMarkdown>
                </div>
              ) : (
                <p className="text-sm text-gray-500 italic">This entry has no content — rate your recall of the title above.</p>
              )}
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium text-gray-500 mb-2">Rate your recall:</p>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { q: 0, label: 'Complete blackout', color: 'bg-red-500' },
                  { q: 1, label: 'Failed after seeing', color: 'bg-red-400' },
                  { q: 2, label: 'Failed, but familiar', color: 'bg-orange-400' },
                  { q: 3, label: 'Hard to recall', color: 'bg-yellow-400' },
                  { q: 4, label: 'Recalled after pause', color: 'bg-lime-500' },
                  { q: 5, label: 'Perfect recall', color: 'bg-green-500' },
                ].map(({ q, label, color }) => (
                  <button key={q} onClick={() => submitRating(q)}
                    className={`${color} text-gray-900 rounded-lg px-3 py-4 text-sm font-medium hover:opacity-90 transition-opacity`}
                  >
                    {q}
                    <br />
                    <span className="text-xs opacity-80">{label}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
