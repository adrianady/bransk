import { useState, useEffect } from 'react'
import { ArrowLeft, Pencil, Archive, ArchiveRestore, Trash2, GraduationCap, Sparkles, Loader2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../lib/api'
import { subjectTags } from '../lib/tags'
import { useToast } from './Toast'
import type { Entry, Relationship, TodoItem, SpacedRepetitionItem, Reminder, StudyQASource, StudyThesisEvidenceLink } from '../lib/types'
import { StudyQA } from './StudyQA'
import { EntryForm } from './EntryForm'
import { StudyPdfModal } from './StudyPdfModal'

// Wide markdown tables scroll inside their panel on a phone instead of widening
// the page; the table itself stays `display: table`, so desktop alignment is
// untouched. `markdown-table-wrap` lives in index.css.
function getMarkdownComponents(entryTitle?: string) {
  const normTitle = entryTitle ? entryTitle.trim().toLowerCase().replace(/[\s\-_]+/g, ' ') : ''
  return {
    table: ({ node: _node, ...props }: React.ComponentProps<'table'> & { node?: unknown }) => (
      <div className="markdown-table-wrap">
        <table {...props} />
      </div>
    ),
    h1: ({ children, ...props }: React.ComponentProps<'h1'>) => {
      const text = String(children || '').trim().toLowerCase().replace(/[\s\-_]+/g, ' ')
      // Avoid repeating the exact same title if the entry body starts with # Title
      if (normTitle && (text === normTitle || normTitle.includes(text) || text.includes(normTitle))) {
        return null
      }
      return <h2 className="text-base sm:text-lg font-bold mt-4 mb-2 text-gray-900 dark:text-gray-100" {...props}>{children}</h2>
    },
    h2: ({ ...props }: React.ComponentProps<'h2'>) => (
      <h3 className="text-sm sm:text-base font-semibold mt-3 mb-1.5 text-gray-900 dark:text-gray-100" {...props} />
    ),
  }
}

interface Props {
  entry: Entry
  onBack: () => void
  onUpdate: (entry: Entry) => void
  onSelectEntry?: (entry: Entry) => void
  onOpenProjectClaim?: (sectionId: string, claimId: number) => void
  initialPdfSource?: StudyQASource | null
  onPdfSourceConsumed?: () => void
  backLabel?: string
}

const CANONICAL_RELATIONS = [
  { value: 'causes', label: 'cauzează (determinare cauzală directă)' },
  { value: 'modulates', label: 'modulează (reglează sau ajustează activitatea)' },
  { value: 'inhibits', label: 'inhibă (blochează sau suprimă)' },
  { value: 'supports', label: 'susține (dovezi empirice pro)' },
  { value: 'contradicts', label: 'contrazice (dovezi empirice contra)' },
  { value: 'derives_from', label: 'derivă din (filiație conceptuală)' },
  { value: 'extends', label: 'extinde (dezvoltă teoria sau metoda)' },
  { value: 'precedes', label: 'precede (precursor cronologic)' },
  { value: 'includes', label: 'include (conține ca parte integrantă)' },
  { value: 'influences', label: 'influențează (impact indirect)' },
  { value: 'correlates_with', label: 'corelează cu (asociere statistică)' },
]

export function EntryDetail({ entry, onBack, onUpdate, onSelectEntry, onOpenProjectClaim, initialPdfSource, onPdfSourceConsumed, backLabel }: Props) {
  const toast = useToast()
  const [relationships, setRelationships] = useState<Relationship[]>([])
  const [allEntries, setAllEntries] = useState<Entry[]>([])
  const [showEdit, setShowEdit] = useState(false)
  const [pdfModalOpen, setPdfModalOpen] = useState(false)
  const [pdfSource, setPdfSource] = useState<StudyQASource | null>(null)
  const [thesisEvidence, setThesisEvidence] = useState<StudyThesisEvidenceLink[]>([])
  const [showLinkPicker, setShowLinkPicker] = useState(false)
  const [selectedKind, setSelectedKind] = useState('causes')
  const [mechanismText, setMechanismText] = useState('')
  const [linkTarget, setLinkTarget] = useState<number | null>(null)
  // Hundreds of entries make a plain <select> unusable on a phone; this narrows it.
  const [linkFilter, setLinkFilter] = useState('')
  const [todo, setTodo] = useState<TodoItem | null>(null)
  const [todoDraft, setTodoDraft] = useState<{ priority: string; effort: string; due_date: string }>({ priority: 'normal', effort: 'm', due_date: '' })
  const [srItem, setSrItem] = useState<SpacedRepetitionItem | null>(null)
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [showReminderForm, setShowReminderForm] = useState(false)
  const [reminderTitle, setReminderTitle] = useState('')
  const [reminderTrigger, setReminderTrigger] = useState('')
  const [reminderType, setReminderType] = useState('once')
  const [appraising, setAppraising] = useState(false)
  const [activeTab, setActiveTab] = useState<'info' | 'links' | 'todo' | 'review' | 'reminders' | 'qa'>('info')

  useEffect(() => {
    Promise.all([
      api.relationships.list(entry.id),
      api.entries.list({ limit: 500 }),
    ]).then(([rels, entries]) => {
      setRelationships(rels)
      setAllEntries(entries.filter(e => e.id !== entry.id))
    })
    api.todos.list().then(todos => {
      const t = todos.find(t => t.entry_id === entry.id)
      if (t) {
        setTodo(t)
        setTodoDraft({ priority: t.priority, effort: t.effort, due_date: t.due_date || '' })
      }
    })
    api.spacedRepetition.list().then(items => {
      const item = items.find(i => i.entry_id === entry.id)
      if (item) setSrItem(item)
    })
    api.reminders.list().then(rs => {
      setReminders(rs.filter(r => r.entry_id === entry.id))
    })
    api.project.evidenceForStudy(entry.id)
      .then(setThesisEvidence)
      .catch(() => setThesisEvidence([]))
  }, [entry.id])

  useEffect(() => {
    if (!initialPdfSource || initialPdfSource.entry_id !== entry.id) return
    setPdfSource(initialPdfSource)
    setPdfModalOpen(true)
    onPdfSourceConsumed?.()
  }, [entry.id, initialPdfSource?.document_id])

  const addRelation = async () => {
    if (!linkTarget) return
    try {
      const fullLabel = mechanismText.trim() ? `${selectedKind} | ${mechanismText.trim()}` : selectedKind
      await api.relationships.create({ from_id: entry.id, to_id: linkTarget, label: fullLabel })
      const rels = await api.relationships.list(entry.id)
      setRelationships(rels)
      setShowLinkPicker(false)
      setMechanismText('')
      setLinkTarget(null)
      toast.success('Legătură științifică adăugată')
    } catch (err: any) {
      toast.error('Legătura nu a putut fi adăugată: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const addToReview = async () => {
    try {
      const item = await api.spacedRepetition.add(entry.id)
      setSrItem(item)
      toast.success('Adăugată în programul de recapitulare')
    } catch (err: any) {
      toast.error('Nu a putut fi adăugată la recapitulare: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const addTodo = async () => {
    try {
      const item = await api.todos.create(entry.id)
      setTodo(item)
      setTodoDraft({ priority: item.priority, effort: item.effort, due_date: item.due_date || '' })
      toast.success('Sarcină creată')
    } catch (err: any) {
      toast.error('Sarcina nu a putut fi creată: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const saveTodoDraft = async () => {
    if (!todo) return
    try {
      const updated = await api.todos.update(entry.id, {
        priority: todoDraft.priority,
        effort: todoDraft.effort,
        due_date: todoDraft.due_date || null,
      })
      setTodo(updated)
      setTodoDraft({ priority: updated.priority, effort: updated.effort, due_date: updated.due_date || '' })
      toast.success('Detaliile sarcinii au fost salvate')
    } catch (err: any) {
      toast.error('Sarcina nu a putut fi salvată: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const addReminder = async () => {
    if (!reminderTrigger) return
    try {
      const created = await api.reminders.create(entry.id, {
        title: reminderTitle,
        trigger_at: reminderTrigger ? new Date(reminderTrigger).toISOString() : reminderTrigger,
        reminder_type: reminderType,
      })
      setReminders(prev => [...prev, created])
      setShowReminderForm(false)
      setReminderTitle('')
      setReminderTrigger('')
      setReminderType('once')
      toast.success('Reamintire setată')
    } catch (err: any) {
      toast.error('Reamintirea nu a putut fi creată: ' + (err?.message || 'eroare necunoscută'))
    }
  }

  const importanceLabel = (n: number) => {
    const labels: Record<number, string> = { 1: 'Scăzută', 2: 'Medie', 3: 'Importantă', 4: 'Foarte importantă', 5: 'Critică' }
    return labels[n] || 'Normală'
  }

  const qualityLabel = (n: number | null) => {
    if (!n) return ''
    const labels: Record<number, string> = {
      1: 'Slabă', 2: 'Limitată', 3: 'Moderată', 4: 'Puternică', 5: 'Calitate ridicată',
    }
    return labels[n] || ''
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="card">
        <div className="flex items-start justify-between mb-3 gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <button onClick={onBack} className="icon-btn shrink-0" aria-label="Înapoi" title={backLabel || "Înapoi"}>
                <ArrowLeft className="w-5 h-5" />
              </button>
              {backLabel && (
                <button onClick={onBack} className="text-xs text-chrono-600 dark:text-chrono-400 hover:underline font-medium shrink-0">
                  {backLabel}
                </button>
              )}
            </div>
            <h2 className="text-base sm:text-xl font-bold leading-snug break-words line-clamp-3">{entry.title}</h2>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-xs sm:text-sm text-gray-500 min-w-0">
              {entry.entry_date && <span>📅 {entry.entry_date}</span>}
              <span>⭐ {importanceLabel(entry.importance)}</span>
              {entry.source && <span className="break-all min-w-0">📚 {entry.source}</span>}
            </div>
          </div>
          <div className="flex gap-1.5 shrink-0">
            <button onClick={() => setShowEdit(true)} className="icon-btn" aria-label="Editează intrarea" title="Editează intrarea">
              <Pencil className="w-4 h-4" />
            </button>
            <button
              onClick={async () => {
                try {
                  const updated = await api.entries.update(entry.id, { archived: !entry.archived })
                  onUpdate(updated)
                  toast.success(entry.archived ? 'Intrare restaurată' : 'Intrare arhivată')
                } catch (err: any) {
                  toast.error('Intrarea nu a putut fi actualizată: ' + (err?.message || 'eroare necunoscută'))
                }
              }}
              className="icon-btn"
              title={entry.archived ? 'Restaurează intrarea' : 'Arhivează intrarea'}
              aria-label={entry.archived ? 'Restaurează intrarea' : 'Arhivează intrarea'}
            >
              {entry.archived ? <ArchiveRestore className="w-4 h-4" /> : <Archive className="w-4 h-4" />}
            </button>
            <button onClick={async () => {
              if (confirm('Ștergi această intrare?')) {
                try {
                  await api.entries.delete(entry.id)
                  toast.success('Intrare ștearsă')
                  onBack()
                } catch (err: any) {
                  toast.error('Intrarea nu a putut fi ștearsă: ' + (err?.message || 'eroare necunoscută'))
                }
              }
            }} className="icon-btn text-madder" aria-label="Șterge intrarea" title="Șterge intrarea">
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </div>

        {entry.tags && (
          <div className="flex gap-1 flex-wrap mb-3">
            {subjectTags(entry.tags).map(t => (
              <span key={t} className="badge bg-chrono-100 text-chrono-700 dark:bg-chrono-900/50 dark:text-chrono-400">{t}</span>
            ))}
          </div>
        )}

        {/* Study metadata */}
        {entry.study_type && (
          <div className="mb-4 p-3 rounded-lg bg-accent-soft border border-accent/20 text-sm space-y-1">
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span className="badge badge-accent">{entry.study_type}</span>
              {entry.entry_date && <span>📅 {entry.entry_date.slice(0, 4)}</span>}
              {entry.quality != null && (
                <span title={qualityLabel(entry.quality)}>
                  {'★'.repeat(entry.quality)}{'☆'.repeat(5 - entry.quality)}
                  <span className="text-gray-500 ml-1">calitate {entry.quality}/5</span>
                </span>
              )}
            </div>
            {entry.authors && <p className="text-gray-600 dark:text-gray-400"><strong>Autori:</strong> {entry.authors}</p>}
            {(!entry.design || entry.quality == null || !entry.effect_direction) && (
              <div>
                <button
                  onClick={async () => {
                    setAppraising(true)
                    try {
                      const res = await api.studies.aiAppraisal(entry.id)
                      toast.success(`AI: ${res.design || 'design necunoscut'} · ${res.effect_direction || 'efect n/d'} · ${res.quality ?? '?'}/5`)
                      onUpdate({
                        ...entry,
                        design: res.design || entry.design,
                        effect_direction: res.effect_direction || entry.effect_direction,
                        quality: res.quality ?? entry.quality,
                      })
                    } catch (err) {
                      toast.error('Evaluarea AI a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
                    } finally {
                      setAppraising(false)
                    }
                  }}
                  disabled={appraising}
                  className="btn-secondary text-xs py-1 px-2.5 inline-flex items-center gap-1.5"
                  title="Modelul estimează designul, direcția efectului și calitatea din textul studiului; poți corecta apoi în editor"
                >
                  {appraising ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                  AI estimează designul și calitatea
                </button>
              </div>
            )}
            {/* A DOI resolved against Crossref is the trustworthy identifier.
                The stored `source` URL came from the research pipeline and is
                often wrong, so it is shown as "unverified" beside it. */}
            {entry.doi && (
              <p className="text-gray-700 dark:text-gray-300">
                <strong>DOI:</strong>{' '}
                <a
                  href={`https://doi.org/${entry.doi}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-chrono-600 hover:underline break-all"
                >
                  {entry.doi}
                </a>
                <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded bg-verdigris-soft text-verdigris">
                  verificat Crossref
                </span>
              </p>
            )}
            {entry.bib_year && (
              <p className="text-gray-600 dark:text-gray-400 text-xs">
                <strong>An bibliografic:</strong> {entry.bib_year}
              </p>
            )}
            {entry.source && (
              <p className="text-gray-600 dark:text-gray-400 break-all">
                <strong>Sursă:</strong>{' '}
                {entry.source.startsWith('http') ? (
                  <a href={entry.source} target="_blank" rel="noreferrer" className="text-chrono-600 hover:underline">{entry.source}</a>
                ) : entry.source}
                {entry.doi && (
                  <span className="ml-1.5 text-[10px] text-brass">
                    (link original — nu corespunde mereu cu lucrarea)
                  </span>
                )}
              </p>
            )}
            {entry.pdf_path && (
              <p className="pt-1">
                <button
                  type="button"
                  onClick={() => { setPdfSource(null); setPdfModalOpen(true) }}
                  className="btn-secondary text-xs px-2.5 py-1 inline-flex items-center gap-1.5 font-semibold text-chrono-700 dark:text-chrono-300 bg-chrono-50 dark:bg-chrono-950/60 border border-chrono-200 dark:border-chrono-800 hover:bg-chrono-100 dark:hover:bg-chrono-900 transition-colors"
                >
                  📄 Citește PDF & Notițe
                </button>
              </p>
            )}
          </div>
        )}

        {/* Content */}
        <div className="prose prose-sm dark:prose-invert max-w-none mt-4">
          {entry.content ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={getMarkdownComponents(entry.title)}>{entry.content}</ReactMarkdown>
          ) : (
            <p className="text-gray-400 italic">Fără conținut</p>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 overflow-x-auto no-scrollbar pb-px flex-nowrap border-b border-gray-200 dark:border-gray-700">
        {(['info', 'links', 'todo', 'review', 'reminders', 'qa'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`px-2.5 sm:px-4 py-2 text-xs sm:text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === tab
                ? 'border-chrono-600 text-chrono-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab === 'info' && '📋 Informații'}
            {tab === 'links' && `🔗 Legături (${relationships.length})`}
            {tab === 'todo' && '✅ Sarcini'}
            {tab === 'review' && '🔄 Recapitulare'}
            {tab === 'reminders' && `⏰ Reamintiri (${reminders.length})`}
            {tab === 'qa' && '💬 Întrebări'}
          </button>
        ))}
      </div>

      {/* Info Tab */}
      {activeTab === 'info' && (
        <div className="card">
          <h3 className="font-semibold mb-3">Detalii intrare</h3>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <div>
              <dt className="text-xs text-gray-500 mb-0.5">ID</dt>
              <dd>#{entry.id}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500 mb-0.5">Importanță</dt>
              <dd>
                {'★'.repeat(entry.importance)}{'☆'.repeat(5 - entry.importance)}{' '}
                <span className="text-gray-500">{importanceLabel(entry.importance)}</span>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500 mb-0.5">Stare</dt>
              <dd>{entry.archived ? 'Arhivată' : 'Activă'}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500 mb-0.5">Legături în graf</dt>
              <dd>{relationships.length}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500 mb-0.5">Creată</dt>
              <dd>{new Date(entry.created_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500 mb-0.5">Ultima actualizare</dt>
              <dd>{new Date(entry.updated_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500 mb-0.5">Program de recapitulare</dt>
              <dd>
                {srItem
                  ? srItem.next_review
                    ? `Scadentă ${new Date(srItem.next_review).toLocaleDateString()}`
                    : 'Programată'
                  : 'Nu este în rotația de recapitulare'}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500 mb-0.5">Reamintiri</dt>
              <dd>{reminders.length}</dd>
            </div>
          </dl>
        </div>
      )}

      {/* Links Tab */}
      {activeTab === 'links' && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Legături</h3>
            <button onClick={() => setShowLinkPicker(true)} className="btn-primary text-sm">+ Adaugă legătură</button>
          </div>

          {showLinkPicker && (
            <div className="mb-4 p-4 bg-gray-50 dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700 rounded-xl space-y-3">
              <h4 className="text-sm font-semibold">Creează o nouă conexiune deterministă</h4>
              <div className="space-y-1">
                <label className="text-xs text-gray-500 font-medium" htmlFor="link-target-filter">Caută ținta:</label>
                <input
                  id="link-target-filter"
                  value={linkFilter}
                  onChange={e => setLinkFilter(e.target.value)}
                  placeholder="Scrie câteva litere din titlu…"
                  className="input w-full text-sm mb-1"
                />
                <label className="text-xs text-gray-500 font-medium">Țintă conexiune:</label>
                <select value={linkTarget ?? ''} onChange={e => setLinkTarget(Number(e.target.value))} className="input w-full text-sm">
                  <option value="">Selectează intrarea sau studiul țintă...</option>
                  {allEntries
                    .filter(e => !linkFilter.trim() || (e.title || '').toLowerCase().includes(linkFilter.trim().toLowerCase()))
                    .map(e => (
                      <option key={e.id} value={e.id}>{e.title}</option>
                    ))}
                </select>
                {linkFilter.trim() && (
                  <p className="text-[11px] text-gray-500">
                    {allEntries.filter(e => (e.title || '').toLowerCase().includes(linkFilter.trim().toLowerCase())).length} rezultate
                  </p>
                )}
              </div>
              <div className="space-y-1">
                <label className="text-xs text-gray-500 font-medium">Tip relație:</label>
                <select value={selectedKind} onChange={e => setSelectedKind(e.target.value)} className="input w-full text-sm font-medium">
                  {CANONICAL_RELATIONS.map(r => (
                    <option key={r.value} value={r.value}>{r.value} — {r.label}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-gray-500 font-medium">Mecanism / Detaliu științific (opțional):</label>
                <input
                  value={mechanismText}
                  onChange={e => setMechanismText(e.target.value)}
                  className="input w-full text-sm"
                  placeholder="ex. Stimulare sinaptogenetică sau replicare RCT"
                />
              </div>
              <div className="flex gap-2 pt-1">
                <button onClick={addRelation} disabled={!linkTarget} className="btn-primary text-sm">
                  Salvează legătura
                </button>
                <button onClick={() => { setShowLinkPicker(false); setLinkTarget(null); setMechanismText('') }} className="btn-ghost text-sm">
                  Anulează
                </button>
              </div>
            </div>
          )}

          {relationships.length === 0 ? (
            <p className="text-sm text-gray-500">Nicio legătură încă. Leagă această intrare de altele din graful tău de cunoștințe.</p>
          ) : (
            <div className="space-y-2">
              {relationships.map(rel => (
                <div key={rel.id} className="flex items-center justify-between p-2 bg-gray-50 dark:bg-gray-800 rounded-lg text-sm">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span aria-hidden="true">{rel.from_id === entry.id ? '→' : '←'}</span>
                    <button
                      onClick={() => {
                        const target = allEntries.find(e => e.id === (rel.from_id === entry.id ? rel.to_id : rel.from_id))
                        if (target && onSelectEntry) onSelectEntry(target)
                      }}
                      className={`font-medium truncate ${onSelectEntry ? 'text-chrono-700 hover:underline' : ''}`}
                      title={onSelectEntry ? 'Deschide intrarea legată' : undefined}
                    >
                      {allEntries.find(e => e.id === (rel.from_id === entry.id ? rel.to_id : rel.from_id))?.title || `Intrarea ${rel.from_id === entry.id ? rel.to_id : rel.from_id}`}
                    </button>
                    {rel.label && <span className="text-gray-500 ml-1">({rel.label})</span>}
                  </span>
                  <button onClick={async () => {
                    try {
                      await api.relationships.delete(rel.id)
                      setRelationships(prev => prev.filter(r => r.id !== rel.id))
                      toast.success('Legătură eliminată')
                    } catch (err: any) {
                      toast.error('Legătura nu a putut fi eliminată: ' + (err?.message || 'eroare necunoscută'))
                    }
                  }} className="text-madder hover:text-madder/80 text-xs">Elimină</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Todo Tab */}
      {activeTab === 'todo' && (
        <div className="card">
          <h3 className="font-semibold mb-3">Sarcină</h3>
          {todo ? (
            <div className="space-y-2">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={todo.completed}
                  onChange={async () => {
                    try {
                      const updated = await api.todos.update(entry.id, { completed: !todo.completed })
                      setTodo(updated)
                    } catch (err: any) {
                      toast.error('Sarcina nu a putut fi actualizată: ' + (err?.message || 'eroare necunoscută'))
                    }
                  }}
                  className="rounded" />
                <span className={todo.completed ? 'line-through text-gray-400' : ''}>
                  {todo.completed ? 'Finalizată' : 'În lucru'}
                </span>
              </label>
              <div className="grid grid-cols-3 gap-2 pt-1">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Prioritate</label>
                  <select
                    value={todoDraft.priority}
                    onChange={e => setTodoDraft(d => ({ ...d, priority: e.target.value }))}
                    className="input text-sm"
                  >
                    <option value="critical">critică</option>
                    <option value="important">importantă</option>
                    <option value="normal">normală</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Efort</label>
                  <select
                    value={todoDraft.effort}
                    onChange={e => setTodoDraft(d => ({ ...d, effort: e.target.value }))}
                    className="input text-sm"
                  >
                    <option value="s">S</option>
                    <option value="m">M</option>
                    <option value="l">L</option>
                    <option value="xl">XL</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Scadență</label>
                  <input
                    type="date"
                    value={todoDraft.due_date}
                    onChange={e => setTodoDraft(d => ({ ...d, due_date: e.target.value }))}
                    className="input text-sm"
                  />
                </div>
              </div>
              <button onClick={saveTodoDraft} className="btn-secondary text-sm">Salvează detaliile sarcinii</button>
              <button onClick={async () => {
                try {
                  await api.todos.delete(entry.id)
                  setTodo(null)
                  toast.success('Sarcină eliminată')
                } catch (err: any) {
                  toast.error('Sarcina nu a putut fi eliminată: ' + (err?.message || 'eroare necunoscută'))
                }
              }} className="btn-ghost text-sm text-madder">Elimină sarcina</button>
            </div>
          ) : (
            <div>
              <p className="text-sm text-gray-500 mb-2">Nicio sarcină pentru această intrare.</p>
              <button onClick={addTodo} className="btn-primary text-sm">+ Adaugă ca sarcină</button>
            </div>
          )}
        </div>
      )}

      {/* Reminders Tab */}
      {activeTab === 'reminders' && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Reamintiri</h3>
            <button onClick={() => setShowReminderForm(!showReminderForm)} className="btn-primary text-sm">+ Adaugă reamintire</button>
          </div>

          {showReminderForm && (
            <div className="mb-4 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-2">
              <input value={reminderTitle} onChange={e => setReminderTitle(e.target.value)}
                className="input" placeholder="Titlu reamintire (opțional)" />
              <div className="grid grid-cols-2 gap-2">
                <input type="datetime-local" value={reminderTrigger} onChange={e => setReminderTrigger(e.target.value)}
                  className="input" />
                <select value={reminderType} onChange={e => setReminderType(e.target.value)} className="input">
                  <option value="once">O dată</option>
                  <option value="daily">Zilnic</option>
                  <option value="weekly">Săptămânal</option>
                  <option value="monthly">Lunar</option>
                </select>
              </div>
              <div className="flex gap-2">
                <button onClick={addReminder} disabled={!reminderTrigger} className="btn-primary text-sm">Salvează</button>
                <button onClick={() => setShowReminderForm(false)} className="btn-ghost text-sm">Anulează</button>
              </div>
            </div>
          )}

          {reminders.length === 0 ? (
            <p className="text-sm text-gray-500">Nicio reamintire pentru această intrare încă.</p>
          ) : (
            <div className="space-y-2">
              {reminders.map(rem => (
                <div key={rem.id} className="flex items-center justify-between p-2 bg-gray-50 dark:bg-gray-800 rounded-lg text-sm">
                  <div>
                    <p className="font-medium">{rem.title || 'Reamintire'}</p>
                    <p className="text-xs text-gray-500">
                      {new Date(rem.trigger_at).toLocaleString()} · {rem.reminder_type}
                      {rem.notified ? ' · ✅ notificată' : ' · ⏳ în așteptare'}
                    </p>
                  </div>
                  <button onClick={async () => {
                    try {
                      await api.reminders.delete(entry.id, rem.id)
                      setReminders(prev => prev.filter(r => r.id !== rem.id))
                      toast.success('Reamintire eliminată')
                    } catch (err: any) {
                      toast.error('Reamintirea nu a putut fi eliminată: ' + (err?.message || 'eroare necunoscută'))
                    }
                  }} className="text-madder hover:text-madder/80 text-xs">Elimină</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* QA Tab */}
      {activeTab === 'qa' && (
        <div className="space-y-4">
          {thesisEvidence.length > 0 && (
            <div className="card p-3 space-y-2 border-chrono-200 dark:border-chrono-900/60">
              <h3 className="text-xs font-semibold text-chrono-700 dark:text-chrono-300 flex items-center gap-1.5">
                <GraduationCap className="w-4 h-4" /> Folosit în harta tezei ({thesisEvidence.length})
              </h3>
              {thesisEvidence.map(link => (
                <div key={link.evidence_id} className="rounded-md bg-gray-50 dark:bg-gray-800/60 p-2 text-xs">
                  <p className="font-medium">{link.section_title} · {link.relation}</p>
                  <p className="mt-0.5 text-gray-600 dark:text-gray-300">{link.claim}</p>
                  {link.document && (
                    <p className="mt-1 text-[10px] text-gray-500">
                      Fragment #{link.document.chunk_index}
                      {link.document.page_verified && link.document.page_number != null
                        ? ` · pagina PDF ${link.document.page_number}`
                        : ' · pagina neverificată'}
                      {` — ${link.document.preview}`}
                    </p>
                  )}
                  {link.note_preview && <p className="mt-1 text-[10px] text-gray-500">Notiță: {link.note_preview}</p>}
                  <button
                    type="button"
                    onClick={() => onOpenProjectClaim?.(link.section_id, link.claim_id)}
                    disabled={!onOpenProjectClaim}
                    className="mt-1 text-[11px] text-chrono-700 dark:text-chrono-300 hover:underline disabled:opacity-50"
                  >
                    Deschide afirmația în hartă →
                  </button>
                </div>
              ))}
            </div>
          )}
          <StudyQA
            entryId={entry.id}
            title={entry.title}
            onOpenSource={source => { setPdfSource(source); setPdfModalOpen(true) }}
          />
        </div>
      )}

      {/* Review Tab */}
      {activeTab === 'review' && (
        <div className="card">
          <h3 className="font-semibold mb-3">Repetare spațiată</h3>
          {srItem ? (
            <div className="space-y-2 text-sm">
              <p>Factor de ușurință: <span className="font-medium">{srItem.ease_factor.toFixed(2)}</span></p>
              <p>Interval: <span className="font-medium">{srItem.interval} zile</span></p>
              <p>Repetiții: <span className="font-medium">{srItem.repetitions}</span></p>
              {srItem.next_review && <p>Următoarea recapitulare: <span className="font-medium">{srItem.next_review}</span></p>}
              <div className="mt-3">
                <p className="text-xs text-gray-500 mb-2">Evaluează-ți reamintirea:</p>
                <div className="flex gap-1">
                  {[0, 1, 2, 3, 4, 5].map(q => (
                    <button key={q} onClick={async () => {
                      try {
                        const updated = await api.spacedRepetition.review(srItem.id, q)
                        setSrItem(updated)
                        toast.success(`Evaluat ${q}/6 — următoarea recapitulare ${updated.next_review ? new Date(updated.next_review).toLocaleDateString() : 'azi'}`)
                      } catch (err: any) {
                        toast.error('Evaluarea nu a putut fi trimisă: ' + (err?.message || 'eroare necunoscută'))
                      }
                    }}
                      className="btn-ghost text-xs px-2 py-1"
                    >{q}</button>
                  ))}
                </div>
              </div>
              <button onClick={async () => {
                try {
                  await api.spacedRepetition.remove(entry.id)
                  setSrItem(null)
                  toast.success('Eliminată din recapitulare')
                } catch (err: any) {
                  toast.error('Nu a putut fi eliminată din recapitulare: ' + (err?.message || 'eroare necunoscută'))
                }
              }} className="btn-ghost text-sm text-madder mt-2">Elimină din recapitulare</button>
            </div>
          ) : (
            <div>
              <p className="text-sm text-gray-500 mb-2">Nu este în rotația de recapitulare.</p>
              <button onClick={addToReview} className="btn-primary text-sm">+ Adaugă la recapitulare</button>
            </div>
          )}
        </div>
      )}

      {/* Edit Modal */}
      {showEdit && (
        <EntryForm
          editEntry={entry}
          onClose={() => setShowEdit(false)}
          onSaved={(updated) => { onUpdate(updated); setShowEdit(false) }}
        />
      )}

      {/* Native In-App PDF Reader & Notes */}
      {pdfModalOpen && (
        <StudyPdfModal
          study={entry}
          isOpen={pdfModalOpen}
          sourceToOpen={pdfSource}
          onClose={() => { setPdfModalOpen(false); setPdfSource(null) }}
          onNotesUpdated={() => onUpdate(entry)}
        />
      )}
    </div>
  )
}
