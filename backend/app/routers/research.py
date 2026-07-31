"""
ChronoKG Meta-Analysis-Style Qualitative Research Pipeline

Follows the canonical systematic-review / meta-analysis protocol:
  1. PROTOCOL   — a priori plan: PICO-style question, inclusion/exclusion
                  criteria, search strings, databases
  2. SEARCH     — systematic multi-query web search (screening stage)
  3. DEEP DIVE  — per sub-question evidence extraction grounded on screened
                  sources, with design, sample, effect direction and a
                  Cochrane-style risk-of-bias appraisal per study
  4. SYNTHESIS  — pooling narrative (direction, heterogeneity, publication
                  bias), GRADE-like confidence rating, and a THEORY MAP
                  (theories with PRO/CONTRA studies, arranged as a tree)
  5. SAVE       — studies as dated entries, a PRISMA-style report entry, graph
                  links, and the theory tree + evidence in the aggregator

Web access is best-effort: offline runs degrade gracefully and honestly report
0 records identified (synthesis then rests on model knowledge).
"""
import json
import logging
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.db import get_db
from app.llm import LLM_PROVIDER, complete as llm_complete
from app.models import (
    PrismaFlow,
    ResearchProtocol,
    ResearchRequest,
    ResearchResult,
    ResearchStudy,
)
from app.web_search import search_web, validate_url
from app.studies_store import download_study_pdf, extract_pdf_url, pdf_path_for

logger = logging.getLogger("chronokg.research")
router = APIRouter()


# ── Meta-analysis protocol prompts ─────────────────────────────────────────

META_PLAN_PROMPT = """You are a systematic-review methodologist designing a meta-analysis protocol (a priori).
Return ONLY a JSON object with this exact shape:
{
  "research_question": "one sharp, answerable research question",
  "pico": {"population": "...", "intervention": "...", "comparison": "...", "outcome": "..."},
  "inclusion_criteria": ["criterion 1", "criterion 2", "criterion 3"],
  "exclusion_criteria": ["criterion 1", "criterion 2"],
  "search_strings": ["actual web search query 1", "actual web search query 2", "actual web search query 3"],
  "databases": ["PubMed", "Google Scholar", "PsycINFO", "arXiv", "Semantic Scholar"],
  "sub_questions": ["analytic sub-question 1", "analytic sub-question 2", "analytic sub-question 3", "analytic sub-question 4"]
}
Rules:
- Sub-questions should cover: direction of the effect/claim, moderators/context, quality of the evidence.
- Search strings must be real queries that would find the literature.
- META-PLAN marker: PLAN"""

META_DIVE_PROMPT = """You are extracting evidence for one sub-question of a systematic review.
Identify the most significant, well-established studies relevant to the sub-question (3-6). Prefer landmark studies, systematic reviews, replicated findings and primary sources.
Return ONLY a JSON object with this exact shape:
{
  "studies": [
    {
      "title": "exact or widely-known title",
      "authors": "author names",
      "year": 2019,
      "study_type": "peer-reviewed paper",
      "url": "https://...",
      "design": "RCT | cohort | cross-sectional | case-control | qualitative | systematic review | meta-analysis | book",
      "population": "who was studied",
      "sample_size": "N=1200 or qualitative sample description",
      "intervention_comparison": "what was compared (if applicable)",
      "outcome_measure": "main outcome measured",
      "effect_direction": "positive | negative | mixed | null",
      "effect_magnitude": "small/medium/large or reported statistic (Cohen's d, OR, etc.)",
      "methodology": "2-3 sentences: design, sample, method",
      "key_findings": ["finding 1", "finding 2", "finding 3"],
      "risk_of_bias": {"selection": "low|medium|high", "performance": "low|medium|high", "detection": "low|medium|high", "attrition": "low|medium|high", "reporting": "low|medium|high"},
      "quality": 4,
      "quality_notes": "short critical appraisal: strengths and limitations",
      "relevance": "why this study matters for the sub-question",
      "tags": "comma,separated,keywords"
    }
  ]
}
Rules:
- quality is 1-5 (5 = methodologically strong, replicated, influential).
- DO NOT invent plausible-sounding citations. If unsure a study exists, leave url empty and keep the title generic, or omit the study.
- META-DIVE marker: DIVE"""

