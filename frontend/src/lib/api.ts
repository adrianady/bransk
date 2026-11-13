import type {
  AnalysisCapabilities,
  ClaimAggregate,
  ClaimAggregateResponse,
  ClaimAnalysis,
  ClaimStance,
  ClaimStanceInput,
  ClaimStancesResponse,
  NoteCheck,
  NoteCheckList,
  NoteCheckResult,
  NoteCheckStatus,
  ParseSummary,
  PageTextResponse,
  StanceDirection,
  StanceStatus,
  VerifySummary,
  AiCustomModelsResponse,
  AiCustomProviderState,
  AiCustomSaveRequest,
  AiOpenCodeConfigState,
  AiOpenCodeSaveRequest,
  AiOpenCodeConsoleConfigState,
  AiOpenCodeConsoleSaveRequest,
  AiModelChoiceRequest,
  AiModelChoiceResponse,
  AiModelsResponse,
  AiProviderChoiceResponse,
  AiProvidersResponse,
  AiStatusResponse,
  AiTestConnectionResponse,
  BulkImportResponse,
  ChatHistoryItem,
  ChatTask,
  BibliographyResponse,
  ConceptEvidence,
  DuplicateEntry,
  DuplicateGroup,
  DuplicatesResponse,
  ExtractedConcept,
  GlobalSearchResponse,
  MergeStudiesPayload,
  PdfHighlight,
  ScreeningDecisionRow,
  ScreeningSummary,
  Entry,
  ExternalSearchResponse,
  GraphData,
  Idea,
  MaintenanceReport,
  Project,
  ProjectAnalysisResponse,
  ProjectFeedbackResponse,
  ProjectEvidenceMap,
  StudyThesisEvidenceLink,
  ProjectReadinessResponse,
  ProjectSchemaResponse,
  ProjectStudiesResponse,
  ProviderHealth,
  TagCleanupReport,
  RelationLabel,
  Relationship,
  Reminder,
  ResearchResult,
  ResolvedStudyMetadata,
  SourceRunResult,
  SourcesStatus,
  SpacedRepetitionItem,
  StructureExampleResponse,
  StudyCheckResponse,
  StudyNote,
  StudyQAResponse,
  StudySourcesResponse,
  Tag,
  Theory,
  TheoryEvidence,
  TheoryNode,
  TheoryStatus,
  Thought,
  TimelineBuckets,
  TimelineEntry,
  TimelineStats,
  TitleAnalysis,
  TodoItem,
  TopicBrainstormResponse,
  TopicsResponse,
  Questionnaire,
  ProjectSummary,
  ZoteroStatus,
  ZoteroImportResult,
} from './types';

// Dynamic API Base URL resolution with fallback to relative /api proxy
const BASE = (import.meta as any).env?.VITE_API_BASE_URL || '/api';

// ── Legacy bearer token for network access ──────────────────────────────────
// It remains supported for existing devices and account setup/reset. Password
// sessions use an HttpOnly cookie and never put the password in browser storage.
const TOKEN_KEY = 'chronokg-token';
/** Fired when the server rejects the stored token, so the gate can ask again. */
export const AUTH_EVENT = 'chronokg:unauthorized';

export function getAuthToken(): string {
  try {
    const stored = localStorage.getItem(TOKEN_KEY) || '';
    const token = stored.trim();
    // A damaged/copied token can contain Unicode replacement characters or
    // control bytes. Fetch's Headers conversion throws before a request is
    // sent when that happens, preventing the app from starting at all.
    if (token && /[^\t\x20-\x7E\x80-\xFF]/.test(token)) {
      localStorage.removeItem(TOKEN_KEY);
      return '';
    }
    if (stored !== token) {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    }
    return token;
  } catch {
    return '';
  }
}

export function setAuthToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode / storage disabled: the token just is not remembered */
  }
}

export function clearAuthToken(): void {
  setAuthToken('');
}

function notifyUnauthorized(): void {
  try {
    window.dispatchEvent(new Event(AUTH_EVENT));
  } catch {
    /* server-side rendering / tests without window */
  }
}

// Network-level failure hook. The offline sync layer registers a listener so
// a failed fetch instantly marks the server unreachable (Tailscale off etc.)
// without waiting for the next health probe. api.ts never imports sync.ts —
// the listener is registered at startup, so there is no import cycle.
let onNetworkFailure: (() => void) | null = null;

export function setNetworkFailureListener(fn: (() => void) | null): void {
  onNetworkFailure = fn;
}

/** Headers for calls that use fetch directly (streams, PDFs, and auth status). */
function requestHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...extra };
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export interface AuthStatus {
  required: boolean
  password_configured?: boolean
  authenticated?: boolean
  token_authenticated?: boolean
  session_authenticated?: boolean
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  // Short timeout: a hung server must not leave the gate on "checking…".
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 5000);
  try {
    const res = await fetch(`${BASE}/auth/status`, {
      signal: controller.signal,
      headers: requestHeaders(),
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * An HTTP response the server refused, carrying the facts the UI needs.
 *
 * Plain `Error` dropped the status code, so a 409 (duplicate) and a 422
 * (validation) looked alike and callers fell back to matching substrings.
 */
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Turn FastAPI's error body into one sentence a person can read.
 *
 * A validation error arrives as a list of `{ msg, loc }` objects; the old code
 * interpolated that list into a toast and showed `[object Object]`. The `loc`
 * path helps ("title: String should have at most 500 characters"), but its
 * first element is just the request section ("body", "query"), so it is dropped.
 */
function readableError(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail.trim()) return detail.trim();

  if (Array.isArray(detail) && detail.length > 0) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item.trim() || null;
        if (!item || typeof item !== 'object') return null;
        const { msg, loc } = item as { msg?: unknown; loc?: unknown };
        if (typeof msg !== 'string' || !msg.trim()) return null;
        const where = Array.isArray(loc)
          ? loc.filter((p) => typeof p === 'string' || typeof p === 'number').slice(1).join('.')
          : '';
        return where ? `${where}: ${msg.trim()}` : msg.trim();
      })
      .filter((part): part is string => !!part);
    if (parts.length > 0) return parts.join('; ');
  }

  if (detail && typeof detail === 'object') {
    const fallback = (detail as { message?: unknown; error?: unknown }).message
      ?? (detail as { message?: unknown; error?: unknown }).error;
    if (typeof fallback === 'string' && fallback.trim()) return fallback.trim();
  }

  return `HTTP ${status}`;
}

