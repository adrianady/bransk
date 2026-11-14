"""
Bransk — Pydantic models for API request/response.
"""
import re
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

# A DOI is "10.<registrant>/<suffix>". Accept it with or without the
# https://doi.org/ prefix, strip the prefix, and refuse anything else rather
# than storing a value that will resolve to a different paper.
_DOI_RE = re.compile(r"^10\.\d{4,9}/[^\s]+$")
_DOI_PREFIX_RE = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/", re.IGNORECASE)


def normalize_doi(value: str) -> str:
    """Return the bare DOI, or raise ValueError when it is not one."""
    text = (value or "").strip()
    if not text:
        return ""
    text = _DOI_PREFIX_RE.sub("", text).strip()
    if not _DOI_RE.match(text):
        raise ValueError(
            "A DOI looks like 10.1038/nrn.2016.44 (optionally as https://doi.org/10.1038/nrn.2016.44)"
        )
    return text


# ── Entry models ──
class EntryCreate(BaseModel):
    # extra="forbid": a misspelled field (e.g. `url=` instead of `source=`)
    # used to be silently dropped, losing data without any error.
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=500)
    content: str = ""
    entry_date: Optional[str] = None  # ISO date or None
    tags: str = ""
    source: str = ""
    importance: int = Field(1, ge=1, le=5)
    study_type: str = ""
    authors: str = ""
    quality: Optional[int] = Field(None, ge=1, le=5)
    url_validated: bool = False
    design: str = ""
    effect_direction: str = ""
    pdf_path: str = ""
    # A real bibliographic identifier (e.g. 10.1038/nrn.2016.44). Kept apart
    # from `source`, whose URL is often wrong. Validated at the model level so a
    # typo can never become a DOI that resolves to a different paper.
    doi: str = ""

    @field_validator("doi")
    @classmethod
    def _check_doi(cls, value):
        return normalize_doi(value)

    read_status: str = "unread"
    isbn: str = ""

    @field_validator("read_status")
    @classmethod
    def _check_read_status(cls, value):
        allowed = {"unread", "reading", "read", "annotated"}
        text = (value or "").strip().lower()
        if text and text not in allowed:
            raise ValueError(f"read_status must be one of {sorted(allowed)}")
        return text or "unread"

    @field_validator("isbn")
    @classmethod
    def _check_isbn(cls, value):
        return (value or "").strip()[:40]


class EntryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    entry_date: Optional[str] = None
    tags: Optional[str] = None
    source: Optional[str] = None
    importance: Optional[int] = Field(None, ge=1, le=5)
    archived: Optional[bool] = None
    study_type: Optional[str] = None
    authors: Optional[str] = None
    quality: Optional[int] = Field(None, ge=1, le=5)
    url_validated: Optional[bool] = None
    design: Optional[str] = None
    effect_direction: Optional[str] = None
    pdf_path: Optional[str] = None
    doi: Optional[str] = None
    read_status: Optional[str] = None
    isbn: Optional[str] = None

    @field_validator("doi")
    @classmethod
    def _check_doi(cls, value):
        return normalize_doi(value) if value is not None else value

    @field_validator("read_status")
    @classmethod
    def _check_read_status(cls, value):
        if value is None:
            return value
        allowed = {"unread", "reading", "read", "annotated"}
        text = str(value).strip().lower()
        if text not in allowed:
            raise ValueError(f"read_status must be one of {sorted(allowed)}")
        return text

    @field_validator("isbn")
    @classmethod
    def _check_isbn(cls, value):
        return value.strip()[:40] if isinstance(value, str) else value


class EntryResponse(BaseModel):
    id: int
    title: str
    # Full content comes from the detail endpoint; the list view projects a
    # short `content_preview` instead to keep the payload small.
    content: str = ""
    content_preview: str = ""
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
    # Real bibliographic identifiers (resolved against Crossref), kept apart
    # from the unreliable `source` URL the research pipeline produced.
    doi: str = ""
    doi_source: str = ""
    doi_checked_at: str = ""
    bib_title: str = ""
    bib_authors: str = ""
    bib_year: str = ""
    pdf_hash: str = ""
    read_status: str = "unread"
    isbn: str = ""


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
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field("#6366f1", max_length=20)


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
    has_relationships: bool = False
    content_preview: str = ""


class TimelineResponse(BaseModel):
    entries: list[TimelineEntry]
    total: int
    from_date: Optional[str] = None
    to_date: Optional[str] = None


# ── Chat models ──
class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    # Long AI research replies can exceed the old 10k cap; keep the validation
    # generous and let the LLM-assembly step truncate per-message context.
    content: str = Field(..., max_length=100000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=100000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=100)
    entry_id: Optional[int] = None
    page: Optional[int] = None


class ChatResponse(BaseModel):
    reply: str


