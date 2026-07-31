import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../lib/api'
import type { SpacedRepetitionItem } from '../lib/types'

export function ReviewPanel() {
  const [queue, setQueue] = useState<SpacedRepetitionItem[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [showAnswer, setShowAnswer] = useState(false)
  const [loading, setLoading] = useState(true)

  const loadQueue = async () => {
    setLoading(true)
    try {
      const items = await api.spacedRepetition.queue()
      setQueue(items)
      setCurrentIndex(0)
      setShowAnswer(false)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadQueue() }, [])

  const submitRating = async (quality: number) => {
    const item = queue[currentIndex]
    await api.spacedRepetition.review(item.id, quality)
    if (currentIndex < queue.length - 1) {
      setCurrentIndex(prev => prev + 1)
      setShowAnswer(false)
    } else {
      await loadQueue()
    }
  }

  // Load entry titles for display
  const [entryTitles, setEntryTitles] = useState<Record<number, string>>({})
  const [entryContents, setEntryContents] = useState<Record<number, string>>({})

  useEffect(() => {
    if (queue.length > 0) {
      api.entries.list({ limit: 500 }).then(allEntries => {
        const titleMap: Record<number, string> = {}
        const contentMap: Record<number, string> = {}
        allEntries.forEach(e => {
          titleMap[e.id] = e.title
          if (e.content) contentMap[e.id] = e.content
        })
        setEntryTitles(titleMap)
        setEntryContents(contentMap)
      })
    }
  }, [queue])

  if (loading) return <div className="text-center py-8 text-gray-500">Loading review queue...</div>

  if (queue.length === 0) {
    return (
      <div className="card text-center py-12">
        <h2 className="text-xl font-bold mb-2">Spaced Repetition Review</h2>
        <p className="text-gray-500 mb-4">No entries due for review!</p>
        <p className="text-sm text-gray-400">Add entries to review from their detail page. Check back later for scheduled reviews.</p>
        <button onClick={loadQueue} className="btn-secondary mt-4">Refresh</button>
      </div>
    )
  }

  const current = queue[currentIndex]

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Spaced Repetition Review</h2>
          <p className="text-sm text-gray-500">{currentIndex + 1} of {queue.length} due · SM-2 Algorithm</p>
        </div>
        <button onClick={loadQueue} className="btn-secondary">↻ Refresh</button>
      </div>

      <div className="card max-w-2xl mx-auto">
        <div className="flex items-center gap-2 mb-4">
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
            current.repetitions === 0 ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'
          }`}>
            {current.repetitions === 0 ? 'New' : `Review #${current.repetitions}`}
          </span>
          <span className="text-xs text-gray-500">EF: {current.ease_factor.toFixed(2)}</span>
        </div>

        <h3 className="text-lg font-semibold mb-4">
          {entryTitles[current.entry_id] || `Entry #${current.entry_id}`}
        </h3>

        {!showAnswer ? (
          <button onClick={() => setShowAnswer(true)}
            className="btn-primary w-full py-4 text-lg">
            Show Answer
          </button>
        ) : (
          <div>
            <div className="mb-6 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
              {entryContents[current.entry_id] ? (
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{entryContents[current.entry_id]}</ReactMarkdown>
                </div>
              ) : (
                <p className="text-sm text-gray-500 italic">This entry has no content — rate your recall of the title above.</p>
              )}
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium text-gray-500 mb-2">Rate your recall:</p>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { q: 0, label: 'Complete blackout', color: 'bg-red-500' },
                  { q: 1, label: 'Failed after seeing', color: 'bg-red-400' },
                  { q: 2, label: 'Failed, but familiar', color: 'bg-orange-400' },
                  { q: 3, label: 'Hard to recall', color: 'bg-yellow-400' },
                  { q: 4, label: 'Recalled after pause', color: 'bg-lime-500' },
                  { q: 5, label: 'Perfect recall', color: 'bg-green-500' },
                ].map(({ q, label, color }) => (
                  <button key={q} onClick={() => submitRating(q)}
                    className={`${color} text-white rounded-lg px-3 py-4 text-sm font-medium hover:opacity-90 transition-opacity`}
                  >
                    {q}
                    <br />
                    <span className="text-xs opacity-80">{label}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
