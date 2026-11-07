"""Full-text acquisition for studies: one entry point, several sources.

The catalogue stores a `source` URL that is often wrong (the research
pipeline invented plausible links), so downloading "the URL in the entry" is
not enough. This module looks for the paper itself, in a deliberate order:

  1. the entry's own URL, when it already ends in .pdf;
  2. OpenAlex (DOI lookup, then title lookup with the authors to confirm);
  3. Unpaywall — needs a contact email, which the owner puts in
     CHRONOKG_CONTACT_EMAIL; without it the API answers 422;
  4. Semantic Scholar (openAccessPdf), rate-limited without a key;
  5. Europe PMC (PMC ids) — the PMC PDF is behind a JS check, so a full-text
     HTML extraction is tried as well;
  6. arXiv by title for preprint-friendly work.

Every candidate goes through the SSRF guard already used for study PDFs.
Nothing here bypasses a paywall or a bot check: when a publisher answers 403
or a challenge page, the candidate is recorded as failed and the next one is
tried. That is a product decision, not an oversight.

Two outcomes are useful to the app:
  * "pdf"  — a real PDF landed in data/studies and was indexed for RAG;
  * "html" — the full text was extracted from an open HTML page and stored in
             entries.content (FTS search then finds it too).
"""
from __future__ import annotations

import asyncio
import html as html_mod
import logging
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.db import get_db
from app.models import normalize_doi
from app.studies_store import (
    assert_public_download_url,
    download_study_pdf,
    is_valid_pdf_file,
    pdf_path_for,
)

logger = logging.getLogger("chronokg.fulltext")

CONTACT_EMAIL = os.getenv("CHRONOKG_CONTACT_EMAIL", "").strip()
USER_AGENT = (
    f"Bransk/0.1 (local research app; contact: {CONTACT_EMAIL})"
    if CONTACT_EMAIL
    else "Bransk/0.1 (local research app)"
)
HONEST_SOURCES_ONLY = True  # no mirror that defeats a bot check

# Polite pacing: Semantic Scholar allows ~1 request/second unkeyed, and the
# others are public services we should not hammer either.
_S2_MIN_INTERVAL = 1.1
_last_s2_call = 0.0

DISCOVERY_TIMEOUT = 8.0
DOWNLOAD_TIMEOUT = 60.0
MIN_HTML_TEXT = 1500  # below this, the page is a wall, not an article
MAX_HTML_TEXT = 400_000  # one paper, not a book

_TITLE_CLEAN = re.compile(r"\s*\([^)]*\d{4}[^)]*\)")
_AUTHOR_PREFIX = re.compile(r"^[A-Z][\w.'&-]*(?:\s*&\s*[A-Z][\w.'&-]*)?\s+[-–—]\s+")


@dataclass
class Candidate:
    url: str
    source: str
    kind: str = "pdf"  # "pdf" | "html"
    note: str = ""


@dataclass
class FulltextResult:
    entry_id: int
    status: str = "none"  # "pdf" | "html" | "none"
    source: str = ""
    url: str = ""
    chars: int = 0
    chunks: int = 0
    index_error: bool = False
    text_save_error: bool = False
    tried: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "status": self.status,
            "source": self.source,
            "url": self.url,
            "chars": self.chars,
            "chunks": self.chunks,
            "index_error": self.index_error,
            "text_save_error": self.text_save_error,
            "tried": self.tried,
        }


def clean_title(title: str) -> str:
    """Strip the catalogue decorations a search cannot match.

    "Friston - Free Energy Principle (2010)" or "Machine Theory of Mind
    (DeepMind)" are catalogue labels, not the paper's title.
    """
    text = _TITLE_CLEAN.sub(" ", title or "")
    text = _AUTHOR_PREFIX.sub("", text.strip())
    text = re.sub(r"\s*\((?:[A-Z][\w&.'-]*|et al\.?)\)\s*$", "", text.strip())
    return " ".join(text.split())


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


def title_similarity(a: str, b: str) -> float:
    import difflib

    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return 0.0
    return difflib.SequenceMatcher(None, fa, fb).ratio()


def _entry_row(entry_id: int) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, title, doi, source, content, authors, study_type "
            "FROM entries WHERE id = ?",
            [entry_id],
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


