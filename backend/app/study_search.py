"""
Academic study search and query formulation across open scholarly databases.

Provides high-yield scholarly search across:
  * OpenAlex (250M+ scientific works, metadata, DOIs, open-access PDF links)
  * Europe PMC (biomedical & life sciences literature, PMC IDs, direct PDF renders)
  * arXiv (physics, mathematics, computer science, quantitative biology preprints)

Includes bilingual query translation (Romanian scientific terms -> English)
and multi-term combinatorial query generation.
"""
from __future__ import annotations

import asyncio
import difflib
import html as html_mod
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger("chronokg.study_search")

OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"
EUROPE_PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ARXIV_API = "https://export.arxiv.org/api/query"

_HEADERS = {
    "User-Agent": "Bransk/1.0 (local academic research knowledge graph app; mailto:research@chronokg.local)",
    "Accept": "application/json, text/xml, */*",
}

_RO_EN_TERMS: dict[str, str] = {
    "somn": "sleep",
    "somnului": "sleep",
    "memorie": "memory",
    "memoriei": "memory",
    "consolidare": "consolidation",
    "consolidarea": "consolidation",
    "creier": "brain",
    "creierului": "brain",
    "neuroplasticitate": "neuroplasticity",
    "neuroplasticitatii": "neuroplasticity",
    "neuroplasticității": "neuroplasticity",
    "invatare": "learning",
    "învățare": "learning",
    "invatarii": "learning",
    "învățării": "learning",
    "atentie": "attention",
    "atenție": "attention",
    "atentiei": "attention",
    "atenției": "attention",
    "constiinta": "consciousness",
    "conștiință": "consciousness",
    "constiintei": "consciousness",
    "conștiinței": "consciousness",
    "depresie": "depression",
    "depresiei": "depression",
    "anxietate": "anxiety",
    "anxietatii": "anxiety",
    "anxietății": "anxiety",
    "stres": "stress",
    "stresului": "stress",
    "alimentatie": "diet nutrition",
    "alimentație": "diet nutrition",
    "exercitiu": "exercise",
    "exercițiu": "exercise",
    "fizic": "physical",
    "mecanism": "mechanism",
    "mecanisme": "mechanisms",
    "mecanismele": "mechanisms",
    "cauzalitate": "causality causal",
    "cauza": "cause",
    "cauze": "causes",
    "efect": "effect",
    "efecte": "effects",
    "efectele": "effects",
    "tratament": "treatment",
    "tratamentul": "treatment",
    "terapie": "therapy",
    "boala": "disease",
    "boli": "diseases",
    "sanatate": "health",
    "sănătate": "health",
    "inteligenta": "intelligence",
    "inteligență": "intelligence",
    "artificiala": "artificial",
    "artificială": "artificial",
    "retele": "networks",
    "rețele": "networks",
    "neuronale": "neural",
    "celule": "cells",
    "celular": "cellular",
    "sinapse": "synapses",
    "sinaptic": "synaptic",
    "genetica": "genetics",
    "genetică": "genetics",
    "imbatranire": "aging",
    "îmbătrânire": "aging",
    "longevitate": "longevity",
    "inflamatie": "inflammation",
    "inflamație": "inflammation",
    "sistem": "system",
    "imunitar": "immune",
    "axul": "axis",
    "intestin": "gut microbiota",
    "microbiom": "microbiome",
    "despre": "",
    "privind": "",
    "asupra": "on",
    "si": "and",
    "și": "and",
    "in": "in",
    "în": "in",
    "cu": "with",
    "prin": "through",
    "studii": "",
    "studiu": "",
    "articole": "",
    "articol": "",
    "lucrari": "",
    "lucrări": "",
    "cercetare": "",
    "cercetari": "",
    "cercetări": "",
}


_STOPWORDS = {
    "ale", "al", "ai", "a", "de", "la", "pe", "in", "cu", "din", "prin",
    "despre", "pentru", "privind", "asupra", "sau", "ori", "si", "ca",
    "ce", "cum", "care", "un", "o", "unui", "unei", "unor", "dintre", "sub",
    "catre", "studii", "studiu", "articole", "articol", "lucrari", "lucrare",
    "cercetare", "cercetari", "cauta", "gaseste", "descarca", "salveaza",
    "tema", "privire", "legatura"
}

