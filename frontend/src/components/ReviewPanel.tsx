import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { MaintenanceReport, SpacedRepetitionItem, TagCleanupReport } from '../lib/types'

export function ReviewPanel() {
  const [mode, setMode] = useState<'integrity' | 'sr'>('integrity')

  return (
    <div>
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700 mb-4 overflow-x-auto no-scrollbar flex-nowrap">
        {([
          { id: 'integrity' as const, label: '🔬 Integritatea datelor', desc: 'Reanalizează și repară toate datele' },
          { id: 'sr' as const, label: '🔄 Repetiție spațiată', desc: 'Program de verificări (SM-2)' },
        ]).map(tab => (
          <button key={tab.id} onClick={() => setMode(tab.id)}
            className={`px-3 sm:px-4 py-3 touch-40 text-xs sm:text-sm font-medium border-b-2 transition-colors whitespace-nowrap shrink-0 ${
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
  const toast = useToast()
  const [running, setRunning] = useState(false)
  const [log, setLog] = useState<{ phase: string; message: string }[]>([])
  const [report, setReport] = useState<MaintenanceReport | null>(null)
  const [error, setError] = useState('')
  const [tagReport, setTagReport] = useState<TagCleanupReport | null>(null)
  const [tagRunning, setTagRunning] = useState(false)

  const runTags = async (dryRun: boolean) => {
    setTagRunning(true)
    try {
      const result = await api.maintenance.normalizeTags(dryRun)
      setTagReport(result)
      if (!dryRun) {
        toast.success(
          `Etichete curățate: ${result.entries_changed} intrări, ${result.tags_inserted} etichete adăugate în tabelă.`,
        )
      }
    } catch (err) {
      toast.error('Curățarea etichetelor a eșuat: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setTagRunning(false)
    }
  }

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
      if (!response.body) throw new Error('Răspuns fără conținut')
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
            else if (parsed.type === 'error') setError(parsed.content || 'Reanaliza a eșuat')
          } catch { /* skip malformed frames */ }
        }
      }
    } catch (err: any) {
      setError(err?.message || 'Reanaliza a eșuat')
    } finally {
      setRunning(false)
    }
  }

  const stats = report ? [
    { label: 'Intrări scanate', value: report.entries_scanned },
    { label: 'Intrări reparate', value: report.entries_fixed },
    { label: 'Date reparate', value: report.dates_fixed },
    { label: 'Legături greșite șterse', value: report.relationships_removed + report.self_links_removed },
    { label: 'PDF-uri recitite', value: report.pdfs_found, sub: `${report.pdfs_reanalyzed} reanalizate` },
    { label: 'Teorii actualizate', value: report.theories_updated },
  ] : []

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-xl font-bold mb-1">Integritatea datelor și reanaliză</h2>
        <p className="text-sm text-gray-500 mb-4">
          Scanează toată baza de cunoștințe și o repară, ca grafurile, cronologia și arborele teoriilor să fie corecte:
          normalizează intrările (date, etichete, titluri), curăță legăturile rupte, reconstruiește indexul de căutare,
          recitește cu AI fiecare PDF de studiu descărcat și recalculează starea teoriilor din dovezile pro și contra.
        </p>
        <button onClick={run} disabled={running} className="btn-primary">
          {running ? 'Se reanalizează…' : '🔬 Pornește reanaliza completă'}
        </button>
      </div>

      <div className="card">
        <h3 className="font-semibold mb-1">Etichete</h3>
        <p className="text-sm text-gray-500 mb-3">
          Curăță doar vocabularul de etichete: scoate datele și cuvintele de umplutură
          („the”, „of”), elimină duplicatele și umple tabela <code>tags</code> din
          coloana folosită de filtre. Nu recitește PDF-uri și nu cheamă modelul.
        </p>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => runTags(true)} disabled={tagRunning} className="btn-secondary text-sm">
            {tagRunning ? 'Verific…' : 'Verifică (probă)'}
          </button>
          {tagReport?.dry_run && tagReport.entries_changed > 0 && (
            <button onClick={() => runTags(false)} disabled={tagRunning} className="btn-primary text-sm">
              Aplică pe {tagReport.entries_changed} intrări
            </button>
          )}
        </div>

        {tagReport && (
          <div className="mt-3 text-sm text-gray-600 dark:text-gray-400 space-y-2">
            <p>
              {tagReport.entries_changed === 0
                ? 'Etichetele sunt deja curate.'
                : `${tagReport.entries_changed} din ${tagReport.entries_scanned} intrări de schimbat.`}
              {tagReport.dry_run
                ? ` ${tagReport.tags_missing_from_table} etichete lipsesc din tabelă.`
                : ` ${tagReport.tags_inserted} etichete adăugate în tabelă.`}
            </p>
            {tagReport.samples.length > 0 && (
              <ul className="space-y-1 text-xs">
                {tagReport.samples.map(sample => (
                  <li key={sample.id} className="min-w-0">
                    <span className="font-medium">#{sample.id}</span> {sample.title}
                    <span className="block text-gray-400 break-words">
                      {sample.before || '(gol)'} → {sample.after || '(gol)'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
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
        <div className="card border-madder/30 text-sm text-madder">
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
            <p>🗂️ {report.entries_active} intrări active · 🔗 {report.relationships_count} relații · 🧬 {report.theories_count} teorii</p>
            <p>🔄 Index FTS reconstruit: {report.fts_rebuilt ? 'da' : 'nu'} · ⚠️ {report.pdfs_unreadable} PDF-uri ilizibile · ❌ {report.pdf_errors} erori PDF</p>
            {report.warnings.length > 0 && (
              <div className="pt-2 space-y-1">
                {report.warnings.map((w, i) => (
                  <p key={i} className="text-brass">⚠️ {w}</p>
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
      toast.error('Coada de verificări nu s-a putut încărca: ' + (err?.message || 'eroare necunoscută'))
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
      toast.error('Nota nu s-a putut trimite: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const [entryTitles, setEntryTitles] = useState<Record<number, string>>({})
  const [entryContents, setEntryContents] = useState<Record<number, string>>({})

  useEffect(() => {
    // The queue endpoint joins entry_title/content_preview, so the old
    // 500-entry download (titles only, full text wasted) is gone.
    const titleMap: Record<number, string> = {}
    const contentMap: Record<number, string> = {}
    queue.forEach(item => {
      titleMap[item.entry_id] = item.entry_title || `Intrarea nr. ${item.entry_id}`
      if (item.content_preview) contentMap[item.entry_id] = item.content_preview
    })
    setEntryTitles(titleMap)
    setEntryContents(contentMap)
  }, [queue])

  if (loading) return <div className="text-center py-8 text-gray-500">Se încarcă coada de verificări…</div>

  if (queue.length === 0) {
    return (
      <div className="card text-center py-12">
        <h2 className="text-xl font-bold mb-2">Verificare cu repetiție spațiată</h2>
        <p className="text-gray-500 mb-4">Nicio intrare de verificat acum!</p>
        <p className="text-sm text-gray-400">Adaugă intrări la verificare din pagina lor de detaliu. Revino mai târziu pentru verificările programate.</p>
        <button onClick={loadQueue} className="btn-secondary mt-4">Reîmprospătează</button>
      </div>
    )
  }

  const current = queue[currentIndex]

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Verificare cu repetiție spațiată</h2>
          <p className="text-sm text-gray-500">{currentIndex + 1} din {queue.length} scadente · algoritmul SM-2</p>
        </div>
        <button onClick={loadQueue} className="btn-secondary">↻ Reîmprospătează</button>
      </div>

      <div className="card max-w-2xl mx-auto">
        <div className="flex items-center gap-2 mb-4">
          <span className={`badge ${current.repetitions === 0 ? 'badge-verdigris' : 'badge-accent'}`}>
            {current.repetitions === 0 ? 'Nouă' : `Verificarea nr. ${current.repetitions}`}
          </span>
          <span className="text-xs text-gray-500">EF: {current.ease_factor.toFixed(2)}</span>
        </div>

        <h3 className="text-lg font-semibold mb-4">
          {entryTitles[current.entry_id] || `Intrarea nr. ${current.entry_id}`}
        </h3>

        {!showAnswer ? (
          <button onClick={() => setShowAnswer(true)}
            className="btn-primary w-full py-4 text-lg">
            Arată răspunsul
          </button>
        ) : (
          <div>
            <div className="mb-6 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
              {entryContents[current.entry_id] ? (
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{entryContents[current.entry_id]}</ReactMarkdown>
                </div>
              ) : (
                <p className="text-sm text-gray-500 italic">Intrarea asta nu are conținut — notează-ți recunoașterea după titlul de mai sus.</p>
              )}
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium text-gray-500 mb-2">Cât de bine ți-ai amintit:</p>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { q: 0, label: 'Blank total', color: 'bg-madder' },
                  { q: 1, label: 'Greșit, apoi mi-am amintit', color: 'bg-madder/80' },
                  { q: 2, label: 'Greșit, dar familiar', color: 'bg-brass/70' },
                  { q: 3, label: 'Greu de reamintit', color: 'bg-brass' },
                  { q: 4, label: 'Reamintit după o pauză', color: 'bg-verdigris/70' },
                  { q: 5, label: 'Reamintit perfect', color: 'bg-verdigris' },
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
