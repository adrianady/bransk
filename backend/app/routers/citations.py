"""
Citations router — BibTeX/RIS export and the thesis bibliography.

The research library is the thesis's evidence base, but until now none of it
reached the written work: the export was a bare .md dump and every citation
had to be retyped by hand. This router derives both from saved data:

* `GET /api/citations/export`    — BibTeX or RIS for the library (the formats
  Zotero, Mendeley, JabRef and Word/LibreOffice all import);
* `GET /api/citations/bibliography` — APA-style reference list, split into
  the studies actually cited in the thesis chapters and the rest.

The export mirrors the `/studies` definition of a study, so what the Studies
screen shows is what gets exported.
"""
import json
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse

from app.db import get_db

router = APIRouter()

# Same definition of "study" the /studies endpoint uses.
_STUDY_WHERE = """archived = 0
                 AND (study_type != ''
                      OR tags LIKE '%study%'
                      OR tags LIKE '%pdf%'
                      OR content LIKE '%**PDF:**%')"""


def _display_name(entry: dict) -> str:
    """Best title for a reference: the Crossref-resolved one wins."""
    return (entry.get("bib_title") or entry.get("title") or "").strip()


def _display_authors(entry: dict) -> str:
    return (entry.get("bib_authors") or entry.get("authors") or "").strip()


def _display_year(entry: dict) -> str:
    year = (entry.get("bib_year") or "").strip()
    if not year and entry.get("entry_date"):
        year = str(entry["entry_date"])[:4]
    return year if year.isdigit() else ""


