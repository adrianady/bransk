import { useState, useRef, useEffect, memo } from 'react'
import { X, Trash2, Square, Loader2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../lib/api'
import { useToast } from './Toast'
import type { ProviderHealth, ChatAction } from '../lib/types'

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
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  provider?: string
  model?: string
}

const friendlyError = (err: any): string => {
  const raw = err?.detail ?? err?.message ?? err
  if (typeof raw === 'string') return raw
  // FastAPI validation errors arrive as a list of { type, loc, msg, input }.
  if (Array.isArray(raw)) {
    const msgs = raw
      .map((d: any) => (typeof d === 'string' ? d : d?.msg))
      .filter(Boolean)
    if (msgs.length > 0) return msgs.join('; ')
  }
  try { return JSON.stringify(raw) } catch { return String(raw) }
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
}: {
  role: 'user' | 'assistant'
  content: string
  provider?: string
  model?: string
}) {
  const shown = role === 'assistant' ? stripActionTags(content) : content
  // A reply that was nothing but protocol tags has no prose to show.
  if (!shown) return null
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
        <div className={`prose prose-sm max-w-none ${role === 'user' ? 'prose-invert' : 'dark:prose-invert'} prose-headings:text-sm prose-headings:font-semibold prose-a:text-chrono-600`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{shown}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
})

