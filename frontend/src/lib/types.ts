export interface Entry {
  id: number;
  title: string;
  content: string;
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
  content_preview: string;
}

export interface TimelineStats {
  earliest: string | null;
  latest: string | null;
  total: number;
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
}

export interface GraphEdge {
  id: number;
  from: number;
  to: number;
  label: string;
  arrows: string;
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
}

export interface TodoItem {
  id: number;
  entry_id: number;
  due_date: string | null;
  completed: boolean;
  priority: string;
  effort: string;
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
}

export interface TheoryNode {
  id: number;
  name: string;
  description: string;
  parent_id: number | null;
  status: TheoryStatus;
  pro_count: number;
  contra_count: number;
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
