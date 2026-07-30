import type { Entry, Relationship, Tag, TimelineEntry, TimelineStats, GraphData, SpacedRepetitionItem, TodoItem, Reminder, Thought } from './types';

// Dynamic API Base URL resolution with fallback to relative /api proxy
const BASE = (import.meta as any).env?.VITE_API_BASE_URL || '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000); // 30s network timeout

  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...options?.headers },
      signal: options?.signal || controller.signal,
      ...options,
    });
    clearTimeout(timeoutId);

    if (!res.ok) {
      const err = await res.json().catch(() => ({ message: res.statusText }));
      throw new Error(err.detail || err.error || err.message || `HTTP ${res.status}`);
    }
    if (res.status === 204) return undefined as T;
    return res.json();
  } catch (err: any) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      throw new Error('Request timed out after 30 seconds');
    }
    throw err;
  }
}

export const api = {
  // Entries
  entries: {
    list: (params?: { archived?: boolean; tag?: string }) => {
      const q = new URLSearchParams();
      if (params?.archived) q.set('archived', 'true');
      if (params?.tag) q.set('tag', params.tag);
      return request<Entry[]>(`/entries?${q}`);
    },
    get: (id: number) => request<Entry>(`/entries/${id}`),
    create: (data: Partial<Entry>) =>
      request<Entry>('/entries', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Entry>) =>
      request<Entry>(`/entries/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<void>(`/entries/${id}`, { method: 'DELETE' }),
  },

  // Search
  search: (q: string, params?: { from_date?: string; to_date?: string; tags?: string }) => {
    const p = new URLSearchParams({ q });
    if (params?.from_date) p.set('from_date', params.from_date);
    if (params?.to_date) p.set('to_date', params.to_date);
    if (params?.tags) p.set('tags', params.tags);
    return request<{ entry: Entry; rank: number }[]>(`/search?${p}`);
  },

  // Timeline
  timeline: {
    get: (params?: { from_date?: string; to_date?: string; tags?: string }) => {
      const p = new URLSearchParams();
      if (params?.from_date) p.set('from_date', params.from_date);
      if (params?.to_date) p.set('to_date', params.to_date);
      if (params?.tags) p.set('tags', params.tags);
      return request<{ entries: TimelineEntry[]; total: number }>(`/timeline?${p}`);
    },
    stats: () => request<TimelineStats>('/timeline/stats'),
  },

  // Relationships / Graph
  relationships: {
    list: (entryId?: number) => {
      const q = entryId ? `?entry_id=${entryId}` : '';
      return request<Relationship[]>(`/relationships${q}`);
    },
    create: (data: { from_id: number; to_id: number; label?: string }) =>
      request<Relationship>('/relationships', { method: 'POST', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<void>(`/relationships/${id}`, { method: 'DELETE' }),
    graph: () => request<GraphData>('/relationships/graph'),
  },

  // Tags
  tags: {
    list: () => request<Tag[]>('/tags'),
    create: (data: { name: string; color?: string }) =>
      request<Tag>('/tags', { method: 'POST', body: JSON.stringify(data) }),
    delete: (id: number) => request<void>(`/tags/${id}`, { method: 'DELETE' }),
  },

  // Chat
  chat: {
    send: (message: string, history?: { role: string; content: string }[]) =>
      request<{ reply: string }>('/chat', {
        method: 'POST',
        body: JSON.stringify({ message, history: history || [] }),
      }),
    stream: (message: string, history?: { role: string; content: string }[], signal?: AbortSignal) => {
      return fetch(`${BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, history: history || [] }),
        signal,
      });
    },
  },

  // Spaced Repetition
  spacedRepetition: {
    list: () => request<SpacedRepetitionItem[]>('/spaced-repetition/items'),
    queue: () => request<SpacedRepetitionItem[]>('/spaced-repetition/queue'),
    add: (entryId: number) =>
      request<SpacedRepetitionItem>(`/spaced-repetition/items/${entryId}`, { method: 'POST' }),
    remove: (entryId: number) =>
      request<void>(`/spaced-repetition/items/${entryId}`, { method: 'DELETE' }),
    review: (itemId: number, quality: number) =>
      request<SpacedRepetitionItem>(`/spaced-repetition/items/${itemId}/review`, {
        method: 'POST',
        body: JSON.stringify({ quality }),
      }),
  },

  // Todos
  todos: {
    list: () => request<TodoItem[]>('/todos'),
    create: (entryId: number, data?: { due_date?: string; priority?: string; effort?: string }) =>
      request<TodoItem>(`/entries/${entryId}/todo`, {
        method: 'POST',
        body: JSON.stringify(data || {}),
      }),
    update: (entryId: number, data: Partial<TodoItem>) =>
      request<TodoItem>(`/entries/${entryId}/todo`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    delete: (entryId: number) =>
      request<void>(`/entries/${entryId}/todo`, { method: 'DELETE' }),
  },

  // Reminders
  reminders: {
    list: () => request<Reminder[]>('/reminders'),
    due: () => request<Reminder[]>('/reminders?due=true'),
    create: (entryId: number, data: { title?: string; trigger_at: string; reminder_type?: string }) =>
      request<Reminder>(`/entries/${entryId}/reminders`, { method: 'POST', body: JSON.stringify(data) }),
    delete: (entryId: number, reminderId: number) =>
      request<void>(`/entries/${entryId}/reminders/${reminderId}`, { method: 'DELETE' }),
  },

  // Thoughts
  thoughts: {
    list: () => request<Thought[]>('/thoughts'),
    create: (data: { name: string; description?: string; tag_ids?: string }) =>
      request<Thought>('/thoughts', { method: 'POST', body: JSON.stringify(data) }),
    get: (id: number) => request<Thought>(`/thoughts/${id}`),
    update: (id: number, data: { name: string; description?: string; tag_ids?: string }) =>
      request<Thought>(`/thoughts/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) => request<void>(`/thoughts/${id}`, { method: 'DELETE' }),
    entries: (id: number) => request<Entry[]>(`/thoughts/${id}/entries`),
  },
};
