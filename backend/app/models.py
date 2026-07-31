"""
ChronoKG — Pydantic models for API request/response.
"""
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Entry models ──
class EntryCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = ""
    entry_date: Optional[str] = None  # ISO date or None
    tags: str = ""
    source: str = ""
    importance: int = 1
    study_type: str = ""
    authors: str = ""
    quality: Optional[int] = None
    url_validated: bool = False
    design: str = ""
    effect_direction: str = ""
    pdf_path: str = ""


class EntryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    entry_date: Optional[str] = None
    tags: Optional[str] = None
    source: Optional[str] = None
    importance: Optional[int] = None
    archived: Optional[bool] = None
    study_type: Optional[str] = None
    authors: Optional[str] = None
    quality: Optional[int] = None
    url_validated: Optional[bool] = None
    design: Optional[str] = None
    effect_direction: Optional[str] = None
    pdf_path: Optional[str] = None


class EntryResponse(BaseModel):
    id: int
    title: str
    content: str
    entry_date: Optional[str] = None
    created_at: str
    updated_at: str
    tags: str
    source: str
    importance: int
    archived: bool
    study_type: str = ""
    authors: str = ""
    quality: Optional[int] = None
    url_validated: bool = False
    design: str = ""
    effect_direction: str = ""
    pdf_path: str = ""


# ── Relationship models ──
class RelationshipCreate(BaseModel):
    from_id: int
    to_id: int
    label: str = ""


class RelationshipResponse(BaseModel):
    id: int
    from_id: int
    to_id: int
    label: str
    created_at: str


# ── Tag models ──
class TagCreate(BaseModel):
    name: str
    color: str = "#6366f1"


class TagResponse(BaseModel):
    id: int
    name: str
    color: str


# ── Search models ──
class SearchResult(BaseModel):
    entry: EntryResponse
    rank: float = 0.0


# ── Timeline models ──
class TimelineEntry(BaseModel):
    id: int
    title: str
    entry_date: Optional[str] = None
    tags: str
    importance: int
    relationship_count: int = 0
    content_preview: str = ""


class TimelineResponse(BaseModel):
    entries: list[TimelineEntry]
    total: int
    from_date: Optional[str] = None
    to_date: Optional[str] = None


# ── Chat models ──
class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., max_length=10000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=100)


class ChatResponse(BaseModel):
    reply: str


# ── Spaced Repetition models ──
class SpacedRepetitionItem(BaseModel):
    id: int
    entry_id: int
    ease_factor: float
    interval: int
    repetitions: int
    next_review: Optional[str] = None
    quiz_enabled: bool = True
    question_count: int = 3


class ReviewSubmit(BaseModel):
    quality: int = Field(..., ge=0, le=5)


# ── Todo models ──
class TodoCreate(BaseModel):
    due_date: Optional[str] = None
    priority: str = "normal"
    effort: str = "m"


class TodoUpdate(BaseModel):
    due_date: Optional[str] = None
    completed: Optional[bool] = None
    priority: Optional[str] = None
    effort: Optional[str] = None


class TodoResponse(BaseModel):
    id: int
    entry_id: int
    due_date: Optional[str] = None
    completed: bool
    priority: str
    effort: str


# ── Reminder models ──
class ReminderCreate(BaseModel):
    title: str = ""
    trigger_at: str
    reminder_type: str = "once"
    recurrence: Optional[str] = None


class ReminderResponse(BaseModel):
    id: int
    entry_id: int
    title: str
    trigger_at: str
    reminder_type: str
    recurrence: Optional[str] = None
    notified: bool


# ── Thought models ──
class ThoughtCreate(BaseModel):
    name: str
    description: str = ""
    tag_ids: str = ""


class ThoughtResponse(BaseModel):
    id: int
    name: str
    description: str
    tag_ids: str
    created_at: str


# ── Research / Studies models ──
class ResearchProtocol(BaseModel):
    research_question: str = ""
    pico: dict = Field(default_factory=dict)
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    search_strings: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)


class PrismaFlow(BaseModel):
    records_identified: int = 0
    records_screened: int = 0
    full_text_assessed: int = 0
    studies_included: int = 0


class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=500)
    depth: str = Field("deep", pattern="^(quick|deep)$")
    save: bool = True


class ResearchStudy(BaseModel):
    title: str
    authors: str = ""
    year: Optional[int] = None
    study_type: str = "study"
    url: str = ""
    url_validated: bool = False
    design: str = ""
    population: str = ""
    sample_size: str = ""
    effect_direction: str = ""
    effect_magnitude: str = ""
    risk_of_bias: dict = Field(default_factory=dict)
    methodology: str = ""
    key_findings: list[str] = Field(default_factory=list)
    quality: int = Field(3, ge=1, le=5)
    quality_notes: str = ""
    relevance: str = ""
    tags: str = ""
    entry_id: Optional[int] = None


class ResearchResult(BaseModel):
    topic: str
    summary: str
    synthesis: str
    protocol: ResearchProtocol = Field(default_factory=ResearchProtocol)
    prisma: PrismaFlow = Field(default_factory=PrismaFlow)
    heterogeneity: str = ""
    publication_bias: str = ""
    confidence: str = ""  # high | moderate | low | very low (GRADE-like)
    themes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    thematic_links: list[dict] = Field(default_factory=list)
    theories: list[dict] = Field(default_factory=list)
    studies: list[ResearchStudy] = Field(default_factory=list)
    entries_created: list[int] = Field(default_factory=list)
    relationships_created: int = 0
    report_entry_id: Optional[int] = None


# ── Theory Aggregator models ──
class TheoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    description: str = ""
    parent_id: Optional[int] = None
    status: str = Field("emerging", pattern="^(supported|contested|refuted|emerging)$")


class TheoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[int] = None
    status: Optional[str] = None


class Theory(BaseModel):
    id: int
    name: str
    description: str = ""
    parent_id: Optional[int] = None
    status: str = "emerging"
    created_at: str = ""
    updated_at: str = ""


class TheoryNode(BaseModel):
    id: int
    name: str
    description: str = ""
    parent_id: Optional[int] = None
    status: str = "emerging"
    pro_count: int = 0
    contra_count: int = 0
    children: list["TheoryNode"] = Field(default_factory=list)


class TheoryEvidenceIn(BaseModel):
    entry_id: int
    stance: str = Field(..., pattern="^(pro|contra)$")
    note: str = ""
    weight: int = Field(3, ge=1, le=5)


class TheoryEvidence(BaseModel):
    id: int
    theory_id: int
    entry_id: int
    entry_title: str = ""
    stance: str
    note: str = ""
    weight: int = 3
    created_at: str = ""
