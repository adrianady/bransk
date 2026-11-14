import { useState, useRef, useEffect, memo, useCallback } from 'react'
import { X, Trash2, Square, Loader2, Zap, BookOpen } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../lib/api'
import { useToast } from './Toast'
import { StudyPdfModal } from './StudyPdfModal'
import { useReadingContext } from '../lib/readingContext'
import type { ProviderHealth, ChatAction, ChatTask, Entry } from '../lib/types'
interface Props {
  onClose: () => void
}

// Human labels for actions the backend can emit. Anything missing here still
// renders through the generic fallback below, so a new capability is never
// silent in the transcript.
const ACTION_LABELS: Record<string, string> = {
  entry_updated: 'Intrare actualizată',
  section_updated: 'Text de capitol scris',
  project_updated: 'Teză actualizată',
  plan_updated: 'Planul tezei actualizat',
  study_updated: 'Studiu actualizat',
  study_added: 'Studiu adăugat',
  tags_added: 'Etichete adăugate',
  relationship_updated: 'Legătură actualizată',
  theory_updated: 'Teorie actualizată',
  todo_completed: 'Sarcină finalizată',
  review_scheduled: 'Recapitulare programată',
  entry_deleted: 'Intrare ștearsă',
  entries_merged: 'Intrări unificate',
  relationship_deleted: 'Legătură eliminată',
  theory_deleted: 'Teorie ștearsă',
  study_unlinked: 'Dovadă de studiu deconectată',
  todo_deleted: 'Sarcină ștearsă',
  reminder_deleted: 'Reamintire ștearsă',
  pdf_indexed: 'PDF de studiu indexat',
  data_reanalyzed: 'Verificare de integritate reluată',
  study_pdf_downloaded: 'PDF descărcat',
  studies_results: 'Studii găsite',
  screening_decision_recorded: 'Decizie PRISMA salvată',
  task_started: 'Task activ pornit',
  task_updated: 'Progres task',
  task_completed: 'Task finalizat',
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  provider?: string
  model?: string
  action?: ChatAction
}

const friendlyError = (err: unknown): string => {
  if (!err) return ''
  if (typeof err === 'string') return err
  if (typeof err === 'object') {
    const raw = ('detail' in err && err.detail) || ('message' in err && err.message)
    if (typeof raw === 'string') return raw
    if (Array.isArray(raw)) {
      const msgs = raw
        .map((d: unknown) => {
          if (typeof d === 'string') return d
          if (d && typeof d === 'object' && 'msg' in d && typeof d.msg === 'string') return d.msg
          return ''
        })
        .filter(Boolean)
      if (msgs.length > 0) return msgs.join('; ')
    }
    try { return JSON.stringify(raw || err) } catch { return String(err) }
  }
  return String(err)
}

// Long AI replies (e.g. research pipeline plans) must not blow past the
// backend's per-message history limit or flood the next LLM call.
const MAX_HISTORY_LEN = 10000
const MAX_HISTORY_MESSAGES = 12

const providerIcon = (p?: string) => {
  if (p === 'deepseek') return '🔵'
  if (p === 'ollama') return '🟢'
  return '🤖'
}

const providerLabel = (p?: string) => {
  if (p === 'deepseek') return 'DeepSeek'
  if (p === 'ollama') return 'Ollama local'
  return 'AI'
}

// A wide markdown table must scroll inside the chat bubble on a phone instead of
// widening the page. The table stays a real `display: table` (desktop column
// alignment untouched); only the wrapper scrolls — see `markdown-table-wrap` in
// index.css. The same few lines live in the other renderers touched by this
// change, because it may not add a shared module.
const markdownComponents = {
  table: ({ node: _node, ...props }: React.ComponentProps<'table'> & { node?: unknown }) => (
    <div className="markdown-table-wrap">
      <table {...props} />
    </div>
  ),
}

// Protocol tags like [SEARCH_KB: ...] or [WRITE_SECTION: ...] are commands, not
// prose. Drop whole lines that are only a tag so the transcript stays readable;
// a message that was nothing but a tag renders no bubble at all.
export const stripActionTags = (text: string): string =>
  text
    .split('\n')
    .filter(line => !/^\s*\[[A-Z][A-Z0-9_]*\s*:[^\]]*\]\s*$/.test(line))
    .join('\n')
    .replace(/\[[A-Z][A-Z0-9_]{2,}\s*:[^\]]*\]/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

