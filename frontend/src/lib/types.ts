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
