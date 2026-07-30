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


class EntryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    entry_date: Optional[str] = None
    tags: Optional[str] = None
    source: Optional[str] = None
    importance: Optional[int] = None
    archived: Optional[bool] = None


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


class TimelineResponse(BaseModel):
    entries: list[TimelineEntry]
    total: int
    from_date: Optional[str] = None
    to_date: Optional[str] = None


# ── Chat models ──
class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    history: list[ChatMessage] = []


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
