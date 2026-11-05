import { useEffect, useState } from 'react'
import { CheckCircle2, AlertCircle, Info, Loader2, Sparkles, Type } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { TitleAnalysis } from '../lib/types'

const LEVEL_ICON = {
  critical: AlertCircle,
  warning: AlertCircle,
  info: Info,
} as const

const LEVEL_COLOR = {
  critical: 'text-madder',
  warning: 'text-brass',
  info: 'text-muted',
} as const

/**
 * The title read as an advisor reads it: what it promises, what it hides, and
 * three alternatives. The checks run without a model, so this panel is useful
 * the moment it opens; the written reading costs one call and waits for a click.
 */
export function TitlePanel() {
  const toast = useToast()
  const [data, setData] = useState<TitleAnalysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [reviewing, setReviewing] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      setData(await api.project.title())
    } catch (err) {
      toast.error('Analiza titlului a eșuat: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const review = async () => {
    setReviewing(true)
    try {
      setData(await api.project.reviewTitle())
    } catch (err) {
      toast.error('Lectura titlului a eșuat: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setReviewing(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-4">
        <Loader2 className="w-4 h-4 animate-spin" /> Citesc titlul…
      </div>
    )
  }
  if (!data) return null

  const color = data.score >= 85 ? 'text-verdigris'
    : data.score >= 60 ? 'text-brass'
    : 'text-madder'

  return (
    <div className="card">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
        <h3 className="font-semibold flex items-center gap-2">
          <Type className="w-4 h-4" /> Analiza titlului
        </h3>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`text-sm font-bold tabular-nums ${color}`}>{data.score}/100</span>
          <button onClick={review} disabled={reviewing} className="btn-secondary text-xs">
            {reviewing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
            Citește-l critic
          </button>
        </div>
      </div>
      <p className="text-sm text-muted mb-3">{data.verdict}</p>

      {/* The shape of the title, measured — not guessed. */}
      <div className="flex flex-wrap gap-2 mb-3 text-xs">
        <span className="badge badge-muted">
          {data.stats.words} cuvinte
        </span>
        <span className={`badge ${data.stats.population.length ? 'badge-verdigris' : 'badge-madder'}`}>
          {data.stats.population.length ? 'populație numită' : 'fără populație'}
        </span>
        {data.stats.vague.length > 0 && (
          <span className="badge badge-brass">
            {data.stats.vague.length} cuvinte vagi
          </span>
        )}
        {data.stats.acronyms.length > 0 && (
          <span className="badge badge-muted">
            {data.stats.acronyms.join(', ')}
          </span>
        )}
      </div>

      <ul className="space-y-1.5">
        {data.checks.map((c, i) => {
          const Icon = LEVEL_ICON[c.level]
          return (
            <li key={i} className="flex gap-2 items-start min-w-0">
              <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${LEVEL_COLOR[c.level]}`} aria-hidden />
              <div className="min-w-0">
                <span className="text-sm break-words">{c.message}</span>
                {c.action && (
                  <span className="block text-xs text-gray-500 break-words">{c.action}</span>
                )}
              </div>
            </li>
          )
        })}
      </ul>

      {data.review && (
        <div className="mt-4 pt-3 border-t border-line">
          <p className="section-label mb-2">Lectura critică</p>
          <div className="text-sm whitespace-pre-wrap break-words">{data.review}</div>
        </div>
      )}
      {!data.review && data.note && (
        <p className="text-xs text-gray-500 mt-3">{data.note}</p>
      )}
      {!data.review && !data.note && (
        <p className="text-xs text-gray-400 mt-3">
          „Citește-l critic” cere modelului o lectură de îndrumător: ce promite, ce nu spune și trei variante.
        </p>
      )}
    </div>
  )
}
