import { useEffect, useState } from 'react'
import {
  Compass, Loader2, CheckCircle2, AlertCircle, XCircle, FileText, ChevronRight, Lightbulb,
} from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { Project, TopicCandidate, TopicCoverage, TopicsResponse } from '../lib/types'

interface Props {
  onProjectChange: (p: Project) => void
  onOpenTitle: () => void
}

const VERDICT_STYLE: Record<string, { icon: typeof CheckCircle2; cls: string; label: string }> = {
  'solidă': { icon: CheckCircle2, cls: 'text-verdigris', label: 'solidă' },
  'posibilă': { icon: AlertCircle, cls: 'text-brass', label: 'posibilă' },
  'fragilă': { icon: AlertCircle, cls: 'text-brass', label: 'fragilă' },
  'nepotrivită': { icon: XCircle, cls: 'text-madder', label: 'nepotrivită' },
}

function CoverageLine({ cov }: { cov: TopicCoverage }) {
  const style = VERDICT_STYLE[cov.verdict] ?? VERDICT_STYLE['posibilă']
  const Icon = style.icon
  return (
    <div className="space-y-1.5">
      <p className={`flex items-center gap-1.5 text-sm font-medium ${style.cls}`}>
        <Icon className="w-4 h-4 shrink-0" aria-hidden />
        {style.label} — {cov.matched} studii potrivite
      </p>
      <div className="flex flex-wrap gap-1.5 text-xs">
        <span className="badge badge-muted">
          {cov.with_doi} cu DOI
        </span>
        <span className="badge badge-muted">
          {cov.with_body} cu text citibil
        </span>
        <span className="badge badge-muted">
          {cov.designs.length} tipuri de design
        </span>
      </div>
      <p className="text-xs text-gray-500">{cov.note}</p>
      {cov.top_titles.length > 0 && (
        <ul className="text-xs text-gray-600 dark:text-gray-400 space-y-0.5">
          {cov.top_titles.map((t, i) => (
            <li key={i} className="break-words">· {t}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * Choosing the topic, which is harder than wording a title. Every candidate is
 * measured against the real library, so the screen says what can be written
 * from what exists instead of proposing topics the library cannot carry.
 */
export function TopicPanel({ onProjectChange, onOpenTitle }: Props) {
  const toast = useToast()
  const [data, setData] = useState<TopicsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [choosing, setChoosing] = useState<string | null>(null)
  const [titleDraft, setTitleDraft] = useState('')
  const [questionDraft, setQuestionDraft] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      setData(await api.project.topics())
    } catch (err) {
      toast.error('Temele nu s-au putut încărca: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const choose = async (title: string, question: string) => {
    setChoosing(title)
    try {
      const updated = await api.project.chooseTopic(title, question)
      onProjectChange(updated)
      setTitleDraft('')
      setQuestionDraft('')
      toast.success('Tema a fost aleasă. Titlul și întrebarea s-au actualizat.')
      await load()
    } catch (err) {
      toast.error('Alegerea a eșuat: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setChoosing(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted py-6">
        <Loader2 className="w-4 h-4 animate-spin" /> Măsor ce poate duce biblioteca…
      </div>
    )
  }
  if (!data) return null

  const own = data.candidates.filter(c => c.source === 'current')
  const seeds = data.candidates.filter(c => c.source !== 'current')

  return (
    <div className="space-y-4">
      <div className="card">
        <h3 className="font-semibold flex items-center gap-2 mb-1">
          <Compass className="w-4 h-4" /> Alegerea temei
        </h3>
        <p className="text-sm text-gray-500">
          Fiecare temă de mai jos e măsurată pe biblioteca ta: câte studii o acoperă,
          câte au DOI, câte au text din care poți cita. O temă fără studii nu e o temă,
          e o intenție.
        </p>
        <button onClick={onOpenTitle} className="btn-secondary text-sm mt-3">
          <ChevronRight className="w-4 h-4" /> Vezi analiza titlului actual
        </button>
      </div>

      {/* The topic in use, measured like any other */}
      {own.map(c => (
        <div key={c.title} className="card border-accent/30">
          <p className="section-label mb-1">Tema ta acum</p>
          <h4 className="font-semibold break-words mb-2">{c.title}</h4>
          <CoverageLine cov={c.coverage} />
        </div>
      ))}

      {/* Seeds, best-supported first */}
      <div className="card">
        <h3 className="font-semibold mb-1">Teme pe care biblioteca le poate duce</h3>
        <p className="text-sm text-gray-500 mb-3">
          Ordinea vine din măsurătoarea de mai sus, nu dintr-o preferință a mea.
        </p>
        <ul className="divide-y divide-line">
          {seeds.map(c => (
            <li key={c.title} className="py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
                <div className="min-w-0">
                  <h4 className="font-medium text-sm break-words">{c.title}</h4>
                  <p className="text-xs text-gray-500 break-words">{c.question}</p>
                </div>
                <button
                  onClick={() => choose(c.title, c.question)}
                  disabled={choosing !== null}
                  className="btn-secondary text-xs shrink-0"
                >
                  {choosing === c.title
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <CheckCircle2 className="w-3.5 h-3.5" />}
                  Alege tema
                </button>
              </div>
              <CoverageLine cov={c.coverage} />
            </li>
          ))}
        </ul>
      </div>

      {/* A topic of your own */}
      <div className="card">
        <h3 className="font-semibold mb-1">Sau scrie-ți tema</h3>
        <p className="text-sm text-gray-500 mb-3">
          O poți alege pe a ta; măsurătoarea se face după ce o salvezi.
        </p>
        <div className="space-y-2">
          <input
            value={titleDraft}
            onChange={e => setTitleDraft(e.target.value)}
            placeholder="Titlul temei tale"
            className="input w-full text-sm"
            aria-label="Titlul temei"
          />
          <input
            value={questionDraft}
            onChange={e => setQuestionDraft(e.target.value)}
            placeholder="Întrebarea de cercetare (opțional)"
            className="input w-full text-sm"
            aria-label="Întrebarea de cercetare"
          />
          <button
            onClick={() => choose(titleDraft, questionDraft)}
            disabled={choosing !== null || titleDraft.trim().length < 3}
            className="btn-primary text-sm"
          >
            {choosing === titleDraft ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
            Folosește tema asta
          </button>
        </div>
      </div>

      {/* Ideas already noted, so this page connects to the brainstorming pad */}
      {data.ideas.length > 0 && (
        <div className="card">
          <h3 className="font-semibold flex items-center gap-2 mb-2">
            <Lightbulb className="w-4 h-4" /> Din ideile tale
          </h3>
          <ul className="text-sm space-y-1">
            {data.ideas.slice(0, 6).map((i, idx) => (
              <li key={idx} className="flex gap-2 min-w-0">
                <span className="badge text-xs badge-muted shrink-0">
                  {i.category}
                </span>
                <span className="break-words">{i.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