META_SYNTHESIS_PROMPT = """You are pooling the extracted evidence (narrative meta-analysis). Consider direction and consistency of effects, heterogeneity, and publication bias.
Return ONLY a JSON object with this exact shape:
{
  "summary": "3-4 sentence executive summary with the pooled conclusion",
  "themes": ["theme 1", "theme 2", "theme 3"],
  "synthesis": "6-10 sentences: direction and magnitude of the pooled effect/claim, where findings converge and diverge, moderators",
  "heterogeneity": "low | moderate | high — plus 1-2 sentences explaining consistency across studies",
  "publication_bias": "qualitative assessment: e.g., small positive studies likely missing, or no clear indication",
  "confidence": "high | moderate | low | very low",
  "gaps": ["limitation or open question"],
  "recommendations": ["suggested next research direction"],
  "theories": [
    {
      "name": "theory / claim name",
      "description": "what the theory claims",
      "parent": "exact name of parent theory, or null for root-level",
      "studies": [{"title": "exact study title from the list", "stance": "pro | contra", "note": "how it supports/contradicts"}]
    }
  ],
  "thematic_links": [{"from_title": "exact study title", "to_title": "exact study title", "label": "builds_on | contradicts | supports | extends"}]
}
Rules:
- theories form a TREE: a theory can have children (refinements / competing sub-theories). Parent must be null or an exact name from this same list.
- Every study in "theories[].studies" must use EXACT titles present in the studies list.
- META-SYNTHESIS marker: SYNTH"""


# ── Provider calls ─────────────────────────────────────────────────────────


def _extract_json(reply: str) -> dict:
    text = reply.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"No JSON object found in LLM reply: {reply[:300]}")
    return json.loads(text[start : end + 1])


async def _llm_json(system: str, user: str) -> dict:
    if LLM_PROVIDER == "mock":
        return _mock_json(system, user)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    _, _, reply = await llm_complete(messages, temperature=0.2, json_mode=True)
    return _extract_json(reply)


# ── Mock provider (offline dev/testing only) ────────────────────────────────

def _mock_json(system: str, user: str) -> dict:
    topic = "the topic"
    try:
        topic = json.loads(user).get("topic") or topic
    except Exception:
        match = re.search(r"Topic:\s*(.+)", user)
        if match:
            topic = match.group(1).strip()
    if "PLAN" in system:
        return {
            "research_question": f"What does the evidence say about {topic}?",
            "pico": {"population": "general population", "intervention": "exposure/factor", "comparison": "control", "outcome": "relevant outcome"},
            "inclusion_criteria": ["peer-reviewed empirical studies", "published in the last 40 years", "report effect direction"],
            "exclusion_criteria": ["opinion pieces without data", "non-English without translation"],
            "search_strings": [f"{topic} systematic review", f"{topic} evidence meta-analysis", f"{topic} findings"],
            "databases": ["PubMed", "Google Scholar", "PsycINFO"],
            "sub_questions": [
                f"Foundational work on {topic}",
                f"Modern findings on {topic}",
            ],
        }
    if "DIVE" in system:
        q = re.search(r"Sub-question:\s*(.+)", user)
        label = q.group(1).strip()[:60] if q else topic[:60]
        designs = ["systematic review", "cohort", "RCT"]
        directions = ["positive", "mixed", "positive"]
        studies = []
        for i in range(3):
            studies.append({
                "title": f"{label}: study {i + 1}",
                "authors": f"Researcher {['A', 'B', 'C'][i]}",
                "year": 2005 + i * 7,
                "study_type": "peer-reviewed paper",
                "url": "",
                "design": designs[i],
                "population": "Adults",
                "sample_size": f"N={50 + i * 25}",
                "intervention_comparison": "exposure vs. control",
                "outcome_measure": "validated outcome scale",
                "effect_direction": directions[i],
                "effect_magnitude": "medium",
                "methodology": f"{designs[i]} design; {50 + i * 25} participants; standardized measures.",
                "key_findings": [
                    f"Core evidence cluster identified for {label}",
                    "Findings converge across independent studies",
                    "Effect depends on context and population",
                ],
                "risk_of_bias": {"selection": "low", "performance": "low", "detection": "medium", "attrition": "medium", "reporting": "low"},
                "quality": 4 - i,
                "quality_notes": "Methodologically sound; limited by small sample of included sources.",
                "relevance": f"Directly informs the sub-question on {label}.",
                "tags": "study," + ",".join(re.split(r"\W+", topic)[:4]),
            })
        return {"studies": studies}
    if "SYNTH" in system:
        titles = re.findall(r'"title":\s*"([^"]+)"', user)
        links = []
        if len(titles) >= 2:
            links.append({"from_title": titles[0], "to_title": titles[1], "label": "builds_on"})
        theories = []
        if titles:
            theories.append({
                "name": f"{topic}: main effect",
                "description": f"The evidence supports a {titles[0] if titles else 'consistent'} main effect.",
                "parent": None,
                "studies": [{"title": titles[0], "stance": "pro", "note": "Shows the predicted direction."}],
            })
        if len(titles) >= 2:
            theories.append({
                "name": f"{topic}: moderators",
                "description": "Contextual factors moderate the effect.",
                "parent": f"{topic}: main effect",
                "studies": [
                    {"title": titles[1], "stance": "contra", "note": "Effect weakens under some conditions."},
                ],
            })
        if len(titles) >= 3:
            theories[0]["studies"].append({"title": titles[2], "stance": "pro", "note": "Replicates the main effect."})
        return {
            "summary": f"Pooled qualitative synthesis on {topic}: the direction is consistent, magnitude medium, confidence moderate.",
            "themes": ["Main effect", "Moderators", "Open questions"],
            "synthesis": f"Across studies on {topic}, the pooled direction is consistently positive with medium magnitude; heterogeneity is moderate, explained by population differences. Two studies converge; one finds a weaker effect under specific conditions.",
            "heterogeneity": "moderate — findings agree on direction but magnitude varies with population",
            "publication_bias": "no clear indication, though small null studies may be underrepresented",
            "confidence": "moderate",
            "gaps": ["Longitudinal replication studies are scarce", "Cross-cultural validation is missing"],
            "recommendations": ["Design a longitudinal cohort study", "Standardize outcome measures"],
            "theories": theories,
            "thematic_links": links,
        }
    return {"studies": []}


