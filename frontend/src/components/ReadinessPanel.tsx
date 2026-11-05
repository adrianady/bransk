import { useEffect, useState } from 'react'
import { CheckCircle2, AlertCircle, XCircle, Loader2, Sparkles, Save } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from './Toast'
import { SourcesPanel } from './SourcesPanel'
import type {
  Project, ProjectPlan, ProjectReadinessResponse, ProjectSchemaResponse, ReadinessItem,
} from '../lib/types'

interface Props {
  project: Project | null
  onProjectChange: (p: Project) => void
}

/** The eight fields a thesis needs that are not the outline. */
const PLAN_FIELDS: { key: keyof ProjectPlan; label: string; hint: string; rows: number }[] = [
  { key: 'participants', label: 'Participanți', hint: 'Cine intră în studiu: criterii de includere, număr, grupe.', rows: 2 },
  { key: 'instruments', label: 'Instrumente', hint: 'Ce măsori și cu ce: chestionare, scale, aparatură.', rows: 2 },
  { key: 'hypotheses', label: 'Ipoteze', hint: 'Ce te aștepți să găsești, formulat verificabil.', rows: 3 },
  { key: 'variables', label: 'Variabile', hint: 'Independente, dependente, de control.', rows: 2 },
  { key: 'analysis', label: 'Analiză', hint: 'Testul statistic sau metoda calitativă pe care o aplici.', rows: 2 },
  { key: 'ethics', label: 'Etică', hint: 'Consimțământ informat, aprobare, confidențialitate.', rows: 2 },
  { key: 'schedule', label: 'Calendar', hint: 'Etape și termene până la predare.', rows: 2 },
  { key: 'risks', label: 'Riscuri', hint: 'Ce poate merge prost și ce faci atunci.', rows: 2 },
]

const STATE_ICON = {
  ready: CheckCircle2,
  partial: AlertCircle,
  missing: XCircle,
} as const

const STATE_COLOR = {
  ready: 'text-verdigris',
  partial: 'text-brass',
  missing: 'text-madder',
} as const