/** Read an error body without ever throwing on a non-JSON response. */
async function readErrorBody(res: Response): Promise<{ detail: unknown; body: unknown }> {
  const body = await res.json().catch(() => null);
  if (body && typeof body === 'object') {
    const record = body as { detail?: unknown; error?: unknown; message?: unknown };
    const detail = record.detail ?? record.error ?? record.message ?? null;
    return { detail, body };
  }
  return { detail: null, body };
}

async function request<T>(path: string, options?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = options?.timeoutMs ?? 30000;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const { timeoutMs: _timeoutMs, ...init } = options ?? {};

  try {
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: init.credentials ?? 'include',
      // Merge instead of spreading the caller's headers over these: spreading
      // `options` afterwards used to drop Content-Type whenever a caller passed
      // its own header object.
      headers: requestHeaders(
        init.headers ? Object.fromEntries(new Headers(init.headers).entries()) : {},
      ),
      signal: init.signal || controller.signal,
    });
    clearTimeout(timeoutId);

    if (res.status === 401 && path !== '/auth/login') notifyUnauthorized();

    if (!res.ok) {
      // Keep the status and the parsed detail on the error so callers can tell
      // "duplicate" from "invalid" instead of guessing at the message.
      const { detail } = await readErrorBody(res);
      throw new ApiError(readableError(detail, res.status), res.status, detail);
    }
    if (res.status === 204) return undefined as T;
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return res.json();
    }
    return (await res.text()) as unknown as T;
  } catch (err: any) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      // Our own timeout means the server did not answer — unreachable as far
      // as this request is concerned. A caller-supplied signal is the
      // caller's own cancel, not a reachability signal.
      if (onNetworkFailure && !init.signal) onNetworkFailure();
      throw new Error(`Request timed out after ${timeoutMs / 1000} seconds`);
    }
    if (onNetworkFailure && err instanceof TypeError) onNetworkFailure();
    throw err;
  }
}

export async function requestFormData<T>(path: string, formData: FormData, timeoutMs = 60000): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const token = getAuthToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  try {
    const res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers,
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (res.status === 401 && path !== '/auth/login') notifyUnauthorized();

    if (!res.ok) {
      const { detail } = await readErrorBody(res);
      throw new ApiError(readableError(detail, res.status), res.status, detail);
    }
    if (res.status === 204) return undefined as T;
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return res.json();
    }
    return (await res.text()) as unknown as T;
  } catch (err: unknown) {
    clearTimeout(timeoutId);
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error(`Request timed out after ${timeoutMs / 1000} seconds`);
    }
    throw err;
  }
}

