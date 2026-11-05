import { useEffect, useState } from 'react'
import { Link2, Loader2, Sparkles, CheckCircle2, XCircle, AlertTriangle } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { SourcesStatus, SourceRunResult } from '../lib/types'

/**
 * Missing sources. The work is done by the app's own model: it proposes the
 * bibliographic title of a study and Crossref accepts or refuses it, so nothing
 * unverified reaches the library. The summaries job is separate and says so:
 * text the model writes about a study it did not read is marked as such.
 */
export function SourcesPanel() {
  const toast = useToast()
  const [status, setStatus] = useState<SourcesStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<'dois' | 'summaries' | null>(null)
  const [limit, setLimit] = useState(10)
  const [doisResult, setDoisResult] = useState<SourceRunResult | null>(null)
  const [sumResult, setSumResult] = useState<SourceRunResult | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      setStatus(await api.sources.status())
    } catch (err) {
      toast.error('Starea surselor nu s-a putut citi: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (kind: 'dois' | 'summaries') => {
    setRunning(kind)
    try {
      const res = kind === 'dois'
        ? await api.sources.resolveDois(limit)
        : await api.sources.fillSummaries(limit)
      // Both jobs report only the fields their own kind fills; the rest default
      // to empty so the render never has to guess.
      const normalised = {
        found: [], missed: [], written: [], skipped: [],
        ...(res as Partial<SourceRunResult>),
      } as SourceRunResult
      if (kind === 'dois') setDoisResult(normalised)
      else setSumResult(normalised)
      await load()
      toast.success(res.note)
    } catch (err) {
      toast.error('Rularea a eșuat: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setRunning(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-4">
        <Loader2 className="w-4 h-4 animate-spin" /> Număr ce lipsește…
      </div>
    )
  }
  if (!status) return null

  return (
    <div className="space-y-4">
      <div className="card">
        <h3 className="font-semibold flex items-center gap-2 mb-1">
          <Link2 className="w-4 h-4" /> Surse lipsă
        </h3>
        <p className="text-sm text-gray-500">
          Modelul din aplicație propune titlul bibliografic, iar Crossref acceptă sau refuză.
          Un DOI greșit nu poate intra: ce nu trece verificarea rămâne gol, nu ghicit.
        </p>
        <div className="flex flex-wrap gap-2 mt-3 text-xs">
          <span className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
            {status.studies} studii
          </span>
          <span className="badge badge-brass">
            {status.without_doi} fără DOI
          </span>
          <span className="badge badge-madder">
            {status.without_any_source} fără nicio sursă
          </span>
          <span className="badge bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
            {status.thin} sub 200 de caractere
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2 mt-4">
          <label className="text-sm text-gray-500" htmlFor="src-limit">Câte odată</label>
          <input
            id="src-limit"
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={e => setLimit(Math.max(1, Math.min(200, Number(e.target.value) || 1)))}
            className="input w-20 text-sm"
          />
          <button onClick={() => run('dois')} disabled={running !== null}
            className="btn-primary text-sm">
            {running === 'dois'
              ? <><Loader2 className="w-4 h-4 animate-spin" /> Caut DOI…</>
              : <><Sparkles className="w-4 h-4" /> Rezolvă DOI-urile</>}
          </button>
          <button onClick={() => run('summaries')} disabled={running !== null}
            className="btn-secondary text-sm">
            {running === 'summaries'
              ? <><Loader2 className="w-4 h-4 animate-spin" /> Scriu rezumate…</>
              : <><Sparkles className="w-4 h-4" /> Rezumate pentru cele subțiri</>}
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-2">
          Crossref limitează cererile, deci fiecare studiu ia ~2 secunde. Rezultatele rămân mai jos.
        </p>
      </div>

      {doisResult && (
        <div className="card">
          <h4 className="font-semibold text-sm mb-2">
            DOI: {doisResult.found.length} găsite din {doisResult.checked}
          </h4>
          <ul className="space-y-1.5 text-sm">
            {doisResult.found.map(f => (
              <li key={f.id} className="flex gap-2 items-start min-w-0">
                <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0 text-verdigris" />
                <div className="min-w-0">
                  <span className="break-words">{f.crossref_title}</span>
                  <span className="block text-xs text-gray-500">
                    {f.doi}{f.year ? ` · ${f.year}` : ''} · #{f.id}
                    {f.written === false && ' · nu s-a scris: DOI-ul e deja la altă intrare'}
                  </span>
                </div>
              </li>
            ))}
            {doisResult.missed.map(m => (
              <li key={m.id} className="flex gap-2 items-start min-w-0">
                <XCircle className="w-4 h-4 mt-0.5 shrink-0 text-gray-400" />
                <div className="min-w-0">
                  <span className="text-gray-600 dark:text-gray-400 break-words">{m.catalog}</span>
                  <span className="block text-xs text-gray-500">
                    {m.why}{m.proposed ? ` · propus: „${m.proposed}”` : ''}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {sumResult && (
        <div className="card">
          <h4 className="font-semibold text-sm mb-2 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-brass" />
            Rezumate: {sumResult.written.length} scrise, {sumResult.skipped.length} sărite
          </h4>
          <p className="text-xs text-gray-500 mb-2">
            Textul scris de model e marcat în intrare, ca să nu fie confundat cu ce ai citit din lucrare.
          </p>
          <ul className="space-y-1 text-sm">
            {sumResult.written.map(w => (
              <li key={w.id} className="break-words">
                ✓ #{w.id} {w.title} <span className="text-xs text-gray-500">({w.chars} car)</span>
              </li>
            ))}
            {sumResult.skipped.slice(0, 5).map(s => (
              <li key={s.id} className="text-xs text-gray-500 break-words">
                − #{s.id} {s.title}: {s.why}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