// Memoized so typing in the input (which re-renders the panel) never re-parses
// existing messages through ReactMarkdown. Without this, one long research-plan
// message (tens of thousands of chars) made every keystroke block the UI.
const MessageBubble = memo(function MessageBubble({
  role,
  content,
  provider,
  model,
  action,
  onOpenStudyPdf,
}: {
  role: 'user' | 'assistant'
  content: string
  provider?: string
  model?: string
  action?: ChatAction
  onOpenStudyPdf?: (entryId: number) => void
}) {
  const shown = role === 'assistant' ? stripActionTags(content) : content
  // A reply that was nothing but protocol tags has no prose to show.
  if (!shown && !action) return null
  return (
    <div className={`flex ${role === 'user' ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[88%] rounded-2xl px-4 py-2.5 text-sm ${
        role === 'user'
          ? 'bg-chrono-600 text-white rounded-br-md'
          : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-bl-md shadow-sm'
      }`}>
        {role === 'assistant' && provider && (
          <div className="flex items-center gap-1 mb-1.5">
            <span className="text-xs">{providerIcon(provider)}</span>
            <span className="text-[10px] text-gray-400 font-medium uppercase">{providerLabel(provider)}</span>
            {model && <span className="text-[10px] text-gray-400">· {model.split(':')[0]}</span>}
          </div>
        )}
        {shown && (
          <div className={`prose prose-sm max-w-none ${role === 'user' ? 'prose-invert' : 'dark:prose-invert'} prose-headings:text-sm prose-headings:font-semibold prose-a:text-chrono-600`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{shown}</ReactMarkdown>
          </div>
        )}
        {action?.type === 'study_pdf_downloaded' && action.entry_id && (
          <div className="mt-2.5 pt-2 border-t border-gray-100 dark:border-gray-700/60 flex items-center gap-2">
            <button
              onClick={() => onOpenStudyPdf?.(action.entry_id)}
              className="btn-primary text-xs px-2.5 py-1 flex items-center gap-1.5"
            >
              <BookOpen className="w-3.5 h-3.5" />
              Deschide PDF-ul
            </button>
          </div>
        )}
      </div>
    </div>
  )
})

export function ChatPanel({ onClose }: Props) {
  const toast = useToast()
  const reading = useReadingContext()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [provider, setProvider] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [health, setHealth] = useState<ProviderHealth | null>(null)
  const [researching, setResearching] = useState(false)
  const [pendingConfirm, setPendingConfirm] = useState<{ confirm_id: string; message: string } | null>(null)
  const [activeTask, setActiveTask] = useState<ChatTask | null>(null)
  const [cancellingTask, setCancellingTask] = useState(false)
  const [progressMessage, setProgressMessage] = useState<string | null>(null)
  const [pdfModalStudy, setPdfModalStudy] = useState<Entry | null>(null)
  // Question whose stream died mid-answer; shown as a one-click resend.
  const [retryText, setRetryText] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  // Watchdog for a silently dead stream: byte arrival time + why we aborted.
  const lastByteRef = useRef(0)
  const stalledRef = useRef(false)
  const fullContentRef = useRef('')
  const streamTimerRef = useRef<number | null>(null)

  // Throttle streaming re-renders: flushing accumulated chunks at most every
  // 80ms keeps typing responsive while the model is generating a long reply.
  const flushStreaming = () => {
    streamTimerRef.current = null
    setStreamingContent(fullContentRef.current)
  }
  const queueStreamingFlush = () => {
    if (streamTimerRef.current == null) {
      streamTimerRef.current = window.setTimeout(flushStreaming, 80)
    }
  }

  // Load chat history and provider health on mount
  useEffect(() => {
    api.chat.history.list().then(history => {
      if (history.length > 0) {
        const msgs: Message[] = []
        for (const h of history) {
          if (h.role !== 'system') {
            msgs.push({ role: h.role as 'user' | 'assistant', content: h.content, provider: h.provider, model: h.model })
          }
        }
        setMessages(msgs)
      } else {
        setMessages([{ role: 'assistant', content: 'Salut! Te pot ajuta să explorezi baza de cunoștințe, să salvezi informații, să cercetezi teme și să gestionezi intrări. Ce vrei să facem?' }])
      }
    }).catch(() => {})

    api.chat.health().then(setHealth).catch(() => {})
    const interval = setInterval(() => api.chat.health().then(setHealth).catch(() => {}), 30000)
    return () => clearInterval(interval)
  }, [])

  const refreshActiveTask = useCallback(() => {
    api.chat.getTask().then(res => {
      setActiveTask(res?.task && res.task.status === 'active' ? res.task : null)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    refreshActiveTask()
    const taskInterval = setInterval(refreshActiveTask, 8000)
    return () => clearInterval(taskInterval)
  }, [refreshActiveTask])

  const handleCancelTask = async () => {
    setCancellingTask(true)
    try {
      await api.chat.cancelTask()
      setActiveTask(null)
      toast.info('Taskul activ a fost anulat')
      setMessages(prev => [...prev, { role: 'assistant', content: '🛑 Taskul activ a fost anulat.' }])
    } catch (err: any) {
      toast.error('Anularea a eșuat: ' + (err?.message || 'eroare necunoscută'))
    } finally {
      setCancellingTask(false)
    }
  }

  const handleOpenStudyPdf = async (entryId: number) => {
    try {
      const entry = await api.entries.get(entryId)
      setPdfModalStudy(entry)
    } catch (err: any) {
      toast.error('Nu am putut deschide PDF-ul studiului: ' + (err?.message || 'eroare'))
    }
  }

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, streamingContent])
  useEffect(() => { inputRef.current?.focus() }, [])

  // Esc closes the chat panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => () => {
    if (streamTimerRef.current != null) clearTimeout(streamTimerRef.current)
  }, [])

  const sendMessage = async () => {
    if (!input.trim() || loading) return
    const controller = new AbortController()
    abortRef.current = controller
    const userMsg: Message = { role: 'user', content: input.trim() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)
    setStreamingContent('')
    setProgressMessage(null)
    fullContentRef.current = ''
    setProvider(null)
    try {
      const history = messages
        .slice(-MAX_HISTORY_MESSAGES)
        .map(m => ({ role: m.role, content: m.content.slice(0, MAX_HISTORY_LEN) }))
      const response = await api.chat.stream(
        input.trim().slice(0, 100000),
        history,
        controller.signal,
        {
          entry_id: reading.entryId ?? undefined,
          page: reading.page ?? undefined,
        },
      )
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }))
        throw new Error(friendlyError(err) || `HTTP ${response.status}`)
      }
      if (!response.body) throw new Error('Răspuns fără conținut')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let currentProvider = ''
      let currentModel = ''
      let actions: ChatAction[] = []

      // A dead link (phone locked, Wi-Fi switched, Tailscale drop) does not always
      // reject the read: it can stay pending forever. 90 s without a single byte
      // is treated as an interruption, so the user gets their partial answer and
      // a resend button instead of an endless spinner. The server also emits
      // `: ping` keepalive comments during silent stretches, so reaching 90 s
      // means the link itself is gone, not just a slow model.
      lastByteRef.current = Date.now()
      stalledRef.current = false
      const watchdog = window.setInterval(() => {
        if (Date.now() - lastByteRef.current > 90000) {
          stalledRef.current = true
          controller.abort()
        }
      }, 5000)

      try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        lastByteRef.current = Date.now()
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim()
            if (data === '[DONE]') continue
            try {
              const parsed = JSON.parse(data)
              if (parsed.type === 'chunk' && parsed.content) {
                fullContentRef.current += parsed.content
                queueStreamingFlush()
              } else if (parsed.type === 'reset_content') {
                fullContentRef.current = ''
                setStreamingContent('')
              } else if (parsed.type === 'provider') {
                currentProvider = parsed.provider
                currentModel = parsed.model
                setProvider(currentProvider)
                setModel(currentModel)
              } else if (parsed.type === 'progress') {
                if (parsed.phase === 'research') {
                  setResearching(true)
                }
                const phaseLabels: Record<string, string> = {
                  search: 'Căutare studii',
                  download: 'Descărcare PDF',
                  protocol: 'Protocol cercetare',
                  dive: 'Analiză dovezi',
                  synthesis: 'Sinteză academică',
                  save: 'Salvare date',
                  pdf: 'Indexare PDF',
                  maintenance: 'Verificare integritate',
                }
                const phaseStr = typeof parsed.phase === 'string' ? parsed.phase : ''
                const label = phaseStr && phaseLabels[phaseStr] ? phaseLabels[phaseStr] : ''
                const rawMsg = typeof parsed.message === 'string' ? parsed.message : ''
                const msg = rawMsg || (label ? `Fază: ${label}` : 'Se procesează...')
                setProgressMessage(label && !msg.startsWith(label) ? `[${label}] ${msg}` : msg)
              } else if (parsed.type === 'research' && parsed.result) {
                setResearching(false)
                setProgressMessage(null)
                const result = parsed.result
                const studiesArr = Array.isArray(result.studies) ? result.studies : []
                const studyLines = studiesArr
                  .map((s: { title?: string; year?: number | null; study_type?: string; quality?: number }, i: number) =>
                    `${i + 1}. **${s.title || 'Studiu'}**${s.year ? ` (${s.year})` : ''} — ${s.study_type || 'studiu'} — calitate ${s.quality ?? 3}/5`)
                  .join('\n')
                const entriesCreated = Array.isArray(result.entries_created) ? result.entries_created : []
                const theoriesArr = Array.isArray(result.theories) ? result.theories : []
                const researchMsg =
                  `📚 **Cercetare finalizată: "${result.topic || 'Temă'}"**\n\n` +
                  `${result.summary || ''}\n\n` +
                  `**Studii salvate (${studiesArr.length}):**\n${studyLines}\n\n` +
                  (result.confidence ? `**Încredere:** ${result.confidence}\n\n` : '') +
                  `✅ ${entriesCreated.length} intrări create · ${result.relationships_created || 0} legături în graf` +
                  (theoriesArr.length ? ` · 🧬 ${theoriesArr.length} teorii în agregator` : '') +
                  `. Deschide vizualizarea **Studii** sau **Teorii** pentru a explora.`
                setMessages(prev => [...prev, { role: 'assistant', content: researchMsg }])
              } else if (parsed.type === 'tool') {
                setMessages(prev => [...prev, { role: 'assistant', content: `🛠️ ${parsed.message || 'Unelte folosite'}` }])
              } else if (parsed.type === 'error') {
                setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ **Eroare:** ${friendlyError(parsed.content) || 'Ceva nu a funcționat.'}` }])
              } else if (parsed.type === 'actions') {
                actions = parsed.content || []
              }
            } catch {}
          }
        }
      }

      if (streamTimerRef.current != null) {
        clearTimeout(streamTimerRef.current)
        streamTimerRef.current = null
      }
      const fullContent = fullContentRef.current

      if (fullContent) {
        setMessages(prev => [...prev, { role: 'assistant', content: fullContent, provider: currentProvider, model: currentModel }])
      }
      // Show action results as assistant messages
      for (const action of actions) {
        let actionMsg = ''
        if (action.type === 'entry_created') actionMsg = `📝 **Intrare salvată:** "${action.title}" (ID: ${action.id})`
        else if (action.type === 'relationship_created') actionMsg = `🔗 **Legătură creată:** Intrarea ${action.from} → ${action.to} (${action.label})`
        else if (action.type === 'todo_added') actionMsg = `✅ **Sarcină adăugată** la intrarea ${action.entry_id}`
        else if (action.type === 'sr_scheduled') actionMsg = `🔄 **Adăugată în programul de recapitulare:** intrarea ${action.entry_id}`
        else if (action.type === 'web_search_request') actionMsg = `🔍 **Interogare de cercetare:** "${action.query}"`
        else if (action.type === 'research_request') actionMsg = `🔬 **Cercetare solicitată:** "${action.topic}"`
        else if (action.type === 'entry_fetched' && action.entry) {
          const e = action.entry
          actionMsg = `📄 **Intrarea #${e.id}: ${e.title}** (${e.entry_date || 'fără dată'})\n\n${(e.content || 'Fără conținut').slice(0, 1500)}`
        }
        else if (action.type === 'entries_listed') {
          const results = Array.isArray(action.results) ? action.results : []
          actionMsg = `🗂️ **Intrări${action.tag ? ` etichetate "${action.tag}"` : ''} (${results.length}):**\n` + (
            results.length
              ? results.map((r: { id: number; title: string; entry_date?: string }, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
              : 'Nimic găsit.'
          )
        }
        else if (action.type === 'relationships_listed') {
          const results = Array.isArray(action.results) ? action.results : []
          actionMsg = `🔗 **Legături pentru intrarea #${action.entry_id} (${results.length}):**\n` + (
            results.length
              ? results.map((r: { from_id: number; to_id: number; label?: string }) => `- #${r.from_id} --${r.label || 'related_to'}--> #${r.to_id}`).join('\n')
              : 'Nicio legătură încă.'
          )
        }
        else if (action.type === 'theories_listed') {
          const results = Array.isArray(action.results) ? action.results : []
          actionMsg = `🧬 **Teorii${action.query ? ` care corespund "${action.query}"` : ''} (${results.length}):**\n` + (
            results.length
              ? results.map((r: { id: number; name: string; status: string; pro_count: number; contra_count: number }) => `- #${r.id} **${r.name}** [${r.status}] — pro ${r.pro_count} / contra ${r.contra_count}`).join('\n')
              : 'Nimic găsit.'
          )
        }
        else if (action.type === 'entry_appended') actionMsg = `✏️ **Intrarea #${action.entry_id} actualizată** (conținut adăugat)`
        else if (action.type === 'entry_archived') actionMsg = `🗄️ **Intrarea #${action.entry_id} arhivată**`
        else if (action.type === 'thought_created') actionMsg = `💭 **Gând creat:** "${action.name}"`
        else if (action.type === 'tag_created') actionMsg = `🏷️ **Etichetă creată:** "${action.name}"`
        else if (action.type === 'reminder_added') actionMsg = `⏰ **Reamintire setată** pentru intrarea #${action.entry_id}`
        else if (action.type === 'theory_created') actionMsg = `🧬 **Teorie creată:** "${action.name}" (ID ${action.id})`
        else if (action.type === 'study_linked') actionMsg = `🔗 **Dovadă legată:** intrarea #${action.entry_id} → teoria #${action.theory_id} (${action.stance})`
        else if (action.type === 'web_results') {
          const results = Array.isArray(action.results) ? action.results : []
          actionMsg = `🌐 **Rezultate web pentru "${action.query}" (${results.length}):**\n` + (
            results.length
              ? results.map((r: { title: string; url: string; snippet?: string }, i: number) => `${i + 1}. [${r.title}](${r.url})${r.snippet ? ` — ${r.snippet.slice(0, 150)}` : ''}`).join('\n')
              : 'Niciun rezultat (offline sau blocat).'
          )
        }
        else if (action.type === 'studies_results') {
          const results = Array.isArray(action.results) ? action.results : []
          actionMsg = `📚 **Studii găsite pentru "${action.query || ''}" (${results.length}):**\n\n` + (
            results.length
              ? results.map((r: { title?: string; year?: number | null; authors?: string; doi?: string; is_oa?: boolean; source?: string; snippet?: string }, i: number) => {
                  const title = r.title || 'Fără titlu'
                  const year = r.year ? ` (${r.year})` : ''
                  const authors = r.authors ? ` — *${r.authors}*` : ''
                  const doiLink = r.doi ? ` [DOI: ${r.doi}](https://doi.org/${r.doi})` : ''
                  const oa = r.is_oa ? ' `[Open Access]`' : ''
                  const source = r.source ? ` · *${r.source}*` : ''
                  const snippet = r.snippet ? `\n   > ${r.snippet.slice(0, 160).replace(/\n/g, ' ')}...` : ''
                  return `${i + 1}. **${title}**${year}${authors}${oa}${source}${doiLink}${snippet}`
                }).join('\n\n')
              : 'Niciun studiu găsit pentru această căutare.'
          )
        }
        else if (action.type === 'study_pdf_downloaded') {
          const title = action.title ? ` "${action.title}"` : ` #${action.entry_id}`
          actionMsg = `📥 **PDF descărcat cu succes pentru studiul${title}**\n` +
            (action.pdf_path ? `- Fișier: \`${action.pdf_path}\`\n` : '') +
            `- Intrarea #${action.entry_id} are textul complet indexat.\n\n` +
            `*Apasă pe butonul de mai jos pentru a citi PDF-ul în aplicație.*`
        }
        else if (action.type === 'task_started') {
          actionMsg = `⚡ **Task activ pornit:** ${action.goal || action.details || ''}`
        }
        else if (action.type === 'task_updated') {
          actionMsg = `🔄 **Progres task:** ${action.details || ''}`
        }
        else if (action.type === 'task_completed') {
          actionMsg = `✅ **Task finalizat:** ${action.goal || action.details || ''}`
        }
        else if (action.type === 'screening_decision_recorded') {
          actionMsg = `⚖️ **Decizie PRISMA:** Studiul #${action.entry_id} → **${action.decision}** (${action.reason || 'fără motiv specificat'})`
        }
        else if (action.type === 'documents_results') {
          const results = Array.isArray(action.results) ? action.results : []
          actionMsg = `📄 **Potriviri PDF de studiu pentru "${action.query}" (${results.length}):**\n` + (
            results.length
              ? results.map((r: { entry_title: string; content?: string }, i: number) => `${i + 1}. **${r.entry_title}** — ${(r.content || '').slice(0, 220)}`).join('\n')
              : 'Nicio potrivire PDF încă. Descarcă PDF-urile de studiu, apoi rulează Verificare → Integritatea datelor pentru a le indexa.'
          )
        }
        else if (action.type === 'search_results') {
          const results = Array.isArray(action.results) ? action.results : []
          actionMsg = `🔎 **Rezultate căutare pentru "${action.query}":**\n` + (
            results.length
              ? results.map((r: { id: number; title: string; entry_date?: string }, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
              : 'Nicio intrare găsită.'
          )
        }
        else if (action.type === 'timeline_results') {
          const results = Array.isArray(action.results) ? action.results : []
          actionMsg = `📅 **Rezultate cronologie** (${action.from || 'început'} → ${action.to || 'acum'}):\n` + (
            results.length
              ? results.map((r: { id: number; title: string; entry_date?: string }, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
              : 'Nicio intrare în acest interval.'
          )
        }
        else if (action.type === 'error') actionMsg = `⚠️ ${action.detail}`
        else if (action.type === 'pending_confirm') {
          setPendingConfirm({ confirm_id: action.confirm_id, message: action.message || 'Acțiunea necesită confirmarea ta.' })
        }
        else {
          const detail = action.detail || action.message || action.title || action.name
            || action.section_title || action.label || ''
          const label = ACTION_LABELS[action.type] || action.type.replace(/_/g, ' ')
          const ref = action.id ?? action.entry_id ?? action.theory_id ?? action.keep_id
          actionMsg = `⚙️ **${label}**${detail ? ` — ${detail}` : ref !== undefined ? ` (#${ref})` : ''}`
        }
        if (actionMsg) setMessages(prev => [...prev, { role: 'assistant', content: actionMsg, action }])
      }
      } finally {
        clearInterval(watchdog)
      }
    } catch (err: unknown) {
      const isAbort = err instanceof Error && err.name === 'AbortError'
      if (isAbort) {
        if (stalledRef.current) {
          // The watchdog fired: the link went silent mid-answer.
          const partial = fullContentRef.current.trim()
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: partial
              ? `${partial}\n\n---\n⚠️ **Conexiunea a tăcut înainte de final.** Textul de mai sus e parțial — poți cere din nou răspunsul complet.`
              : '⚠️ **Răspunsul nu a ajuns.** Conexiunea s-a întrerupt — verifică rețeaua și încearcă din nou.',
          }])
          setRetryText(userMsg.content)
          stalledRef.current = false
        } else {
          setMessages(prev => [...prev, { role: 'assistant', content: '⏹️ Generarea s-a oprit.' }])
        }
      } else {
        const partial = fullContentRef.current.trim()
        if (partial) {
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: `${partial}\n\n---\n⚠️ **Conexiunea s-a întrerupt înainte de final.** Textul de mai sus e parțial — poți cere din nou răspunsul complet.`,
          }])
        } else {
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: '⚠️ **Răspunsul nu a ajuns întreg.** Conexiunea s-a întrerupt înainte de primul cuvânt — verifică rețeaua și încearcă din nou.',
          }])
        }
        setRetryText(userMsg.content)
      }
    } finally {
      abortRef.current = null
      if (streamTimerRef.current != null) {
        clearTimeout(streamTimerRef.current)
        streamTimerRef.current = null
      }
      fullContentRef.current = ''
      setLoading(false)
      setStreamingContent('')
      setResearching(false)
      setProgressMessage(null)
      refreshActiveTask()
    }
  }

  const resolveConfirm = async (approve: boolean) => {
    if (!pendingConfirm) return
    const { confirm_id, message } = pendingConfirm
    setPendingConfirm(null)
    try {
      const res = await api.chat.confirm(confirm_id, approve)
      if (approve && res.result?.type === 'entry_archived') {
        setMessages(prev => [...prev, { role: 'assistant', content: `✅ **Confirmat:** intrarea #${res.result!.entry_id} arhivată.` }])
      } else if (approve) {
        setMessages(prev => [...prev, { role: 'assistant', content: '✅ **Confirmat:** acțiunea a fost aprobată și executată.' }])
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: '⛔ **Acțiune respinsă** — nu s-a schimbat nimic.' }])
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'eroare necunoscută'
      toast.error('Confirmarea a eșuat: ' + msg)
      setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ **Eroare:** ${friendlyError(err)}` }])
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  // Sync input state with textarea ref for programmatic changes (quick actions, retry)
  useEffect(() => {
    if (inputRef.current && inputRef.current.value !== input) {
      inputRef.current.value = input
    }
  }, [input])

  const suggestedPrompts = [
    'Rezumă-mi intrările',
    'Salvează informații despre calculul cuantic',
    'Creează o legătură între intrările mele',
    'Cercetează neuroștiința memoriei',
    'Cercetează istoria fizicii'
  ]

  return (
    <>
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
        <div className="flex items-center gap-2">
          <span className="text-lg">{providerIcon(provider || health?.active)}</span>
          <div>
            <h3 className="font-semibold text-sm">Chat AI</h3>
            <div className="flex items-center gap-2">
              <span className={`inline-block w-2 h-2 rounded-full ${health?.active ? 'bg-verdigris' : 'bg-madder'}`} />
              <span className="text-xs text-gray-500">
                {provider ? providerLabel(provider) : health?.active ? providerLabel(health.active) : 'Se verifică...'}
                {model && <span className="ml-1 opacity-60">· {model.split(':')[0]}</span>}
              </span>
              {loading && (
                <span className="flex items-center gap-1.5 text-xs font-medium text-chrono-700 dark:text-chrono-400">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {progressMessage ? (progressMessage.length > 25 ? progressMessage.slice(0, 23) + '…' : progressMessage) : (researching ? 'Cercetare în curs…' : 'Se generează…')}
                </span>
              )}
              <button
                onClick={() => api.chat.history.clear().then(() => {
                  setMessages([{ role: 'assistant', content: 'Istoric șters. Cu ce te pot ajuta?' }])
                  toast.success('Istoricul chatului a fost șters')
                })}
                className="icon-btn p-1 touch-40-neg ml-1 text-madder"
                title="Șterge istoricul"
                aria-label="Șterge istoricul chatului"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-2 rounded-lg text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 min-w-[36px] min-h-[36px] flex items-center justify-center hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          aria-label="Închide panoul de chat"
          title="Închide chatul (Esc)"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Active Task Banner */}
      {activeTask && activeTask.status === 'active' && (
        <div className="bg-amber-500/10 dark:bg-amber-500/20 border-b border-amber-500/30 px-3.5 py-2 flex items-center justify-between gap-2.5 text-xs shrink-0">
          <div className="flex items-start gap-2 min-w-0">
            <Zap className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="font-semibold text-amber-800 dark:text-amber-300">⚡ Task activ:</span>
                <span className="font-medium text-gray-900 dark:text-gray-100 truncate max-w-[240px]" title={activeTask.goal}>
                  {activeTask.goal}
                </span>
              </div>
              {activeTask.details && (
                <p className="text-[11px] text-gray-600 dark:text-gray-400 line-clamp-1 mt-0.5">
                  {activeTask.details}
                </p>
              )}
            </div>
          </div>
          <button
            onClick={handleCancelTask}
            disabled={cancellingTask}
            className="px-2 py-1 min-h-[40px] rounded bg-amber-500/15 hover:bg-amber-500/25 text-amber-800 dark:text-amber-200 font-medium transition-colors border border-amber-500/30 shrink-0"
            title="Renunță la taskul activ"
          >
            {cancellingTask ? 'Se anulează…' : 'Anulează task'}
          </button>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 bg-gray-50 dark:bg-gray-950">
        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            role={msg.role}
            content={msg.content}
            provider={msg.provider}
            model={msg.model}
            action={msg.action}
            onOpenStudyPdf={handleOpenStudyPdf}
          />
        ))}

        {/* Streaming message */}
        {streamingContent && (
          <div className="flex justify-start">
            <div className="max-w-[88%] rounded-2xl px-4 py-2.5 text-sm bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-bl-md shadow-sm">
              {provider && (
                <div className="flex items-center gap-1 mb-1.5">
                  <span className="text-xs">{providerIcon(provider)}</span>
                  <span className="text-[10px] text-gray-400 font-medium uppercase">{providerLabel(provider)}</span>
                </div>
              )}
              <div className="prose prose-sm max-w-none dark:prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{stripActionTags(streamingContent)}</ReactMarkdown>
              </div>
            </div>
          </div>
        )}

        {/* Loading / Progress indicator */}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-2xl px-4 py-2.5 text-sm bg-white dark:bg-gray-800 border border-chrono-200 dark:border-chrono-800/40 rounded-bl-md shadow-sm text-gray-700 dark:text-gray-300 flex items-center gap-2.5">
              <Loader2 className="w-4 h-4 animate-spin text-chrono-600 dark:text-chrono-400 shrink-0" />
              <div className="flex flex-col">
                <span className="font-medium text-xs text-chrono-600 dark:text-chrono-400">
                  {progressMessage || (researching ? '🔬 Rulez cercetare calitativă (plan → analize → sinteză)...' : 'AI-ul lucrează')}
                </span>
                {!progressMessage && !streamingContent && (
                  <span className="flex gap-1 mt-1">
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Stop control, always visible while the model is working */}
        {loading && (
          <div className="flex justify-start">
            <button
              onClick={() => abortRef.current?.abort()}
              className="btn-danger text-xs px-3 py-1.5"
              title="Oprește generarea"
              aria-label="Oprește generarea"
            >
              <Square className="w-3.5 h-3.5 fill-current" /> Oprește generarea
            </button>
          </div>
        )}

        {/* Destructive-action confirmation */}
        {pendingConfirm && (
          <div className="card border-brass/30 bg-brass-soft p-3 space-y-2">
            <p className="text-sm font-medium text-brass flex items-center gap-1.5">
              🔒 {pendingConfirm.message}
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => resolveConfirm(true)}
                className="btn-primary text-xs px-3 py-1.5"
              >Aprobă</button>
              <button
                onClick={() => resolveConfirm(false)}
                className="btn-ghost text-xs px-3 py-1.5"
              >Respinge</button>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Suggested prompts */}
      {messages.length <= 2 && !loading && (
        <div className="px-3 py-2 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
          <div className="flex flex-wrap gap-1.5">
            {suggestedPrompts.map(p => (
              <button key={p} onClick={() => { setInput(p); inputRef.current?.focus() }}
                className="text-xs px-2.5 py-1.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-chrono-100 hover:text-chrono-700 dark:hover:bg-chrono-900/30 dark:hover:text-chrono-400 transition-colors"
              >{p}</button>
            ))}
          </div>
        </div>
      )}

      {/* Input area */}
      <div className="p-3 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
        {retryText && !loading && (
          <div className="mb-2 flex items-center gap-2 text-xs">
            <button
              onClick={() => { setInput(retryText); setRetryText(null) }}
              className="btn-secondary text-xs"
            >Pune întrebarea din nou</button>
            <button onClick={() => setRetryText(null)} className="btn-ghost text-xs">Renunță</button>
          </div>
        )}
        <div className="flex gap-2 items-end">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Întreabă orice... (Shift+Enter pentru linie nouă)"
            className="input flex-1 resize-none text-sm min-h-[44px] max-h-[120px]"
            rows={1}
          />
          <button onClick={sendMessage} disabled={loading || !input.trim()}
            className="btn-primary h-[44px] px-4 disabled:opacity-50"
          >{loading ? 'Se lucrează…' : 'Trimite'}</button>
        </div>
        <p className="text-[10px] text-gray-400 mt-1 text-center">
          AI-ul poate crea și edita intrări, scrie în capitole, căuta în cunoștințele tale,
          gestiona sarcini, cerceta teme și șterge sau uni cu confirmare.
        </p>
      </div>

      {/* Study PDF Reader Modal */}
      {pdfModalStudy && (
        <StudyPdfModal
          study={pdfModalStudy}
          isOpen={Boolean(pdfModalStudy)}
          onClose={() => setPdfModalStudy(null)}
        />
      )}
    </>
  )
}
