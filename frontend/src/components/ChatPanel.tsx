import { useState, useRef, useEffect } from 'react'

interface Props {
  onClose: () => void
}

interface Message {
  role: 'user' | 'assistant'
  content: string
}

export function ChatPanel({ onClose }: Props) {
  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', content: 'Hello! I can help you explore your knowledge base. Ask me anything about your entries, time periods, or connections between topics.' }
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const sendMessage = async () => {
    if (!input.trim() || loading) return

    const userMsg: Message = { role: 'user', content: input.trim() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)
    setStreamingContent('')

    try {
      const { api } = await import('../lib/api')
      const history = messages.map(m => ({ role: m.role, content: m.content }))

      const response = await api.chat.stream(input.trim(), history)
      if (!response.body) throw new Error('No response body')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullContent = ''

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
              }
            } catch { /* skip malformed */ }
          }
        }
      }

      setMessages(prev => [...prev, { role: 'assistant', content: fullContent }])
    } catch (err: any) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `⚠️ Error: ${err.message || 'Could not reach AI. Make sure DEEPSEEK_API_KEY is set.'}`
      }])
    } finally {
      setLoading(false)
      setStreamingContent('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <>
      <div className="flex items-center justify-between p-3 border-b border-gray-200 dark:border-gray-700">
        <h3 className="font-semibold flex items-center gap-2">
          <span>🤖</span> AI Chat
        </h3>
        <button onClick={onClose} className="btn-ghost p-1 text-lg">✕</button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-xl px-3 py-2 text-sm ${
              msg.role === 'user'
                ? 'bg-chrono-600 text-white'
                : 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
            }`}>
              <p className="whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}
        {streamingContent && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-xl px-3 py-2 text-sm bg-gray-100 dark:bg-gray-800">
              <p className="whitespace-pre-wrap">{streamingContent}</p>
            </div>
          </div>
        )}
        {loading && !streamingContent && (
          <div className="flex justify-start">
            <div className="rounded-xl px-3 py-2 text-sm bg-gray-100 dark:bg-gray-800 text-gray-500">
              Thinking...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="p-3 border-t border-gray-200 dark:border-gray-700">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your knowledge base..."
            className="input flex-1 resize-none"
            rows={2}
          />
          <button onClick={sendMessage} disabled={loading || !input.trim()}
            className="btn-primary self-end disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </>
  )
}
