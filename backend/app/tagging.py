"""Tag vocabulary hygiene, shared by every write path.

The `entries.tags` column is a comma-separated string, and over time three kinds
of noise collected in it:

  * bare ISO dates ("1973-01-01") left by the research pipeline — they duplicate
    `entry_date` and turn the tag filter into a list of years;
  * English stop words ("the", "of", "and") from splitting a topic into words;
  * duplicates with different casing inside the same row.

A tag is a subject someone chose, so nothing here drops a rare real tag
("Carey", "Fodor"). The `tags` table, previously written only by the test suite,
is kept in sync on every write so the two views of the vocabulary agree.
"""
from __future__ import annotations

import re

ISO_DATE = re.compile(r"^-?\d{4}-\d{2}-\d{2}$")

# Deliberately short: known noise, not a model of what a tag should be.
STOPWORDS = {
    "the", "of", "and", "a", "an", "in", "on", "for", "to", "with", "by", "from",
    "is", "are", "was", "were", "that", "this", "as", "at", "or", "it", "its",
}

DEFAULT_TAG_COLOR = "#6366f1"
MAX_TAG_LENGTH = 100
MAX_TAGS_LENGTH = 500


def is_noise_tag(tag: str) -> bool:
    """True for a tag that should not exist: a date, a stop word, punctuation."""
    text = (tag or "").strip()
    if not text:
        return True
    if ISO_DATE.match(text):
        return True
    if text.lower() in STOPWORDS:
        return True
    return not any(ch.isalnum() for ch in text)


def normalize_tags(raw: str | list[str] | tuple[str, ...] | None, sep: str = ", ") -> str:
    """Return a clean, de-duplicated tag string.

    Splits on commas/semicolons/pipes (the separators the app has written over
    time), trims and unquotes each tag, drops noise, and keeps the first
    spelling of a case-insensitive duplicate. `sep` is the join string so the
    repair script can keep its historical comma-without-space format.
    """
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple, set)):
        parts: list[str] = [str(part) for part in raw]
    else:
        parts = re.split(r"[,;|]", str(raw))

    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        tag = part.strip().strip('"').strip()
        if not tag or is_noise_tag(tag):
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag[:MAX_TAG_LENGTH])
    return sep.join(out)[:MAX_TAGS_LENGTH]


def tag_names(raw: str | list[str] | None) -> list[str]:
    """The normalized tags as a list, ready for the `tags` table."""
    cleaned = normalize_tags(raw)
    return [name.strip() for name in cleaned.split(",") if name.strip()]


def sync_tags(conn, raw: str | list[str] | None) -> int:
    """Insert any tag missing from the `tags` table; returns how many were new.

    Does not commit: the caller owns the transaction, so an entry write and its
    vocabulary update land together or not at all.
    """
    inserted = 0
    for name in tag_names(raw):
        cur = conn.execute(
            "INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)",
            [name, DEFAULT_TAG_COLOR],
        )
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
    return inserted