export function setupSharedAccount(username: string, password: string) {
  return request<{ configured: boolean; username: string }>('/auth/setup', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export function loginSharedAccount(username: string, password: string) {
  return request<{ authenticated: boolean; username: string }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export function logoutSharedAccount() {
  return request<void>('/auth/logout', { method: 'POST', body: '{}' });
}

export function changeSharedPassword(password: string) {
  return request<{ updated: boolean }>('/auth/password', {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
}

export async function fetchStudiesPage(params?: { limit?: number; offset?: number }): Promise<{ items: Entry[]; total: number }> {
  const q = new URLSearchParams();
  if (params?.limit) q.set('limit', String(params.limit));
  if (params?.offset) q.set('offset', String(params.offset));
  const query = q.toString();
  const res = await fetch(`${BASE}/studies${query ? `?${query}` : ''}`, {
    headers: requestHeaders(),
    credentials: 'include',
  });
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) {
    const { detail } = await readErrorBody(res);
    throw new ApiError(readableError(detail, res.status), res.status, detail);
  }
  const items: Entry[] = await res.json();
  const header = res.headers.get('X-Total-Count');
  const total = header === null ? NaN : Number(header);
  // A missing header (older backend) must not hide studies: fall back to the
  // page length, which is what the screen showed before.
  return { items, total: Number.isFinite(total) ? total : items.length };
}

export const api = {
  // Entries
  entries: {
    list: (params?: { archived?: boolean; tag?: string; limit?: number; offset?: number; include?: string; sort?: string }) => {
      const q = new URLSearchParams();
      if (params?.archived) q.set('archived', 'true');
      if (params?.tag) q.set('tag', params.tag);
      if (params?.limit) q.set('limit', String(params.limit));
      if (params?.offset) q.set('offset', String(params.offset));
      if (params?.include) q.set('include', params.include);
      if (params?.sort) q.set('sort', params.sort);
      return request<Entry[]>(`/entries?${q}`);
    },
    // Same query as `list`, but also reads X-Total-Count so the UI can show
    // „100 din 275" instead of just the loaded page size.
    listPage: async (params?: { archived?: boolean; tag?: string; limit?: number; offset?: number; include?: string; sort?: string }): Promise<{ items: Entry[]; total: number }> => {
      const q = new URLSearchParams();
      if (params?.archived) q.set('archived', 'true');
      if (params?.tag) q.set('tag', params.tag);
      if (params?.limit) q.set('limit', String(params.limit));
      if (params?.offset) q.set('offset', String(params.offset));
      if (params?.include) q.set('include', params.include);
      if (params?.sort) q.set('sort', params.sort);
      const res = await fetch(`${BASE}/entries?${q}`, {
        headers: requestHeaders(),
        credentials: 'include',
      });
      if (res.status === 401) notifyUnauthorized();
      if (!res.ok) {
        const { detail } = await readErrorBody(res);
        throw new ApiError(readableError(detail, res.status), res.status, detail);
      }
      const items: Entry[] = await res.json();
      const header = res.headers.get('X-Total-Count');
      const total = header === null ? NaN : Number(header);
      return { items, total: Number.isFinite(total) ? total : items.length };
    },
    get: (id: number) => request<Entry>(`/entries/${id}`),
    create: (data: Partial<Entry>) =>
      request<Entry>('/entries', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Entry>) =>
      request<Entry>(`/entries/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<void>(`/entries/${id}`, { method: 'DELETE' }),
    uploadPdf: (id: number, file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      return requestFormData<Entry>(`/entries/${id}/pdf`, formData, 120000);
    },
    updateStatus: (id: number, read_status: string) =>
      request<Entry>(`/entries/${id}/status`, { method: 'PATCH', body: JSON.stringify({ read_status }) }),
  },

  // Citations — BibTeX/RIS export and the thesis bibliography
  citations: {
    bibliography: () => request<BibliographyResponse>('/citations/bibliography'),
    cite: (entryId: number) => request<string>(`/citations/cite/${entryId}`),
    // File download: the backend answers with Content-Disposition attachment.
    export: async (format: 'bibtex' | 'ris', doiOnly = false): Promise<void> => {
      const q = new URLSearchParams({ format });
      if (doiOnly) q.set('doi_only', 'true');
      const res = await fetch(`${BASE}/citations/export?${q}`, {
        headers: requestHeaders(),
        credentials: 'include',
      });
      if (res.status === 401) notifyUnauthorized();
      if (!res.ok) {
        const { detail } = await readErrorBody(res);
        throw new ApiError(readableError(detail, res.status), res.status, detail);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = format === 'ris' ? 'bransk-bibliografie.ris' : 'bransk-bibliografie.bib';
      a.click();
      URL.revokeObjectURL(url);
    },
  },

  // Highlights — text selections saved from the native PDF reader
  highlights: {
    list: (entryId: number) => request<PdfHighlight[]>(`/studies/${entryId}/highlights`),
    create: (entryId: number, data: { quote: string; note?: string; page_number?: number | null; document_id?: number | null }) =>
      request<PdfHighlight>(`/studies/${entryId}/highlights`, { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: { note: string }) =>
      request<PdfHighlight>(`/highlights/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    remove: (id: number) => request<void>(`/highlights/${id}`, { method: 'DELETE' }),
  },

  // Search
  search: (q: string, params?: { from_date?: string; to_date?: string; tags?: string }) => {
    const p = new URLSearchParams({ q });
    if (params?.from_date) p.set('from_date', params.from_date);
    if (params?.to_date) p.set('to_date', params.to_date);
    if (params?.tags) p.set('tags', params.tags);
    return request<{ entry: Entry; rank: number }[]>(`/search?${p}`);
  },

  // Global search — entries, studies, notes, highlights and PDF chunks
  globalSearch: (q: string) =>
    request<GlobalSearchResponse>(`/search/global?q=${encodeURIComponent(q)}`),

  // Timeline
  timeline: {
    get: (params?: { from_date?: string; to_date?: string; tags?: string; q?: string }) => {
      const p = new URLSearchParams();
      if (params?.from_date) p.set('from_date', params.from_date);
      if (params?.to_date) p.set('to_date', params.to_date);
      if (params?.tags) p.set('tags', params.tags);
      if (params?.q) p.set('q', params.q);
      return request<{ entries: TimelineEntry[]; total: number }>(`/timeline?${p}`);
    },
    stats: () => request<TimelineStats>('/timeline/stats'),
    buckets: (params?: { bucket?: 'century' | 'decade' | 'year'; from_date?: string; to_date?: string; tags?: string; q?: string }) => {
      const p = new URLSearchParams();
      if (params?.bucket) p.set('bucket', params.bucket);
      if (params?.from_date) p.set('from_date', params.from_date);
      if (params?.to_date) p.set('to_date', params.to_date);
      if (params?.tags) p.set('tags', params.tags);
      if (params?.q) p.set('q', params.q);
      return request<TimelineBuckets>(`/timeline/buckets?${p}`);
    },
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
    graph: (params?: { project_id?: number; only_read?: boolean; only_project_evidence?: boolean; study_type?: string }) => {
      const q = new URLSearchParams();
      if (params?.project_id != null) q.set('project_id', String(params.project_id));
      if (params?.only_read) q.set('only_read', 'true');
      if (params?.only_project_evidence) q.set('only_project_evidence', 'true');
      if (params?.study_type) q.set('study_type', params.study_type);
      const query = q.toString();
      return request<GraphData>(`/relationships/graph${query ? `?${query}` : ''}`);
    },
    labels: () => request<{ total: number; labels: RelationLabel[] }>('/relationships/labels'),
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
      request<{ reply: string; provider: string; model: string; actions: any[] }>('/chat', {
        method: 'POST',
        body: JSON.stringify({ message, history: history || [] }),
      }),
    stream: (message: string, history?: { role: string; content: string }[], signal?: AbortSignal) => {
      const pending = fetch(`${BASE}/chat/stream`, {
        method: 'POST',
        headers: requestHeaders(),
        credentials: 'include',
        body: JSON.stringify({ message, history: history || [] }),
        signal,
      });
      // A rejected token must reach the gate even on a streamed call.
      pending.then(res => { if (res.status === 401) notifyUnauthorized() }).catch(() => {});
      return pending;
    },
    history: {
      list: () => request<ChatHistoryItem[]>('/chat/history'),
      clear: () => request<void>('/chat/history', { method: 'DELETE' }),
    },
    health: () => request<ProviderHealth>('/chat/health'),
    confirm: (confirmId: string, approve = true) =>
      request<{ status: string; result?: { type: string; entry_id?: number } }>('/chat/confirm', {
        method: 'POST',
        body: JSON.stringify({ confirm_id: confirmId, approve }),
      }),
    getTask: () => request<{ task: ChatTask | null }>('/chat/task'),
    cancelTask: () => request<{ status: string }>('/chat/task', { method: 'DELETE' }),
  },

  // AI status and model choice. These lived in AiStatusModal as bare
  // fetch('/api/ai/...') calls, which ignored BASE/VITE_API_BASE_URL, had no
  // timeout and turned every failure into "HTTP <n>".
  ai: {
    status: () => request<AiStatusResponse>('/ai/status'),
    models: (provider?: string) => request<AiModelsResponse>(
      `/ai/models${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`,
      { timeoutMs: 20000 },
    ),
    // An empty id clears the override and returns to the .env default.
    setModel: (model: string, provider?: string) =>
      request<AiModelChoiceResponse>('/ai/model', {
        method: 'POST',
        body: JSON.stringify({ model_id: model, ...(provider ? { provider } : {}) } satisfies AiModelChoiceRequest),
      }),
    // Every provider the server knows, which one is in use, and the failover order.
    providers: () => request<AiProvidersResponse>('/ai/providers', { timeoutMs: 20000 }),
    setProvider: (provider: string) =>
      request<AiProviderChoiceResponse>('/ai/provider', {
        method: 'POST',
        body: JSON.stringify({ provider }),
      }),
    // A provider the user types in: URL + key, then "fetch models" to check it.
    custom: () => request<AiCustomProviderState>('/ai/custom'),
    customModels: (body: AiCustomSaveRequest) =>
      request<AiCustomModelsResponse>('/ai/custom/models', {
        method: 'POST',
        body: JSON.stringify(body),
        timeoutMs: 30000,
      }),
    saveCustom: (body: AiCustomSaveRequest) =>
      request<{ saved: boolean; base_url: string; label: string; model: string; active: string }>(
        '/ai/custom',
        { method: 'POST', body: JSON.stringify(body) },
      ),
    openCode: () => request<AiOpenCodeConfigState>('/ai/opencode'),
    saveOpenCode: (body: AiOpenCodeSaveRequest) =>
      request<AiOpenCodeConfigState>('/ai/opencode', {
        method: 'POST', body: JSON.stringify(body), timeoutMs: 30000,
      }),
    openCodeConsole: () => request<AiOpenCodeConsoleConfigState>('/ai/opencode-console'),
    saveOpenCodeConsole: (body: AiOpenCodeConsoleSaveRequest) =>
      request<AiOpenCodeConsoleConfigState>('/ai/opencode-console', {
        method: 'POST', body: JSON.stringify(body), timeoutMs: 30000,
      }),
    openCodeGo: () => request<AiOpenCodeConsoleConfigState>('/ai/opencode-go'),
    saveOpenCodeGo: (body: AiOpenCodeConsoleSaveRequest) =>
      request<AiOpenCodeConsoleConfigState>('/ai/opencode-go', {
        method: 'POST', body: JSON.stringify(body), timeoutMs: 30000,
      }),
    testConnection: (provider: string, model = '') =>
      request<AiTestConnectionResponse>('/ai/test-connection', {
        method: 'POST',
        body: JSON.stringify({ provider, model }),
        timeoutMs: 25000,
      }),
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
    notify: (id: number) =>
      request<void>(`/reminders/${id}/notify`, { method: 'POST' }),
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

  // Qualitative research / Studies
  research: {
    run: (topic: string, depth: 'quick' | 'deep' = 'deep', save = true) =>
      request<ResearchResult>('/research', {
        method: 'POST',
        body: JSON.stringify({ topic, depth, save }),
        timeoutMs: 300000, // deep research runs many LLM + web calls
      }),
    stream: (topic: string, depth: 'quick' | 'deep' = 'deep', save = true, signal?: AbortSignal) => {
      const pending = fetch(`${BASE}/research/stream`, {
        method: 'POST',
        headers: requestHeaders(),
        credentials: 'include',
        body: JSON.stringify({ topic, depth, save }),
        signal,
      });
      pending.then(res => { if (res.status === 401) notifyUnauthorized() }).catch(() => {});
      return pending;
    },
  },

  // Studies (entries with study_type set)
  studies: {
    // One page (the endpoint caps it at `limit`), plus the real number of
    // studies in the library from X-Total-Count, so the screen can say
    // "100 of 106" instead of reporting the page size as the library size.
    list: (params?: { limit?: number; offset?: number }) => fetchStudiesPage(params),
    // Walk every page, so no study can stay invisible behind the page cap.
    all: async (pageSize = 100): Promise<{ items: Entry[]; total: number }> => {
      const first = await fetchStudiesPage({ limit: pageSize, offset: 0 })
      const items = [...first.items]
      while (items.length < first.total) {
        const next = await fetchStudiesPage({ limit: pageSize, offset: items.length })
        if (next.items.length === 0) break
        items.push(...next.items)
      }
      return { items, total: first.total }
    },
    download: (id: number) =>
      // A PDF comes from a publisher over the internet: the old 30 s default
      // aborted slow but healthy downloads and the UI blamed the URL.
      request<{ pdf_path: string; indexed_chunks?: number }>(`/studies/${id}/download`, {
        method: 'POST',
        timeoutMs: 300000,
      }),
    downloadAll: () =>
      request<{ downloaded: number[]; failed: { id: number; title: string }[]; indexed_chunks?: number }>(
        '/studies/download-all',
        {
          method: 'POST',
          timeoutMs: 300000, // batch downloads can take minutes
        },
      ),
    uploadPdf: (id: number, file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      return requestFormData<Entry>(`/entries/${id}/pdf`, formData, 120000);
    },
    // PDF.js supports request headers, so keep credentials out of URLs that
    // can be copied into browser history, logs, or referrer metadata.
    pdfUrl: (id: number) => `${BASE}/studies/${id}/pdf`,
    pdfHeaders: () => {
      const token = getAuthToken();
      return token ? { Authorization: `Bearer ${token}` } : {};
    },
    // Searches every open source (OpenAlex, Unpaywall, Semantic Scholar,
    // Europe PMC, arXiv) for the paper itself, then indexes what it finds.
    fulltext: (id: number) =>
      request<{
        entry_id: number
        status: 'pdf' | 'html' | 'none'
        source: string
        url: string
        chars: number
        chunks: number
        index_error?: boolean
        text_save_error?: boolean
        tried: { url: string; source: string; kind?: string; error?: string }[]
      }>(`/studies/${id}/fulltext`, { method: 'POST', timeoutMs: 300000 }),
    searchExternal: (query: string, maxResults = 15) =>
      request<ExternalSearchResponse>('/studies/search-external', {
        method: 'POST',
        body: JSON.stringify({ query, max_results: maxResults }),
        timeoutMs: 35000,
      }),
    resolveDoi: (identifier: string) =>
      request<ResolvedStudyMetadata>('/studies/resolve-doi', {
        method: 'POST',
        body: JSON.stringify({ identifier }),
        timeoutMs: 25000,
      }),
    // Reads the PDF's own metadata + first page to prefill title/authors/DOI/
    // ISBN and to suggest the study/book classification before upload.
    extractPdfMetadata: (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      return requestFormData<{
        title: string;
        authors: string;
        year: string;
        doi: string;
        isbn: string;
        study_type: string;
        page_count: number;
        has_text_layer: boolean;
        source: string;
      }>('/studies/extract-pdf-metadata', formData, 60000);
    },
    importBulk: (data: { studies?: any[]; raw_text?: string; auto_download_pdfs?: boolean }) =>
      request<BulkImportResponse>('/studies/import-bulk', {
        method: 'POST',
        body: JSON.stringify(data),
        timeoutMs: 300000,
      }),
    bulkDownload: (entryIds: number[]) =>
      request<{ downloaded: number[]; failed: { id: number; title: string }[]; indexed_chunks: number }>(
        '/studies/bulk-download',
        {
          method: 'POST',
          body: JSON.stringify({ entry_ids: entryIds }),
          timeoutMs: 300000,
        },
      ),
    bulkDelete: (entryIds: number[]) =>
      request<{ deleted: number }>('/studies/bulk-delete', {
        method: 'POST',
        body: JSON.stringify({ entry_ids: entryIds }),
      }),
    // PRISMA screening decisions
    screeningSummary: () => request<ScreeningSummary>('/studies/screening/summary'),
    screeningDecisions: () => request<Record<string, ScreeningDecisionRow>>('/studies/screening/decisions'),
    setScreening: (entryId: number, data: { decision: 'include' | 'exclude' | 'maybe'; reason?: string }) =>
      request<ScreeningDecisionRow>(`/studies/${entryId}/screening`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    clearScreening: (entryId: number) =>
      request<void>(`/studies/${entryId}/screening`, { method: 'DELETE' }),
    getDuplicates: () => request<DuplicatesResponse>('/studies/duplicates'),
    merge: (payload: MergeStudiesPayload) =>
      request<{ success: boolean; keep_entry: Entry }>('/studies/merge', { method: 'POST', body: JSON.stringify(payload) }),
    // AI-assisted methodology: design / effect direction / quality proposal
    aiAppraisal: (entryId: number) =>
      request<{ entry_id: number; design: string; effect_direction: string; quality: number | null; justification: string }>(
        `/studies/${entryId}/ai-appraisal`,
        { method: 'POST', body: '{}', timeoutMs: 60000 },
      ),
    notes: {
      list: (entryId: number) => request<StudyNote[]>(`/studies/${entryId}/notes`),
      all: () => request<StudyNote[]>('/studies/notes/all'),
      create: (entryId: number, data: {
        content: string; course_context?: string; page_number?: number | null; source_document_ids?: number[]
      }) =>
        request<StudyNote>(`/studies/${entryId}/notes`, {
          method: 'POST',
          body: JSON.stringify(data),
        }),
      update: (noteId: number, data: {
        content?: string; course_context?: string; page_number?: number | null; source_document_ids?: number[]
      }) =>
        request<StudyNote>(`/studies/notes/${noteId}`, {
          method: 'PUT',
          body: JSON.stringify(data),
        }),
      delete: (noteId: number) =>
        request<{ deleted: boolean; id: number }>(`/studies/notes/${noteId}`, {
          method: 'DELETE',
        }),
    },
  },

  // Theory Aggregator
  theories: {
    list: () => request<Theory[]>('/theories'),
    tree: () => request<TheoryNode[]>('/theories/tree'),
    allEvidence: () => request<{ id: number; theory_id: number; theory_name: string; entry_id: number; entry_title: string; stance: 'pro' | 'contra'; note: string; weight: number; created_at: string }[]>('/theories/evidence'),
    create: (data: { name: string; description?: string; parent_id?: number | null; status?: TheoryStatus }) =>
      request<Theory>('/theories', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<{ name: string; description: string; parent_id: number | null; status: TheoryStatus }>) =>
      request<Theory>(`/theories/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) => request<void>(`/theories/${id}`, { method: 'DELETE' }),
    evidence: (id: number) => request<TheoryEvidence[]>(`/theories/${id}/evidence`),
    addEvidence: (id: number, data: { entry_id: number; stance: 'pro' | 'contra'; note?: string; weight?: number }) =>
      request<TheoryEvidence>(`/theories/${id}/evidence`, { method: 'POST', body: JSON.stringify(data) }),
    removeEvidence: (theoryId: number, evidenceId: number) =>
      request<void>(`/theories/${theoryId}/evidence/${evidenceId}`, { method: 'DELETE' }),
  },

  // Claim hierarchy + stance edges (slice 5). Claims are leaves of the theory
  // tree; a stance edge pins one study's verdict to a verbatim page quote, so
  // the backend rejects quotes that do not match the canonical page text.
  claims: {
    create: (body: { parent_id: number; name: string; statement: string; test_variable?: string; test_condition?: string }) =>
      request<Theory>('/claims', { method: 'POST', body: JSON.stringify(body) }),
    stances: (claimId: number, status?: StanceStatus) =>
      request<ClaimStancesResponse>(
        `/claims/${claimId}/stances${status ? `?status=${encodeURIComponent(status)}` : ''}`,
      ),
    proposeStance: (claimId: number, body: ClaimStanceInput) =>
      request<ClaimStance>(`/claims/${claimId}/stances`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    // The router speaks `action: 'accept' | 'reject'`; the UI speaks a status.
    // Translate here so callers keep the accepted/rejected vocabulary.
    decideStance: (stanceId: number, body: {
      status: 'accepted' | 'rejected';
      direction?: StanceDirection;
      evidence_quote?: string;
      correction?: string;
    }) => {
      const payload: Record<string, unknown> = {
        action: body.status === 'accepted' ? 'accept' : 'reject',
      };
      if (body.direction !== undefined) payload.direction = body.direction;
      if (body.evidence_quote !== undefined) payload.evidence_quote = body.evidence_quote;
      // claim_stance has no `correction` column; the reviewer's wording is
      // stored in `reasoning`, which is what the analysis panel shows.
      if (body.correction !== undefined) payload.reasoning = body.correction;
      return request<ClaimStance>(`/claims/stances/${stanceId}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      });
    },
    // GET /claims/{id}/aggregate names the verdict `claim_verdict` and the
    // study list `per_entry`. Normalise both so one ClaimAggregate type serves
    // this endpoint and the analysis rollup.
    aggregate: async (claimId: number): Promise<ClaimAggregate> => {
      const raw = await request<ClaimAggregateResponse>(`/claims/${claimId}/aggregate`);
      return {
        claim_id: raw.claim_id,
        claim_name: raw.claim_name,
        verdict: raw.claim_verdict,
        claim_verdict: raw.claim_verdict,
        studies_accepted: raw.studies_accepted,
        per_entry: raw.per_entry,
        per_study: raw.per_entry.map((entry) => ({
          entry_id: entry.entry_id,
          entry_title: entry.entry_title,
          verdict: entry.verdict,
          sides: entry.directions,
        })),
      };
    },
    // Claims touched by one study (entry): which claim_stance edges reference it.
    forEntry: (entryId: number) =>
      request<{
        entry_id: number;
        entry_title: string;
        claims: Array<
          ClaimStance & { claim_name?: string; statement?: string }
        >;
        bias: { available: boolean; reason: string };
      }>(`/entries/${entryId}/analysis`),
  },

  // Stance analysis panel: deterministic dry run, no writes, no model.
  analysis: {
    run: (claimId: number, entryId: number) =>
      request<ClaimAnalysis>(`/claims/${claimId}/analyze`, {
        method: 'POST',
        body: JSON.stringify({ entry_id: entryId }),
        timeoutMs: 120000, // NLI windows score one by one
      }),
    capabilities: (claimId: number) =>
      request<AnalysisCapabilities>(`/claims/${claimId}/analyze/capabilities`),
  },

  // Verified notes: deterministic check of note statements against page text.
  notes: {
    check: (entryId: number, noteId: number, apply = false) =>
      request<NoteCheckResult>(
        `/studies/${entryId}/notes/${noteId}/check${apply ? '?apply=true' : ''}`,
        { method: 'POST', timeoutMs: 120000 },
      ),
    checks: (entryId: number) =>
      request<NoteCheckList>(`/studies/${entryId}/note-checks`),
    updateCheck: (id: number, body: {
      status?: NoteCheckStatus;
      quote?: string;
      correction?: string;
      page_printed?: string;
    }) =>
      request<NoteCheck>(`/note-checks/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    verifySummary: (entryId: number) =>
      request<VerifySummary>(`/studies/${entryId}/verify/summary`),
  },

  // Per-page document parse (classification ledger, canonical text, renders).
  parse: {
    summary: (entryId: number) =>
      request<ParseSummary>(`/studies/${entryId}/parse/summary`, { timeoutMs: 60000 }),
    pageText: (entryId: number, page: number) =>
      request<PageTextResponse>(`/studies/${entryId}/pages/${page}/text`),
    // Image loads in <img src>, so it cannot carry an Authorization header;
    // the session cookie is what authenticates it.
    pageImageUrl: (entryId: number, page: number) =>
      `${BASE}/studies/${entryId}/pages/${page}/image`,
    // SSE endpoint — open with EventSource/fetch, not through request().
    streamUrl: (entryId: number, opts?: {
      pages?: string;
      provider?: string;
      model?: string;
      force?: boolean;
      dpi?: number;
    }) => {
      const q = new URLSearchParams();
      if (opts?.pages) q.set('pages', opts.pages);
      if (opts?.provider) q.set('provider', opts.provider);
      if (opts?.model) q.set('model', opts.model);
      if (opts?.force) q.set('force', 'true');
      if (opts?.dpi != null) q.set('dpi', String(opts.dpi));
      const query = q.toString();
      return `${BASE}/studies/${entryId}/parse/stream${query ? `?${query}` : ''}`;
    },
  },

  // Study QA + health check
  studyQA: {
    ask: (entryId: number, question: string) =>
      request<StudyQAResponse>(`/studies/${entryId}/qa`, {
        method: 'POST',
        body: JSON.stringify({ question }),
        timeoutMs: 120000,
      }),
    check: (entryId: number) =>
      request<StudyCheckResponse>(`/studies/${entryId}/check`),
    sources: (entryId: number) =>
      request<StudySourcesResponse>(`/studies/${entryId}/sources`),
  },

  // Thesis project centre
  project: {
    get: () => request<Project>('/project'),
    update: (patch: Partial<Project>) =>
      request<Project>('/project', { method: 'PUT', body: JSON.stringify(patch) }),
    // Thesis as Word: the backend assembles chapters + bibliography.
    downloadDocx: async (): Promise<void> => {
      const res = await fetch(`${BASE}/project/export/docx`, {
        headers: requestHeaders(),
        credentials: 'include',
      });
      if (res.status === 401) notifyUnauthorized();
      if (!res.ok) {
        const { detail } = await readErrorBody(res);
        throw new ApiError(readableError(detail, res.status), res.status, detail);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `teza-${new Date().toISOString().slice(0, 10)}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    },
    studies: (limit = 12) =>
      request<ProjectStudiesResponse>(`/project/studies?limit=${limit}`),
    analysis: () => request<ProjectAnalysisResponse>('/project/analysis', { timeoutMs: 30000 }),
    // Generation costs real money and takes seconds: only on an explicit click.
    regenerateAnalysis: () =>
      request<ProjectAnalysisResponse>('/project/analysis', { method: 'POST', timeoutMs: 180000 }),
    readiness: () => request<ProjectReadinessResponse>('/project/readiness'),
    // Topic candidates, each measured against the real library.
    topics: () => request<TopicsResponse>('/project/topics'),
    chooseTopic: (title: string, question = '') =>
      request<Project>('/project/topics/choose', {
        method: 'POST',
        body: JSON.stringify({ title, question }),
      }),
    brainstormTopics: (focus = '', count = 5) =>
      request<TopicBrainstormResponse>('/project/topics/brainstorm', {
        method: 'POST',
        body: JSON.stringify({ focus, count }),
        timeoutMs: 90000,
      }),
    // Deterministic checks only — free, instant.
    title: () => request<TitleAnalysis>('/project/title'),
    // Adds a reviewer's reading: one LLM call, on an explicit click.
    reviewTitle: () => request<TitleAnalysis>('/project/title', { method: 'POST', timeoutMs: 180000 }),
    schema: () => request<ProjectSchemaResponse>('/project/schema'),
    evidenceMap: () => request<ProjectEvidenceMap>('/project/map'),
    createClaim: (sectionId: string, text: string) =>
      request('/project/claims', {
        method: 'POST', body: JSON.stringify({ section_id: sectionId, text }),
      }),
    deleteClaim: (claimId: number) =>
      request<void>(`/project/claims/${claimId}`, { method: 'DELETE' }),
    addEvidence: (claimId: number, data: {
      entry_id: number; document_id?: number | null; note_id?: number | null;
      relation?: 'supports' | 'contradicts' | 'contextualizes'
    }) =>
      request(`/project/claims/${claimId}/evidence`, {
        method: 'POST', body: JSON.stringify(data),
      }),
    removeEvidence: (claimId: number, evidenceId: number) =>
      request<void>(`/project/claims/${claimId}/evidence/${evidenceId}`, { method: 'DELETE' }),
    evidenceForStudy: (entryId: number) =>
      request<StudyThesisEvidenceLink[]>(`/project/evidence?entry_id=${entryId}`),
    ideas: {
      list: (category = '') =>
        request<Idea[]>(`/project/ideas${category ? `?category=${encodeURIComponent(category)}` : ''}`),
      create: (idea: { text: string; category?: string; section_id?: string }) =>
        request<Idea>('/project/ideas', { method: 'POST', body: JSON.stringify(idea) }),
      update: (id: number, data: { text: string; category?: string }) =>
        request<Idea>(`/project/ideas/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
      remove: (id: number) => request<void>(`/project/ideas/${id}`, { method: 'DELETE' }),
      suggest: () =>
        request<{ ideas: { text: string; category: string }[]; from_llm: boolean; note?: string }>(
          '/project/ideas/suggest', { method: 'POST', timeoutMs: 180000 },
        ),
    },
    feedback: () => request<ProjectFeedbackResponse>('/project/feedback', { timeoutMs: 180000 }),
    // The reviewer's read is one LLM call (5–39 s): it runs only when the user
    // explicitly asks, never when the tab merely opens.
    feedbackReview: () =>
      request<ProjectFeedbackResponse>('/project/feedback/review', {
        method: 'POST',
        timeoutMs: 180000,
      }),
    structureExample: (patch: Partial<Project> = {}) =>
      request<StructureExampleResponse>('/project/structure/example', {
        method: 'POST',
        body: JSON.stringify(patch),
        timeoutMs: 180000,
      }),
  },

  // Missing sources: the app's model fills them, Crossref verifies.
  sources: {
    status: () => request<SourcesStatus>('/sources/status'),
    resolveDois: (limit = 10, dryRun = false) =>
      request<SourceRunResult>('/sources/dois', {
        method: 'POST',
        body: JSON.stringify({ limit, dry_run: dryRun }),
        timeoutMs: 600000,
      }),
    fillSummaries: (limit = 10, dryRun = false) =>
      request<SourceRunResult>('/sources/summaries', {
        method: 'POST',
        body: JSON.stringify({ limit, dry_run: dryRun }),
        timeoutMs: 600000,
      }),
  },

  // Data integrity / re-analysis
  maintenance: {
    // Tag-only repair: no PDF re-reading, no model call. Dry run by default.
    normalizeTags: (dryRun = true) =>
      request<TagCleanupReport>(`/maintenance/tags?dry_run=${dryRun ? 'true' : 'false'}`, {
        method: 'POST',
        timeoutMs: 60000,
      }),
    analyze: (signal?: AbortSignal) => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000);
      const onExternalAbort = () => controller.abort();
      signal?.addEventListener('abort', onExternalAbort);
      return fetch(`${BASE}/maintenance/analyze`, {
        method: 'POST',
        headers: requestHeaders(),
        credentials: 'include',
        signal: controller.signal,
      }).finally(() => {
        clearTimeout(timeoutId);
        signal?.removeEventListener('abort', onExternalAbort);
      });
    },
  },

  // Research Instruments & Questionnaires
  questionnaires: {
    list: (projectId?: number) =>
      request<Questionnaire[]>(`/questionnaires${projectId ? `?project_id=${projectId}` : ''}`),
    get: (id: number) => request<Questionnaire>(`/questionnaires/${id}`),
    create: (data: Partial<Questionnaire>) =>
      request<Questionnaire>('/questionnaires', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Questionnaire>) =>
      request<Questionnaire>(`/questionnaires/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<{ status: string; deleted_id: number }>(`/questionnaires/${id}`, { method: 'DELETE' }),
    aiGenerate: (id: number) =>
      request<Questionnaire>(`/questionnaires/${id}/ai-generate`, { method: 'POST', timeoutMs: 180000 }),
    aiCritique: (id: number) =>
      request<{ questionnaire_id: number; title: string; critique: string }>(`/questionnaires/${id}/ai-critique`, {
        method: 'POST',
        timeoutMs: 180000,
      }),
  },

  // Multi-Project Isolation
  projects: {
    list: () => request<{ active_id: number; projects: ProjectSummary[] }>('/projects'),
    create: (data: { title: string; work_type?: string; field?: string; description?: string; research_q?: string; methodology?: string }) =>
      request<Project>('/projects', { method: 'POST', body: JSON.stringify(data) }),
    activate: (id: number) => request<{ status: string; active_id: number; project: Project }>(`/projects/${id}/activate`, { method: 'POST' }),
    delete: (id: number) => request<{ status: string; deleted_id: number }>(`/projects/${id}`, { method: 'DELETE' }),
  },

  // Zotero Integration
  zotero: {
    status: (customPath?: string) =>
      request<ZoteroStatus>(`/zotero/status${customPath ? `?path=${encodeURIComponent(customPath)}` : ''}`),
    importLocal: (options?: {
      collection?: string;
      data_dir?: string;
      storage_dir?: string;
      dedup_by_doi?: boolean;
      dedup_by_title?: boolean;
    }) =>
      request<ZoteroImportResult>('/zotero/import/local', {
        method: 'POST',
        body: JSON.stringify(options || {}),
        timeoutMs: 180000,
      }),
    importFile: (formData: FormData) =>
      requestFormData<ZoteroImportResult>('/zotero/import/file', formData, 120000),
    syncApi: (apiKey: string, userId: string) =>
      request<ZoteroImportResult>('/zotero/sync/api', {
        method: 'POST',
        body: JSON.stringify({ api_key: apiKey, user_id: userId }),
        timeoutMs: 180000,
      }),
    export: () => `${(import.meta as any).env?.VITE_API_BASE_URL || '/api'}/zotero/export`,
  },

  // AI-extracted concepts (factors / subfactors / constructs) + evidence links
  aiConcepts: {
    extract: (payload: { entry_ids?: number[]; project_id?: number | null; text_selection?: string; quote?: string; page_number?: number; entry_id?: number }) =>
      request<{ concepts: ExtractedConcept[]; relations?: any[] }>('/ai/extract-concepts', { method: 'POST', body: JSON.stringify(payload), timeoutMs: 120000 }),
    getProjectConcepts: (projectId: number) =>
      request<{ concepts: ExtractedConcept[]; relations?: any[] }>(`/project/${projectId}/concepts`),
  },
};