class ConfirmRequest(BaseModel):
    confirm_id: str = Field(..., min_length=1, max_length=64)
    approve: bool = True


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
    # Joined from entries so the UI can render titles without downloading the
    # whole entries list just to label a review item.
    entry_title: str = ""
    content_preview: str = ""


class ReviewSubmit(BaseModel):
    quality: int = Field(..., ge=0, le=5)


# ── Todo models ──
class TodoCreate(BaseModel):
    due_date: Optional[str] = None
    priority: str = Field("normal", pattern="^(critical|important|normal)$")
    effort: str = Field("m", pattern="^(s|m|l|xl)$")


class TodoUpdate(BaseModel):
    due_date: Optional[str] = None
    completed: Optional[bool] = None
    priority: Optional[str] = Field(None, pattern="^(critical|important|normal)$")
    effort: Optional[str] = Field(None, pattern="^(s|m|l|xl)$")


class TodoResponse(BaseModel):
    id: int
    entry_id: int
    due_date: Optional[str] = None
    completed: bool
    priority: str
    effort: str
    # Joined from entries so the todo list shows real titles instead of
    # "Intrarea nr. X" beyond the first 500 entries.
    entry_title: str = ""
    content_preview: str = ""


# ── Reminder models ──
class ReminderCreate(BaseModel):
    title: str = ""
    trigger_at: str = Field(..., min_length=1, max_length=40)
    reminder_type: str = Field("once", pattern="^(once|daily|weekly|monthly)$")
    recurrence: Optional[str] = Field(None, pattern="^(daily|weekly|monthly)$")


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


class ScreeningDecisionInput(BaseModel):
    decision: str = Field(..., pattern="^(include|exclude|maybe)$")
    reason: str = Field("", max_length=1000)


class ScreeningSummary(BaseModel):
    studies_total: int = 0
    included: int = 0
    excluded: int = 0
    maybe: int = 0
    undecided: int = 0


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


# ── Thesis project feedback ──
class ProjectFeedbackItem(BaseModel):
    level: str = ""
    message: str = ""
    action: str = ""


class ProjectFeedback(BaseModel):
    """The Feedback tab: rule-based checks plus the reviewer text, once generated.

    `reviewer`, `generated_at`, `cached` and `needs_generation` are optional and
    defaulted so a client that only reads `items`/`reviewer` keeps working: GET
    never carries a reviewer any more (it answers without an LLM call), POST
    either returns fresh text or the stored one.
    """
    items: list[ProjectFeedbackItem] = Field(default_factory=list)
    reviewer: Optional[str] = None
    from_llm: bool = False
    generated_at: Optional[str] = None
    cached: bool = False
    needs_generation: bool = True


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
    pro_weight: int = 0
    contra_weight: int = 0
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


# ── Claim hierarchy + stance edges (slice 5) ──
# Claims are leaves of the theories tree (node_type='claim'). Inputs keep
# plain types on purpose: range/enum checks happen in the router so the 422
# messages can stay in Romanian.


class ClaimCreate(BaseModel):
    parent_id: int
    name: str = Field(..., min_length=1, max_length=300)
    statement: str = Field(..., min_length=1)
    test_variable: str = ""
    test_condition: str = ""


class ClaimStanceIn(BaseModel):
    entry_id: int
    direction: str
    aspect: str = ""
    highlight_id: Optional[int] = None
    evidence_page: Optional[int] = None
    evidence_start: Optional[int] = None
    evidence_end: Optional[int] = None
    evidence_quote: Optional[str] = None
    reasoning: str = ""
    confidence: Optional[float] = None
    proposed_by: str = "ai"
    provider: str = ""
    model: str = ""


class ClaimStancePatch(BaseModel):
    action: Optional[str] = None
    direction: Optional[str] = None
    aspect: Optional[str] = None
    highlight_id: Optional[int] = None
    evidence_page: Optional[int] = None
    evidence_start: Optional[int] = None
    evidence_end: Optional[int] = None
    evidence_quote: Optional[str] = None
    reasoning: Optional[str] = None
    confidence: Optional[float] = None


# ── Document parse models (per-page vision transcription over HTTP) ──
class ParseSummaryParsed(BaseModel):
    native: int = 0
    vision_ok: int = 0
    vision_uncertain: int = 0
    manual: int = 0
    error: int = 0
    missing: int = 0


class ParseSummaryPage(BaseModel):
    page: int
    kind: str = ""
    source: str = ""
    status: str = ""
    chars: int = 0


class ParseSummaryResponse(BaseModel):
    entry_id: int
    pdf_path: str = ""
    kind: str = ""
    pages: int = 0
    pages_native: int = 0
    pages_scanned: int = 0
    pages_empty: int = 0
    parsed: ParseSummaryParsed = Field(default_factory=ParseSummaryParsed)
    per_page: list[ParseSummaryPage] = Field(default_factory=list)


class PageTextResponse(BaseModel):
    page: int
    text: str = ""
    source: str = ""
    status: str = ""
    char_count: int = 0
