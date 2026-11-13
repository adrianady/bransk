"""Canonical page texts — the single source of truth for highlight anchoring.

``pdf_rag.chunk_text()`` normalizes whitespace before splitting, destroying
character positions, and ``MAX_PDF_CHARS`` silently drops pages beyond ~20.
This module stores the verbatim per-page text (one row per
``(entry_id, page_number)`` in ``page_texts``) so highlights can anchor with
real ``(start_offset, end_offset)`` character offsets into that text.
"""

import logging
import re

logger = logging.getLogger("chronokg.page_text")

PER_PAGE_MAX_CHARS = 200_000


def extract_canonical_pages(path) -> list[tuple[int, str]]:
    """Extract ``(1-based page number, verbatim text)`` pairs from a PDF.

    PyMuPDF first, pypdf fallback — mirroring ``pdf_rag.extract_pdf_pages``
    but with no global cap (per-page cap of 200k chars) and no whitespace
    normalization: ``page.get_text()`` is returned verbatim. Pages whose text
    is empty/whitespace-only are skipped (nothing recorded for them).
    """
    pages: list[tuple[int, str]] = []
    try:
        import pymupdf  # PyMuPDF

        doc = pymupdf.open(str(path))
        try:
            for page_number, page in enumerate(doc, start=1):
                try:
                    text = page.get_text() or ""
                except Exception:
                    continue
                if not text.strip():
                    continue
                pages.append((page_number, text[:PER_PAGE_MAX_CHARS]))
            return pages
        finally:
            doc.close()
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            if not text.strip():
                continue
            pages.append((page_number, text[:PER_PAGE_MAX_CHARS]))
        return pages
    except Exception:
        return []


#: A line that is essentially just a number, allowing a little punctuation
#: around it ("795", "— 795 —", "p. 795"). Leading/trailing letters are not
#: accepted: a page number is not embedded in a sentence.
_PAGE_LINE_RE = re.compile(r"^\s*[^0-9]{0,4}(\d{2,4})[^0-9]{0,4}\s*$")

