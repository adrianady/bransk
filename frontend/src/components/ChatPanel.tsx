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
  if (p === 'ollama') return 'Ollama Local'
  return 'AI'
}

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
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
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
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
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
        setMessages([{ role: 'assistant', content: 'Hello! I can help you explore your knowledge base, save information, research topics, and manage entries. What would you like to do?' }])
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
      if (!response.body) throw new Error('No response body')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let currentProvider = ''
      let currentModel = ''
      let actions: ChatAction[] = []

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
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
                  .map((s: any, i: number) => `${i + 1}. **${s.title}**${s.year ? ` (${s.year})` : ''} — ${s.study_type || 'study'} — quality ${s.quality}/5`)
                  .join('\n')
                const researchMsg =
                  `📚 **Research complete: "${result.topic}"**\n\n` +
                  `${result.summary || ''}\n\n` +
                  `**Studies saved (${(result.studies || []).length}):**\n${studyLines}\n\n` +
                  (result.confidence ? `**Confidence:** ${result.confidence}\n\n` : '') +
                  `✅ ${(result.entries_created || []).length} entries created · ${result.relationships_created || 0} graph links` +
                  ((result.theories || []).length ? ` · 🧬 ${result.theories.length} theories in the aggregator` : '') +
                  `. Open the **Studies** or **Theories** view to explore.`
                setMessages(prev => [...prev, { role: 'assistant', content: researchMsg }])
              } else if (parsed.type === 'tool') {
                setMessages(prev => [...prev, { role: 'assistant', content: `🛠️ ${parsed.message || 'Tools used'}` }])
              } else if (parsed.type === 'error') {
                setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ **Error:** ${friendlyError(parsed.content) || 'Something went wrong.'}` }])
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
          if (action.type === 'entry_created') actionMsg = `📝 **Entry saved:** "${action.title}" (ID: ${action.id})`
          else if (action.type === 'relationship_created') actionMsg = `🔗 **Relationship created:** Entry ${action.from} → ${action.to} (${action.label})`
          else if (action.type === 'todo_added') actionMsg = `✅ **Task added** to entry ${action.entry_id}`
          else if (action.type === 'sr_scheduled') actionMsg = `🔄 **Added to review schedule:** entry ${action.entry_id}`
          else if (action.type === 'web_search_request') actionMsg = `🔍 **Research query:** "${action.query}"`
          else if (action.type === 'research_request') actionMsg = `🔬 **Research requested:** "${action.topic}"`
          else if (action.type === 'entry_fetched' && action.entry) {
            const e = action.entry
            actionMsg = `📄 **Entry #${e.id}: ${e.title}** (${e.entry_date || 'no date'})\n\n${(e.content || 'No content').slice(0, 1500)}`
          }
          else if (action.type === 'entries_listed') {
            const results = action.results || []
            actionMsg = `🗂️ **Entries${action.tag ? ` tagged "${action.tag}"` : ''} (${results.length}):**\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
                : 'None found.'
            )
          }
          else if (action.type === 'relationships_listed') {
            const results = action.results || []
            actionMsg = `🔗 **Links for entry #${action.entry_id} (${results.length}):**\n` + (
              results.length
                ? results.map((r: any) => `- #${r.from_id} --${r.label || 'related_to'}--> #${r.to_id}`).join('\n')
                : 'No relationships yet.'
            )
          }
          else if (action.type === 'theories_listed') {
            const results = action.results || []
            actionMsg = `🧬 **Theories${action.query ? ` matching "${action.query}"` : ''} (${results.length}):**\n` + (
              results.length
                ? results.map((r: any) => `- #${r.id} **${r.name}** [${r.status}] — pro ${r.pro_count} / contra ${r.contra_count}`).join('\n')
                : 'None found.'
            )
          }
          else if (action.type === 'entry_appended') actionMsg = `✏️ **Entry #${action.entry_id} updated** (content appended)`
          else if (action.type === 'entry_archived') actionMsg = `🗄️ **Entry #${action.entry_id} archived**`
          else if (action.type === 'thought_created') actionMsg = `💭 **Thought created:** "${action.name}"`
          else if (action.type === 'tag_created') actionMsg = `🏷️ **Tag created:** "${action.name}"`
          else if (action.type === 'reminder_added') actionMsg = `⏰ **Reminder set** on entry #${action.entry_id}`
          else if (action.type === 'theory_created') actionMsg = `🧬 **Theory created:** "${action.name}" (ID ${action.id})`
          else if (action.type === 'study_linked') actionMsg = `🔗 **Evidence linked:** entry #${action.entry_id} → theory #${action.theory_id} (${action.stance})`
          else if (action.type === 'web_results') {
            const results = action.results || []
            actionMsg = `🌐 **Web results for "${action.query}" (${results.length}):**\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. [${r.title}](${r.url})${r.snippet ? ` — ${r.snippet.slice(0, 150)}` : ''}`).join('\n')
                : 'No results (offline or blocked).'
            )
          }
          else if (action.type === 'documents_results') {
            const results = action.results || []
            actionMsg = `📄 **Study PDF matches for "${action.query}" (${results.length}):**\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. **${r.entry_title}** — ${(r.content || '').slice(0, 220)}`).join('\n')
                : 'No PDF matches yet. Download study PDFs, then run Review → Data integrity to index them.'
            )
          }
          else if (action.type === 'search_results') {
            const results = action.results || []
            actionMsg = `🔎 **Search results for "${action.query}":**\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
                : 'No entries found.'
            )
          }
          else if (action.type === 'timeline_results') {
            const results = action.results || []
            actionMsg = `📅 **Timeline results** (${action.from || 'start'} → ${action.to || 'now'}):\n` + (
              results.length
                ? results.map((r: any, i: number) => `${i + 1}. **${r.title}** (ID ${r.id}${r.entry_date ? `, ${r.entry_date}` : ''})`).join('\n')
                : 'No entries in this range.'
            )
          }
          else if (action.type === 'error') actionMsg = `⚠️ ${action.detail}`
          else if (action.type === 'pending_confirm') {
            setPendingConfirm({ confirm_id: action.confirm_id, message: action.message || 'Action needs your confirmation.' })
          }
          if (actionMsg) setMessages(prev => [...prev, { role: 'assistant', content: actionMsg }])
        }
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        setMessages(prev => [...prev, { role: 'assistant', content: '⏹️ Generation stopped.' }])
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ **Error:** ${friendlyError(err) || 'Something went wrong.'}` }])
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
        setMessages(prev => [...prev, { role: 'assistant', content: `✅ **Confirmed:** entry #${res.result!.entry_id} archived.` }])
      } else if (approve) {
        setMessages(prev => [...prev, { role: 'assistant', content: '✅ **Confirmed:** action approved and executed.' }])
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: '⛔ **Action rejected** — nothing was changed.' }])
      }
    } catch (err: any) {
      toast.error('Confirmation failed: ' + (err?.message || 'unknown error'))
      setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ **Error:** ${friendlyError(err)}` }])
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  const suggestedPrompts = [
    'Summarize my entries',
    'Save information about quantum computing',
    'Create a relationship between my entries',
    'Research the neuroscience of memory',
    'Research the history of physics'
  ]

  return (
    <>
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
        <div className="flex items-center gap-2">
          <span className="text-lg">{providerIcon(provider || health?.active)}</span>
          <div>
            <h3 className="font-semibold text-sm">AI Chat</h3>
            <div className="flex items-center gap-2">
              <span className={`inline-block w-2 h-2 rounded-full ${health?.active === 'deepseek' ? 'bg-green-500' : health?.active === 'ollama' ? 'bg-yellow-500' : 'bg-red-500'}`} />
              <span className="text-xs text-gray-500">
                {provider ? providerLabel(provider) : health?.active ? providerLabel(health.active) : 'Checking...'}
                {model && <span className="ml-1 opacity-60">· {model.split(':')[0]}</span>}
              </span>
              {loading && (
                <span className="flex items-center gap-1.5 text-xs font-medium text-chrono-700 dark:text-chrono-400">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {researching ? 'Researching…' : 'Generating…'}
                </span>
              )}
              <button
                onClick={() => api.chat.history.clear().then(() => {
                  setMessages([{ role: 'assistant', content: 'History cleared. How can I help?' }])
                  toast.success('Chat history cleared')
                })}
                className="icon-btn p-1 ml-1 text-red-400"
                title="Clear history"
                aria-label="Clear chat history"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
        <button onClick={onClose} className="icon-btn" aria-label="Close chat panel" title="Close chat (Esc)">
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
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingContent}</ReactMarkdown>
              </div>
            </div>
          </div>
        )}

        {/* Loading dots */}
        {researching && (
          <div className="flex justify-start">
            <div className="rounded-2xl px-4 py-2.5 text-sm bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-bl-md shadow-sm text-gray-600 dark:text-gray-400 flex items-center gap-2.5">
              <Loader2 className="w-4 h-4 animate-spin text-chrono-600 dark:text-chrono-400 shrink-0" />
              <span>🔬 Running qualitative research (plan → deep dives → synthesis)...</span>
            </div>
          </div>
        )}

        {loading && !streamingContent && !researching && (
          <div className="flex justify-start">
            <div className="rounded-2xl px-4 py-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-sm flex items-center gap-2.5">
              <Loader2 className="w-4 h-4 animate-spin text-chrono-600 dark:text-chrono-400 shrink-0" />
              <span className="text-sm text-gray-600 dark:text-gray-400">AI is working</span>
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
              title="Stop generating"
              aria-label="Stop generating"
            >
              <Square className="w-3.5 h-3.5 fill-current" /> Stop generating
            </button>
          </div>
        )}

        {/* Destructive-action confirmation */}
        {pendingConfirm && (
          <div className="card border-amber-300 dark:border-amber-700 p-3 space-y-2">
            <p className="text-sm font-medium text-amber-700 dark:text-amber-400 flex items-center gap-1.5">
              🔒 {pendingConfirm.message}
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => resolveConfirm(true)}
                className="btn-primary text-xs px-3 py-1.5"
              >Approve</button>
              <button
                onClick={() => resolveConfirm(false)}
                className="btn-ghost text-xs px-3 py-1.5"
              >Reject</button>
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
        <div className="flex gap-2 items-end">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything... (Shift+Enter for new line)"
            className="input flex-1 resize-none text-sm min-h-[44px] max-h-[120px]"
            rows={1}
          />
          <button onClick={sendMessage} disabled={loading || !input.trim()}
            className="btn-primary h-[44px] px-4 disabled:opacity-50"
          >{loading ? 'Working…' : 'Send'}</button>
        </div>
        <p className="text-[10px] text-gray-400 mt-1 text-center">
          AI can create entries, search your knowledge, manage tasks, and research topics
        </p>
      </div>
    </>
  )
}
