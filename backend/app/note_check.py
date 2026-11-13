"""Checked statements: every note sentence is verified against the source PDF.

The rule this module exists to enforce: a quote stored for a note must be
citable from the PDF, so it comes from the *source page*, never from the
researcher's sentence. Deterministic exact matching only — the LLM nuance
classifier is a later layer and is not wired here.
"""

import re

from app.page_text import get_printed_page
from app.stance.passages import split_sentences

#: The seven statuses of a checked statement, in the order the UI shows them:
#: ``verificat`` (text says exactly this), ``verificat_cu_nuanta`` (essence
#: correct, wording needs precision), ``incomplet`` (correct but a
#: sense-changing condition is missing), ``subestimat`` (the source is
#: stronger than the note), ``corectat`` (the note says something else),
#: ``depasit`` (the note describes an earlier version of the theory),
#: ``neverificat`` (not found in the accessible source).
NOTE_CHECK_STATUSES = (
    "verificat",
    "verificat_cu_nuanta",
    "incomplet",
    "subestimat",
    "corectat",
    "depasit",
    "neverificat",
)

#: Statements shorter than this are fragments, not claims.
MIN_STATEMENT_CHARS = 25

#: Paraphrase is allowed only at the edges: this many consecutive tokens must
#: appear verbatim on the page for a statement to count as supported.
MIN_RUN_TOKENS = 8


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", _normalize(text))


def split_statements(text: str) -> list[str]:
    """Split note text into standalone statements, dropping short fragments."""
    statements = []
    for sentence in split_sentences(text):
        sentence = sentence.strip()
        if len(sentence) >= MIN_STATEMENT_CHARS:
            statements.append(sentence)
    return statements


def _statement_runs(tokens: list[str]) -> list[str]:
    """The verbatim runs that may support a statement, longest available first."""
    if not tokens:
        return []
    if len(tokens) <= MIN_RUN_TOKENS:
        return [" ".join(tokens)]
    runs: list[str] = []
    seen: set[str] = set()
    for start in range(0, len(tokens) - MIN_RUN_TOKENS + 1):
        run = " ".join(tokens[start:start + MIN_RUN_TOKENS])
        if run not in seen:
            seen.add(run)
            runs.append(run)
    return runs


def _quote_from_page(page_text: str, run: str) -> str:
    """Return the source page's sentence containing ``run`` (verbatim).

    Falls back to the line, then to the run itself — all three are wording
    taken from the page, which is the property that makes the quote citable.
    """
    for sentence in split_sentences(page_text):
        if run in _normalize(sentence):
            return sentence.strip()
    for line in page_text.splitlines():
        line = line.strip()
        if line and run in _normalize(line):
            return line
    return run


def _coverage(statement_tokens: list[str], page_text: str) -> float:
    if not statement_tokens:
        return 0.0
    page_tokens = set(_tokens(page_text))
    present = sum(1 for token in statement_tokens if token in page_tokens)
    return round(present / len(statement_tokens), 4)


def find_support(conn, entry_id: int, statement: str) -> dict | None:
    """Find the first canonical page that supports ``statement``, or None.

    Matching is whitespace-collapsed and lowercased. Paraphrase is tolerated
    only at the edges: the statement's longest 8-token run must appear
    verbatim on the page. The returned ``quote`` is the page's own sentence
    containing that run and ``coverage`` is the fraction of the statement's
    tokens present on that page.
    """
    runs = _statement_runs(_tokens(statement))
    if not runs:
        return None
    statement_tokens = _tokens(statement)
    try:
        rows = conn.execute(
            "SELECT page_number, text FROM page_texts"
            " WHERE entry_id = ? ORDER BY page_number",
            [entry_id],
        ).fetchall()
    except Exception:
        return None
    for row in rows:
        page_number = row["page_number"]
        page_text = row["text"] or ""
        norm_page = _normalize(page_text)
        if not norm_page:
            continue
        for run in runs:
            if run not in norm_page:
                continue
            printed = get_printed_page(conn, entry_id, page_number)
            return {
                "page_pdf": int(page_number),
                "page_printed": printed or "",
                "quote": _quote_from_page(page_text, run),
                "method": "exact",
                "coverage": _coverage(statement_tokens, page_text),
            }
    return None


def check_note(conn, note_id: int, *, apply: bool = False) -> dict:
    """Check every statement of a note against its entry's canonical pages.

    Deterministic statuses only: found → ``verificat``, not found →
    ``neverificat``. Nothing is written unless ``apply=True``; then the note's
    existing ``note_checks`` rows are replaced in one transaction, leaving
    other notes untouched.
    """
    row = conn.execute(
        "SELECT id, entry_id, content FROM study_notes WHERE id = ?", [note_id]
    ).fetchone()
    if row is None:
        return {
            "note_id": note_id,
            "statements": [],
            "summary": {"verificat": 0, "neverificat": 0, "total": 0},
        }

    entry_id = int(row["entry_id"])
    results: list[dict] = []
    for statement in split_statements(row["content"] or ""):
        support = find_support(conn, entry_id, statement)
        if support is not None:
            results.append({
                "statement": statement,
                "status": "verificat",
                "page_pdf": support["page_pdf"],
                "page_printed": support["page_printed"],
                "quote": support["quote"],
                "correction": "",
                "method": support["method"],
                "coverage": support["coverage"],
            })
        else:
            results.append({
                "statement": statement,
                "status": "neverificat",
                "page_pdf": None,
                "page_printed": "",
                "quote": "",
                "correction": "",
                "method": "exact",
                "coverage": 0.0,
            })

    summary = {
        "verificat": sum(1 for item in results if item["status"] == "verificat"),
        "neverificat": sum(1 for item in results if item["status"] == "neverificat"),
        "total": len(results),
    }

    if apply:
        try:
            conn.execute("DELETE FROM note_checks WHERE note_id = ?", [note_id])
            for item in results:
                conn.execute(
                    """INSERT INTO note_checks
                       (note_id, entry_id, statement, status, page_pdf, page_printed,
                        quote, correction, method, confidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                    [
                        note_id,
                        entry_id,
                        item["statement"],
                        item["status"],
                        item["page_pdf"],
                        item["page_printed"],
                        item["quote"],
                        item["correction"],
                        item["method"],
                    ],
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {"note_id": note_id, "statements": results, "summary": summary}
