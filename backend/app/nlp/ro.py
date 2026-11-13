"""Romanian NLP utilities for ChronoKG.

Centralized stopwords, stem mappings, and regex patterns used across
chat, project, search, and study_search modules.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Set

import difflib

# ──────────────────────────────────────────────────────────────────────────────
# Stopwords (Romanian + English common) — used for keyword extraction, tagging
# ──────────────────────────────────────────────────────────────────────────────
RO_STOPWORDS: Set[str] = {
    # Romanian articles, pronouns, prepositions, conjunctions
    "care", "ceea", "acest", "aceasta", "aceste", "acesti", "acestui", "acestor",
    "sunt", "este", "fost", "fiind", "pentru", "despre", "din", "sau", "dar",
    "iar", "daca", "cand", "unde", "cum", "mai", "foarte", "poate", "prin",
    "intre", "asupra", "dintre", "lui", "unei", "unui", "unor",
    # English common (appear in mixed queries)
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "cat", "cel", "cea", "cei", "cele", "ale", "alor", "sale", "sai",
}

# ──────────────────────────────────────────────────────────────────────────────
# Romanian stem → English concept mapping — used for query translation
# ──────────────────────────────────────────────────────────────────────────────
RO_STEM_MAP: dict[str, str] = {
    "experient": "experience",
    "experien": "experience",
    "advers": "adverse",
    "capacitat": "capacity",
    "capacit": "capacity",
    "mentaliz": "mentalizing",
    "mentalizare": "mentalizing",
    "rezilient": "resilience",
    "rezili": "resilience",
    "econom": "economic",
    "economic": "economic",
    "social": "social",
    "cultural": "cultural",
    "copilar": "childhood",
    "copil": "child",
    "adult": "adult",
    "adul": "adult",
    "trauma": "trauma",
    "depriv": "deprivation",
    "amenint": "threat",
    "factor": "factor",
    "mediu": "environment",
    "implicat": "implication",
    "rezult": "result",
    "efect": "effect",
    "studiu": "study",
    "analiz": "analysis",
    "corelat": "correlation",
    "mediat": "mediation",
    "moder": "moderation",
    "protect": "protection",
    "suport": "support",
    "atașament": "attachment",
    "atasament": "attachment",
    "paren": "parent",
    "părinte": "parent",
    "matern": "maternal",
    "paternal": "paternal",
    "reflexiv": "reflective",
    "funcțion": "functioning",
    "function": "functioning",
    "auto-mentaliz": "self-mentalizing",
    "hetero-mentaliz": "other-mentalizing",
    "epistemic": "epistemic",
    "încreder": "trust",
    "increder": "trust",
    "disoci": "dissociation",
    "trădare": "betrayal",
    "tradare": "betrayal",
    "neglij": "neglect",
    "abuz": "abuse",
    "maltrata": "maltreatment",
    "stres": "stress",
    "cortizol": "cortisol",
    "hipotalam": "hypothalamic",
    "hipofiz": "pituitary",
    "supraren": "adrenal",
    "reglare": "regulation",
    "securiz": "secure",
    "internaliz": "internalizing",
    "externaliz": "externalizing",
    "comportament": "behavior",
    "emoțional": "emotional",
    "emotional": "emotional",
    "problem": "problem",
    "simptom": "symptom",
    "diagnost": "diagnosis",
    "terapi": "therapy",
    "interven": "intervention",
    "preven": "prevention",
    "factor": "factor",
    "risc": "risk",
    "protectiv": "protective",
    "buffer": "buffer",
    "rezilien": "resilience",
}

# ──────────────────────────────────────────────────────────────────────────────
# Regex patterns — all compiled once, reused everywhere
# ──────────────────────────────────────────────────────────────────────────────

# Research intent verbs (English + Romanian, flexible diacritics)
RESEARCH_VERBS_RE = re.compile(
    r"\b("
    r"make|run|do|search|find|add|create|start|gather|compile|collect|research|look up|download|fetch|"
    r"caut[aăâ]?|caut[aăâ]-mi|g[aăâ]se[sșş]te|g[aăâ]se[sșş]te-mi|desc[aăâ]rc[aăâe]|desc[aăâ]rc[aăâe]-mi|cerceteaz[aăâ]|"
    r"adun[aăâ]|str[aâ]nge|colecteaz[aăâ]|adaug[aăâ]|analizeaz[aăâ]|identific[aăâ]"
    r")\b",
    re.IGNORECASE,
)

# Scope to user's own knowledge base (never escalate to external research)
KB_SCOPE_RE = re.compile(
    r"\b("
    r"biblioteca\s+mea|baza\s+mea\s+de\s+cuno[șs]tin[țt]e|knowledge\s+base|my\s+library|"
    r"my\s+notes|noti[țt]ele\s+mele|notele\s+mele|intr[aă]rile\s+mele|my\s+entries|"
    r"[îi]n\s+bibliotec[aă]|in\s+my\s+kb|din\s+bibliotec[aă]"
    r")\b",
    re.IGNORECASE,
)

# Batch download verbs (Romanian + English)
DOWNLOAD_BATCH_VERB_RE = re.compile(
    r"\b(desc[aăâ]rc\w*|download\w*|salveaz[aăâ]\w*|fetch|ob[țt]ine|obtine)\b",
    re.IGNORECASE,
)

DOWNLOAD_BATCH_SCOPE_RE = re.compile(
    r"\b("
    r"toate|toat[aă]|all|every|lips[aă]|missing|f[aăâ]r[aăâ]|pdf-urile|pdfs|"
    r"remaining|r[aă]mase|text\s+integral|full[- ]text"
    r")\b",
    re.IGNORECASE,
)

DOWNLOAD_STRONG_VERB_RE = re.compile(
    r"\b(desc[aăâ]rc\w*|download\w*|fetch|ob[țt]ine|obtine)\b",
    re.IGNORECASE,
)

DOWNLOAD_TARGET_RE = re.compile(
    r"\b("
    r"pdf|pdf-uri|pdfs|file|files|fi[șs]ier|paper\w*|stud(?:y|ies|i\w*)|"
    r"articol\w*|document\w*|full.?text|fulltext|integral"
    r")\b",
    re.IGNORECASE,
)

DOWNLOAD_FILE_TARGET_RE = re.compile(
    r"\b("
    r"pdf|pdf-uri|pdfs|file|files|fi[șs]ier|document\w*"
    r")\b",
    re.IGNORECASE,
)

# Screen studies task trigger
TASK_SCREEN_RE = re.compile(
    r"\b("
    r"screen|screeneaz[aă]|selecteaz[aă]|filtreaz[aă]|evaluez[aă]|clasific[aă]|"
    r"include|exclude|maybe|poate|s[aă]rite|skip"
    r")\b",
    re.IGNORECASE,
)

# Code fences / inline code — never execute actions inside these
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Strict CREATE_ENTRY tag format
CREATE_ENTRY_STRICT_RE = re.compile(
    r'\[CREATE_ENTRY:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(\d+)\]',
    re.DOTALL,
)

# ──────────────────────────────────────────────────────────────────────────────
# Text helpers
# ──────────────────────────────────────────────────────────────────────────────

def fold(s: str) -> str:
    """Normalize: NFKD, strip diacritics, lowercase, collapse whitespace."""
    d = unicodedata.normalize("NFKD", s or "")
    return " ".join("".join(c for c in d if not unicodedata.combining(c)).lower().split())


def surnames(authors: str) -> set[str]:
    """Extract normalized surnames from author string."""
    out = set()
    for part in re.split(r"[;,]| and ", authors or ""):
        words = [w for w in re.findall(r"[A-Za-zĂÂÎȘȚăâîșț\-]+", part) if len(w) > 2]
        if words:
            out.add(fold(words[-1]))
    return out


def title_similarity(a: str, b: str) -> float:
    """Difflib ratio on folded titles."""
    return difflib.SequenceMatcher(None, fold(a), fold(b)).ratio()