import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import type { Entry } from '../lib/types'

interface Props {
  onSelectEntry: (entry: Entry) => void
}

const typeColors: Record<string, string> = {
  'peer-reviewed paper': 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  'systematic review': 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  'meta-analysis': 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  book: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  'case study': 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
  study: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
}

export function StudiesView({ onSelectEntry }: Props) {
  const [studies, setStudies] = useState<Entry[]>([])
  const [theoryLinks, setTheoryLinks] = useState<Record<number, { theory_name: string; stance: string }[]>>({})
  const [loading, setLoading] = useState(true)
  const [filterType, setFilterType] = useState('')
  const [filterQuality, setFilterQuality] = useState(0)

  const load = async () => {
    setLoading(true)
    try {
      const [items, evidence] = await Promise.all([api.studies.list(), api.theories.allEvidence()])
      setStudies(items)
      const map: Record<number, { theory_name: string; stance: string }[]> = {}
      evidence.forEach(e => {
        if (!map[e.entry_id]) map[e.entry_id] = []
        map[e.entry_id].push({ theory_name: e.theory_name, stance: e.stance })
      })
      setTheoryLinks(map)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const types = [...new Set(studies.map(s => s.study_type).filter(Boolean))].sort()
  const filtered = studies.filter(s => {
    if (filterType && s.study_type !== filterType) return false
    if (filterQuality > 0 && (s.quality ?? 0) < filterQuality) return false
    return true
  })

  if (loading) return <div className="text-center py-8 text-gray-500">Loading studies...</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Studies</h2>
          <p className="text-sm text-gray-500">
            Qualitative research findings · {filtered.length} of {studies.length} studies
          </p>
        </div>
        <button onClick={load} className="btn-secondary">↻ Refresh</button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <select value={filterType} onChange={e => setFilterType(e.target.value)} className="input w-auto">
          <option value="">All types</option>
          {types.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={filterQuality} onChange={e => setFilterQuality(Number(e.target.value))} className="input w-auto">
          <option value={0}>Any quality</option>
          <option value={4}>4★ and up</option>
          <option value={3}>3★ and up</option>
        </select>
        {(filterType || filterQuality > 0) && (
          <button onClick={() => { setFilterType(''); setFilterQuality(0) }} className="btn-ghost text-sm">Clear filters</button>
        )}
      </div>

      {filtered.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-500 mb-2">No studies yet.</p>
          <p className="text-sm text-gray-400">
            Ask the AI to research a topic — studies are saved here, appear on the timeline by publication year,
            and are linked into the knowledge graph.
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map(study => (
            <button key={study.id} onClick={() => onSelectEntry(study)}
              className="card text-left hover:border-chrono-300 dark:hover:border-chrono-700 transition-colors flex flex-col"
            >
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <h3 className="font-semibold leading-snug">{study.title}</h3>
                {study.quality != null && (
                  <span className="text-xs shrink-0" title={`Quality ${study.quality}/5`}>
                    {'★'.repeat(study.quality)}{'☆'.repeat(5 - study.quality)}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {study.study_type && (
                  <span className={`badge text-xs ${typeColors[study.study_type] || typeColors.study}`}>{study.study_type}</span>
                )}
                {study.design && (
                  <span className="badge text-xs bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">{study.design}</span>
                )}
                {study.effect_direction && (
                  <span className={`badge text-xs ${
                    study.effect_direction === 'positive' ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300'
                    : study.effect_direction === 'negative' ? 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                    : study.effect_direction === 'mixed' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                    : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                  }`}>
                    {study.effect_direction === 'positive' ? '↗' : study.effect_direction === 'negative' ? '↘' : study.effect_direction === 'mixed' ? '⇄' : '○'} {study.effect_direction}
                  </span>
                )}
                {study.entry_date && <span className="text-xs text-gray-500">📅 {study.entry_date.slice(0, 4)}</span>}
              </div>
              {study.authors && <p className="text-xs text-gray-500 mb-2 line-clamp-1">✍️ {study.authors}</p>}
              {study.source && (
                <p className="text-xs text-gray-400 truncate mb-2">
                  🔗 {study.source}
                  <span className={`ml-1 ${study.url_validated ? 'text-green-500' : 'text-amber-500'}`}>
                    {study.url_validated ? '✓ verified' : '○ unverified'}
                  </span>
                </p>
              )}
              <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-3 flex-1">
                {study.content.replace(/[#*`\[\]()]/g, '').slice(0, 300)}
              </p>
              {study.tags && (
                <div className="flex gap-1 mt-2 flex-wrap">
                  {study.tags.split(',').map(t => t.trim()).filter(t => t && t !== 'study').slice(0, 4).map(t => (
                    <span key={t} className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">{t}</span>
                  ))}
                </div>
              )}
              {theoryLinks[study.id] && theoryLinks[study.id].length > 0 && (
                <div className="flex gap-1 mt-2 flex-wrap">
                  {theoryLinks[study.id].map((t, i) => (
                    <span key={i} title={`Evidence for this theory in the aggregator`}
                      className={`badge text-xs ${t.stance === 'pro' ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300' : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'}`}>
                      🧬 {t.theory_name} ({t.stance})
                    </span>
                  ))}
                </div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
