"""
External study import and bulk resolution services for Bransk.

Provides:
  * Bulk identifier resolution (DOIs, arXiv IDs, PubMed PMIDs, URLs)
  * Duplicate checking against existing Bransk entries (via DOI & title matching)
  * Multi-study batch insertion with normalized metadata, tags, and date
  * Automated background full-text & PDF acquisition with RAG indexing
  * Bulk download and deletion actions
"""
from __future__ import annotations

import asyncio
import html as html_mod
import logging
import re
from datetime import date
from typing import Any, Optional
import xml.etree.ElementTree as ET

import httpx

from app.db import get_db
from app.models import normalize_doi
from app.study_dedup import same_study, titles_match
from app.studies_store import download_study_pdf, extract_pdf_url, is_valid_pdf_file, pdf_path_for
from app.pdf_rag import index_pdf
from app.tagging import normalize_tags, sync_tags

logger = logging.getLogger("chronokg.study_import")

CROSSREF_API = "https://api.crossref.org/works"
EUROPE_PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ARXIV_API = "https://export.arxiv.org/api/query"

_HEADERS = {
    "User-Agent": "Bransk/1.0 (local academic research knowledge graph; mailto:research@chronokg.local)",
    "Accept": "application/json, text/xml, */*",
}


def extract_identifiers(raw_text: str) -> list[str]:
    """Extract distinct DOIs, arXiv IDs, PubMed PMIDs, and URLs from freeform text."""
    if not raw_text or not raw_text.strip():
        return []

    found: list[str] = []
    seen: set[str] = set()

    def add(item: str):
        cleaned = item.strip().rstrip(".;,)%]\"'")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            found.append(cleaned)

    # 1. Standard DOIs (e.g., 10.1038/s41586-020-2012-7)
    for m in re.finditer(r"10\.\d{4,9}/[^\s,;\"'<>]+", raw_text):
        add(m.group(0))

    # 2. arXiv IDs (e.g., arXiv:2301.07067 or arxiv.org/abs/2301.07067 or 2301.07067)
    for m in re.finditer(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", raw_text, re.IGNORECASE):
        add(f"arXiv:{m.group(1)}")

    # 3. PubMed PMIDs (e.g., pubmed.ncbi.nlm.nih.gov/32188939/ or PMID: 32188939)
    for m in re.finditer(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|pmid:\s*)([0-9]{5,10})", raw_text, re.IGNORECASE):
        add(f"PMID:{m.group(1)}")

    # 4. Europe PMC / PMC IDs (e.g., PMC7144277 or pmcid: PMC7144277)
    for m in re.finditer(r"\b(PMC[0-9]{4,10})\b", raw_text, re.IGNORECASE):
        add(m.group(1).upper())

    # 5. Standalone lines that are direct URLs
    for line in raw_text.splitlines():
        line_s = line.strip()
        if re.match(r"^https?://[^\s]+$", line_s):
            add(line_s)
        # Standalone arXiv pattern without prefix on its own line: e.g. "2301.07067"
        elif re.match(r"^[0-9]{4}\.[0-9]{4,5}(?:v\d+)?$", line_s):
            add(f"arXiv:{line_s}")

    return found


async def resolve_identifier_metadata(identifier: str, client: Optional[httpx.AsyncClient] = None) -> Optional[dict]:
    """Resolve a DOI, arXiv ID, PubMed PMID, or URL into structured study metadata."""
    ident = identifier.strip()
    if not ident:
        return None

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=_HEADERS)
        close_client = True

    try:
        # Check arXiv
        if ident.lower().startswith("arxiv:") or "arxiv.org" in ident.lower():
            m = re.search(r"([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", ident)
            if m:
                return await _resolve_arxiv(client, m.group(1))

        # Check PMID
        if ident.upper().startswith("PMID:"):
            pmid = ident.split(":", 1)[1].strip()
            return await _resolve_europe_pmc_query(client, f"EXT_ID:{pmid} AND SRC:MED")

        # Check PMCID
        if ident.upper().startswith("PMC") and ident[3:].isdigit():
            return await _resolve_europe_pmc_query(client, f"PMCID:{ident.upper()}")

        # Check if URL contains DOI
        doi_match = re.search(r"10\.\d{4,9}/[^\s,;\"'<>]+", ident)
        bare_doi = doi_match.group(0).rstrip(".;,") if doi_match else ""
        if not bare_doi and (ident.startswith("10.") and "/" in ident):
            bare_doi = ident.rstrip(".;,")

        if bare_doi:
            res = await _resolve_crossref_doi(client, bare_doi)
            if res:
                return res
            # Fallback to Europe PMC
            res_pmc = await _resolve_europe_pmc_query(client, f'DOI:"{bare_doi}"')
            if res_pmc:
                return res_pmc

        # If it's a PubMed URL
        pm_match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/([0-9]{5,10})", ident)
        if pm_match:
            return await _resolve_europe_pmc_query(client, f"EXT_ID:{pm_match.group(1)} AND SRC:MED")

        # If it's a generic academic URL or unresolved
        return None
    finally:
        if close_client:
            await client.aclose()