_STEM_MAP: dict[str, str] = {
    # Adversity & trauma
    "experient": "experiences",
    "advers": "adverse",
    "traum": "trauma",
    "abuz": "abuse",
    "neglij": "neglect",
    "stres": "stress",
    "amenint": "threat",
    "depriv": "deprivation",
    # Mentalizing & psychology
    "mentaliz": "mentalization",
    "reflexiv": "reflective functioning",
    "rezilient": "resilience",
    "atasament": "attachment",
    "psihopatolog": "psychopathology",
    "depresi": "depression",
    "anxiet": "anxiety",
    "personalitat": "personality",
    "borderline": "borderline",
    "disocier": "dissociation",
    "disociat": "dissociation",
    "epistem": "epistemic trust",
    # Environment & social
    "factor": "factors",
    "mediu": "environment",
    "social": "social",
    "cultural": "cultural",
    "economic": "economic",
    "socioeconomic": "socioeconomic",
    "comunitar": "community",
    "parent": "parental",
    "parint": "parents",
    "matern": "maternal",
    "copil": "child children",
    "adolescent": "adolescents",
    "adult": "adults",
    # Design & mechanics
    "efect": "effects",
    "individual": "individual",
    "combinat": "combined cumulative",
    "cumulativ": "cumulative",
    "capacitat": "capacity",
    "implicat": "implications",
    "mediere": "mediation",
    "mediator": "mediator",
    "moderare": "moderation",
    "relati": "relationship",
    "mecanism": "mechanisms",
    "cauzal": "causal causality",
    "cauz": "cause",
    # Neuroscience & general
    "somn": "sleep",
    "memori": "memory",
    "consolid": "consolidation",
    "creier": "brain",
    "neuroplastic": "neuroplasticity",
    "invatar": "learning",
    "tratament": "treatment",
    "terapi": "therapy",
    "sanatat": "health",
    "cognitiv": "cognitive",
    "comportament": "behavioral",
    "alimentat": "nutrition diet",
    "exerciti": "exercise physical",
    "imunitar": "immune",
    "inflamat": "inflammation",
    "microbiom": "microbiome gut",
    "intestin": "gut microbiota",
    "sinaps": "synapses synaptic",
    "celul": "cellular cells",
    "genetic": "genetics",
    "imbatranir": "aging",
    "longevitat": "longevity",
}


def _strip_ro_accents(s: str) -> str:
    return (
        s.replace("ț", "t")
        .replace("ţ", "t")
        .replace("ș", "s")
        .replace("ş", "s")
        .replace("ă", "a")
        .replace("â", "a")
        .replace("î", "i")
    )


def translate_scientific_query(query: str) -> str:
    """Translate Romanian concepts in the query to standard scientific English terms."""
    q = query.strip()
    if not q:
        return ""
    words = re.findall(r"[\w-]+", q.lower())
    terms: list[str] = []
    seen: set[str] = set()

    for w in words:
        w_norm = _strip_ro_accents(w)
        if w_norm in _STOPWORDS:
            continue
        matched = False
        # Direct dictionary match first
        if w in _RO_EN_TERMS:
            t = _RO_EN_TERMS[w]
            matched = True
            for e in t.split():
                if e and e not in seen:
                    terms.append(e)
                    seen.add(e)
        if not matched:
            for stem, eng in _STEM_MAP.items():
                if w_norm.startswith(stem):
                    for e in eng.split():
                        if e not in seen:
                            terms.append(e)
                            seen.add(e)
                    matched = True
                    break
        if not matched and len(w_norm) > 2:
            if w_norm not in seen:
                terms.append(w_norm)
                seen.add(w_norm)

    return " ".join(terms) if terms else q


def combine_search_terms(query: str) -> list[str]:
    """Generate academic query combinations (core terms, mechanistic, and systematic review)."""
    base = translate_scientific_query(query)
    words = [w for w in re.findall(r"[\w-]+", base) if len(w) > 2]
    if not words:
        return [query]
    combinations = [" ".join(words)]
    if len(words) >= 2:
        # Core terms
        combinations.append(f'"{words[0]} {words[1]}"')
    if "meta-analysis" not in base.lower() and "review" not in base.lower():
        combinations.append(f"{' '.join(words[:4])} meta-analysis")
    return list(dict.fromkeys(combinations))


def _title_similarity(a: str, b: str) -> float:
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


