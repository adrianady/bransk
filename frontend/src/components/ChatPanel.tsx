import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../lib/api'
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

export function ChatPanel({ onClose }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [provider, setProvider] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [health, setHealth] = useState<ProviderHealth | null>(null)
  const [researching, setResearching] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

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

  const sendMessage = async () => {
    if (!input.trim() || loading) return
    const userMsg: Message = { role: 'user', content: input.trim() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)
    setStreamingContent('')
    setProvider(null)

    try {
      const history = messages.map(m => ({ role: m.role, content: m.content }))
      const response = await api.chat.stream(input.trim(), history)
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }))
        throw new Error(err.detail || err.message || `HTTP ${response.status}`)
      }
      if (!response.body) throw new Error('No response body')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullContent = ''
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
                fullContent += parsed.content
                setStreamingContent(fullContent)
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
              } else if (parsed.type === 'actions') {
                actions = parsed.content || []
              }
            } catch {}
          }
        }
      }

      if (fullContent) {
        setMessages(prev => [...prev, { role: 'assistant', content: fullContent, provider: currentProvider, model: currentModel }])
        // Show action results as assistant messages
        for (const action of actions) {
          let actionMsg = ''
          if (action.type === 'entry_created') actionMsg = `📝 **Entry saved:** "${action.title}" (ID: ${action.id})`
          else if (action.type === 'relationship_created') actionMsg = `🔗 **Relationship created:** Entry ${action.from} → ${action.to} (${action.label})`
          else if (action.type === 'todo_added') actionMsg = `✅ **Task added** to entry ${action.entry_id}`
          else if (action.type === 'sr_scheduled') actionMsg = `🔄 **Added to review schedule:** entry ${action.entry_id}`
          else if (action.type === 'web_search') actionMsg = `🔍 **Research query:** "${action.query}"`
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
          if (actionMsg) setMessages(prev => [...prev, { role: 'assistant', content: actionMsg }])
        }
      }
    } catch (err: any) {
      setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ **Error:** ${err.message || 'Something went wrong.'}` }])
    } finally {
      setLoading(false)
      setStreamingContent('')
      setResearching(false)
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
              <button onClick={() => api.chat.history.clear().then(() => setMessages([{ role: 'assistant', content: 'History cleared. How can I help?' }]))} className="btn-ghost text-xs text-red-400 p-0.5 ml-1" title="Clear history">🗑️</button>
            </div>
          </div>
        </div>
        <button onClick={onClose} className="btn-ghost p-1 text-lg">✕</button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 bg-gray-50 dark:bg-gray-950">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[88%] rounded-2xl px-4 py-2.5 text-sm ${
              msg.role === 'user'
                ? 'bg-chrono-600 text-white rounded-br-md'
                : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-bl-md shadow-sm'
            }`}>
              {msg.role === 'assistant' && msg.provider && (
                <div className="flex items-center gap-1 mb-1.5">
                  <span className="text-xs">{providerIcon(msg.provider)}</span>
                  <span className="text-[10px] text-gray-400 font-medium uppercase">{providerLabel(msg.provider)}</span>
                  {msg.model && <span className="text-[10px] text-gray-400">· {msg.model.split(':')[0]}</span>}
                </div>
              )}
              <div className={`prose prose-sm max-w-none ${msg.role === 'user' ? 'prose-invert' : 'dark:prose-invert'} prose-headings:text-sm prose-headings:font-semibold prose-a:text-chrono-600`}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
              </div>
            </div>
          </div>
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
            <div className="rounded-2xl px-4 py-2.5 text-sm bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-bl-md shadow-sm text-gray-600 dark:text-gray-400">
              🔬 Running qualitative research (plan → deep dives → synthesis)...
            </div>
          </div>
        )}

        {loading && !streamingContent && !researching && (
          <div className="flex justify-start">
            <div className="rounded-2xl px-4 py-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
              <div className="flex gap-1">
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
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
          >Send</button>
        </div>
        <p className="text-[10px] text-gray-400 mt-1 text-center">
          AI can create entries, search your knowledge, manage tasks, and research topics
        </p>
      </div>
    </>
  )
}