#: Four-digit values that read as years, not page numbers.
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _page_candidate(text: str) -> int | None:
    """Return the printed page number visible at the top/bottom of a page.

    Only the first and last 3 non-empty lines are inspected. A candidate must
    be a standalone 2–4 digit number; years (19xx/20xx) are rejected because
    they normally appear inside body text, and a page printed "2019" is far
    more likely a year than a page.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return None
    edges = lines[:3] + lines[-3:]
    for line in edges:
        match = _PAGE_LINE_RE.match(line)
        if not match:
            continue
        value = match.group(1)
        if _YEAR_RE.match(value):
            continue
        try:
            number = int(value)
        except ValueError:
            continue
        if number < 1:
            continue
        return number
    return None


def detect_printed_pages(pages: list[tuple[int, str]]) -> dict[int, str]:
    """Detect the number printed on each page, or ``''`` when unknown.

    A number is accepted only when its sequence is monotonic non-decreasing
    across the pages that carry one, with an increment of 1–2 per PDF page.
    Two real cases drive this:

    * consecutive pages carrying consecutive numbers (PDF index aligned or
      offset), and
    * book chapters where only every second page carries a number — there the
      step is 2 PDF pages for an increment of 1, so the numbered pages are
      accepted and the gaps stay empty rather than being inferred.

    A page that fails validation gets ``''``; never a guess.
    """
    result: dict[int, str] = {page_number: "" for page_number, _ in pages}
    candidates: dict[int, int] = {}
    for page_number, text in pages:
        candidate = _page_candidate(text)
        if candidate is not None:
            candidates[page_number] = candidate
    if not candidates:
        return result

    ordered = sorted(candidates.items())
    # Pages whose candidate is corroborated by at least one neighbour.
    accepted: set[int] = set()
    for (prev_page, prev_num), (page, num) in zip(ordered, ordered[1:]):
        step = page - prev_page
        if step < 1 or step > 2:
            continue  # too far apart to validate a sequence
        delta = num - prev_num
        if 1 <= delta <= 2:
            accepted.add(prev_page)
            accepted.add(page)
    for page_number, number in ordered:
        if page_number in accepted:
            result[page_number] = str(number)
    return result


def ensure_page_texts(conn, entry_id: int, path, force: bool = False) -> int:
    """Write canonical page rows idempotently. Returns rows written."""
    pages = extract_canonical_pages(path)
    if not pages:
        return 0
    printed_pages = detect_printed_pages(pages)
    written = 0
    for page_number, text in pages:
        char_count = len(text)
        printed_page = printed_pages.get(page_number, "")
        try:
            if force:
                # Only native rows may be replaced; never overwrite a
                # 'manual' or 'vision' row.
                row = conn.execute(
                    "SELECT source FROM page_texts WHERE entry_id = ? AND page_number = ?",
                    [entry_id, page_number],
                ).fetchone()
                if row is not None:
                    try:
                        source = row["source"]
                    except Exception:
                        source = row[0]
                    if source in ("manual", "vision"):
                        continue
                    conn.execute(
                        """INSERT OR REPLACE INTO page_texts
                           (entry_id, page_number, text, source, status, char_count, printed_page)
                           VALUES (?, ?, ?, 'native', 'native', ?, ?)""",
                        [entry_id, page_number, text, char_count, printed_page],
                    )
                    written += 1
                else:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO page_texts
                           (entry_id, page_number, text, source, status, char_count, printed_page)
                           VALUES (?, ?, ?, 'native', 'native', ?, ?)""",
                        [entry_id, page_number, text, char_count, printed_page],
                    )
                    if cur.rowcount:
                        written += cur.rowcount
            else:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO page_texts
                       (entry_id, page_number, text, source, status, char_count, printed_page)
                       VALUES (?, ?, ?, 'native', 'native', ?, ?)""",
                    [entry_id, page_number, text, char_count, printed_page],
                )
                if cur.rowcount:
                    written += cur.rowcount
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill indexing
            logger.info("ensure_page_texts skipped page %s: %s", page_number, exc)
            continue
    try:
        conn.commit()
    except Exception:
        pass
    return written


def get_page_text(conn, entry_id: int, page_number: int) -> dict | None:
    """Return the canonical page row, or None when absent."""
    try:
        row = conn.execute(
            "SELECT entry_id, page_number, text, source, status, char_count, printed_page"
            " FROM page_texts WHERE entry_id = ? AND page_number = ?",
            [entry_id, page_number],
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        keys = [
            "entry_id", "page_number", "text", "source", "status",
            "char_count", "printed_page",
        ]
        return dict(zip(keys, row))


def get_printed_page(conn, entry_id: int, page_number: int) -> str | None:
    """Return the printed page number for a page, or None when the row is absent.

    An existing page whose printed number could not be determined returns ``''``
    (unknown), which callers must distinguish from ``None`` (no such page).
    """
    try:
        row = conn.execute(
            "SELECT printed_page FROM page_texts WHERE entry_id = ? AND page_number = ?",
            [entry_id, page_number],
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    try:
        value = row["printed_page"]
    except Exception:
        value = row[0]
    return value or ""


def _normalize(text: str) -> str:
    """Collapse whitespace, lowercase, strip."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace + lowercase, keeping per-char origin indices.

    Returns ``(normalized, origin)`` where ``origin[i]`` is the index in the
    original ``text`` that produced ``normalized[i]``. A whitespace run
    collapses to a single ``' '`` mapped to the first whitespace char of the
    run; leading/trailing whitespace is stripped (with its map entries).
    Lowercase expansions (e.g. U+0130) map every produced char to the same
    original index, so offsets always stay valid.
    """
    norm_chars: list[str] = []
    origin: list[int] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch.isspace():
            # Collapse the whole run to one space.
            if norm_chars and norm_chars[-1] != " ":
                norm_chars.append(" ")
                origin.append(i)
            # else: leading whitespace or already collapsed — skip
            while i < n and text[i].isspace():
                i += 1
            continue
        lowered = ch.lower()
        for lc in lowered:
            norm_chars.append(lc)
            origin.append(i)
        i += 1
    # Strip a trailing collapsed space (and its map entry).
    if norm_chars and norm_chars[-1] == " ":
        norm_chars.pop()
        origin.pop()
    return "".join(norm_chars), origin


def locate_quote(conn, entry_id: int, page_number: int, quote: str) -> tuple[int, int] | None:
    """Find ``quote`` in the canonical page text, returning original offsets.

    Both page text and quote are normalized (whitespace collapsed, lowercased,
    stripped); the search runs on the normalized copies, but the returned
    ``(start, end)`` are character offsets into the *original* page text via
    the index map. Quotes that start/end mid-whitespace-run work because edge
    whitespace is stripped before matching. Returns None when the page has no
    canonical text, the quote is empty after normalization, or there is no
    match.
    """
    if not quote or not (quote.strip()):
        return None
    row = get_page_text(conn, entry_id, page_number)
    if row is None:
        return None
    text = row.get("text") or ""
    if not text.strip():
        return None
    norm_page, origin = _normalize_with_map(text)
    norm_quote = _normalize(quote)
    if not norm_quote or not norm_page:
        return None
    idx = norm_page.find(norm_quote)
    if idx == -1:
        return None
    end_exclusive = idx + len(norm_quote)
    try:
        start_orig = origin[idx]
        end_orig = origin[end_exclusive - 1] + 1
    except IndexError:
        return None
    if end_orig <= start_orig:
        return None
    # Defensive: the slice must round-trip to the normalized quote.
    if _normalize(text[start_orig:end_orig]) != norm_quote:
        # Fallback scan: try every occurrence until one round-trips.
        pos = idx + 1
        while True:
            nxt = norm_page.find(norm_quote, pos)
            if nxt == -1:
                return None
            try:
                s = origin[nxt]
                e = origin[nxt + len(norm_quote) - 1] + 1
            except IndexError:
                return None
            if e > s and _normalize(text[s:e]) == norm_quote:
                return (s, e)
            pos = nxt + 1
    return (start_orig, end_orig)