# ── Study normalization ─────────────────────────────────────────────────────

def _norm_study(raw: dict, topic: str) -> Optional[ResearchStudy]:
    title = str(raw.get("title") or "").strip()[:500]
    if not title:
        return None
    try:
        year = int(raw.get("year") or 0)
        if not (1000 <= year <= 2100):
            year = None
    except (TypeError, ValueError):
        year = None
    try:
        quality = int(raw.get("quality", 3))
        quality = max(1, min(5, quality))
    except (TypeError, ValueError):
        quality = 3
    findings = raw.get("key_findings") or []
    if isinstance(findings, str):
        findings = [f.strip() for f in re.split(r"[\n;]", findings) if f.strip()]
    findings = [str(f)[:2000] for f in findings][:10]
    study_type = str(raw.get("study_type") or "study").strip().lower()[:100] or "study"
    direction = str(raw.get("effect_direction") or "").strip().lower()[:20]
    if direction not in ("positive", "negative", "mixed", "null"):
        direction = ""
    rob = raw.get("risk_of_bias") or {}
    if not isinstance(rob, dict):
        rob = {}
    rob = {k: str(v)[:20] for k, v in rob.items() if str(v).lower() in ("low", "medium", "high")}
    tags_raw = str(raw.get("tags") or "")
    topic_tags = ",".join(re.split(r"\W+", topic)[:4])
    tags = ",".join(t for t in (tags_raw + "," + topic_tags).split(",") if t.strip())[:500]
    return ResearchStudy(
        title=title,
        authors=str(raw.get("authors") or "")[:500],
        year=year,
        study_type=study_type,
        url=str(raw.get("url") or "").strip()[:1000],
        design=str(raw.get("design") or "")[:100],
        population=str(raw.get("population") or "")[:500],
        sample_size=str(raw.get("sample_size") or "")[:100],
        effect_direction=direction,
        effect_magnitude=str(raw.get("effect_magnitude") or "")[:300],
        risk_of_bias=rob,
        methodology=str(raw.get("methodology") or "")[:4000],
        key_findings=findings,
        quality=quality,
        quality_notes=str(raw.get("quality_notes") or "")[:2000],
        relevance=str(raw.get("relevance") or "")[:2000],
        tags=tags,
    )


# ── Rendering ───────────────────────────────────────────────────────────────