async def _resolve_crossref_doi(client: httpx.AsyncClient, doi: str) -> Optional[dict]:
    try:
        resp = await client.get(f"{CROSSREF_API}/{doi}", headers=_HEADERS, timeout=8.0)
        if resp.status_code != 200:
            return None
        msg = resp.json().get("message") or {}
        title_list = msg.get("title") or []
        title = str(title_list[0] if title_list else "").strip()
        if not title:
            return None

        author_objs = msg.get("author") or []
        authors_list = [
            f"{a.get('given', '')} {a.get('family', '')}".strip() or a.get("family", "")
            for a in author_objs[:5]
            if a.get("family") or a.get("name")
        ]
        authors_str = ", ".join(filter(None, authors_list)) or "Unknown"
        if len(author_objs) > 5:
            authors_str += " et al."

        issued = (msg.get("issued", {}).get("date-parts") or [[None]])[0]
        year = issued[0] if issued and isinstance(issued[0], int) else None

        pdf_url = ""
        for lk in msg.get("link") or []:
            ct = (lk.get("content-type") or "").lower()
            if "pdf" in ct:
                pdf_url = lk.get("URL") or ""
                break

        abstract = str(msg.get("abstract") or "")
        clean_abstract = re.sub(r"<[^>]+>", " ", abstract).strip()

        stype = "peer-reviewed paper"
        type_str = str(msg.get("type") or "").lower()
        if "book" in type_str:
            stype = "book"
        elif any(w in title.lower() for w in ["systematic review", "meta-analysis"]):
            stype = "systematic review" if "systematic" in title.lower() else "meta-analysis"

        clean_doi = (msg.get("DOI") or doi).strip()
        return {
            "title": title,
            "authors": authors_str,
            "year": year,
            "doi": clean_doi,
            "url": msg.get("URL") or f"https://doi.org/{clean_doi}",
            "pdf_url": pdf_url,
            "abstract": clean_abstract,
            "source": "crossref",
            "study_type": stype,
            "is_oa": bool(pdf_url),
        }
    except Exception as exc:
        logger.info("Crossref resolution failed for DOI %s: %s", doi, exc)
        return None


