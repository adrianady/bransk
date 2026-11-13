export interface Entry {
  id: number;
  title: string;
  // Full text comes from the detail endpoint; list views carry a short
  // preview so the payload stays small (see X-Total-Count on /entries).
  content?: string;
  content_preview?: string;
  entry_date: string | null;
  created_at: string;
  updated_at: string;
  tags: string;
  source: string;
  importance: number;
  archived: boolean;
  study_type: string;
  authors: string;
  quality: number | null;
  url_validated: boolean;
  design: string;
  effect_direction: string;
  pdf_path: string;
  doi?: string;
  doi_source?: string;
  doi_checked_at?: string;
  bib_title?: string;
  bib_authors?: string;
  bib_year?: string;
  read_status?: 'unread' | 'reading' | 'read' | 'annotated';
  isbn?: string;
  pdf_hash?: string;
}

export interface DuplicateEntry {
  id: number;
  title: string;
  doi?: string;
  isbn?: string;
  pdf_hash?: string;
  study_type?: string;
  read_status?: string;
  has_pdf: boolean;
  authors?: string;
  created_at: string;
}

export interface DuplicateGroup {
  reason: 'doi' | 'isbn' | 'pdf_hash' | 'title';
  value?: string;
  entries: DuplicateEntry[];
}

export interface DuplicatesResponse {
  duplicate_groups: DuplicateGroup[];
  count: number;
  total_groups: number;
  /** Extra rows beyond the first entry of each group (what merge removes). */
  total_duplicates: number;
}

/** Suggested bibliographic fields read from an uploaded PDF. */
export interface PdfMetadataSuggestion {
  title: string;
  authors: string;
  year: string;
  doi: string;
  isbn: string;
  study_type: string;
  page_count: number;
  has_text_layer: boolean;
  source: string;
}

export interface MergeStudiesPayload {
  keep_id: number;
  duplicate_id: number;
  preferred_title?: string;
  merge_notes?: boolean;
  merge_highlights?: boolean;
  merge_evidence?: boolean;
  merge_relations?: boolean;
}

export interface ExtractedConcept {
  id: number;
  project_id?: number | null;
  name: string;
  concept_type: 'factor' | 'subfactor' | 'construct' | 'mechanism' | 'outcome';
  description?: string;
  parent_concept_id?: number | null;
  created_at: string;
  evidence_count?: number;
}

export interface ConceptEvidence {
  id: number;
  concept_id: number;
  entry_id: number;
  quote: string;
  page_number?: number | null;
  note?: string;
  study_title?: string;
  created_at: string;
}

export interface ExternalStudyResult {
  title: string;
  authors: string;
  year: number | null;
  doi: string;
  pdf_url: string;
  source: string;
  snippet: string;
  is_oa: boolean;
  already_in_library?: boolean;
  existing_id?: number | null;
}

export interface ExternalSearchResponse {
  query: string;
  results: ExternalStudyResult[];
  total: number;
}

export interface ResolvedStudyMetadata {
  title: string;
  authors: string;
  year: number | null;
  doi: string;
  url: string;
  pdf_url: string;
  abstract: string;
  source: string;
  study_type: string;
  is_oa: boolean;
}

export interface BulkImportResponse {
  imported: {
    id: number;
    title: string;
    authors?: string;
    year?: number | null;
    doi?: string;
    pdf_url?: string;
    source?: string;
    pdf_downloaded: boolean;
    pdf_index_failed: boolean;
  }[];
  skipped: {
    title: string;
    doi?: string;
    reason: string;
    existing_id?: number;
  }[];
  unresolved_identifiers?: { identifier: string; reason: string }[];
  total_imported: number;
  total_skipped: number;
  pdfs_downloaded: number;
  pdfs_failed: number;
  pdfs_index_failed: number;
}

export interface StudyNote {
  id: number;
  entry_id: number;
  course_context: string;
  content: string;
  page_number?: number | null;
  page_verified?: boolean;
  created_at: string;
  updated_at: string;
  sources?: StudySource[];
  study_title?: string;
  study_authors?: string;
  study_date?: string;
  pdf_path?: string;
}

export interface StudySource {
  document_id: number;
  entry_id: number;
  chunk_index: number;
  page_number: number | null;
  page_verified: boolean;
  preview: string;
}