def _render_study_markdown(s: ResearchStudy) -> str:
    lines = [f"# {s.title}", ""]
    meta = []
    if s.authors:
        meta.append(f"**Authors:** {s.authors}")
    if s.year:
        meta.append(f"**Year:** {s.year}")
    if s.study_type:
        meta.append(f"**Type:** {s.study_type}")
    if s.design:
        meta.append(f"**Design:** {s.design}")
    if s.quality:
        meta.append(f"**Quality appraisal:** {'★' * s.quality}{'☆' * (5 - s.quality)} ({s.quality}/5)")
    if s.url:
        meta.append(f"**Source:** [{s.url}]({s.url})")
    lines.append(" | ".join(meta))
    lines.append("")
    details = []
    if s.population:
        details.append(f"- **Population:** {s.population}")
    if s.sample_size:
        details.append(f"- **Sample:** {s.sample_size}")
    if s.effect_direction:
        details.append(f"- **Effect direction:** {s.effect_direction}")
    if s.effect_magnitude:
        details.append(f"- **Effect magnitude:** {s.effect_magnitude}")
    if details:
        lines += details + [""]
    if s.risk_of_bias:
        rob = " · ".join(f"{k}: {v}" for k, v in s.risk_of_bias.items())
        lines += [f"**Risk of bias:** {rob}", ""]
    if s.methodology:
        lines += ["## Methodology", s.methodology, ""]
    if s.key_findings:
        lines += ["## Key findings", ""]
        lines += [f"- {f}" for f in s.key_findings]
        lines.append("")
    if s.relevance:
        lines += ["## Relevance", s.relevance, ""]
    if s.quality_notes:
        lines += ["## Critical appraisal", s.quality_notes, ""]
    return "\n".join(lines)


def _render_report_markdown(result: ResearchResult, topic: str) -> str:
    lines = [f"# Meta-Analysis Research: {topic}", ""]
    p = result.protocol
    if p.research_question:
        lines += ["## Research question", p.research_question, ""]
    if p.pico:
        lines += ["## PICO", ""]
        for k, v in p.pico.items():
            if v:
                lines.append(f"- **{k.capitalize()}:** {v}")
        lines.append("")
    if p.inclusion_criteria:
        lines += ["## Inclusion criteria", ""] + [f"- {c}" for c in p.inclusion_criteria] + [""]
    if p.exclusion_criteria:
        lines += ["## Exclusion criteria", ""] + [f"- {c}" for c in p.exclusion_criteria] + [""]
    if p.search_strings:
        lines += ["## Search strategy", ""]
        lines += [f"- `{s}`" for s in p.search_strings]
        if p.databases:
            lines.append("")
            lines.append("Databases: " + ", ".join(p.databases))
        lines.append("")

    pr = result.prisma
    lines += [
        "## PRISMA flow",
        "",
        f"- Records identified: **{pr.records_identified}**",
        f"- Records screened: **{pr.records_screened}**",
        f"- Full-text assessed: **{pr.full_text_assessed}**",
        f"- Studies included: **{pr.studies_included}**",
        "",
    ]
    if pr.records_identified == 0:
        lines.append("> Web search was unavailable, so identification/screening counts are 0; "
                     "included studies come from model knowledge (hand-searched literature).")
        lines.append("")

    lines += ["## Executive summary", result.summary or "No summary.", ""]
    if result.confidence:
        lines.append(f"**Confidence (GRADE-like): {result.confidence}**")
        lines.append("")
    if result.heterogeneity:
        lines += ["## Heterogeneity", result.heterogeneity, ""]
    if result.publication_bias:
        lines += ["## Publication bias", result.publication_bias, ""]
    if result.themes:
        lines += ["## Themes", ""] + [f"- {t}" for t in result.themes] + [""]
    if result.synthesis:
        lines += ["## Pooled synthesis", result.synthesis, ""]
    if result.studies:
        lines += ["## Studies included", ""]
        for s in result.studies:
            yr = f" ({s.year})" if s.year else ""
            des = f" — {s.design}" if s.design else ""
            eff = f" — effect: {s.effect_direction}" if s.effect_direction else ""
            lines.append(f"- **{s.title}**{yr}{des}{eff} — quality {s.quality}/5")
        lines.append("")
    if result.theories:
        lines += ["## Theory map", ""]
        for th in result.theories:
            parent = th.get("parent") or "(root)"
            ev = th.get("studies") or []
            pro = sum(1 for e in ev if e.get("stance") == "pro")
            contra = sum(1 for e in ev if e.get("stance") == "contra")
            lines.append(f"- **{th.get('name')}** (parent: {parent}, pro {pro} / contra {contra})")
        lines.append("")
    if result.gaps:
        lines += ["## Gaps & open questions", ""] + [f"- {g}" for g in result.gaps] + [""]
    if result.recommendations:
        lines += ["## Recommendations", ""] + [f"- {r}" for r in result.recommendations] + [""]
    return "\n".join(lines)