async def _s2_get(client: httpx.AsyncClient, doi: str) -> dict | None:
    global _last_s2_call
    wait = _S2_MIN_INTERVAL - (time.monotonic() - _last_s2_call)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_s2_call = time.monotonic()
    try:
        resp = await client.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "title,openAccessPdf,externalIds"},
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - an optional source
        logger.info("Semantic Scholar unavailable (%s)", type(exc).__name__)
        return None


async def _openalex_by_doi(client: httpx.AsyncClient, doi: str) -> dict | None:
    try:
        resp = await client.get(
            f"https://api.openalex.org/works/https://doi.org/{doi}",
            params={"mailto": CONTACT_EMAIL} if CONTACT_EMAIL else None,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("OpenAlex DOI lookup failed (%s)", type(exc).__name__)
        return None


async def _openalex_by_title(client: httpx.AsyncClient, title: str) -> dict | None:
    try:
        resp = await client.get(
            "https://api.openalex.org/works",
            params={
                "search": clean_title(title),
                "per-page": 5,
                **({"mailto": CONTACT_EMAIL} if CONTACT_EMAIL else {}),
            },
        )
        if resp.status_code != 200:
            return None
        for work in resp.json().get("results", []):
            if title_similarity(title, work.get("title") or "") >= 0.72:
                return work
    except Exception as exc:  # noqa: BLE001
        logger.info("OpenAlex title search failed (%s)", type(exc).__name__)
    return None


def _pdfs_from_openalex(work: dict) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()

    def add(url: str | None, note: str) -> None:
        if url and url not in seen:
            seen.add(url)
            out.append(Candidate(url=url, source="openalex", note=note))

    best = work.get("best_oa_location") or {}
    add(best.get("pdf_url"), "best open-access location")
    for loc in work.get("locations") or []:
        add(loc.get("pdf_url"), (loc.get("host_type") or "") + " location")
    return out


async def _unpaywall(client: httpx.AsyncClient, doi: str) -> list[Candidate]:
    if not CONTACT_EMAIL:
        logger.info("Unpaywall skipped: set CHRONOKG_CONTACT_EMAIL to use it")
        return []
    try:
        resp = await client.get(
            f"https://api.unpaywall.org/v2/{doi}", params={"email": CONTACT_EMAIL}
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("Unpaywall unavailable (%s)", type(exc).__name__)
        return []
    out: list[Candidate] = []
    for loc in data.get("oa_locations") or []:
        url = loc.get("url_for_pdf") or ""
        if url:
            out.append(Candidate(url=url, source="unpaywall", note=loc.get("host_type") or ""))
        elif (loc.get("url") or "").endswith(".pdf"):
            out.append(Candidate(url=loc["url"], source="unpaywall", note=loc.get("host_type") or ""))
    return out


async def _europe_pmc(client: httpx.AsyncClient, doi: str, title: str) -> list[Candidate]:
    query = f"DOI:{doi}" if doi else f'TITLE:"{clean_title(title)}"'
    try:
        resp = await client.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "format": "json", "resultType": "lite"},
        )
        if resp.status_code != 200:
            return []
        results = resp.json().get("resultList", {}).get("result", [])
    except Exception as exc:  # noqa: BLE001
        logger.info("Europe PMC unavailable (%s)", type(exc).__name__)
        return []
    out: list[Candidate] = []
    for item in results[:3]:
        pmcid = item.get("pmcid")
        if not pmcid:
            continue
        out.append(Candidate(
            url=f"https://europepmc.org/articles/{pmcid}?pdf=render",
            source="europepmc", note="PMC render",
        ))
        out.append(Candidate(
            url=f"https://europepmc.org/article/MED/{item['pmid']}" if item.get("pmid") else
                f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
            source="europepmc", kind="html", note="PMC full text page",
        ))
    return out


async def _arxiv(client: httpx.AsyncClient, title: str) -> list[Candidate]:
    try:
        resp = await client.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f'all:"{clean_title(title)}"', "max_results": 3},
        )
        if resp.status_code != 200:
            return []
        entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.S)
    except Exception as exc:  # noqa: BLE001
        logger.info("arXiv unavailable (%s)", type(exc).__name__)
        return []
    out: list[Candidate] = []
    for block in entries:
        m_title = re.search(r"<title>(.*?)</title>", block, re.S)
        m_id = re.search(r"<id>http://arxiv.org/abs/([^<]+)</id>", block)
        if not (m_title and m_id):
            continue
        if title_similarity(title, html_mod.unescape(m_title.group(1).strip())) >= 0.85:
            out.append(Candidate(
                url=f"https://arxiv.org/pdf/{m_id.group(1)}", source="arxiv", note="preprint",
            ))
    return out