def _clean_author_name(name: str) -> str:
    cleaned = re.sub(r"\s*[\(\[]\s*(?:eds?\.?|editors?|ed|eds)\s*[\)\]]", "", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(Wiley Online Library\)|\(ScienceDirect\)|\(MDPI\)", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" ,;.")


def _parse_author_name(name: str) -> tuple[str, str]:
    """Extracts (surname, initials) accurately from either 'First Middle Last'
    or 'Last FirstInitial' (e.g. PubMed style 'Descartes F', 'Doba K')."""
    cleaned = _clean_author_name(name)
    tokens = [t for t in re.split(r"\s+", cleaned.strip()) if t]
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        s = tokens[0]
        return s[:1].upper() + s[1:], ""

    last = tokens[-1].rstrip(".")
    first = tokens[0].rstrip(".")
    if len(last) <= 2 and last.isalpha() and last.isupper() and (len(first) > 2 or not first.isupper()):
        surname = " ".join(tokens[:-1])
        initials = " ".join(f"{ch.upper()}." for ch in last)
    else:
        surname = tokens[-1]
        initials = " ".join(f"{t[0].upper()}." for t in tokens[:-1] if t)

    surname = surname[:1].upper() + surname[1:]
    return surname, initials


def _split_authors(authors: str) -> list[str]:
    """Authors are stored comma-separated; a trailing 'et al.' attaches to the
    last name ('Ioan Smith, Jane Roe et al.'), so strip it before splitting."""
    text = re.sub(r"\s*et\.?\s+al\.?\s*$", "", (authors or "").strip(), flags=re.IGNORECASE)
    parts = [p.strip() for p in re.split(r",|;", text) if p.strip()]
    return [p for p in parts if p.lower() not in ("et al.", "et al")]


def _has_others(authors: str) -> bool:
    """Whether the stored string ends in 'et al.' (more authors follow)."""
    return bool(re.search(r"\bet\.?\s+al\.?\s*$", (authors or "").strip(), re.IGNORECASE))


def _in_text_citation(entry: dict) -> str:
    authors = _split_authors(_display_authors(entry))
    year = _display_year(entry) or "n.d."
    if not authors:
        return f"({_display_name(entry) or 'fără autor'}, {year})"
    s1, _ = _parse_author_name(authors[0])
    if not s1:
        s1 = "fără autor"
    if len(authors) == 1 and not _has_others(_display_authors(entry)):
        return f"({s1}, {year})"
    if len(authors) == 2 and not _has_others(_display_authors(entry)):
        s2, _ = _parse_author_name(authors[1])
        return f"({s1} & {s2 or 'al.'}, {year})"
    return f"({s1} et al., {year})"


def _cite_key(entry: dict, used: set) -> str:
    """smith2021memory — first author's surname, year, first title word."""
    authors = _split_authors(_display_authors(entry))
    surname = ""
    if authors:
        s, _ = _parse_author_name(authors[0])
        surname = re.sub(r"[^a-zA-Z]", "", s).lower() or "anonim"
    year = _display_year(entry) or "nd"
    tokens = re.findall(r"[a-zA-ZăâîșțşţĂÂÎȘȚŞŢ]+", _display_name(entry))
    word = (tokens[0] if tokens else "").lower() or "fara-titlu"
    key = f"{surname}{year}{word}"
    candidate, suffix = key, 0
    while candidate in used:
        suffix += 1
        candidate = f"{key}{chr(96 + suffix)}"  # a, b, c...
    used.add(candidate)
    return candidate


def _to_bibtex(entry: dict, used: set) -> str:
    title = _display_name(entry)
    authors = _split_authors(_display_authors(entry))
    year = _display_year(entry)
    doi = (entry.get("doi") or "").strip()
    # `source` often holds a label ("crossref") rather than a URL; only a real
    # link belongs in the BibTeX `url` field.
    source = (entry.get("source") or "").strip()
    url = source if source.startswith(("http://", "https://")) else ""
    if not url and doi:
        url = f"https://doi.org/{doi}"
    study_type = (entry.get("study_type") or "").lower()

    if "book" in study_type:
        etype = "book"
    elif not authors or not year:
        etype = "misc"
    else:
        etype = "article"

    # BibTeX separates authors with " and "; "et al." becomes "others".
    bib_authors = " and ".join(authors) if authors else ""
    if _has_others(_display_authors(entry)):
        bib_authors = (bib_authors + " and others").strip()

    lines = [f"@{etype}{{{_cite_key(entry, used)},"]
    if bib_authors:
        lines.append(f"  author = {{{bib_authors}}},")
    lines.append(f"  title = {{{title}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if url:
        lines.append(f"  url = {{{url}}},")
    lines.append("}")
    return "\n".join(lines)


def _to_ris(entry: dict) -> str:
    doi = (entry.get("doi") or "").strip()
    url = (entry.get("source") or "").strip() or (f"https://doi.org/{doi}" if doi else "")
    study_type = (entry.get("study_type") or "").lower()
    lines = ["TY  - " + ("BOOK" if "book" in study_type else "JOUR")]
    title = _display_name(entry)
    if title:
        lines.append(f"TI  - {title}")
    for author in _split_authors(_display_authors(entry)):
        lines.append(f"AU  - {author}")
    year = _display_year(entry)
    if year:
        lines.append(f"PY  - {year}")
    if doi:
        lines.append(f"DO  - {doi}")
    if url:
        lines.append(f"UR  - {url}")
    lines.append("ER  - ")
    return "\n".join(lines)


def _fetch_studies(conn, ids: Optional[list[int]], doi_only: bool):
    where = _STUDY_WHERE
    params: list = []
    if ids:
        placeholders = ",".join("?" * len(ids))
        where += f" AND id IN ({placeholders})"
        params = list(ids)
    if doi_only:
        where += " AND doi != ''"
    rows = conn.execute(
        f"""SELECT id, title, entry_date, source, study_type, authors, quality,
                   doi, bib_title, bib_authors, bib_year
            FROM entries WHERE {where} ORDER BY bib_year DESC, entry_date DESC, id""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/citations/export")
def export_citations(
    format: str = Query("bibtex", pattern="^(bibtex|ris)$"),
    ids: Optional[str] = Query(None, description="Comma-separated entry ids; empty = whole library"),
    doi_only: bool = False,
):
    """Export studies as a BibTeX or RIS bibliography file."""
    id_list = None
    if ids:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "ids must be a comma-separated list of integers")
        if not id_list:
            raise HTTPException(400, "ids must not be empty")

    conn = get_db()
    try:
        studies = _fetch_studies(conn, id_list, doi_only)
    finally:
        conn.close()
    if not studies:
        raise HTTPException(404, "No studies match this export")

    if format == "ris":
        body = "\n".join(_to_ris(s) for s in studies) + "\n"
        filename = "bransk-bibliografie.ris"
        media = "application/x-research-info-systems"
    else:
        used: set = set()
        body = "\n\n".join(_to_bibtex(s, used) for s in studies) + "\n"
        filename = "bransk-bibliografie.bib"
        media = "application/x-bibtex"
    return Response(
        content=body,
        media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _bibliography_references(conn) -> list[dict]:
    """APA references for the whole library, with the `cited` flag computed
    against the thesis chapter text."""
    studies = _fetch_studies(conn, None, doi_only=False)
    row = conn.execute("SELECT structure_json FROM project WHERE id = 1").fetchone()

    thesis_text = ""
    if row:
        try:
            structure = json.loads(row["structure_json"] or "[]")
            if isinstance(structure, list):
                thesis_text = "\n".join(
                    str(section.get("content") or "") for section in structure
                    if isinstance(section, dict)
                )
        except (TypeError, json.JSONDecodeError):
            thesis_text = ""

    folded_text = thesis_text.lower()
    references = []
    for s in studies:
        authors = _split_authors(_display_authors(s))
        year = _display_year(s)
        surname = ""
        if authors:
            surname, _ = _parse_author_name(authors[0])
        # APA 7: Smith, J., & Roe, J. (2021). Title. https://doi.org/...
        def _initials(name: str) -> str:
            s, inits = _parse_author_name(name)
            if not s:
                return ""
            if not inits:
                return s
            return f"{s}, {inits}"
        def _apa_join(names: list[str]) -> str:
            # "A, B, & C" — comma between all, ampersand before the last.
            if len(names) <= 1:
                return names[0] if names else ""
            return ", ".join(names[:-1]) + ", & " + names[-1]

        if len(authors) > 3 or (authors and _has_others(_display_authors(s))):
            author_part = _apa_join([_initials(a) for a in authors[:3]])
            author_part = author_part + ", et al." if author_part else "et al."
        elif len(authors) > 1:
            author_part = _apa_join([_initials(a) for a in authors[:3]])
        elif authors:
            author_part = _initials(authors[0])
        else:
            author_part = _display_name(s) or "Fără autor"

        doi = (s.get("doi") or "").strip()
        tail = f" https://doi.org/{doi}" if doi else ""
        apa = f"{author_part} ({year}). {_display_name(s)}.{tail}".replace(".. ", ". ").replace("..", ".").strip()
        if apa.endswith(".."):
            apa = apa[:-1]

        cited = bool(surname) and bool(year) and f"{surname.lower()}" in folded_text and year in folded_text
        in_text = _in_text_citation(s)
        references.append({
            "entry_id": s["id"],
            "title": _display_name(s),
            "authors": _display_authors(s),
            "year": year,
            "doi": doi,
            "apa": apa,
            "in_text": in_text,
            "cited": cited,
            "has_doi": bool(doi),
        })
    references.sort(key=lambda r: (r["apa"] or "zzz").lower())
    return references


@router.get("/citations/bibliography")
def bibliography(only_cited: bool = False):
    """APA-style reference list, derived from the library and the chapters.

    A study counts as cited when its first author's surname and its year both
    appear in the same chapter paragraph of the thesis structure. The list is
    split into `cited` and `library` so the writer sees what the text actually
    references before exporting.
    """
    conn = get_db()
    try:
        references = _bibliography_references(conn)
    finally:
        conn.close()
    if only_cited:
        references = [r for r in references if r["cited"]]
    return {
        "total": len(references),
        "cited_count": sum(1 for r in references if r["cited"]),
        "references": references,
    }


@router.get("/citations/cite/{entry_id}", response_class=PlainTextResponse)
def cite_snippet(entry_id: int):
    """The (Autor, An) snippet for a study, ready to paste into a chapter."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, title, study_type, authors, entry_date, doi, bib_title, bib_authors, bib_year FROM entries WHERE id = ? AND archived = 0",
            [entry_id],
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Entry not found")
    return _in_text_citation(dict(row))