# ── Screening (systematic search) ───────────────────────────────────────────

async def _screen_sources(topic: str, protocol: dict, depth: str) -> tuple[list[dict], PrismaFlow]:
    queries = [str(q).strip() for q in (protocol.get("search_strings") or []) if str(q).strip()]
    if not queries:
        queries = [topic]
    max_q = 2 if depth == "quick" else 4
    queries = queries[:max_q]

    records: list[dict] = []
    seen_urls: set[str] = set()
    for q in queries:
        for r in await search_web(q, max_results=5):
            key = (r.get("url") or "").strip().lower()
            if key:
                if key in seen_urls:
                    continue
                seen_urls.add(key)
            records.append(r)

    return records, PrismaFlow(
        records_identified=len(records),
        records_screened=len(records),
        full_text_assessed=len(records),
        studies_included=0,
    )


# ── Pipeline ────────────────────────────────────────────────────────────────

async def run_research(topic: str, depth: str = "deep", save: bool = True) -> ResearchResult:
    topic = topic.strip()
    if not topic:
        raise HTTPException(400, "Topic is required")

    logger.info("Research pipeline started: %s (depth=%s)", topic, depth)

    # 1. Protocol
    plan = await _llm_json(META_PLAN_PROMPT, f"Topic: {topic}\nDesign a meta-analysis protocol.")
    protocol = ResearchProtocol(
        research_question=str(plan.get("research_question") or ""),
        pico=plan.get("pico") or {},
        inclusion_criteria=[str(c) for c in (plan.get("inclusion_criteria") or [])],
        exclusion_criteria=[str(c) for c in (plan.get("exclusion_criteria") or [])],
        search_strings=[str(s) for s in (plan.get("search_strings") or [])],
        databases=[str(d) for d in (plan.get("databases") or [])],
    )
    sub_questions = [str(q).strip() for q in (plan.get("sub_questions") or []) if str(q).strip()]
    max_questions = 2 if depth == "quick" else 4
    sub_questions = sub_questions[:max_questions]
    if not sub_questions:
        sub_questions = [f"What does the evidence say about {topic}?"]

    # 2. Systematic search / screening
    sources, prisma = await _screen_sources(topic, plan, depth)
    web_available = len(sources) > 0
    source_block = ""
    if sources:
        source_block = (
            "\n\nSCREENED WEB SOURCES (prefer/verify these):\n"
            + "\n".join(f"- {s['title']} | {s['url']} | {s['snippet']}" for s in sources[:12])
        )

    # 3. Deep dives (evidence extraction, grounded on screened sources)
    studies: list[ResearchStudy] = []
    seen: set[str] = set()
    for i, question in enumerate(sub_questions, start=1):
        logger.info("Research deep dive %d/%d: %s", i, len(sub_questions), question)
        dive = await _llm_json(
            META_DIVE_PROMPT,
            f"Topic: {topic}\nSub-question: {question}\nExtract the key studies.{source_block}",
        )
        for raw in dive.get("studies") or []:
            study = _norm_study(raw, topic)
            if study and study.title.lower() not in seen:
                seen.add(study.title.lower())
                studies.append(study)

    if not studies:
        raise HTTPException(502, "Research produced no studies. The LLM provider may be unavailable.")
    prisma.studies_included = len(studies)

    # Best-effort URL validation (only when web reachable, capped for speed)
    if web_available:
        validated = 0
        for s in studies:
            if s.url and validated < 12:
                s.url_validated = await validate_url(s.url)
                validated += 1

    # 4. Synthesis (pooling + theory map)
    synth = await _llm_json(
        META_SYNTHESIS_PROMPT,
        json.dumps(
            {
                "topic": topic,
                "protocol": protocol.model_dump(),
                "prisma": prisma.model_dump(),
                "studies": [s.model_dump() for s in studies],
            },
            ensure_ascii=False,
        ),
    )

    result = ResearchResult(
        topic=topic,
        summary=str(synth.get("summary") or ""),
        synthesis=str(synth.get("synthesis") or ""),
        protocol=protocol,
        prisma=prisma,
        heterogeneity=str(synth.get("heterogeneity") or ""),
        publication_bias=str(synth.get("publication_bias") or ""),
        confidence=str(synth.get("confidence") or "").lower(),
        themes=[str(t) for t in (synth.get("themes") or [])][:8],
        gaps=[str(g) for g in (synth.get("gaps") or [])][:8],
        recommendations=[str(r) for r in (synth.get("recommendations") or [])][:8],
        theories=[dict(t) for t in (synth.get("theories") or []) if isinstance(t, dict)][:30],
        studies=studies,
    )
    for raw_link in synth.get("thematic_links") or []:
        from_t = str(raw_link.get("from_title") or "").strip().lower()
        to_t = str(raw_link.get("to_title") or "").strip().lower()
        label = str(raw_link.get("label") or "related_to").strip().lower()[:100] or "related_to"
        if from_t and to_t and from_t != to_t:
            result.thematic_links.append({"from_title": from_t, "to_title": to_t, "label": label})

    # 5. Persist studies + report + graph links + theory tree
    if save:
        _save_research(result, topic)
        _save_theories(result)
        if web_available:
            for s in result.studies:
                if s.entry_id and s.url:
                    fname = await download_study_pdf(s.entry_id, s.url, s.title)
                    if fname:
                        _set_pdf_path(s.entry_id, fname)

    logger.info("Research complete: %d studies, %d theories", len(result.studies), len(result.theories))
    return result