def _arxiv_ids_in_entry(entry: dict) -> list[str]:
    """arXiv IDs the user already saved in the source URL or the content."""
    import re

    blob = f"{entry.get('source') or ''} {entry.get('content') or ''}"
    ids: list[str] = []
    for m in re.finditer(r"ar[Xx]iv[:\s]*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", blob):
        ids.append(m.group(1))
    for m in re.finditer(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", blob, re.I):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


async def _websearch_pdfs(title: str, client: httpx.AsyncClient | None = None) -> list[Candidate]:
    """Web-browsing step: DuckDuckGo/Serper finds direct PDF links by title.

    Runs LAST in the chain — open-academic sources are trusted first, this is
    the catch-all for papers that exist only as stray PDFs on the open web.
    """
    if not title or len(title.strip()) < 8:
        return []
    try:
        from app.web_search import search_web

        results = await search_web(f'"{clean_title(title)}" filetype:pdf', max_results=6)
    except Exception as exc:  # noqa: BLE001 - discovery must never raise
        logger.info("Web search for PDFs failed: %s", type(exc).__name__)
        return []
    out: list[Candidate] = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        if not (".pdf" in url.lower() or "pdf" in (r.get("title") or "").lower()):
            continue
        out.append(Candidate(url=url, source="websearch", note="found via web search"))
    return out


async def find_fulltext(entry: dict, client: httpx.AsyncClient | None = None) -> list[Candidate]:
    """Ordered list of places where this study's text may live."""
    own = client is None
    if own:
        client = httpx.AsyncClient(
            timeout=DISCOVERY_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            follow_redirects=True,
        )
    try:
        title = entry.get("title") or ""
        doi = ""
        try:
            doi = normalize_doi(entry.get("doi") or "")
        except ValueError:
            doi = ""
        candidates: list[Candidate] = []

        source_url = (entry.get("source") or "").strip()
        if urlparse(source_url).path.lower().endswith(".pdf"):
            candidates.append(Candidate(url=source_url, source="entry", note="stored URL"))

        work = await _openalex_by_doi(client, doi) if doi else None
        if work is None:
            work = await _openalex_by_title(client, title)
        if work:
            candidates.extend(_pdfs_from_openalex(work))

        if doi:
            candidates.extend(await _unpaywall(client, doi))
            s2 = await _s2_get(client, doi)
            oa = (s2 or {}).get("openAccessPdf") or {}
            if oa.get("url"):
                candidates.append(Candidate(url=oa["url"], source="semanticscholar"))

        candidates.extend(await _europe_pmc(client, doi, title))
        candidates.extend(await _arxiv(client, title))
        # arXiv IDs the user already saved — the exact preprint, no title match.
        for aid in _arxiv_ids_in_entry(entry):
            candidates.append(Candidate(url=f"https://arxiv.org/pdf/{aid}", source="arxiv", note="saved arXiv ID"))
        # Web-browsing catch-all: direct PDFs found by search, tried last.
        try:
            candidates.extend(await _websearch_pdfs(title))
        except Exception as exc:  # noqa: BLE001 - discovery must never raise
            logger.info("Web search candidates failed: %s", type(exc).__name__)

        seen: set[str] = set()
        unique: list[Candidate] = []
        for cand in candidates:
            if cand.url in seen:
                continue
            seen.add(cand.url)
            unique.append(cand)
        return unique
    finally:
        if own:
            await client.aclose()


def html_to_text(page: str) -> str:
    """Readable text out of an article page, without a headless browser.

    Enough for PMC and repository landing pages: drop scripts/styles, prefer
    the article body, strip the tags and collapse the whitespace.
    """
    page = re.sub(r"<script.*?</script>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<style.*?</style>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<(noscript|svg|nav|footer|header).*?</\1>", " ", page, flags=re.S | re.I)
    body = None
    for pattern in (r"<article[^>]*>(.*?)</article>", r"<main[^>]*>(.*?)</main>", r"<body[^>]*>(.*?)</body>"):
        m = re.search(pattern, page, re.S | re.I)
        if m:
            body = m.group(1)
            break
    body = body or page
    body = re.sub(r"<(p|div|br|h[1-6]|li|tr)[^>]*>", "\n", body, flags=re.I)
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", body))
    text = " ".join(text.split())
    return text[:MAX_HTML_TEXT]


async def _fetch_html_text(client: httpx.AsyncClient, url: str) -> str:
    await asyncio.to_thread(assert_public_download_url, url)
    resp = await client.get(url)
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype:
        return ""
    text = html_to_text(resp.text)
    return text if len(text) >= MIN_HTML_TEXT else ""


async def fetch_fulltext(entry_id: int, *, index: bool = True) -> dict:
    """Try every source for one study and store what is found."""
    entry = _entry_row(entry_id)
    result = FulltextResult(entry_id=entry_id)
    if not entry:
        result.tried.append({"url": "", "source": "", "error": "entry not found"})
        return result.as_dict()

    async with httpx.AsyncClient(
        timeout=DOWNLOAD_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        follow_redirects=True,
    ) as client:
        try:
            candidates = await find_fulltext(entry, client)
        except Exception as exc:  # noqa: BLE001 - discovery must never raise
            logger.warning("Full-text discovery failed for #%s: %s", entry_id, exc)
            candidates = []

        for cand in candidates:
            attempt = {"url": cand.url[:300], "source": cand.source, "kind": cand.kind}
            try:
                if cand.kind == "html":
                    text = await _fetch_html_text(client, cand.url)
                    if not text:
                        attempt["error"] = "no article text (wall or too short)"
                        result.tried.append(attempt)
                        continue
                    # The text belongs on disk (backend/data/studytext, on the
                    # HDD with the project); the database only gets the chunks
                    # needed for semantic search. entries.content keeps a short
                    # excerpt so the row itself says where the paper came from.
                    from app import studytext

                    saved = await asyncio.to_thread(studytext.save_text, entry_id, entry["title"], text)
                    conn = get_db()
                    try:
                        conn.execute(
                            "UPDATE entries SET content = ?, updated_at = datetime('now') "
                            "WHERE id = ? AND length(coalesce(content,'')) < ?",
                            [text[:8000], entry_id, 8000],
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    result.status, result.source, result.url = "html", cand.source, cand.url
                    result.chars = len(text)
                    attempt["chars"] = len(text)
                    attempt["file"] = str(saved)
                    result.tried.append(attempt)
                    if index:
                        result.chunks = await studytext.index_text(entry_id, text)
                    return result.as_dict()

                fname = await download_study_pdf(entry_id, cand.url, entry["title"])
                if not fname or not is_valid_pdf_file(pdf_path_for(entry_id)):
                    attempt["error"] = "download rejected (not a PDF)"
                    result.tried.append(attempt)
                    continue
                result.status, result.source, result.url = "pdf", cand.source, cand.url
                attempt["file"] = fname
                # Persist pdf_path HERE as well: without this, the file sat on
                # disk while the row kept saying "no PDF" — the screen went on
                # offering a download that had already succeeded.
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE entries SET pdf_path = ?, updated_at = datetime('now') WHERE id = ?",
                        [fname, entry_id],
                    )
                    conn.commit()
                finally:
                    conn.close()
                result.tried.append(attempt)
                if index:
                    from app import studytext
                    from app.pdf_rag import extract_pdf_text, index_pdf

                    try:
                        result.chunks = await index_pdf(entry_id, pdf_path_for(entry_id))
                    except Exception as exc:  # noqa: BLE001 - the file is still useful
                        result.index_error = True
                        logger.warning("Indexing #%s failed: %s", entry_id, exc)
                    else:
                        # Keep the readable text next to the PDF as well: one
                        # grep-able file per study, and the source for a future
                        # re-index that does not need the PDF again.
                        try:
                            pdf_text = await asyncio.to_thread(extract_pdf_text, pdf_path_for(entry_id))
                            if pdf_text.strip():
                                await asyncio.to_thread(studytext.save_text, entry_id, entry["title"], pdf_text)
                        except Exception as exc:  # noqa: BLE001 - RAG chunks remain indexed
                            result.text_save_error = True
                            logger.warning("Saving extracted text for #%s failed: %s", entry_id, exc)
                return result.as_dict()
            except ValueError as exc:  # SSRF guard said no
                attempt["error"] = f"blocked: {exc}"
                result.tried.append(attempt)
            except Exception as exc:  # noqa: BLE001
                attempt["error"] = type(exc).__name__
                result.tried.append(attempt)
        return result.as_dict()
