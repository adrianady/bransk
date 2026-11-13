"""Span-anchored evidence passages for stance analysis.

A passage is a slice of the canonical ``page_texts.text`` for
``(entry_id, page_number)``. Only highlights that carry ``start_offset`` /
``end_offset`` can become passages: without a span there is no verifiable
anchor, so they are reported as skipped instead of silently upgraded from a
fuzzy ``quote`` match.
"""
import re

from app.page_text import get_page_text

MIN_WINDOW_CHARS = 80

#: Abbreviations that must never end a sentence, even when followed by a
#: capital letter. Lowercased; compared against the token ending the candidate
#: sentence (quotes/brackets trailing the token are stripped first).
_ABBREVIATIONS = {
    "e.g.", "i.e.", "et al.", "al.", "vs.", "cf.", "fig.", "figs.", "p.", "pp.",
    "no.", "etc.", "vol.", "ed.", "eds.", "ch.", "sec.", "ca.", "approx.",
}

_BOUNDARY_RE = re.compile(r"""([.!?]+["'”’)\]]*)(\s+)""")
_TRAILING_PUNCT_RE = re.compile(r"""["'”’)\]]+$""")


def page_passage(conn, entry_id: int, page_number, start_offset, end_offset) -> dict | None:
    """Slice the canonical page text into a passage, or None when unusable.

    Returns ``{text, page, start, end, source, status}``. None when the page
    has no canonical row, an offset is missing, or the slice is empty /
    whitespace-only.
    """
    if page_number is None or start_offset is None or end_offset is None:
        return None
    page = get_page_text(conn, entry_id, page_number)
    if page is None:
        return None
    text = page.get("text") or ""
    if not text:
        return None
    try:
        start = max(0, int(start_offset))
        end = min(len(text), int(end_offset))
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    slice_ = text[start:end]
    if not slice_.strip():
        return None
    return {
        "text": slice_,
        "page": int(page_number),
        "start": start,
        "end": end,
        "source": page.get("source") or "",
        "status": page.get("status") or "",
    }


def split_sentences(text: str) -> list[str]:
    """Deterministic sentence split (no ML), abbreviation-aware.

    Boundaries: ``.``/``!``/``?`` (optionally followed by closing quotes or a
    bracket) plus whitespace, where the next non-space character is a capital,
    a digit or an opening quote. Blank lines always split. Abbreviations
    (``e.g.``, ``et al.``, ``Fig.`` …) and decimals do not split: a decimal has
    no whitespace after the dot, and abbreviations are filtered by token.
    """
    if not text:
        return []
    sentences: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        start = 0
        for match in _BOUNDARY_RE.finditer(paragraph):
            next_index = match.end(1) + len(match.group(2))
            if next_index >= len(paragraph):
                continue
            nxt = paragraph[next_index]
            if not (nxt.isupper() or nxt.isdigit() or nxt in "\"'„“‚‘("):
                continue
            head = paragraph[start:match.end(1)].split()
            if not head:
                continue
            token = _TRAILING_PUNCT_RE.sub("", head[-1])
            token = token.strip("([{<\"'„“”’‘‚").lower()
            if token in _ABBREVIATIONS:
                continue
            sentence = paragraph[start:next_index].strip()
            if sentence:
                sentences.append(sentence)
            start = next_index
        tail = paragraph[start:].strip()
        if tail:
            sentences.append(tail)
    return sentences


def make_windows(sentences: list[str], size: int = 3, stride: int = 2) -> list[str]:
    """Sliding windows of ``size`` sentences advancing by ``stride``.

    Consecutive windows share sentences (3/2 shares one). Windows shorter than
    ``MIN_WINDOW_CHARS`` after joining are dropped: an NLI pair made of a
    fragment is noise, not evidence.
    """
    if size <= 0 or stride <= 0:
        return []
    sentences = [s.strip() for s in (sentences or []) if s and s.strip()]
    windows: list[str] = []
    total = len(sentences)
    index = 0
    while index < total:
        chunk = sentences[index:index + size]
        if not chunk:
            break
        joined = " ".join(chunk).strip()
        if len(joined) >= MIN_WINDOW_CHARS:
            windows.append(joined)
        if index + size >= total:
            break
        index += stride
    return windows


def entry_passages(conn, entry_id: int) -> dict:
    """Evidence set for one study: every offset-anchored highlight as a passage.

    Returns ``{passages: [...], skipped: [...]}``. Each passage carries
    ``highlight_id`` and the stored ``quote`` (display / anti-hallucination
    check, never the anchor). Highlights without both offsets are skipped with
    reason ``no_offsets``; those whose page text is missing or whose slice is
    empty are skipped with ``missing_page_text``.
    """
    rows = conn.execute(
        """SELECT id, page_number, quote, start_offset, end_offset
             FROM pdf_highlights
            WHERE entry_id = ?
            ORDER BY page_number, start_offset, id""",
        [entry_id],
    ).fetchall()
    passages: list[dict] = []
    skipped: list[dict] = []
    for row in rows:
        highlight = dict(row)
        if highlight.get("start_offset") is None or highlight.get("end_offset") is None:
            skipped.append({"highlight_id": highlight["id"], "reason": "no_offsets"})
            continue
        passage = page_passage(
            conn,
            entry_id,
            highlight.get("page_number"),
            highlight["start_offset"],
            highlight["end_offset"],
        )
        if passage is None:
            skipped.append(
                {"highlight_id": highlight["id"], "reason": "missing_page_text"}
            )
            continue
        passage["highlight_id"] = highlight["id"]
        passage["quote"] = highlight.get("quote") or ""
        passages.append(passage)
    return {"passages": passages, "skipped": skipped}