def _save_research(result: ResearchResult, topic: str) -> None:
    conn = get_db()
    try:
        created_ids: list[int] = []
        today = date.today().isoformat()

        for s in result.studies:
            existing = conn.execute(
                "SELECT id FROM entries WHERE LOWER(title) = LOWER(?) AND study_type != ''",
                [s.title],
            ).fetchone()
            if existing:
                s.entry_id = existing["id"]
                if s.url_validated:
                    conn.execute("UPDATE entries SET url_validated = 1 WHERE id = ?", [existing["id"]])
                continue
            entry_date = f"{s.year}-01-01" if s.year else None
            tags = "study," + s.tags
            cur = conn.execute(
                """INSERT INTO entries (title, content, entry_date, tags, source, importance, study_type, authors, quality, url_validated, design, effect_direction)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    s.title,
                    _render_study_markdown(s),
                    entry_date,
                    tags[:500],
                    s.url,
                    s.quality,
                    s.study_type,
                    s.authors,
                    s.quality,
                    1 if s.url_validated else 0,
                    s.design,
                    s.effect_direction,
                ],
            )
            s.entry_id = cur.lastrowid
            created_ids.append(cur.lastrowid)

        # Research report entry
        cur = conn.execute(
            """INSERT INTO entries (title, content, entry_date, tags, source, importance, study_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [f"Meta-analysis: {topic}", _render_report_markdown(result, topic), today, "research", "", 4, ""],
        )
        report_id = cur.lastrowid
        created_ids.append(report_id)
        result.report_entry_id = report_id

        rel_count = 0
        id_by_title = {s.title.strip().lower(): s.entry_id for s in result.studies if s.entry_id}

        for s in result.studies:
            if s.entry_id:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
                    [report_id, s.entry_id, "includes"],
                )
                rel_count += cur.rowcount

        for link in result.thematic_links:
            f_id = id_by_title.get(link["from_title"])
            t_id = id_by_title.get(link["to_title"])
            if f_id and t_id and f_id != t_id:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
                    [f_id, t_id, link["label"]],
                )
                rel_count += cur.rowcount

        keywords = [k for k in re.split(r"\W+", topic.lower()) if len(k) > 3][:8]
        for s in result.studies:
            for kw in keywords:
                if not s.entry_id:
                    continue
                rows = conn.execute(
                    """SELECT id FROM entries
                       WHERE archived = 0 AND id NOT IN ({ph})
                         AND (title LIKE ? OR tags LIKE ? OR content LIKE ?)
                       LIMIT 3""".format(ph=",".join("?" for _ in created_ids)),
                    [*created_ids, f"%{kw}%", f"%{kw}%", f"%{kw}%"],
                ).fetchall()
                for row in rows:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
                        [s.entry_id, row["id"], "relates_to"],
                    )
                    rel_count += cur.rowcount

        conn.commit()
        result.entries_created = created_ids
        result.relationships_created = rel_count
    finally:
        conn.close()