export interface Relationship {
  id: number;
  from_id: number;
  to_id: number;
  label: string;
  created_at: string;
}

export interface Tag {
  id: number;
  name: string;
  color: string;
}

export interface TimelineEntry {
  id: number;
  title: string;
  entry_date: string | null;
  tags: string;
  importance: number;
  relationship_count: number;
  has_relationships?: boolean;
  content_preview: string;
}

export interface TimelineStats {
  earliest: string | null;
  latest: string | null;
  total: number;
}

/** One period of the timeline density strip (a century, decade or year). */
export interface TimelineBucket {
  /** Century number ("20"), decade ("1990") or year ("1998"). */
  key: string;
  count: number;
  start: string;
  end: string;
  top_tags: string[];
}

export interface TimelineBuckets {
  bucket: 'century' | 'decade' | 'year';
  total: number;
  max_count: number;
  buckets: TimelineBucket[];
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphNode {
  id: number;
  label: string;
  title: string;
  value: number;
  color: string;
  group: string;
  entry_date: string | null;
  tags: string;
  content_preview: string;
  /** Number of visible relationships of this entry. */
  degree: number;
  /** True when no relationship touches this entry. */
  isolated: boolean;
  /** Entry kind ('' for plain notes, 'concept' for AI-extracted factors). */
  study_type?: string;
  /** For concept nodes: factor | subfactor | construct | mechanism | outcome. */
  concept_type?: string;
}

export interface GraphEdge {
  id: number;
  from: number;
  to: number;
  /** Canonical relation code (relates_to, precedes, includes, ...). */
  label: string;
  /** Short Romanian text shown next to the edge. */
  label_display: string;
  /** Free detail kept from the original label ("A -> B"), may be empty. */
  detail: string;
  color: string;
  dashes: boolean | number[];
  arrows: string;
}

/** Counts per canonical relation type, for the graph legend and filter. */
export interface RelationLabel {
  code: string;
  display: string;
  count: number;
  color: string;
  dashes: boolean | number[];
}

export interface SpacedRepetitionItem {
  id: number;
  entry_id: number;
  ease_factor: number;
  interval: number;
  repetitions: number;
  next_review: string | null;
  quiz_enabled: boolean;
  question_count: number;
  // Joined from entries so review cards show real titles without a
  // separate 500-entry download.
  entry_title?: string;
  content_preview?: string;
}

export interface TodoItem {
  id: number;
  entry_id: number;
  due_date: string | null;
  completed: boolean;
  priority: string;
  effort: string;
  // Joined from entries (see SpacedRepetitionItem).
  entry_title?: string;
  content_preview?: string;
}

export interface Reminder {
  id: number;
  entry_id: number;
  title: string;
  trigger_at: string;
  reminder_type: string;
  recurrence: string | null;
  notified: boolean;
}

export interface Thought {
  id: number;
  name: string;
  description: string;
  tag_ids: string;
  created_at: string;
}

// Chat types
export interface ChatHistoryItem {
  role: string;
  content: string;
  provider: string;
  model: string;
  created_at: string;
}

export interface ProviderHealth {
  deepseek: { available: boolean; latency_ms: number };
  ollama: { available: boolean; latency_ms: number };
  active: string;
}

export interface ChatAction {
  type: string;
  [key: string]: any;
}

export interface ChatTask {
  id: number;
  kind: string;
  goal: string;
  status: 'active' | 'completed' | 'failed' | 'cancelled';
  details: string;
  progress: Record<string, any>;
  created_at: string;
  updated_at: string;
}

// Research / Studies types
export interface ResearchStudy {
  title: string;
  authors: string;
  year: number | null;
  study_type: string;
  url: string;
  url_validated: boolean;
  design: string;
  population: string;
  sample_size: string;
  effect_direction: string;
  effect_magnitude: string;
  risk_of_bias: Record<string, string>;
  methodology: string;
  key_findings: string[];
  quality: number;
  quality_notes: string;
  relevance: string;
  tags: string;
  entry_id: number | null;
}

export interface ResearchResult {
  topic: string;
  summary: string;
  synthesis: string;
  protocol: {
    research_question: string;
    pico: Record<string, string>;
    inclusion_criteria: string[];
    exclusion_criteria: string[];
    search_strings: string[];
    databases: string[];
  };
  prisma: {
    records_identified: number;
    records_screened: number;
    full_text_assessed: number;
    studies_included: number;
  };
  heterogeneity: string;
  publication_bias: string;
  confidence: string;
  themes: string[];
  gaps: string[];
  recommendations: string[];
  thematic_links: { from_title: string; to_title: string; label: string }[];
  theories: {
    name: string;
    description: string;
    parent: string | null;
    studies: { title: string; stance: string; note: string }[];
  }[];
  studies: ResearchStudy[];
  entries_created: number[];
  relationships_created: number;
  report_entry_id: number | null;
}

// Theory Aggregator
export type TheoryStatus = 'supported' | 'contested' | 'refuted' | 'emerging'

export interface Theory {
  id: number;
  name: string;
  description: string;
  parent_id: number | null;
  status: TheoryStatus;
  created_at: string;
  updated_at: string;
  // Present on leaves created as claims (node_type='claim'); absent on plain
  // theories, so they stay optional instead of pretending to be empty.
  node_type?: 'theory' | 'claim';
  statement?: string;
  test_variable?: string;
  test_condition?: string;
}

// Study QA + health check
export interface StudyQASource {
  document_id: number;
  entry_id: number;
  chunk_index: number;
  page_number: number | null;
  page_verified: boolean;
  preview: string;
  score: number;
}

export interface StudySourcesResponse {
  entry_id: number;
  title: string;
  sources: StudySource[];
}

export interface StudyQAResponse {
  entry_id: number;
  title: string | null;
  question: string;
  answer: string;
  sources: StudyQASource[];
}

export interface StudyCheckResponse {
  entry_id: number;
  title: string | null;
  is_study: boolean;
  issues: string[];
  suggestions: string[];
  indexed_chunks: number;
}

export interface TheoryNode {
  id: number;
  name: string;
  description: string;
  parent_id: number | null;
  status: TheoryStatus;
  pro_count: number;
  contra_count: number;
  pro_weight: number;
  contra_weight: number;
  children: TheoryNode[];
}

export interface TheoryEvidence {
  id: number;
  theory_id: number;
  entry_id: number;
  entry_title: string;
  stance: 'pro' | 'contra';
  note: string;
  weight: number;
  created_at: string;
}

// Data integrity / re-analysis (Review tab)
export interface MaintenanceReport {
  entries_scanned: number;
  entries_fixed: number;
  dates_fixed: number;
  tags_fixed: number;
  fts_rebuilt: boolean;
  relationships_removed: number;
  self_links_removed: number;
  pdfs_found: number;
  pdfs_reanalyzed: number;
  pdfs_unreadable: number;
  pdf_errors: number;
  evidence_removed: number;
  theories_updated: number;
  undated_entries: number;
  entries_active: number;
  relationships_count: number;
  theories_count: number;
  warnings: string[];
}

/** Result of the tag-only repair endpoint. */
export interface TagCleanupReport {
  dry_run: boolean;
  entries_scanned: number;
  entries_changed: number;
  tags_missing_from_table: number;
  tags_inserted: number;
  samples: { id: number; title: string; before: string; after: string }[];
}

// ── Thesis project (the app's centre) ──
export interface ProjectSection {
  id: string;
  title: string;
  target_words: number;
  description: string;
  content: string;
}

/** The research plan: what a thesis needs beyond its outline. */
export interface ProjectPlan {
  participants?: string;
  instruments?: string;
  hypotheses?: string;
  variables?: string;
  analysis?: string;
  ethics?: string;
  schedule?: string;
  risks?: string;
  // The server sends plan fields dynamically; indexing by a key from
  // `plan_map` must be type-safe.
  [key: string]: string | undefined;
}

/** Which chapter carries each research-plan field (computed by the server). */
export interface ProjectPlanMap {
  by_key: Record<string, string>;
  sections: Record<string, string[]>;
  unmapped: string[];
  filled: number;
  total: number;
}

export interface Project {
  id: number;
  title: string;
  work_type: string;
  field: string;
  description: string;
  research_q: string;
  methodology: string;
  deadline: string;
  structure: ProjectSection[];
  plan?: ProjectPlan;
  plan_map?: ProjectPlanMap;
  created_at: string;
  updated_at: string;
}

export interface ProjectEvidence {
  id: number;
  entry_id: number;
  study_title: string;
  study_authors: string;
  relation: 'supports' | 'contradicts' | 'contextualizes';
  document: StudySource | null;
  note: { note_id: number; page_number: number | null; content: string; sources: StudySource[] } | null;
  created_at: string;
}

export interface ProjectClaim {
  id: number;
  section_id: string;
  section_title?: string;
  text: string;
  created_at: string;
  updated_at: string;
  evidence: ProjectEvidence[];
}

export interface ProjectEvidenceSection {
  id: string;
  title: string;
  description: string;
  target_words: number;
  written_words: number;
  claims: ProjectClaim[];
}

export interface ProjectEvidenceMap {
  sections: ProjectEvidenceSection[];
  orphan_claims: ProjectClaim[];
  progress: {
    written_words: number;
    target_words: number;
    writing_percent: number;
    sections_with_claims: number;
    sections_with_evidence: number;
    total_sections: number;
    claims_with_evidence: number;
    claims_with_pdf_fragment: number;
    total_claims: number;
    evidence_percent: number | null;
  };
}

export interface StudyThesisEvidenceLink {
  evidence_id: number;
  relation: 'supports' | 'contradicts' | 'contextualizes';
  claim_id: number;
  claim: string;
  section_id: string;
  section_title: string;
  document: {
    document_id: number;
    entry_id: number;
    chunk_index: number;
    page_number: number | null;
    page_verified: boolean;
    preview: string;
  } | null;
  note_id: number | null;
  note_preview: string | null;
}

export interface ProjectStudyRecommendation {
  entry_id: number;
  title: string;
  year: string;
  study_type: string;
  design: string;
  quality: number | null;
  effect_direction: string;
  authors: string;
  has_pdf: boolean;
  score: number;
  matched_terms: string[];
  reason: string;
}

export interface ProjectStudiesResponse {
  terms: string[];
  studies: ProjectStudyRecommendation[];
  total_library: number;
}

export interface ProjectAnalysisStats {
  library_studies: number;
  matching_studies: number;
  studies_with_pdf: number;
  studies_with_doi: number;
  effect_directions: Record<string, number>;
  designs: Record<string, number>;
}

export interface ProjectAnalysisResponse {
  analysis: string;
  stats: ProjectAnalysisStats;
  from_llm: boolean;
  top_studies: ProjectStudyRecommendation[];
  /** true when the text came from storage instead of a fresh model call */
  cached?: boolean;
  /** true when the project changed since the text was generated */
  stale?: boolean;
  generated_at?: string;
  /** generation costs an LLM call: the screen asks before spending it */
  needs_generation?: boolean;
}

export interface ReadinessItem {
  area: string;
  state: 'ready' | 'partial' | 'missing';
  have: string;
  missing: string;
  action: string;
  do_first: boolean;
}

export interface ProjectReadinessResponse {
  items: ReadinessItem[];
  summary: {
    ready: number;
    partial: number;
    missing: number;
    total: number;
    score: number;
    verdict: string;
    next: ReadinessItem | null;
  };
  obligations: { level: string; message: string; action: string }[];
}

export interface ProjectSchemaPillar {
  tag: string;
  count: number;
}

export interface ProjectSchemaResponse {
  question: string;
  title: string;
  work_type: string;
  pillars: ProjectSchemaPillar[];
  theories: { id: number; name: string; status: string }[];
  evidence_links: { theory: string; stance: string; n: number }[];
  designs: { design: string; count: number }[];
  top_studies: ProjectStudyRecommendation[];
  variables: { independent: string; instruments: string; analysis: string };
  gaps: ReadinessItem[];
}

export interface Idea {
  id: number;
  text: string;
  category: string;
  section_id: string;
  created_at: string;
}

export interface TopicCoverage {
  terms: string[];
  matched: number;
  with_doi: number;
  with_pdf: number;
  with_body: number;
  designs: string[];
  years: string[];
  verdict: 'solidă' | 'posibilă' | 'fragilă' | 'nepotrivită';
  note: string;
  top_titles: string[];
  matched_terms: string[];
}

export interface TopicCandidate {
  title: string;
  question: string;
  source: 'seed' | 'current' | 'ai_brainstorm';
  coverage: TopicCoverage;
  rationale?: string;
  variables?: string;
  pico?: string;
}

export interface BrainstormTopicItem {
  title: string;
  question: string;
  rationale: string;
  variables: string;
  pico: string;
  source: 'ai_brainstorm';
  coverage: TopicCoverage;
}

export interface TopicBrainstormResponse {
  topics: BrainstormTopicItem[];
  from_llm: boolean;
  count: number;
}

export interface TopicsResponse {
  candidates: TopicCandidate[];
  current: { title: string; research_q: string };
  ideas: { text: string; category: string }[];
}

export interface SourcesStatus {
  studies: number;
  without_doi: number;
  without_any_source: number;
  thin: number;
  without_doi_titles: { id: number; title: string; authors: string; year: string }[];
  thin_titles: { id: number; title: string; chars: number }[];
}

export interface SourceRunResult {
  ok: boolean;
  checked: number;
  dry_run: boolean;
  note: string;
  found: {
    id: number; catalog: string; proposed: string; doi: string;
    crossref_title: string; year: number | null; confidence: string;
    written?: boolean;
  }[];
  missed: { id: number; catalog: string; proposed?: string; why: string }[];
  written: { id: number; title: string; chars: number }[];
  skipped: { id: number; title: string; why: string }[];
}

export interface TitleCheck {
  level: 'critical' | 'warning' | 'info';
  message: string;
  action: string;
}

export interface TitleAnalysis {
  title: string;
  research_q: string;
  stats: {
    words: number;
    chars: number;
    vague: string[];
    population: string[];
    has_question_mark: boolean;
    acronyms: string[];
    has_colon: boolean;
  };
  checks: TitleCheck[];
  score: number;
  verdict: string;
  review: string | null;
  from_llm?: boolean;
  note?: string;
}

export interface FeedbackItem {
  level: 'critical' | 'warning' | 'info';
  message: string;
  action: string;
}

export interface ProjectFeedbackResponse {
  items: FeedbackItem[];
  reviewer: string | null;
  from_llm: boolean;
  /**
   * The GET returns the structural items instantly and leaves the reviewer's
   * read to an explicit POST; older backends still fill `reviewer` in the GET.
   */
  needs_generation?: boolean;
}

export interface StructureExampleResponse {
  structure: ProjectSection[];
  from_llm: boolean;
  note: string;
}

export interface AiModelInfo {
  id: string;
  name: string;
  context_window: number | null;
  input_modalities: string[];
  /** zero-price model (Go includes these; Console hides them) */
  free?: boolean;
}

/** GET /ai/models: the provider's catalogue plus the model actually in use. */
export interface AiModelsResponse {
  models: AiModelInfo[];
  active: string;
  from_provider: boolean;
  /** false when the env default is an alias the catalogue does not list */
  active_listed?: boolean;
  /** false when the current Console model is free-tier and unavailable to external clients */
  active_supported?: boolean;
  /** zero-price models; Console blocks them, Go includes them */
  free_model_ids?: string[];
  /** free models excluded from this service's catalogue (Console only) */
  blocked_free_model_ids?: string[];
  note?: string;
  env_default?: string;
}

/** GET /ai/status: provider/embedding configuration as the server sees it. */
export interface AiStatusResponse {
  provider_mode: string;
  deepseek_model: string;
  deepseek_model_env_default?: string;
  ollama_model: string;
  configured_embed_model: string;
  active_embed_model: string | null;
  semantic_retrieval: boolean;
  embedding_backend?: string;
  embedding_model_installed: boolean;
  embedding_dimension: number | null;
  available_embed_models: string[];
  pdf_chunks_indexed: number;
}

/** POST /ai/model: the id now in force (empty choice = env default). */
export interface AiModelChoiceResponse {
  active: string;
  cleared: boolean;
}

export interface AiModelChoiceRequest {
  model_id: string;
  /** Save a provider-specific model without switching the active provider. */
  provider?: string;
}

/** One entry of GET /ai/providers. */
export interface AiProviderInfo {
  id: string;
  label: string;
  /** true when the key (or local server) makes it usable right now */
  configured: boolean;
  model: string;
  active: boolean;
}

/** GET /ai/providers: every provider known, the active one, the failover order. */
export interface AiProvidersResponse {
  providers: AiProviderInfo[];
  active: string;
  order: string[];
  configured: string[];
}

export interface AiProviderChoiceResponse {
  active: string;
  cleared: boolean;
}

/** GET /ai/custom — the saved endpoint; the key itself is never returned. */
export interface AiCustomProviderState {
  configured: boolean;
  base_url?: string;
  label?: string;
  model?: string;
  has_key?: boolean;
}

/** POST /ai/custom/models — what the endpoint says it serves. */
export interface AiCustomModelsResponse {
  url: string;
  models: { id: string; owned_by?: string }[];
  count: number;
}

export interface AiCustomSaveRequest {
  base_url: string;
  api_key?: string;
  model?: string;
  label?: string;
  activate?: boolean;
}

/** OpenCode native HTTP settings; has_password reflects env presence, never its value. */
export interface AiOpenCodeConfigState {
  configured: boolean;
  base_url?: string;
  directory?: string;
  has_password?: boolean;
  api_protocol?: string;
}

export interface AiOpenCodeSaveRequest {
  base_url: string;
  directory: string;
}

/** OpenCode Console inference settings; the Bearer key itself is never returned. */
export interface AiOpenCodeConsoleConfigState {
  configured: boolean;
  base_url: string;
  has_key: boolean;
}

export interface AiOpenCodeConsoleSaveRequest {
  base_url: string;
  api_key?: string;
  clear_key?: boolean;
}

export interface AiTestConnectionRequest {
  provider: string;
  model?: string;
}

export interface AiTestConnectionResponse {
  ok: boolean;
  provider: string;
  model: string;
  latency_ms: number;
  message: string;
  error?: string;
}

export interface BibliographyReference {
  entry_id: number;
  title: string;
  authors: string;
  year: string;
  doi: string;
  apa: string;
  in_text?: string;
  cited: boolean;
  has_doi: boolean;
}

export interface BibliographyResponse {
  total: number;
  cited_count: number;
  references: BibliographyReference[];
}

export interface PdfHighlight {
  id: number;
  entry_id: number;
  document_id: number | null;
  page_number: number | null;
  quote: string;
  note: string;
  created_at: string;
}

export interface GlobalSearchNote {
  id: number;
  entry_id: number;
  content: string;
  page_number: number | null;
  course_context: string;
  entry_title: string;
}

export interface GlobalSearchHighlight {
  id: number;
  entry_id: number;
  quote: string;
  note: string;
  page_number: number | null;
  entry_title: string;
}

export interface GlobalSearchChunk {
  id: number;
  entry_id: number;
  chunk_index: number;
  page_number: number | null;
  preview: string;
  entry_title: string;
  score?: number;
}

export interface GlobalSearchResponse {
  query: string;
  total: number;
  entries: { id: number; title: string; entry_date: string | null; tags: string; importance: number; content_preview: string }[];
  studies: { id: number; title: string; authors: string; entry_date: string | null; doi: string; pdf_path: string; quality: number | null; study_type: string }[];
  notes: GlobalSearchNote[];
  highlights: GlobalSearchHighlight[];
  chunks: GlobalSearchChunk[];
}

export interface ScreeningSummary {
  studies_total: number;
  included: number;
  excluded: number;
  maybe: number;
  undecided: number;
}

export interface ScreeningDecisionRow {
  entry_id: number;
  decision: 'include' | 'exclude' | 'maybe';
  reason: string;
  updated_at: string;
}
export interface QuestionnaireItem {
  id: string
  text: string
  type: string
  options: string[]
  subscale?: string
}

export interface Questionnaire {
  id: number
  project_id?: number
  title: string
  description: string
  construct: string
  target_population: string
  reliability_notes: string
  items: QuestionnaireItem[]
  created_at: string
  updated_at: string
}

export interface ProjectSummary {
  id: number
  title: string
  work_type: string
  field: string
  updated_at: string
}

export interface ZoteroStatus {
  installed: boolean;
  path?: string;
  storage_path?: string;
  item_count?: number;
  has_database?: boolean;
  has_pdfs?: boolean;
  pdf_count?: number;
  message?: string;
}

export interface ZoteroImportResult {
  imported_count: number;
  skipped_count: number;
  pdfs_copied: number;
  errors?: string[];
  skipped?: { title: string; doi: string; existing_id: number; reason: string }[];
}

// ── Claims, stance edges, note checks, page parse (slice 10a) ────────────────
// Mirrors backend/app/routers/claims.py, analysis.py, note_check.py and
// studies_parse.py. Response shapes follow the routers, not the design sketch:
// where they differ the router wins, because that is what arrives over HTTP.

export type StanceDirection = 'supports' | 'contradicts' | 'mixed' | 'neutral';
export type StanceStatus = 'proposed' | 'accepted' | 'rejected' | 'insufficient';
/** Verdict computed from accepted stance edges; `insufficient` means "not enough studies". */
export type ClaimVerdict = StanceDirection | 'insufficient';

/** One stance edge: a study's verdict on one claim, pinned to a page quote. */
export interface ClaimStance {
  id: number;
  claim_id: number;
  entry_id: number;
  /** Added by the router's JOIN on entries.title. */
  entry_title?: string;
  direction: StanceDirection;
  aspect: string;
  highlight_id?: number | null;
  evidence_page?: number | null;
  evidence_start?: number | null;
  evidence_end?: number | null;
  evidence_quote: string;
  reasoning: string;
  confidence?: number | null;
  status: StanceStatus;
  proposed_by: 'ai' | 'user';
  provider?: string;
  model?: string;
  created_at: string;
  decided_at?: string | null;
}

/** Body for POST /claims/{id}/stances (backend ClaimStanceIn). */
export interface ClaimStanceInput {
  entry_id: number;
  direction: StanceDirection;
  aspect?: string;
  highlight_id?: number | null;
  evidence_page?: number | null;
  evidence_start?: number | null;
  evidence_end?: number | null;
  evidence_quote?: string | null;
  reasoning?: string;
  confidence?: number | null;
  proposed_by?: 'ai' | 'user';
  provider?: string;
  model?: string;
}

/**
 * GET /claims/{id}/stances. Without `status` the router returns all four keys;
 * with `status` it returns only that key — hence every bucket is optional.
 */
export interface ClaimStancesResponse {
  claim_id: number;
  proposed?: ClaimStance[];
  accepted?: ClaimStance[];
  rejected?: ClaimStance[];
  insufficient?: ClaimStance[];
}

/** One accepted study inside a claim aggregate, as returned by claims.py. */
export interface ClaimAggregateEntry {
  entry_id: number;
  entry_title?: string;
  verdict: ClaimVerdict;
  accepted_count: number;
  /** Every accepted direction for this study (repeats allowed). */
  directions: StanceDirection[];
}

/** One study inside the analysis rollup (app/stance/aggregate.py claim_rollup). */
export interface ClaimAggregateStudyRef {
  entry_id: number;
  entry_title?: string;
  verdict: ClaimVerdict;
  sides?: StanceDirection[];
}

/**
 * Aggregate over accepted edges. Two routers produce it:
 * - GET /claims/{id}/aggregate → `per_entry` + `claim_verdict` (no confident/distribution);
 * - the analysis rollup → `per_study` + `distribution` + `confident` + `explanation`.
 * `verdict` is normalised by `api.claims.aggregate` from `claim_verdict`.
 */
export interface ClaimAggregate {
  claim_id: number;
  claim_name?: string;
  verdict: ClaimVerdict;
  claim_verdict?: ClaimVerdict;
  studies_accepted: number;
  /** Analysis rollup only. */
  confident?: boolean;
  /** Analysis rollup only: how many studies landed in each verdict bucket. */
  distribution?: Record<ClaimVerdict, number>;
  per_entry?: ClaimAggregateEntry[];
  per_study?: ClaimAggregateStudyRef[];
  explanation?: string;
}

/** Raw body of GET /claims/{id}/aggregate (before normalisation). */
export interface ClaimAggregateResponse {
  claim_id: number;
  claim_name?: string;
  per_entry: ClaimAggregateEntry[];
  claim_verdict: ClaimVerdict;
  studies_accepted: number;
}

/** One scored passage from POST /claims/{id}/analyze. */
export interface ClaimAnalysisEvidence {
  highlight_id: number;
  page: number;
  start: number;
  end: number;
  quote: string;
  windows_scored: number;
  any_assertive: boolean;
  strong: boolean;
}

export interface ClaimAnalysisSkipped {
  reason: string;
  highlight_id?: number;
}

export interface ClaimAnalysisNli {
  available: boolean;
  scorer?: string;
  model_dir?: string;
  reason?: string;
}

/** Study-level dry-run block of the analysis panel (no edges are written). */
export interface ClaimAnalysisStudy {
  verdict: ClaimVerdict;
  directions: StanceDirection[];
  strong_windows: number;
  source: string;
}

export interface ClaimAnalysis {
  claim_id: number;
  entry_id: number;
  evidence: ClaimAnalysisEvidence[];
  skipped: ClaimAnalysisSkipped[];
  nli: ClaimAnalysisNli;
  aggregate: {
    study: ClaimAnalysisStudy;
    claim: ClaimAggregate;
  };
}

export interface AnalysisCapabilities {
  nli: {
    available: boolean;
    scorer?: string;
    reason?: string;
    expected?: string;
    model_dir?: string;
  };
  llm_rationale: { available: boolean; reason: string };
}

// ── Verified notes ──────────────────────────────────────────────────────────

export type NoteCheckStatus =
  | 'verificat'
  | 'verificat_cu_nuanta'
  | 'incomplet'
  | 'subestimat'
  | 'corectat'
  | 'depasit'
  | 'neverificat';

/** A stored note_checks row (GET /studies/{id}/note-checks, PATCH /note-checks/{id}). */
export interface NoteCheck {
  id: number;
  note_id: number;
  entry_id: number;
  statement: string;
  status: NoteCheckStatus;
  page_pdf?: number | null;
  page_printed?: string;
  quote: string;
  correction: string;
  method: string;
  confidence?: number | null;
}

/** A statement from the dry check: no row id until it has been applied. */
export interface NoteCheckStatement {
  statement: string;
  status: NoteCheckStatus;
  page_pdf?: number | null;
  page_printed?: string;
  quote: string;
  correction: string;
  method: string;
  confidence?: number | null;
  coverage?: number;
  /** Present only when the backend echoed a stored row. */
  id?: number;
  note_id?: number;
  entry_id?: number;
}

export interface NoteCheckResult {
  note_id: number;
  statements: NoteCheckStatement[];
  summary: { verificat: number; neverificat: number; total: number };
}

export interface NoteCheckListRow extends NoteCheck {
  /** Added by the router's JOIN on study_notes. */
  note_content?: string;
  course_context?: string;
  created_at?: string;
}

/**
 * GET /studies/{id}/note-checks. The router returns `statements` plus the same
 * rows bucketed by status; it does not repeat `entry_id` in the body.
 */
export interface NoteCheckList {
  statements: NoteCheckListRow[];
  by_status: Record<NoteCheckStatus, NoteCheckListRow[]>;
}

export interface VerifySummary {
  statements: number;
  by_status: Record<string, number>;
  notes_total: number;
  notes_fully_verified: number;
  pages_with_printed_numbers: number;
  pages_total: number;
}

// ── Per-page document parse ─────────────────────────────────────────────────

export interface ParseSummaryPage {
  page: number;
  kind: string;
  source: string;
  status: string;
  chars: number;
}

export interface ParseSummary {
  entry_id: number;
  pdf_path: string;
  kind: 'native' | 'scanned' | 'mixed' | 'empty' | '';
  pages: number;
  pages_native: number;
  pages_scanned: number;
  pages_empty: number;
  parsed: {
    native: number;
    vision_ok: number;
    vision_uncertain: number;
    manual: number;
    error: number;
    missing: number;
  };
  per_page: ParseSummaryPage[];
}

export interface PageTextResponse {
  page: number;
  text: string;
  source: string;
  status: string;
  char_count: number;
  /** Stored on page_texts but not part of the router's response model today. */
  printed_page?: string;
}