async def _resolve_arxiv(client: httpx.AsyncClient, arxiv_id: str) -> Optional[dict]:
    try:
        clean_id = re.sub(r"v\d+$", "", arxiv_id.strip())
        resp = await client.get(f"{ARXIV_API}?id_list={clean_id}", headers=_HEADERS, timeout=8.0)
        if resp.status_code != 200:
            return None

        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return None

        title_elem = entry.find("atom:title", ns)
        title = " ".join((title_elem.text or "").split()) if title_elem is not None else ""
        if not title:
            return None

        authors = [
            a.find("atom:name", ns).text.strip()
            for a in entry.findall("atom:author", ns)
            if a.find("atom:name", ns) is not None and a.find("atom:name", ns).text
        ]
        authors_str = ", ".join(authors[:5])
        if len(authors) > 5:
            authors_str += " et al."

        pub_elem = entry.find("atom:published", ns)
        year = int(pub_elem.text[:4]) if pub_elem is not None and pub_elem.text else None

        summary_elem = entry.find("atom:summary", ns)
        abstract = " ".join((summary_elem.text or "").split()) if summary_elem is not None else ""

        pdf_url = f"https://arxiv.org/pdf/{clean_id}"
        abs_url = f"https://arxiv.org/abs/{clean_id}"
        doi = f"10.48550/arXiv.{clean_id}"

        return {
            "title": title,
            "authors": authors_str,
            "year": year,
            "doi": doi,
            "url": abs_url,
            "pdf_url": pdf_url,
            "abstract": abstract,
            "source": "arxiv",
            "study_type": "peer-reviewed paper",
            "is_oa": True,
        }
    except Exception as exc:
        logger.info("arXiv resolution failed for ID %s: %s", arxiv_id, exc)
        return None