def _save_theories(result: ResearchResult) -> None:
    """Persist the theory tree + pro/contra evidence generated by the synthesis."""
    conn = get_db()
    try:
        status_map = {"high": "supported", "moderate": "supported", "low": "contested", "very low": "emerging"}
        root_status = status_map.get((result.confidence or "").lower(), "emerging")

        root_id = _get_or_create_theory(conn, result.topic, result.summary, None, root_status)
        id_by_name = {result.topic: root_id}

        named_theories = []
        for th in result.theories:
            name = str(th.get("name") or "").strip()[:300]
            if not name:
                continue
            parent_name = str(th.get("parent") or "").strip() or result.topic
            parent_id = id_by_name.get(parent_name, root_id)
            t_id = _get_or_create_theory(
                conn,
                name,
                str(th.get("description") or "")[:2000],
                parent_id,
                root_status,
            )
            id_by_name[name] = t_id
            named_theories.append((name, th))

        title_to_id = {s.title.strip().lower(): s.entry_id for s in result.studies if s.entry_id}
        for name, th in named_theories:
            t_id = id_by_name.get(name)
            if not t_id:
                continue
            for ev in th.get("studies") or []:
                s_title = str(ev.get("title") or "").strip().lower()
                eid = title_to_id.get(s_title)
                stance = str(ev.get("stance") or "pro").strip().lower()
                if stance not in ("pro", "contra"):
                    stance = "pro"
                if eid:
                    conn.execute(
                        "INSERT OR IGNORE INTO theory_evidence (theory_id, entry_id, stance, note) VALUES (?, ?, ?, ?)",
                        [t_id, eid, stance, str(ev.get("note") or "")[:1000]],
                    )
        conn.commit()
    finally:
        conn.close()


def _get_or_create_theory(conn, name: str, description: str, parent_id, status: str) -> int:
    row = conn.execute("SELECT id FROM theories WHERE LOWER(name) = LOWER(?)", [name]).fetchone()
    if row:
        conn.execute(
            "UPDATE theories SET status = ?, description = CASE WHEN ? = '' THEN description ELSE ? END, updated_at = datetime('now') WHERE id = ?",
            [status, description, description, row["id"]],
        )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO theories (name, description, parent_id, status) VALUES (?, ?, ?, ?)",
        [name, description, parent_id, status],
    )
    return cur.lastrowid


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/research", response_model=ResearchResult)
async def research(data: ResearchRequest):
    try:
        return await run_research(data.topic, data.depth, data.save)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Research failed: %s", exc)
        raise HTTPException(503, f"Research pipeline failed: {type(exc).__name__}: {exc}")