@dataclass
class AcademicStudyResult:
    title: str
    authors: str
    year: int | None
    doi: str
    pdf_url: str
    source: str
    snippet: str
    is_oa: bool = False

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "doi": self.doi,
            "pdf_url": self.pdf_url,
            "source": self.source,
            "snippet": self.snippet,
            "is_oa": self.is_oa,
        }


async def _search_openalex(client: httpx.AsyncClient, query: str, max_results: int = 5) -> list[AcademicStudyResult]:
    try:
        resp = await client.get(
            OPENALEX_API,
            params={"search": query, "per-page": max_results},
            headers=_HEADERS,
            timeout=8.0,
        )
        if resp.status_code != 200:
            return []
        data = resp.json().get("results") or []
        out: list[AcademicStudyResult] = []
        for w in data:
            title = str(w.get("title") or "").strip()
            if not title:
                continue
            authorships = w.get("authorships") or []
            authors_list = [
                str((a.get("author") or {}).get("display_name") or "").strip()
                for a in authorships[:4]
            ]
            authors_str = ", ".join(filter(None, authors_list)) or "Unknown"
            if len(authorships) > 4:
                authors_str += " et al."
            year = w.get("publication_year")
            doi = str(w.get("doi") or "").replace("https://doi.org/", "").strip()
            best_oa = w.get("best_oa_location") or {}
            pdf_url = str(best_oa.get("pdf_url") or "").strip()
            is_oa = bool(w.get("is_oa") or pdf_url)
            # Reconstruct abstract snippet if inverted index present
            abstract_idx = w.get("abstract_inverted_index") or {}
            snippet = ""
            if abstract_idx:
                word_positions: list[tuple[int, str]] = []
                for word, positions in abstract_idx.items():
                    for pos in positions:
                        word_positions.append((pos, word))
                word_positions.sort(key=lambda p: p[0])
                snippet = " ".join(p[1] for p in word_positions[:50])
            if not snippet:
                concepts = [c.get("display_name") for c in (w.get("concepts") or [])[:5] if c.get("display_name")]
                snippet = f"Topics: {', '.join(concepts)}" if concepts else ""
            out.append(AcademicStudyResult(
                title=title,
                authors=authors_str,
                year=year if isinstance(year, int) else None,
                doi=doi,
                pdf_url=pdf_url,
                source="openalex",
                snippet=snippet,
                is_oa=is_oa,
            ))
        return out
    except Exception as exc:
        logger.info("OpenAlex search failed (%s)", type(exc).__name__)
        return []


async def _search_europe_pmc(client: httpx.AsyncClient, query: str, max_results: int = 5) -> list[AcademicStudyResult]:
    try:
        resp = await client.get(
            EUROPE_PMC_API,
            params={"query": query, "format": "json", "pageSize": max_results},
            headers=_HEADERS,
            timeout=8.0,
        )
        if resp.status_code != 200:
            return []
        data = resp.json().get("resultList", {}).get("result", [])
        out: list[AcademicStudyResult] = []
        for r in data:
            title = str(r.get("title") or "").rstrip(".").strip()
            if not title:
                continue
            authors = str(r.get("authorString") or "Unknown").strip()
            year = None
            try:
                year = int(r.get("pubYear"))
            except (ValueError, TypeError):
                pass
            doi = str(r.get("doi") or "").strip()
            pmcid = r.get("pmcid")
            pdf_url = f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""
            isOpenAccess = r.get("isOpenAccess") == "Y" or bool(pmcid)
            snippet = str(r.get("abstractText") or "")[:350]
            out.append(AcademicStudyResult(
                title=title,
                authors=authors,
                year=year,
                doi=doi,
                pdf_url=pdf_url,
                source="europepmc",
                snippet=snippet,
                is_oa=isOpenAccess,
            ))
        return out
    except Exception as exc:
        logger.info("Europe PMC search failed (%s)", type(exc).__name__)
        return []