export function ChatPanel({ onClose }: Props) {
  const toast = useToast()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [provider, setProvider] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [health, setHealth] = useState<ProviderHealth | null>(null)
  const [researching, setResearching] = useState(false)
  const [pendingConfirm, setPendingConfirm] = useState<{ confirm_id: string; message: string } | null>(null)
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
    fullContentRef.current = ''
    setProvider(null)

    try {
      const history = messages
        .slice(-MAX_HISTORY_MESSAGES)
        .map(m => ({ role: m.role, content: m.content.slice(0, MAX_HISTORY_LEN) }))
      const response = await api.chat.stream(input.trim().slice(0, 100000), history, controller.signal)
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
      // reject the read: it can stay pending forever. 40 s without a single byte
      // is treated as an interruption, so the user gets their partial answer and
      // a resend button instead of an endless spinner.
      lastByteRef.current = Date.now()
      stalledRef.current = false
      const watchdog = window.setInterval(() => {
        if (Date.now() - lastByteRef.current > 40000) {
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
              } else if (parsed.type === 'progress' && parsed.phase === 'research') {
                setResearching(true)
              } else if (parsed.type === 'research' && parsed.result) {
                setResearching(false)
                const result = parsed.result
                const studyLines = (result.studies || [])
                  .map((s: any, i: number) => `${i + 1}. **${s.title}**${s.year ? ` (${s.year})` : ''} — ${s.study_type || 'studiu'} — calitate ${s.quality}/5`)
                  .join('\n')
                const researchMsg =
                  `📚 **Cercetare finalizată: "${result.topic}"**\n\n` +
                  `${result.summary || ''}\n\n` +
                  `**Studii salvate (${(result.studies || []).length}):**\n${studyLines}\n\n` +
                  (result.confidence ? `**Încredere:** ${result.confidence}\n\n` : '') +
                  `✅ ${(result.entries_created || []).length} intrări create · ${result.relationships_created || 0} legături în graf` +
                  ((result.theories || []).length ? ` · 🧬 ${result.theories.length} teorii în agregator` : '') +
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
            const results = action.results || []
            actionMsg = `🗂️ **Intrări${action.tag ? ` etichetate "${action.tag}"` : ''} (${results.length}):**\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
                : 'Nimic găsit.'
            )
          }
          else if (action.type === 'relationships_listed') {
            const results = action.results || []
            actionMsg = `🔗 **Legături pentru intrarea #${action.entry_id} (${results.length}):**\n` + (
              results.length
                ? results.map((r: any) => `- #${r.from_id} --${r.label || 'related_to'}--> #${r.to_id}`).join('\n')
                : 'Nicio legătură încă.'
            )
          }
          else if (action.type === 'theories_listed') {
            const results = action.results || []
            actionMsg = `🧬 **Teorii${action.query ? ` care corespund "${action.query}"` : ''} (${results.length}):**\n` + (
              results.length
                ? results.map((r: any) => `- #${r.id} **${r.name}** [${r.status}] — pro ${r.pro_count} / contra ${r.contra_count}`).join('\n')
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
            const results = action.results || []
            actionMsg = `🌐 **Rezultate web pentru "${action.query}" (${results.length}):**\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. [${r.title}](${r.url})${r.snippet ? ` — ${r.snippet.slice(0, 150)}` : ''}`).join('\n')
                : 'Niciun rezultat (offline sau blocat).'
            )
          }
          else if (action.type === 'documents_results') {
            const results = action.results || []
            actionMsg = `📄 **Potriviri PDF de studiu pentru "${action.query}" (${results.length}):**\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. **${r.entry_title}** — ${(r.content || '').slice(0, 220)}`).join('\n')
                : 'Nicio potrivire PDF încă. Descarcă PDF-urile de studiu, apoi rulează Verificare → Integritatea datelor pentru a le indexa.'
            )
          }
          else if (action.type === 'search_results') {
            const results = action.results || []
            actionMsg = `🔎 **Rezultate căutare pentru "${action.query}":**\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
                : 'Nicio intrare găsită.'
            )
          }
          else if (action.type === 'timeline_results') {
            const results = action.results || []
            actionMsg = `📅 **Rezultate cronologie** (${action.from || 'început'} → ${action.to || 'acum'}):\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
                : 'Nicio intrare în acest interval.'
            )
          }
          else if (action.type === 'error') actionMsg = `⚠️ ${action.detail}`
          else if (action.type === 'pending_confirm') {
            setPendingConfirm({ confirm_id: action.confirm_id, message: action.message || 'Acțiunea necesită confirmarea ta.' })
          }
          else {
            // Generic renderer: prefer the human text the server sent, else a
            // readable label from the action type. Keeps new actions visible.
            const detail = action.detail || action.message || action.title || action.name
              || action.section_title || action.label || ''
            const label = ACTION_LABELS[action.type] || action.type.replace(/_/g, ' ')
            const ref = action.id ?? action.entry_id ?? action.theory_id ?? action.keep_id
            actionMsg = `⚙️ **${label}**${detail ? ` — ${detail}` : ref !== undefined ? ` (#${ref})` : ''}`
          }
          if (actionMsg) setMessages(prev => [...prev, { role: 'assistant', content: actionMsg }])
        }
      }
      } finally {
        clearInterval(watchdog)
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') {
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
        // A dropped stream (phone network, Tailscale, tab throttling) arrives as
        // "Error in input stream". Keep whatever text made it to the screen and
        // offer a resend instead of showing a raw browser error.
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
    } catch (err: any) {
      toast.error('Confirmarea a eșuat: ' + (err?.message || 'eroare necunoscută'))
      setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ **Eroare:** ${friendlyError(err)}` }])
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

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
                  {researching ? 'Cercetare în curs…' : 'Se generează…'}
                </span>
              )}
              <button
                onClick={() => api.chat.history.clear().then(() => {
                  setMessages([{ role: 'assistant', content: 'Istoric șters. Cu ce te pot ajuta?' }])
                  toast.success('Istoricul chatului a fost șters')
                })}
                className="icon-btn p-1 ml-1 text-madder"
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

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 bg-gray-50 dark:bg-gray-950">
        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            role={msg.role}
            content={msg.content}
            provider={msg.provider}
            model={msg.model}
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

        {/* Loading dots */}
        {researching && (
          <div className="flex justify-start">
            <div className="rounded-2xl px-4 py-2.5 text-sm bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-bl-md shadow-sm text-gray-600 dark:text-gray-400 flex items-center gap-2.5">
              <Loader2 className="w-4 h-4 animate-spin text-chrono-600 dark:text-chrono-400 shrink-0" />
              <span>🔬 Rulez cercetare calitativă (plan → analize aprofundate → sinteză)...</span>
            </div>
          </div>
        )}

        {loading && !streamingContent && !researching && (
          <div className="flex justify-start">
            <div className="rounded-2xl px-4 py-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-sm flex items-center gap-2.5">
              <Loader2 className="w-4 h-4 animate-spin text-chrono-600 dark:text-chrono-400 shrink-0" />
              <span className="text-sm text-gray-600 dark:text-gray-400">AI-ul lucrează</span>
              <span className="flex gap-1">
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </span>
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
    </>
  )
}