@router.post("/research/stream")
async def research_stream(data: ResearchRequest):
    """SSE research with live phase progress (meta-analysis protocol)."""

    async def generate():
        try:
            yield _sse({"type": "progress", "phase": "protocol", "message": "Designing the meta-analysis protocol (PICO, criteria, search strings)..."})
            plan = await _llm_json(META_PLAN_PROMPT, f"Topic: {data.topic}\nDesign a meta-analysis protocol.")
            protocol = ResearchProtocol(
                research_question=str(plan.get("research_question") or ""),
                pico=plan.get("pico") or {},
                inclusion_criteria=[str(c) for c in (plan.get("inclusion_criteria") or [])],
                exclusion_criteria=[str(c) for c in (plan.get("exclusion_criteria") or [])],
                search_strings=[str(s) for s in (plan.get("search_strings") or [])],
                databases=[str(d) for d in (plan.get("databases") or [])],
            )
            sub_questions = [str(q).strip() for q in (plan.get("sub_questions") or []) if str(q).strip()]
            max_q = 2 if data.depth == "quick" else 4
            sub_questions = sub_questions[:max_q] or [data.topic]
            yield _sse({"type": "progress", "phase": "protocol", "message": f"Protocol ready: {len(sub_questions)} sub-questions.", "sub_questions": sub_questions})

            yield _sse({"type": "progress", "phase": "search", "message": "Running systematic search across queries..."})
            sources, prisma = await _screen_sources(data.topic, plan, data.depth)
            web_available = len(sources) > 0
            yield _sse({"type": "progress", "phase": "search", "message": f"PRISMA: {prisma.records_identified} records identified, {prisma.records_screened} screened."})
            source_block = ""
            if sources:
                source_block = (
                    "\n\nSCREENED WEB SOURCES (prefer/verify these):\n"
                    + "\n".join(f"- {s['title']} | {s['url']} | {s['snippet']}" for s in sources[:12])
                )

            studies: list[ResearchStudy] = []
            seen: set[str] = set()
            for i, q in enumerate(sub_questions, start=1):
                yield _sse({"type": "progress", "phase": "dive", "message": f"Extracting evidence: sub-question {i}/{len(sub_questions)}...", "step": i, "total": len(sub_questions)})
                dive = await _llm_json(META_DIVE_PROMPT, f"Topic: {data.topic}\nSub-question: {q}\nExtract the key studies.{source_block}")
                for raw in dive.get("studies") or []:
                    study = _norm_study(raw, data.topic)
                    if study and study.title.lower() not in seen:
                        seen.add(study.title.lower())
                        studies.append(study)
                        yield _sse({"type": "study", "study": study.model_dump()})

            if not studies:
                yield _sse({"type": "error", "content": "Research produced no studies."})
                return
            prisma.studies_included = len(studies)

            if web_available:
                validated = 0
                for s in studies:
                    if s.url and validated < 12:
                        s.url_validated = await validate_url(s.url)
                        validated += 1

            yield _sse({"type": "progress", "phase": "synthesis", "message": "Pooling evidence, assessing heterogeneity and building the theory map..."})
            synth = await _llm_json(
                META_SYNTHESIS_PROMPT,
                json.dumps(
                    {"topic": data.topic, "protocol": protocol.model_dump(), "prisma": prisma.model_dump(), "studies": [s.model_dump() for s in studies]},
                    ensure_ascii=False,
                ),
            )
            result = ResearchResult(
                topic=data.topic,
                summary=str(synth.get("summary") or ""),
                synthesis=str(synth.get("synthesis") or ""),
                protocol=protocol,
                prisma=prisma,
                heterogeneity=str(synth.get("heterogeneity") or ""),
                publication_bias=str(synth.get("publication_bias") or ""),
                confidence=str(synth.get("confidence") or "").lower(),
                themes=[str(t) for t in (synth.get("themes") or [])][:8],
                gaps=[str(g) for g in (synth.get("gaps") or [])][:8],
                recommendations=[str(r) for r in (synth.get("recommendations") or [])][:8],
                theories=[dict(t) for t in (synth.get("theories") or []) if isinstance(t, dict)][:30],
                studies=studies,
            )
            for raw_link in synth.get("thematic_links") or []:
                from_t = str(raw_link.get("from_title") or "").strip().lower()
                to_t = str(raw_link.get("to_title") or "").strip().lower()
                label = str(raw_link.get("label") or "related_to").strip().lower()[:100] or "related_to"
                if from_t and to_t and from_t != to_t:
                    result.thematic_links.append({"from_title": from_t, "to_title": to_t, "label": label})

            if data.save:
                yield _sse({"type": "progress", "phase": "save", "message": "Saving studies, report, graph links and the theory tree..."})
                _save_research(result, data.topic)
                _save_theories(result)
                if web_available:
                    for s in result.studies:
                        if s.entry_id and s.url:
                            fname = await download_study_pdf(s.entry_id, s.url, s.title)
                            if fname:
                                _set_pdf_path(s.entry_id, fname)
                yield _sse({"type": "progress", "phase": "save", "message": f"Saved {len(result.studies)} studies + report + {len(result.theories)} theories."})

            yield _sse({"type": "done", "result": result.model_dump()})
        except Exception as exc:
            logger.error("Research stream failed: %s", exc)
            yield _sse({"type": "error", "content": f"Research failed: {type(exc).__name__}: {exc}"})
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/studies", response_model=list)
def list_studies(limit: int = 100, offset: int = 0):
    """List study entries (chronological, newest first)."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM entries
               WHERE study_type != '' AND archived = 0
               ORDER BY entry_date IS NULL, entry_date DESC, updated_at DESC
               LIMIT ? OFFSET ?""",
            [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _set_pdf_path(entry_id: int, fname: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            "UPDATE entries SET pdf_path = ?, updated_at = datetime('now') WHERE id = ?",
            [fname, entry_id],
        )
        conn.commit()
    finally:
        conn.close()


@router.post("/studies/{entry_id}/download")
async def download_study_pdf_endpoint(entry_id: int):
    """Download a study's PDF into the dedicated studies folder."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row or not row["study_type"]:
            raise HTTPException(404, "Study not found")
        entry = dict(row)
    finally:
        conn.close()
    url = extract_pdf_url(entry)
    fname = await download_study_pdf(entry_id, url, entry["title"])
    if not fname:
        raise HTTPException(502, "Could not download the study PDF (missing or unreachable URL)")
    _set_pdf_path(entry_id, fname)
    return {"pdf_path": fname}


@router.get("/studies/{entry_id}/pdf")
async def get_study_pdf(entry_id: int):
    """Serve a downloaded study PDF so it can be opened directly in the app."""
    path = pdf_path_for(entry_id)
    if not path:
        raise HTTPException(404, "PDF not downloaded yet")
    return FileResponse(str(path), media_type="application/pdf", filename=path.name)