async def _resolve_europe_pmc_query(client: httpx.AsyncClient, query: str) -> Optional[dict]:
    try:
        resp = await client.get(
            EUROPE_PMC_API,
            params={"query": query, "format": "json", "pageSize": 1},
            headers=_HEADERS,
            timeout=8.0,
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("resultList", {}).get("result", [])
        if not items:
            return None
        r = items[0]

        title = str(r.get("title") or "").rstrip(".").strip()
        if not title:
            return None

        authors = str(r.get("authorString") or "Unknown").strip()
        year = None
        try:
            year = int(r.get("pubYear"))
        except (ValueError, TypeError):
            pass

        doi = str(r.get("doi") or "").strip()
        pmcid = r.get("pmcid")
        pdf_url = f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""
        is_oa = r.get("isOpenAccess") == "Y" or bool(pmcid)
        abstract = str(r.get("abstractText") or "").strip()

        stype = "peer-reviewed paper"
        if any(w in title.lower() for w in ["systematic review", "meta-analysis"]):
            stype = "systematic review" if "systematic" in title.lower() else "meta-analysis"

        source_url = f"https://doi.org/{doi}" if doi else (f"https://europepmc.org/article/MED/{r.get('id')}" if r.get("id") else "")

        return {
            "title": title,
            "authors": authors,
            "year": year,
            "doi": doi,
            "url": source_url,
            "pdf_url": pdf_url,
            "abstract": abstract,
            "source": "europepmc",
            "study_type": stype,
            "is_oa": is_oa,
        }
    except Exception as exc:
        logger.info("Europe PMC query resolution failed for query '%s': %s", query, exc)
        return None


def find_duplicate_study(conn, title: str, doi: str = "", source_url: str = "") -> Optional[dict]:
    """Check if a study already exists in Bransk by matching DOI or normalized title."""
    # 1. Direct DOI check if available
    clean_doi = ""
    if doi:
        try:
            clean_doi = normalize_doi(doi)
        except ValueError:
            clean_doi = doi.strip()

    if clean_doi:
        row = conn.execute(
            "SELECT id, title, doi, source FROM entries WHERE archived = 0 AND doi = ?",
            [clean_doi],
        ).fetchone()
        if row:
            return dict(row)

    # 2. Check title matching against all non-archived studies
    studies = conn.execute(
        """SELECT id, title, source FROM entries
           WHERE archived = 0
             AND (study_type != '' OR tags LIKE '%study%' OR tags LIKE '%pdf%' OR (doi IS NOT NULL AND doi != ''))"""
    ).fetchall()

    for s in studies:
        stitle = s["title"]
        ssource = s["source"] or ""
        if titles_match(title, stitle) or same_study(title, source_url, stitle, ssource):
            return dict(s)

    return None


def _format_study_markdown(
    title: str,
    authors: str,
    year: Optional[int],
    doi: str,
    source: str,
    pdf_url: str,
    abstract: str,
    notes: str = "",
) -> str:
    """Format markdown content body for an imported study."""
    lines = [f"# {title}", ""]
    if authors:
        lines.append(f"**Autori:** {authors}")
    if year:
        lines.append(f"**An:** {year}")
    if doi:
        lines.append(f"**DOI:** {doi}")
    if source:
        lines.append(f"**Sursă:** {source}")
    if pdf_url:
        lines.append(f"**PDF:** {pdf_url}")

    if abstract:
        lines.extend(["", "### Rezumat", abstract])
    if notes:
        lines.extend(["", "### Observații & Note", notes])

    return "\n".join(lines).strip()


def insert_study_entry(conn, data: dict) -> int:
    """Insert a verified study into the `entries` table and commit."""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Title is required for study import")

    authors = str(data.get("authors") or "").strip()
    year = data.get("year")
    try:
        year = int(year) if year is not None else None
    except (ValueError, TypeError):
        year = None

    raw_doi = str(data.get("doi") or "").strip()
    doi = ""
    if raw_doi:
        try:
            doi = normalize_doi(raw_doi)
        except ValueError:
            doi = raw_doi

    source = str(data.get("source") or data.get("url") or (f"https://doi.org/{doi}" if doi else "")).strip()
    pdf_url = str(data.get("pdf_url") or "").strip()
    abstract = str(data.get("abstract") or data.get("snippet") or "").strip()
    notes = str(data.get("notes") or "").strip()

    content = data.get("content") or _format_study_markdown(
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        source=source,
        pdf_url=pdf_url,
        abstract=abstract,
        notes=notes,
    )

    entry_date = f"{year:04d}-01-01" if year else date.today().isoformat()
    raw_tags = str(data.get("tags") or "")
    tags_str = normalize_tags(f"study,external-import,{raw_tags}")

    study_type = str(data.get("study_type") or "peer-reviewed paper").strip()
    quality = data.get("quality")
    if quality is None:
        quality = 4
    try:
        quality = max(1, min(5, int(quality)))
    except (ValueError, TypeError):
        quality = 4

    design = str(data.get("design") or "").strip()
    effect_direction = str(data.get("effect_direction") or "").strip()

    cur = conn.execute(
        """INSERT INTO entries (
            title, content, entry_date, tags, source, importance,
            study_type, authors, quality, url_validated, design,
            effect_direction, doi, bib_title, bib_authors, bib_year
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            title,
            content,
            entry_date,
            tags_str[:500],
            source,
            quality,
            study_type,
            authors,
            quality,
            1 if source else 0,
            design,
            effect_direction,
            doi,
            title,
            authors,
            str(year) if year else "",
        ],
    )
    sync_tags(conn, tags_str)
    conn.commit()
    return cur.lastrowid


async def bulk_import_studies(
    studies: list[dict],
    raw_text: str = "",
    auto_download_pdfs: bool = True,
) -> dict[str, Any]:
    """Import a batch of studies or freeform identifiers into Bransk."""
    candidate_items: list[dict] = list(studies)
    unresolved: list[dict] = []

    # 1. Parse and resolve any raw_text identifiers
    if raw_text and raw_text.strip():
        identifiers = extract_identifiers(raw_text)
        logger.info("Extracted %d identifiers from raw text", len(identifiers))
        if identifiers:
            sem = asyncio.Semaphore(5)
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=_HEADERS) as client:
                async def _res(id_str: str):
                    async with sem:
                        return await resolve_identifier_metadata(id_str, client=client)

                tasks = [_res(i) for i in identifiers]
                resolved_results = await asyncio.gather(*tasks, return_exceptions=True)
                for id_str, r in zip(identifiers, resolved_results):
                    if isinstance(r, dict) and r.get("title"):
                        candidate_items.append(r)
                    else:
                        # Nu arunca identificatorul tăcut: raportul final trebuie
                        # să arate exact ce nu a putut fi rezolvat și de ce.
                        unresolved.append({
                            "identifier": id_str,
                            "reason": (str(r)[:120] if isinstance(r, Exception)
                                       else "Nerezolvat în bazele academice (Crossref/Europe PMC/arXiv)"),
                        })

    # Deduplicate candidates amongst themselves by DOI or title
    unique_candidates: list[dict] = []
    seen_dois: set[str] = set()
    for item in candidate_items:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        doi = (item.get("doi") or "").strip().lower()
        if doi and doi in seen_dois:
            continue
        if any(titles_match(title, u.get("title") or "") for u in unique_candidates):
            continue
        if doi:
            seen_dois.add(doi)
        unique_candidates.append(item)

    imported_records: list[dict] = []
    skipped_records: list[dict] = []

    # 2. Check duplicates in Bransk DB and insert
    conn = get_db()
    try:
        for item in unique_candidates:
            title = item.get("title", "")
            doi = item.get("doi", "")
            url = item.get("url") or item.get("source") or ""
            dup = find_duplicate_study(conn, title, doi=doi, source_url=url)
            if dup:
                skipped_records.append({
                    "title": title,
                    "doi": doi,
                    "reason": f"Studiul există deja în bibliotecă (# {dup['id']}: {dup['title'][:60]})",
                    "existing_id": dup["id"],
                })
                continue

            try:
                new_id = insert_study_entry(conn, item)
                imported_records.append({
                    "id": new_id,
                    "title": title,
                    "authors": item.get("authors", ""),
                    "year": item.get("year"),
                    "doi": doi,
                    "pdf_url": item.get("pdf_url", ""),
                    "source": item.get("source", "") or url,
                    "pdf_downloaded": False,
                    "pdf_index_failed": False,
                })
            except Exception as ins_exc:
                logger.error("Failed inserting study '%s': %s", title, ins_exc)
                skipped_records.append({
                    "title": title,
                    "doi": doi,
                    "reason": f"Eroare la salvare: {ins_exc}",
                })
    finally:
        conn.close()

    # 3. Automated background PDF / Full-text acquisition if requested
    pdfs_downloaded = 0
    pdfs_index_failed = 0
    if auto_download_pdfs and imported_records:
        from app.fulltext import fetch_fulltext

        async def _download_one(rec: dict):
            nonlocal pdfs_downloaded, pdfs_index_failed
            eid = rec["id"]
            pdf_url = rec.get("pdf_url")
            fname = None

            # Attempt direct download first if explicit PDF URL
            if pdf_url:
                try:
                    fname = await download_study_pdf(eid, pdf_url, rec["title"])
                except Exception as dl_err:
                    logger.info("Direct PDF download failed for #%d: %s", eid, dl_err)

            if fname:
                conn_u = get_db()
                try:
                    conn_u.execute("UPDATE entries SET pdf_path = ?, updated_at = datetime('now') WHERE id = ?", [fname, eid])
                    conn_u.commit()
                finally:
                    conn_u.close()
                rec["pdf_downloaded"] = True
                pdfs_downloaded += 1
                # Persisted PDFs remain available to the reader even if RAG
                # indexing fails; report download success truthfully and log
                # the independent indexing failure rather than swallowing it
                # in gather(return_exceptions=True).
                try:
                    await index_pdf(eid, pdf_path_for(eid))
                except Exception as index_exc:
                    rec["pdf_index_failed"] = True
                    pdfs_index_failed += 1
                    logger.warning("PDF indexing after import failed for #%d: %s", eid, index_exc)
                return

            # Fallback to multi-repository full-text hunter (OpenAlex, Unpaywall, PMC, arXiv)
            try:
                ft_res = await fetch_fulltext(eid, index=True)
                if ft_res.get("status") == "pdf":
                    rec["pdf_downloaded"] = True
                    pdfs_downloaded += 1
                    if ft_res.get("index_error"):
                        rec["pdf_index_failed"] = True
                        pdfs_index_failed += 1
            except Exception as ft_exc:
                logger.info("Fulltext acquisition fallback failed for #%d: %s", eid, ft_exc)

        # Download up to 3 concurrently to be polite
        sem_dl = asyncio.Semaphore(3)
        async def _guarded(rec: dict):
            async with sem_dl:
                await _download_one(rec)

        await asyncio.gather(*[_guarded(r) for r in imported_records], return_exceptions=True)

    return {
        "imported": imported_records,
        "skipped": skipped_records,
        "unresolved_identifiers": unresolved,
        "total_imported": len(imported_records),
        "total_skipped": len(skipped_records),
        "pdfs_downloaded": pdfs_downloaded,
        "pdfs_failed": sum(not rec["pdf_downloaded"] for rec in imported_records) if auto_download_pdfs else 0,
        "pdfs_index_failed": pdfs_index_failed,
    }


async def bulk_download_studies(entry_ids: list[int]) -> dict[str, Any]:
    """Batch-download and index PDFs for a specified list of study entry IDs."""
    if not entry_ids:
        return {"downloaded": [], "failed": [], "indexed_chunks": 0}

    from app.fulltext import fetch_fulltext

    conn = get_db()
    try:
        ph = ",".join("?" for _ in entry_ids)
        rows = conn.execute(
            f"SELECT * FROM entries WHERE id IN ({ph}) AND archived = 0",
            entry_ids,
        ).fetchall()
        entries = [dict(r) for r in rows]
    finally:
        conn.close()

    downloaded = []
    failed = []
    total_indexed = 0

    for entry in entries:
        eid = entry["id"]
        if entry.get("pdf_path"):
            downloaded.append(eid)
            continue

        url = extract_pdf_url(entry)
        fname = await download_study_pdf(eid, url, entry["title"]) if url else None
        if not fname:
            try:
                ft_res = await fetch_fulltext(eid, index=True)
                if ft_res.get("status") == "pdf":
                    conn_r = get_db()
                    try:
                        r = conn_r.execute("SELECT pdf_path FROM entries WHERE id = ?", [eid]).fetchone()
                        fname = r["pdf_path"] if r else None
                    finally:
                        conn_r.close()
            except Exception as ft_exc:
                logger.info("Fulltext fallback in bulk-download failed for #%s: %s", eid, ft_exc)

        if fname:
            conn_u = get_db()
            try:
                conn_u.execute("UPDATE entries SET pdf_path = ?, updated_at = datetime('now') WHERE id = ?", [fname, eid])
                conn_u.commit()
            finally:
                conn_u.close()
            downloaded.append(eid)
            total_indexed += await index_pdf(eid, pdf_path_for(eid))
        else:
            failed.append({"id": eid, "title": entry["title"][:80]})

    return {"downloaded": downloaded, "failed": failed, "indexed_chunks": total_indexed}


def bulk_delete_studies(entry_ids: list[int]) -> dict[str, int]:
    """Delete selected studies and their associated relationships and theory evidence."""
    if not entry_ids:
        return {"deleted": 0}

    conn = get_db()
    try:
        ph = ",".join("?" for _ in entry_ids)
        conn.execute(f"DELETE FROM relationships WHERE from_id IN ({ph}) OR to_id IN ({ph})", [*entry_ids, *entry_ids])
        conn.execute(f"DELETE FROM theory_evidence WHERE entry_id IN ({ph})", entry_ids)
        cur = conn.execute(f"DELETE FROM entries WHERE id IN ({ph})", entry_ids)
        deleted_count = cur.rowcount
        conn.commit()
        return {"deleted": deleted_count}
    finally:
        conn.close()
