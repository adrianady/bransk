import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Search, AlertTriangle, CheckCircle2, RotateCcw, Link, Copy, ExternalLink, BookOpen } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { StudyQASource, StudyCheckResponse } from '../lib/types'

// Wide markdown tables scroll inside their panel on a phone instead of widening
// the page; the table itself stays `display: table`, so desktop alignment is
// untouched. `markdown-table-wrap` lives in index.css.
const markdownComponents = {
  table: ({ node: _node, ...props }: React.ComponentProps<'table'> & { node?: unknown }) => (
    <div className="markdown-table-wrap">
      <table {...props} />
    </div>
  ),
}

interface Props {
  entryId: number
  title: string
  onOpenSource?: (source: StudyQASource) => void
}

export function StudyQA({ entryId, title, onOpenSource }: Props) {
  const toast = useToast()
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [sources, setSources] = useState<StudyQASource[]>([])
  const [loading, setLoading] = useState(false)
  const [check, setCheck] = useState<StudyCheckResponse | null>(null)
  const [savingNote, setSavingNote] = useState(false)

  const ask = async () => {
    if (!question.trim()) return
    setLoading(true)
    try {
      const res = await api.studyQA.ask(entryId, question.trim())
      setAnswer(res.answer)
      setSources(res.sources)
    } catch (err: any) {
      toast.error('Întrebarea nu a putut fi trimisă: ' + (err?.message || 'necunoscută'))
    } finally {
      setLoading(false)
    }
  }

  const runCheck = async () => {
    try {
      const res = await api.studyQA.check(entryId)
      setCheck(res)
      if (res.issues.length === 0) {
        toast.success('Studiu verificat — nu lipsește nimic important.')
      } else {
        toast.warning(`${res.issues.length} elemente de verificat în acest studiu.`)
      }
    } catch (err: any) {
      toast.error('Verificarea nu a putut fi rulată: ' + (err?.message || 'necunoscută'))
    }
  }

  const copyAnswer = () => {
    navigator.clipboard.writeText(answer || '')
    toast.success('Răspuns copiat')
  }

  const saveAnswerAsNote = async () => {
    if (!answer.trim() || !sources.length || savingNote) return
    setSavingNote(true)
    try {
      const verifiedPages = [...new Set(
        sources.filter(source => source.page_verified && source.page_number != null)
          .map(source => source.page_number as number),
      )]
      await api.studies.notes.create(entryId, {
        content: `Întrebare: ${question.trim()}\n\nRăspuns:\n${answer.trim()}`,
        course_context: 'Răspuns Study QA',
        page_number: verifiedPages.length === 1 ? verifiedPages[0] : null,
        source_document_ids: sources.map(source => source.document_id),
      })
      toast.success('Răspunsul și fragmentele-sursă au fost salvate în notițe.')
    } catch (err: any) {
      toast.error('Răspunsul nu s-a putut salva: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setSavingNote(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Header with quick actions */}
      <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-sm text-gray-700 dark:text-gray-300">Întrebări despre studiu</h3>
          <p className="text-[11px] text-gray-500 dark:text-gray-400 truncate">
            {title}
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <button onClick={runCheck} className="btn-secondary text-xs px-2 py-1 whitespace-nowrap">
            <RotateCcw className="w-3 h-3 shrink-0" /> Verifică studiu
          </button>
        </div>
      </div>

      {/* Question input */}
      <div className="flex flex-wrap sm:flex-nowrap gap-2">
        <input
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && ask()}
          className="input flex-1 min-w-0 text-sm"
          placeholder="Întreabă ceva despre acest studiu (de ex. care este efectul principal?)"
          disabled={loading}
        />
        <button onClick={ask} disabled={loading || !question.trim()} className="btn-primary text-xs px-3 py-1.5">
          {loading ? 'Caut...' : 'Întreabă'}
        </button>
      </div>

      {/* Answer */}
      {answer && (
        <div className="card p-3 space-y-2">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[11px] font-semibold text-chrono-600 uppercase tracking-wider">Răspuns</span>
            <div className="flex items-center gap-3">
              {sources.length > 0 && (
                <button onClick={saveAnswerAsNote} disabled={savingNote} className="text-[11px] text-gray-500 hover:text-chrono-600 disabled:opacity-50">
                  <BookOpen className="w-3 h-3 shrink-0" /> {savingNote ? 'Salvez…' : 'Salvează notiță'}
                </button>
              )}
              <button onClick={copyAnswer} className="text-[11px] text-gray-500 hover:text-chrono-600">
                <Copy className="w-3 h-3 shrink-0" /> Copiază
              </button>
            </div>
          </div>
          <div className="prose prose-sm dark:prose-invert max-w-none break-words">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{answer}</ReactMarkdown>
          </div>
          {sources.length > 0 && (
            <div className="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700 space-y-1">
              <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">Surse (chunks PDF)</span>
              {sources.map((s, i) => (
                <button
                  key={s.document_id ?? i}
                  type="button"
                  onClick={() => onOpenSource?.(s)}
                  className="w-full flex items-start gap-1.5 text-left text-[10px] text-gray-500 dark:text-gray-400 hover:text-chrono-700 dark:hover:text-chrono-300 rounded py-1"
                  title="Deschide PDF-ul și fragmentul sursă"
                >
                  <ExternalLink className="w-3 h-3 mt-0.5 shrink-0" />
                  <span className="min-w-0">
                    <span className="font-semibold">Fragment #{s.chunk_index}</span>
                    {s.page_verified && s.page_number != null
                      ? ` · pagina PDF ${s.page_number}`
                      : ' · pagina neverificată'}
                    {` · relevanță ${(s.score * 100).toFixed(0)}% — ${s.preview}`}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Check / audit */}
      {check && (
        <div className="card p-3 space-y-2">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[11px] font-semibold text-chrono-600 uppercase tracking-wider">
              Verificare studiu ({check.indexed_chunks} chunks indexați)
            </span>
          </div>
          {check.issues.length === 0 ? (
            <div className="flex items-center gap-2 p-2 bg-verdigris-soft rounded-lg text-sm text-verdigris">
              <CheckCircle2 className="w-4 h-4 shrink-0" /> Studiul pare complet: nu lipsește nimic important.
            </div>
          ) : (
            <div className="space-y-2">
              <p className="section-label text-brass mb-1">
                Ce poate lipsi / trebuie corectat ({check.issues.length})
              </p>
              <ul className="text-sm text-gray-600 dark:text-gray-300 space-y-1 list-disc pl-4">
                {check.issues.map((issue, i) => (
                  <li key={i}>{issue}</li>
                ))}
              </ul>
              {check.suggestions.length > 0 && (
                <div className="mt-1.5 p-2 bg-accent-soft rounded-lg text-xs text-accent-strong">
                  <span className="font-semibold">Sugestii:</span>
                  <ul className="list-disc pl-3 mt-1 space-y-0.5">
                    {check.suggestions.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {!answer && !check && (
        <div className="text-[12px] text-gray-500 italic py-3 text-center">
          Scrie o întrebare despre studiul de mai sus, sau apasă &quot;Verifică studiu&quot; pentru a vedea ce poate lipsi.
        </div>
      )}
    </div>
  )
}