export function ReadinessPanel({ project, onProjectChange }: Props) {
  const toast = useToast()
  const [readiness, setReadiness] = useState<ProjectReadinessResponse | null>(null)
  const [schema, setSchema] = useState<ProjectSchemaResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [plan, setPlan] = useState<ProjectPlan>({})
  const [savingPlan, setSavingPlan] = useState(false)

  useEffect(() => {
    setPlan(project?.plan || {})
  }, [project?.plan])

  const load = async () => {
    setLoading(true)
    try {
      const [r, s] = await Promise.all([api.project.readiness(), api.project.schema()])
      setReadiness(r)
      setSchema(s)
    } catch (err) {
      toast.error('Nu am putut încărca pregătirea: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const savePlan = async () => {
    setSavingPlan(true)
    try {
      const updated = await api.project.update({ plan })
      onProjectChange(updated)
      await load()
      toast.success('Planul de cercetare a fost salvat.')
    } catch (err) {
      toast.error('Salvarea a eșuat: ' + (err instanceof Error ? err.message : ''))
    } finally {
      setSavingPlan(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted py-8">
        <Loader2 className="w-4 h-4 animate-spin" /> Verific ce ai deja…
      </div>
    )
  }

  const summary = readiness?.summary

  return (
    <div className="space-y-5">
      {/* ── Where the thesis stands ── */}
      {summary && (
        <div className="card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h3 className="font-semibold">Ai tot ce îți trebuie pentru temă?</h3>
              <p className="text-sm text-muted">
                {summary.ready} din {summary.total} zone gata
                {summary.partial > 0 && ` · ${summary.partial} parțiale`}
                {summary.missing > 0 && ` · ${summary.missing} lipsesc`}
                {' — '}
                <span className={
                  summary.score >= 75 ? 'text-verdigris'
                  : summary.score >= 45 ? 'text-brass'
                  : 'text-madder'
                }>{summary.verdict}</span>
              </p>
            </div>
            <div className="text-2xl font-bold tabular-nums shrink-0">{summary.score}%</div>
          </div>
          <div className="mt-3 h-1.5 rounded-full bg-surface-2 overflow-hidden" role="progressbar"
            aria-valuenow={summary.score} aria-valuemin={0} aria-valuemax={100}>
            <div className={`h-full transition-all ${
              summary.score >= 75 ? 'bg-verdigris' : summary.score >= 45 ? 'bg-brass' : 'bg-madder'
            }`} style={{ width: `${summary.score}%` }} />
          </div>
          {summary.next && (
            <p className="mt-3 text-sm">
              <span className="text-muted">Următorul pas: </span>
              {summary.next.action || summary.next.missing}
            </p>
          )}
        </div>
      )}

      {/* ── The visual map of the topic ── */}
      {schema && <TopicMap schema={schema} />}

      {/* ── Every area, with what exists and what does not ── */}
      {readiness && (
        <div className="card">
          <h3 className="font-semibold mb-3">Ce ai și ce lipsește</h3>
          <ul className="space-y-2">
            {readiness.items.map(item => (
              <ReadinessRow key={item.area} item={item} />
            ))}
          </ul>
        </div>
      )}

      {/* ── Missing sources: the app's model does the work ── */}
      <SourcesPanel />

      {/* ── The research plan ── */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
          <h3 className="font-semibold">Planul de cercetare</h3>
          <button onClick={savePlan} disabled={savingPlan} className="btn-primary text-sm">
            {savingPlan ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Salvează planul
          </button>
        </div>
        <p className="text-sm text-muted mb-4">
          Părțile pe care o lucrare le are în afara cuprinsului. Ce scrii aici intră și în analiza temei.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {PLAN_FIELDS.map(f => {
            const targetId = project?.plan_map?.by_key?.[f.key as string]
            const target = targetId ? project?.structure?.find(s => s.id === targetId) : undefined
            return (
              <label key={f.key} className="block min-w-0">
                <span className="text-sm font-medium">{f.label}</span>
                <span className="block text-xs text-muted mb-1">{f.hint}</span>
                <textarea
                  value={plan[f.key] || ''}
                  onChange={e => setPlan(p => ({ ...p, [f.key]: e.target.value }))}
                  rows={f.rows}
                  className="input w-full text-sm resize-y"
                />
                <span className={`block text-[11px] mt-1 ${target ? 'text-accent' : 'text-muted'}`}>
                  {target ? `→ ${target.title}` : 'fără capitol propriu — apare la finalul tezei exportate'}
                </span>
              </label>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function ReadinessRow({ item }: { item: ReadinessItem }) {
  const Icon = STATE_ICON[item.state]
  return (
    <li className="flex gap-2 items-start min-w-0">
      <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${STATE_COLOR[item.state]}`} aria-hidden />
      <div className="min-w-0">
        <span className="text-sm font-medium">{item.area}</span>
        {item.have && item.state !== 'missing' && (
          <span className="text-sm text-muted"> — {item.have}</span>
        )}
        {item.missing && (
          <span className="block text-xs text-muted">
            lipsește: {item.missing}
            {item.action ? ` · ${item.action}` : ''}
          </span>
        )}
      </div>
    </li>
  )
}

/**
 * The topic drawn as what it is: a question, the pillars that carry the
 * library, the theories behind it and the designs already found. Missing
 * pieces are named, not drawn as if they existed.
 */
function TopicMap({ schema }: { schema: ProjectSchemaResponse }) {
  const has = (v: string) => Boolean(v && v.trim())

  return (
    <div className="card">
      <h3 className="font-semibold mb-1 flex items-center gap-2">
        <Sparkles className="w-4 h-4" /> Schema temei
      </h3>
      <p className="text-sm text-muted mb-4">
        Construită din ce ai în bibliotecă acum — nu din ce ar trebui să ai.
      </p>

      <div className="space-y-3">
        {/* Question */}
        <MapBox
          title="Întrebarea de cercetare"
          filled={has(schema.question)}
          empty="Nescrisă încă — fără ea, analiza doar ghicește."
        >
          {schema.question}
        </MapBox>

        {/* Pillars */}
        <div>
          <p className="section-label mb-2">Pilonii temei</p>
          {schema.pillars.length ? (
            <div className="flex flex-wrap gap-2">
              {schema.pillars.map(p => (
                <span key={p.tag} className="badge badge-accent">
                  {p.tag} · {p.count}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-sm text-madder">Niciun tag de subiect în bibliotecă.</p>
          )}
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {/* Theories */}
          <div className="min-w-0">
            <p className="section-label mb-2">
              Teorii ({schema.theories.length})
            </p>
            {schema.theories.length ? (
              <ul className="text-sm space-y-1">
                {schema.theories.slice(0, 6).map(t => (
                  <li key={t.id} className="truncate">
                    {t.name} <span className="text-xs text-gray-500">({t.status})</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-madder">Nicio teorie — firul teoretic lipsește.</p>
            )}
          </div>

          {/* Designs */}
          <div className="min-w-0">
            <p className="section-label mb-2">Metode găsite în studii</p>
            {schema.designs.length ? (
              <ul className="text-sm space-y-1">
                {schema.designs.map(d => (
                  <li key={d.design}>{d.design} <span className="text-xs text-gray-500">×{d.count}</span></li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-madder">
                Niciun design notat pe studii — nu știi ce fel de dovezi ai.
              </p>
            )}
          </div>
        </div>

        {/* Variables */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {([
            ['Variabile', schema.variables.independent],
            ['Instrumente', schema.variables.instruments],
            ['Analiză', schema.variables.analysis],
          ] as const).map(([label, value]) => (
            <MapBox key={label} title={label} filled={has(value)} empty="Necompletat" small>
              {value}
            </MapBox>
          ))}
        </div>

        {/* Evidence links */}
        {schema.evidence_links.length > 0 && (
          <div>
            <p className="section-label mb-2">
              Dovezi legate de teorii
            </p>
            <div className="flex flex-wrap gap-2">
              {schema.evidence_links.map((e, i) => (
                <span key={i} className={`badge text-xs ${e.stance === 'pro' ? 'badge-verdigris' : 'badge-madder'}`}>
                  {e.theory} ({e.stance} · {e.n})
                </span>
              ))}
            </div>
          </div>
        )}

        {/* What is missing, named */}
        {schema.gaps.length > 0 && (
          <div className="rounded-lg border border-madder/25 bg-madder-soft/40 p-3">
            <p className="text-sm font-semibold text-madder mb-1">
              Lipsesc {schema.gaps.length} piese
            </p>
            <ul className="text-xs text-muted space-y-0.5">
              {schema.gaps.slice(0, 6).map(g => (
                <li key={g.area}>· {g.area}{g.missing ? ` — ${g.missing}` : ''}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

function MapBox({ title, filled, empty, children, small }: {
  title: string
  filled: boolean
  empty: string
  children: React.ReactNode
  small?: boolean
}) {
  return (
    <div className={`rounded-lg border p-2.5 min-w-0 ${
      filled
        ? 'border-line bg-surface-2/60'
        : 'border-dashed border-madder/40'
    }`}>
      <p className="section-label">{title}</p>
      <p className={`mt-1 text-sm ${filled ? '' : 'text-madder'}`}>
        {filled ? children : empty}
      </p>
    </div>
  )
}