async def _search_crossref(client: httpx.AsyncClient, query: str, max_results: int = 5) -> list[AcademicStudyResult]:
    try:
        resp = await client.get(
            CROSSREF_API,
            params={"query": query, "rows": max_results},
            headers=_HEADERS,
            timeout=8.0,
        )
        if resp.status_code != 200:
            return []
        items = resp.json().get("message", {}).get("items", [])
        out: list[AcademicStudyResult] = []
        for item in items:
            title_list = item.get("title") or []
            title = str(title_list[0] if title_list else "").strip()
            if not title:
                continue
            author_objs = item.get("author") or []
            authors_list = [
                f"{a.get('given', '')} {a.get('family', '')}".strip() or a.get("family", "")
                for a in author_objs[:4]
                if a.get("family") or a.get("name")
            ]
            authors_str = ", ".join(filter(None, authors_list)) or "Unknown"
            if len(author_objs) > 4:
                authors_str += " et al."
            issued = (item.get("issued", {}).get("date-parts") or [[None]])[0]
            year = issued[0] if issued and isinstance(issued[0], int) else None
            doi = str(item.get("DOI") or "").strip()
            pdf_url = ""
            for lk in item.get("link") or []:
                if "pdf" in (lk.get("content-type") or "").lower():
                    pdf_url = lk.get("URL") or ""
                    break
            abstract = str(item.get("abstract") or "")
            clean_abstract = re.sub(r"<[^>]+>", " ", abstract).strip()[:350]
            out.append(AcademicStudyResult(
                title=title,
                authors=authors_str,
                year=year,
                doi=doi,
                pdf_url=pdf_url,
                source="crossref",
                snippet=clean_abstract,
                is_oa=bool(pdf_url),
            ))
        return out
    except Exception as exc:
        logger.info("Crossref search failed (%s)", type(exc).__name__)
        return []


async def _search_arxiv(client: httpx.AsyncClient, query: str, max_results: int = 3) -> list[AcademicStudyResult]:
    try:
        resp = await client.get(
            ARXIV_API,
            params={"search_query": f"all:{query}", "max_results": max_results},
            headers=_HEADERS,
            timeout=8.0,
        )
        if resp.status_code != 200:
            return []
        entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.S)
        out: list[AcademicStudyResult] = []
        for block in entries:
            m_title = re.search(r"<title>(.*?)</title>", block, re.S)
            m_id = re.search(r"<id>http://arxiv.org/abs/([^<]+)</id>", block)
            m_published = re.search(r"<published>(\d{4})", block)
            m_summary = re.search(r"<summary>(.*?)</summary>", block, re.S)
            author_matches = re.findall(r"<author>\s*<name>(.*?)</name>", block, re.S)
            if not (m_title and m_id):
                continue
            title = " ".join(html_mod.unescape(m_title.group(1)).split())
            arxiv_id = m_id.group(1).strip()
            year = int(m_published.group(1)) if m_published else None
            authors = ", ".join(author_matches[:4]) + (" et al." if len(author_matches) > 4 else "")
            snippet = " ".join(html_mod.unescape(m_summary.group(1)).split())[:350] if m_summary else ""
            out.append(AcademicStudyResult(
                title=title,
                authors=authors or "arXiv Authors",
                year=year,
                doi=f"10.48550/arXiv.{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                source="arxiv",
                snippet=snippet,
                is_oa=True,
            ))
        return out
    except Exception as exc:
        logger.info("arXiv search failed (%s)", type(exc).__name__)
        return []


async def search_academic_studies(query: str, max_results: int = 6) -> list[dict]:
    """Search Crossref, Europe PMC, arXiv, and OpenAlex for peer-reviewed studies.
    
    Handles Romanian terminology translation, combines query variants,
    and deduplicates results by title similarity.
    """
    clean_q = query.strip()
    if not clean_q:
        return []
    
    translated_q = translate_scientific_query(clean_q)
    logger.info("Academic search query: '%s' -> translated: '%s'", clean_q, translated_q)
    
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        # Query Crossref, Europe PMC, arXiv, and OpenAlex concurrently
        res_crossref, res_pmc, res_arxiv, res_openalex = await asyncio.gather(
            _search_crossref(client, translated_q, max_results=max_results),
            _search_europe_pmc(client, translated_q, max_results=max_results),
            _search_arxiv(client, translated_q, max_results=3),
            _search_openalex(client, translated_q, max_results=max_results),
            return_exceptions=True,
        )

    candidates: list[AcademicStudyResult] = []
    for r in (res_crossref, res_pmc, res_arxiv, res_openalex):
        if isinstance(r, list):
            candidates.extend(r)

    # Deduplicate by title similarity
    unique: list[AcademicStudyResult] = []
    for cand in candidates:
        if any(_title_similarity(cand.title, u.title) >= 0.85 for u in unique):
            continue
        unique.append(cand)

    # Prioritize items with open access PDF available
    unique.sort(key=lambda s: (not s.is_oa, not bool(s.pdf_url)))
    return [u.as_dict() for u in unique[:max_results]]
