import { useEffect, useState } from 'react'
import {
  ClipboardList, Plus, Trash2, Edit3, Sparkles, Loader2,
  Copy, Check, AlertCircle, HelpCircle, CheckCircle2, ChevronRight,
  ChevronDown, BookOpen, Layers
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { Questionnaire, QuestionnaireItem } from '../lib/types'

interface Props {
  projectId?: number
}

const DEFAULT_OPTIONS_LIKERT5 = [
  '1 = Total dezacord',
  '2 = Dezacord',
  '3 = Neutru / Indecis',
  '4 = Acord',
  '5 = Total acord',
]

export function QuestionnairesPanel({ projectId }: Props) {
  const toast = useToast()
  const [questionnaires, setQuestionnaires] = useState<Questionnaire[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  
  // Editor state
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState('')
  const [construct, setConstruct] = useState('')
  const [targetPopulation, setTargetPopulation] = useState('')
  const [description, setDescription] = useState('')
  const [reliabilityNotes, setReliabilityNotes] = useState('')
  const [items, setItems] = useState<QuestionnaireItem[]>([])
  const [saving, setSaving] = useState(false)

  // AI Operations
  const [generatingAi, setGeneratingAi] = useState(false)
  const [critiquingAi, setCritiquingAi] = useState(false)
  const [critiqueModal, setCritiqueModal] = useState<{ title: string; critique: string } | null>(null)
  const [copiedAppendix, setCopiedAppendix] = useState(false)

  const loadQuestionnaires = async () => {
    setLoading(true)
    try {
      const data = await api.questionnaires.list(projectId)
      setQuestionnaires(data)
      if (data.length > 0 && selectedId === null) {
        selectQuestionnaire(data[0])
      }
    } catch (err) {
      toast.error('Nu s-au putut încărca chestionarele: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadQuestionnaires()
  }, [projectId])

  const selectQuestionnaire = (q: Questionnaire) => {
    setSelectedId(q.id)
    setTitle(q.title)
    setConstruct(q.construct)
    setTargetPopulation(q.target_population)
    setDescription(q.description)
    setReliabilityNotes(q.reliability_notes)
    setItems(q.items || [])
    setEditing(false)
  }

  const startNew = () => {
    setSelectedId(null)
    setTitle('')
    setConstruct('')
    setTargetPopulation('Adulți')
    setDescription('')
    setReliabilityNotes('Alpha Cronbach estimat > 0.80')
    setItems([
      {
        id: 'q1',
        text: '',
        type: 'likert',
        options: [...DEFAULT_OPTIONS_LIKERT5],
        subscale: 'Dimensiunea 1',
      },
    ])
    setEditing(true)
  }

  const addItem = () => {
    const nextId = `q${items.length + 1}`
    setItems([
      ...items,
      {
        id: nextId,
        text: '',
        type: 'likert',
        options: [...DEFAULT_OPTIONS_LIKERT5],
        subscale: items.length > 0 ? items[items.length - 1].subscale : '',
      },
    ])
  }

  const updateItem = (index: number, patch: Partial<QuestionnaireItem>) => {
    const updated = [...items]
    updated[index] = { ...updated[index], ...patch }
    setItems(updated)
  }

  const removeItem = (index: number) => {
    const updated = items.filter((_, i) => i !== index)
    setItems(updated)
  }

  const handleSave = async () => {
    if (!title.trim()) {
      toast.error('Te rugăm să introduci un titlu pentru chestionar.')
      return
    }
    setSaving(true)
    try {
      const payload = {
        title,
        construct,
        target_population: targetPopulation,
        description,
        reliability_notes: reliabilityNotes,
        items: items.filter(it => it.text.trim().length > 0),
      }
      let saved: Questionnaire
      if (selectedId) {
        saved = await api.questionnaires.update(selectedId, payload)
        toast.success('Chestionarul a fost actualizat.')
      } else {
        saved = await api.questionnaires.create(payload)
        toast.success('Chestionarul a fost creat cu succes.')
      }
      await loadQuestionnaires()
      selectQuestionnaire(saved)
      setEditing(false)
    } catch (err) {
      toast.error('Eroare la salvare: ' + (err instanceof Error ? err.message : 'necunoscută'))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Ești sigur că vrei să ștergi acest chestionar?')) return
    try {
      await api.questionnaires.delete(id)
      toast.success('Chestionarul a fost șters.')
      const remaining = questionnaires.filter(q => q.id !== id)
      setQuestionnaires(remaining)
      if (remaining.length > 0) {
        selectQuestionnaire(remaining[0])
      } else {
        startNew()
      }
    } catch (err) {
      toast.error('Eroare la ștergere: ' + (err instanceof Error ? err.message : 'necunoscută'))
    }
  }

  const handleAiGenerate = async (id: number) => {
    setGeneratingAi(true)
    try {
      const updated = await api.questionnaires.aiGenerate(id)
      selectQuestionnaire(updated)
      await loadQuestionnaires()
      toast.success('Itemii au fost generați cu AI pe baza contextului tezei!')
    } catch (err) {
      toast.error('Generarea AI a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setGeneratingAi(false)
    }
  }

  const handleAiCritique = async (id: number) => {
    setCritiquingAi(true)
    try {
      const res = await api.questionnaires.aiCritique(id)
      setCritiqueModal(res)
    } catch (err) {
      toast.error('Analiza critică AI a eșuat: ' + (err instanceof Error ? err.message : 'eroare'))
    } finally {
      setCritiquingAi(false)
    }
  }

  const handleCopyAppendix = async () => {
    const selected = questionnaires.find(q => q.id === selectedId)
    if (!selected) return

    const lines: string[] = []
    lines.push(`# ANEXA 1: INSTRUMENT DE CERCETARE`)
    lines.push(`## ${selected.title}`)
    if (selected.construct) lines.push(`**Construct măsurat:** ${selected.construct}`)
    if (selected.target_population) lines.push(`**Populație țintă:** ${selected.target_population}`)
    if (selected.description) lines.push(`\n${selected.description}\n`)
    if (selected.reliability_notes) lines.push(`*Note metodologice:* ${selected.reliability_notes}\n`)
    lines.push(`### Instrucțiuni pentru participanți:`)
    lines.push(`Vă rugăm să citiți fiecare afirmație cu atenție și să indicați în ce măsură sunteți de acord cu ea.\n`)
    
    lines.push(`### Itemi:`)
    selected.items.forEach((it, idx) => {
      lines.push(`${idx + 1}. ${it.text}`)
      if (it.subscale) lines.push(`   *(Subscală: ${it.subscale})*`)
      lines.push(`   Opțiuni: ${it.options.join(' | ')}\n`)
    })

    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      setCopiedAppendix(true)
      setTimeout(() => setCopiedAppendix(false), 2500)
      toast.success('Chestionarul a fost copiat în clipboard în format Anexă Teză!')
    } catch {
      toast.error('Copierea în clipboard a eșuat. Verifică permisiunile browserului pentru clipboard.')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-10 justify-center">
        <Loader2 className="w-5 h-5 animate-spin text-chrono-600" />
        <span>Se încarcă instrumentele de cercetare…</span>
      </div>
    )
  }

  const selectedQuestionnaire = questionnaires.find(q => q.id === selectedId)

  return (
    <div className="space-y-4">
      {/* ── Top Bar ── */}
      <div className="card flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-base sm:text-lg flex items-center gap-2">
            <ClipboardList className="w-5 h-5 text-chrono-600 dark:text-chrono-400" />
            <span>Zona de Chestionare & Instrumente de Cercetare</span>
          </h3>
          <p className="text-xs sm:text-sm text-gray-500">
            Creează, operaționalizează și evaluează psihometric scalele și chestionarele pentru partea empirică a lucrării.
          </p>
        </div>
        <button
          onClick={startNew}
          className="btn-primary text-xs sm:text-sm flex items-center gap-1.5"
        >
          <Plus className="w-4 h-4" />
          <span>Chestionar Nou</span>
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
        {/* ── Sidebar: List of Questionnaires ── */}
        <div className="md:col-span-4 space-y-2">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider px-1">
            Instrumente ({questionnaires.length})
          </p>
          {questionnaires.length === 0 ? (
            <div className="card text-center py-6">
              <ClipboardList className="w-8 h-8 text-gray-400 mx-auto mb-2" />
              <p className="text-xs text-gray-500 mb-3">Nu există chestionare adăugate în acest proiect.</p>
              <button onClick={startNew} className="btn-secondary text-xs">
                Creează primul chestionar
              </button>
            </div>
          ) : (
            <div className="space-y-1.5">
              {questionnaires.map(q => {
                const isSelected = q.id === selectedId && !editing
                return (
                  <div
                    key={q.id}
                    onClick={() => selectQuestionnaire(q)}
                    className={`card !p-3 cursor-pointer transition-all border ${
                      isSelected
                        ? 'border-chrono-600 dark:border-chrono-400 bg-chrono-50/50 dark:bg-chrono-950/20 shadow-sm'
                        : 'border-transparent hover:border-gray-200 dark:hover:border-gray-700'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <h4 className="text-sm font-semibold truncate text-ink">{q.title || 'Fără titlu'}</h4>
                        <div className="flex items-center gap-2 mt-1 text-xs text-gray-500">
                          <span>{q.items.length} itemi</span>
                          {q.construct && <span>• {q.construct}</span>}
                        </div>
                      </div>
                      <ChevronRight className="w-4 h-4 text-gray-400 shrink-0 mt-0.5" />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* ── Main Area: Questionnaire Details & Item Editor ── */}
        <div className="md:col-span-8 space-y-4">
          {editing || !selectedQuestionnaire ? (
            /* ── Editor Form ── */
            <div className="card space-y-4">
              <div className="flex items-center justify-between border-b pb-3 border-line">
                <h4 className="font-semibold text-sm sm:text-base flex items-center gap-2">
                  <Edit3 className="w-4 h-4 text-chrono-600" />
                  <span>{selectedId ? 'Editează Instrumentul' : 'Instrument Nou de Cercetare'}</span>
                </h4>
                <div className="flex items-center gap-2">
                  {selectedQuestionnaire && (
                    <button
                      onClick={() => setEditing(false)}
                      className="btn-secondary text-xs"
                    >
                      Renunță
                    </button>
                  )}
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="btn-primary text-xs flex items-center gap-1.5"
                  >
                    {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                    <span>Salvează</span>
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-gray-600 dark:text-gray-300 mb-1">
                    Titlul chestionarului / scalei *
                  </label>
                  <input
                    type="text"
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                    placeholder="Ex: Scala de Evaluare a Rezilienței CD-RISC"
                    className="input w-full text-sm"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-gray-600 dark:text-gray-300 mb-1">
                    Construct măsurat
                  </label>
                  <input
                    type="text"
                    value={construct}
                    onChange={e => setConstruct(e.target.value)}
                    placeholder="Ex: Reziliență psihologică, Mecanisme de adaptare"
                    className="input w-full text-sm"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-gray-600 dark:text-gray-300 mb-1">
                    Populație țintă
                  </label>
                  <input
                    type="text"
                    value={targetPopulation}
                    onChange={e => setTargetPopulation(e.target.value)}
                    placeholder="Ex: Adulți, Adolescenți, Studenți"
                    className="input w-full text-sm"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-gray-600 dark:text-gray-300 mb-1">
                    Descriere metodologică și instrucțiuni
                  </label>
                  <textarea
                    value={description}
                    onChange={e => setDescription(e.target.value)}
                    rows={2}
                    placeholder="Descrie scopul instrumentului și cum este administrat participanților..."
                    className="input w-full text-sm"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-gray-600 dark:text-gray-300 mb-1">
                    Fidelitate & Consistență internă (Alpha Cronbach, Scorare)
                  </label>
                  <input
                    type="text"
                    value={reliabilityNotes}
                    onChange={e => setReliabilityNotes(e.target.value)}
                    placeholder="Ex: Alpha Cronbach > 0.85; scorare 1-5, scoruri inversate la itemii 3 și 7"
                    className="input w-full text-sm"
                  />
                </div>
              </div>

              {/* Items Section */}
              <div className="pt-3 border-t border-line space-y-3">
                <div className="flex items-center justify-between">
                  <h5 className="text-xs font-semibold text-gray-600 dark:text-gray-300 uppercase tracking-wider">
                    Itemi de evaluare ({items.length})
                  </h5>
                  <button
                    onClick={addItem}
                    className="btn-secondary text-xs flex items-center gap-1"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Adaugă Item</span>
                  </button>
                </div>

                <div className="space-y-3">
                  {items.map((it, idx) => (
                    <div key={idx} className="p-3 bg-surface-2 rounded-lg border border-line space-y-2">
                      <div className="flex items-start gap-2">
                        <span className="text-xs font-bold text-gray-400 mt-2 shrink-0">{idx + 1}.</span>
                        <input
                          type="text"
                          value={it.text}
                          onChange={e => updateItem(idx, { text: e.target.value })}
                          placeholder="Formulați afirmația sau întrebarea (ex: Reușesc să mă adaptez la schimbare...)"
                          className="input flex-1 text-sm"
                        />
                        <button
                          onClick={() => removeItem(idx)}
                          className="icon-btn text-madder hover:bg-madder/10 mt-1 shrink-0"
                          title="Șterge item"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                        <div>
                          <label className="text-[11px] text-gray-500 block mb-0.5">Subscală / Dimensiune:</label>
                          <input
                            type="text"
                            value={it.subscale || ''}
                            onChange={e => updateItem(idx, { subscale: e.target.value })}
                            placeholder="Ex: Adaptabilitate, Control personal"
                            className="input w-full text-xs"
                          />
                        </div>
                        <div>
                          <label className="text-[11px] text-gray-500 block mb-0.5">Opțiuni de scală (separate prin virgulă):</label>
                          <input
                            type="text"
                            value={it.options.join(', ')}
                            onChange={e => updateItem(idx, { options: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                            className="input w-full text-xs"
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            /* ── Details View ── */
            <div className="card space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b pb-3 border-line">
                <div>
                  <h4 className="font-bold text-base sm:text-xl text-ink">{selectedQuestionnaire.title}</h4>
                  <div className="flex flex-wrap items-center gap-2 mt-1.5 text-xs text-gray-500">
                    <span className="badge badge-chrono">{selectedQuestionnaire.items.length} itemi</span>
                    {selectedQuestionnaire.construct && (
                      <span className="badge badge-muted">Construct: {selectedQuestionnaire.construct}</span>
                    )}
                    {selectedQuestionnaire.target_population && (
                      <span className="badge badge-verdigris">Populație: {selectedQuestionnaire.target_population}</span>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-1.5">
                  <button
                    onClick={() => setEditing(true)}
                    className="btn-secondary text-xs flex items-center gap-1"
                  >
                    <Edit3 className="w-3.5 h-3.5" />
                    <span>Editează</span>
                  </button>
                  <button
                    onClick={() => handleDelete(selectedQuestionnaire.id)}
                    className="btn-secondary text-xs text-madder hover:bg-madder/10 flex items-center gap-1"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Șterge</span>
                  </button>
                </div>
              </div>

              {/* Action Toolbar for Academic AI */}
              <div className="flex flex-wrap items-center gap-2 p-2.5 bg-surface-2 rounded-lg border border-line">
                <button
                  onClick={() => handleAiGenerate(selectedQuestionnaire.id)}
                  disabled={generatingAi}
                  className="btn-secondary text-xs flex items-center gap-1.5 text-chrono-600 dark:text-chrono-400 font-semibold"
                >
                  {generatingAi ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                  <span>Generează / Completează cu AI</span>
                </button>

                <button
                  onClick={() => handleAiCritique(selectedQuestionnaire.id)}
                  disabled={critiquingAi}
                  className="btn-secondary text-xs flex items-center gap-1.5 text-brass"
                >
                  {critiquingAi ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                  <span>Evaluare Psihometrică AI</span>
                </button>

                <button
                  onClick={handleCopyAppendix}
                  className="btn-secondary text-xs flex items-center gap-1.5 ml-auto"
                >
                  {copiedAppendix ? <Check className="w-3.5 h-3.5 text-verdigris" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>Copiază Anexa Lucrării</span>
                </button>
              </div>

              {selectedQuestionnaire.description && (
                <div className="text-xs sm:text-sm text-gray-600 dark:text-gray-300">
                  <p className="font-semibold text-xs text-gray-400 uppercase tracking-wider mb-1">Descriere metodologică:</p>
                  <p>{selectedQuestionnaire.description}</p>
                </div>
              )}

              {selectedQuestionnaire.reliability_notes && (
                <div className="text-xs sm:text-sm text-gray-600 dark:text-gray-300">
                  <p className="font-semibold text-xs text-gray-400 uppercase tracking-wider mb-1">Fidelitate & Consistență:</p>
                  <p className="italic">{selectedQuestionnaire.reliability_notes}</p>
                </div>
              )}

              {/* Items List */}
              <div className="space-y-2 pt-2 border-t border-line">
                <p className="font-semibold text-xs text-gray-400 uppercase tracking-wider mb-2">Itemi operaționalizați:</p>
                {selectedQuestionnaire.items.length === 0 ? (
                  <p className="text-xs text-gray-400 italic">Nu există itemi adăugați. Apasă pe „Generează / Completează cu AI” sau pe „Editează”.</p>
                ) : (
                  <div className="space-y-2">
                    {selectedQuestionnaire.items.map((it, idx) => (
                      <div key={idx} className="p-3 bg-surface-2 rounded-lg border border-line text-sm space-y-1.5">
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-medium text-ink">
                            <span className="font-bold text-gray-400 mr-2">{idx + 1}.</span>
                            {it.text}
                          </p>
                          {it.subscale && (
                            <span className="text-[11px] badge badge-muted shrink-0">{it.subscale}</span>
                          )}
                        </div>
                        <div className="flex flex-wrap gap-1.5 text-xs text-gray-500 pt-1">
                          {it.options.map((opt, oIdx) => (
                            <span key={oIdx} className="px-2 py-0.5 rounded bg-surface border border-line text-[11px]">
                              {opt}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── AI Critique Modal ── */}
      {critiqueModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="card max-w-2xl w-full max-h-[85vh] flex flex-col shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b pb-3 border-line">
              <h4 className="font-bold text-base flex items-center gap-2">
                <CheckCircle2 className="w-5 h-5 text-verdigris" />
                <span>Evaluare Psihometrică AI: {critiqueModal.title}</span>
              </h4>
              <button
                onClick={() => setCritiqueModal(null)}
                className="icon-btn text-gray-400 hover:text-ink"
              >
                ✕
              </button>
            </div>
            <div className="overflow-y-auto flex-1 text-sm prose dark:prose-invert max-w-none pr-1">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {critiqueModal.critique}
              </ReactMarkdown>
            </div>
            <div className="border-t pt-3 border-line flex justify-end">
              <button
                onClick={() => setCritiqueModal(null)}
                className="btn-primary text-xs"
              >
                Închide
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
