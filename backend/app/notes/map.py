"""MAP layer: edges and concepts over verified dossiers.

Product rule (same spirit as claims): an edge exists only with a motive
and a page; a concept exists only if backed by real claims. The LLM
proposes, the server validates and rejects what it cannot match to
existing rows. Never invent codes, pages or links.
"""

import json
import logging
import re

logger = logging.getLogger("chronokg.notes.map")

#: Minimum trimmed motive length for an edge to exist.
EDGE_MOTIVE_MIN_CHARS = 12
#: Claim statuses that count as verified evidence for the map.
VERIFIED_STATUSES = ("verificat", "verificat_cu_nuanta")
#: Prompt caps so one project cannot flood the model.
PROMPT_MAX_CHARS = 24000
MAX_CLAIMS_PER_DOSSIER = 40

_API_NOTE = "Răspunde în limba română."


def _row_dict(row) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


def _clean_json(text: str):
    clean = re.sub(r"^```(?:json)?\s*", "", (text or "").strip())
    return json.loads(re.sub(r"\s*```$", "", clean))


def _coerce_list(raw) -> list[dict]:
    if isinstance(raw, dict):
        for key in ("edges", "concepts", "muchii", "items", "results", "propuneri"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


async def _complete_json(system_prompt: str, user_prompt: str) -> list[dict]:
    """Temperature-0 JSON completion; ``[]`` on any failure. Never raises."""
    from app.llm import complete

    try:
        _, _, text = await complete(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}], json_mode=True, temperature=0)
    except Exception as exc:  # noqa: BLE001 - no proposal on LLM failure
        logger.info("Map proposal failed: %s", exc)
        return []
    try:
        return _coerce_list(_clean_json(text))
    except Exception as exc:  # noqa: BLE001 - malformed JSON means no proposal
        logger.info("Map proposal unparsable: %s", exc)
        return []


async def _llm_edges(dossier_payload, api_note="") -> list[dict]:
    """Propose edges between one dossier's claims. Never raises."""
    claims = dossier_payload.get("claims", []) if isinstance(dossier_payload, dict) else []
    lines = "\n".join(
        f"{c.get('code')}: {c.get('statement')} [pag. {c.get('page_printed') or '?'}"
        f" | {c.get('status')}]" for c in claims if isinstance(c, dict))
    system = ('Ești un cartograf al cunoașterii. Propui muchii între afirmațiile '
              'unui dosar. DOAR JSON: {"edges": [{"from_code": "A1", "to_code": "A2", '
              '"motive": "de ce se leagă (minim 12 caractere)", "page": "4", '
              '"claim_code": "A2"}]}. Coduri doar din listă, diferite între ele; '
              '`claim_code` (implicit `to_code`) susține muchia.')
    user = f"Afirmații:\n{lines}" + (f"\n\nNotă: {api_note}" if api_note else "")
    return await _complete_json(system, user)


async def _llm_concepts(project_payload, api_note="") -> list[dict]:
    """Propose cross-dossier concepts backed by verified claims. Never raises."""
    dossiers = project_payload.get("dossiers", []) if isinstance(project_payload, dict) else []
    blocks = []
    for dos in dossiers:
        if not isinstance(dos, dict):
            continue
        claims = "\n".join(
            f"  {c.get('code')}: {c.get('statement')} [pag. {c.get('page_printed') or '?'}]"
            for c in (dos.get("claims") or []) if isinstance(c, dict))
        blocks.append(f"Dosar {dos.get('dossier_id')} ({dos.get('title') or ''}):\n{claims}")
    system = ('Ești un cartograf al cunoașterii. Propui concepte transversale '
              'sprijinite de afirmații verificate. DOAR JSON: {"concepts": [{"label": '
              '"nume scurt", "description": "...", "links": [{"dossier_id": 1, '
              '"claim_code": "A1", "note": "cum sprijină afirmația conceptul", '
              '"page": "4"}]}]}. DOAR perechi (dossier_id, claim_code) din listă.')
    user = "Dosare:\n" + "\n\n".join(blocks) + (f"\n\nNotă: {api_note}" if api_note else "")
    return await _complete_json(system, user)


def _claim_statuses(conn, dossier_id: int) -> dict:
    rows = conn.execute(
        "SELECT code, status FROM note_claims WHERE dossier_id = ?", [dossier_id]).fetchall()
    return {str(r["code"]): str(r["status"]) for r in rows}


def _page_str(value) -> str:
    return "" if value is None else str(value).strip()


def edge_problems(conn, dossier_id: int, proposal: dict) -> list[str]:
    """Romanian reasons why a manual edge proposal is invalid (empty = ok)."""
    data = dict(proposal or {})
    from_code = str(data.get("from_code") or "").strip()
    to_code = str(data.get("to_code") or "").strip()
    motive = str(data.get("motive") or "").strip()
    page = _page_str(data.get("page"))
    claim_code = str(data.get("claim_code") or "").strip() or to_code
    codes = _claim_statuses(conn, dossier_id)
    problems: list[str] = []
    if not from_code or from_code not in codes or not to_code or to_code not in codes:
        problems.append("cod inexistent")
    if from_code and from_code == to_code:
        problems.append("muchie către sine")
    if len(motive) < EDGE_MOTIVE_MIN_CHARS:
        problems.append("motiv prea scurt")
    if not page:
        problems.append("pagina lipsă")
    if claim_code and claim_code not in codes and "cod inexistent" not in problems:
        problems.append("cod inexistent")
    return problems


def validate_edge(conn, dossier_id: int, proposal: dict) -> dict | None:
    """Normalized edge row, or ``None`` when the proposal breaks the rule."""
    if edge_problems(conn, dossier_id, proposal):
        return None
    data = proposal or {}
    to_code = str(data.get("to_code") or "").strip()
    return {"dossier_id": dossier_id,
            "from_code": str(data.get("from_code") or "").strip(),
            "to_code": to_code,
            "motive": str(data.get("motive") or "").strip(),
            "page": _page_str(data.get("page")),
            "claim_code": str(data.get("claim_code") or "").strip() or to_code}


async def generate_edges(conn, dossier_id: int, *, replace=False, apply=True) -> dict:
    """Ask the LLM for edges, keep only the ones real claims support."""
    if conn.execute(
            "SELECT id FROM note_dossiers WHERE id = ?", [dossier_id]).fetchone() is None:
        raise LookupError("Dosarul nu există.")
    claims = [_row_dict(r) for r in conn.execute(
        "SELECT code, statement, page_printed, status FROM note_claims"
        " WHERE dossier_id = ? ORDER BY id", [dossier_id]).fetchall()]
    status_of = {str(c.get("code")): str(c.get("status")) for c in claims}
    proposals = await _llm_edges({"dossier_id": dossier_id, "claims": claims}, _API_NOTE)
    accepted, seen, rejected = [], set(), 0
    for proposal in proposals:
        row = validate_edge(conn, dossier_id, proposal) if isinstance(proposal, dict) else None
        if row is None:
            rejected += 1
            continue
        if (row["from_code"], row["to_code"]) in seen:
            rejected += 1
            continue
        cited = (row["from_code"], row["to_code"], row["claim_code"])
        if any(status_of.get(code) not in VERIFIED_STATUSES for code in cited):
            rejected += 1  # unverified claims stay out
            continue
        seen.add((row["from_code"], row["to_code"]))
        accepted.append(row)
    if not apply:
        return {"dossier_id": dossier_id, "edges": accepted,
                "accepted": len(accepted), "rejected": rejected}
    try:
        if replace:
            conn.execute("DELETE FROM note_edges WHERE dossier_id = ?", [dossier_id])
        for row in accepted:
            conn.execute(
                "INSERT INTO note_edges (dossier_id, from_code, to_code, motive, page,"
                " claim_code) VALUES (?, ?, ?, ?, ?, ?)",
                [dossier_id, row["from_code"], row["to_code"],
                 row["motive"], row["page"], row["claim_code"]])
        conn.execute(
            "INSERT INTO note_rounds (dossier_id, summary, changed_json, open_json)"
            " VALUES (?, ?, '[]', '[]')",
            [dossier_id, f"Hartă: {len(accepted)} muchii validate, {rejected} respinse."])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    edges = [_row_dict(r) for r in conn.execute(
        "SELECT * FROM note_edges WHERE dossier_id = ? ORDER BY id", [dossier_id]).fetchall()]
    return {"dossier_id": dossier_id, "edges": edges,
            "accepted": len(accepted), "rejected": rejected}


def _next_concept_code(conn, project_id: int) -> str:
    """Next stable concept code (``C{n}``); numbers are never reused.

    Same atomic bump pattern as claim codes, reconciled with the largest
    suffix ever seen. No commit here; the caller owns the transaction.
    """
    best = 0
    try:
        for r in conn.execute(
                "SELECT code FROM note_concepts WHERE project_id = ?", [project_id]).fetchall():
            match = re.fullmatch(r"C(\d+)", (r["code"] or "").strip())
            if match:
                best = max(best, int(match.group(1)))
    except Exception:
        pass
    conn.execute(
        "INSERT OR IGNORE INTO note_concept_seq (project_id, seq) VALUES (?, 0)", [project_id])
    conn.execute(
        "UPDATE note_concept_seq SET seq = max(seq, ?) + 1 WHERE project_id = ?",
        [best, project_id])
    row = conn.execute(
        "SELECT seq FROM note_concept_seq WHERE project_id = ?", [project_id]).fetchone()
    if row is None:
        raise LookupError("Proiectul nu există.")
    return f"C{int(row['seq'])}"


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().casefold())


def _project_claim_index(conn, project_id: int) -> tuple[list[dict], set]:
    """Dossiers with verified claims; ``{(dossier_id, code)}`` of evidence."""
    bundle, evidence = [], set()
    dossiers = conn.execute(
        "SELECT id, entry_id, title FROM note_dossiers WHERE project_id = ? ORDER BY id",
        [project_id]).fetchall()
    for dossier in dossiers:
        item = _row_dict(dossier)
        dossier_id = int(item["id"])
        claims = [_row_dict(r) for r in conn.execute(
            "SELECT code, statement, page_printed, status FROM note_claims"
            " WHERE dossier_id = ? AND status IN ('verificat', 'verificat_cu_nuanta')"
            " ORDER BY id LIMIT ?", [dossier_id, MAX_CLAIMS_PER_DOSSIER]).fetchall()]
        for claim in claims:
            evidence.add((dossier_id, str(claim.get("code"))))
        bundle.append({"dossier_id": dossier_id, "entry_id": item.get("entry_id"),
                       "title": item.get("title") or "", "claims": claims})
    return bundle, evidence


def _cap_prompt(payload: dict) -> dict:
    """Shrink the prompt to ``PROMPT_MAX_CHARS`` by trimming long claim lists."""
    bundle = payload.get("dossiers") or []
    while len(json.dumps(payload, ensure_ascii=False)) > PROMPT_MAX_CHARS \
            and any(b.get("claims") for b in bundle):
        max(bundle, key=lambda b: len(b.get("claims") or []))["claims"].pop()
    return payload


def _valid_concept_link(link, in_project: set, evidence: set) -> dict | None:
    """Normalized concept link, or ``None`` when it cites nothing real."""
    if not isinstance(link, dict):
        return None
    try:
        dossier_id = int(link.get("dossier_id"))
    except (TypeError, ValueError):
        return None
    code = str(link.get("claim_code") or "").strip()
    note = str(link.get("note") or "").strip()
    if dossier_id not in in_project or (dossier_id, code) not in evidence or not note:
        return None
    return {"dossier_id": dossier_id, "claim_code": code,
            "note": note, "page": _page_str(link.get("page"))}


async def generate_project_map(conn, project_id: int, *,
                               replace=False, apply=True) -> dict:
    """Ask the LLM for concepts, keep only those real claims back."""
    bundle, evidence = _project_claim_index(conn, project_id)
    in_project = {b["dossier_id"] for b in bundle}
    proposals = await _llm_concepts(
        _cap_prompt({"project_id": project_id, "dossiers": bundle}), _API_NOTE)
    concepts, by_label, rejected = [], {}, 0
    for proposal in proposals:
        if not isinstance(proposal, dict):
            rejected += 1
            continue
        label = str(proposal.get("label") or "").strip()
        if not label or len(label) > 120:
            rejected += 1
            continue
        raw_links = proposal.get("links") or []
        valid_links, seen_links = [], set()
        for link in raw_links if isinstance(raw_links, list) else []:
            norm = _valid_concept_link(link, in_project, evidence)
            key = (norm["dossier_id"], norm["claim_code"]) if norm else None
            if norm is None or key in seen_links:
                continue  # invalid links drop silently
            seen_links.add(key)
            valid_links.append(norm)
        if not valid_links:
            rejected += 1  # a concept exists only if backed by real claims
            continue
        key = _normalize_label(label)
        if key in by_label:  # same label: merge links into the existing concept
            have = {(x["dossier_id"], x["claim_code"]) for x in by_label[key]["links"]}
            for link in valid_links:
                if (link["dossier_id"], link["claim_code"]) not in have:
                    by_label[key]["links"].append(link)
            continue
        code = _next_concept_code(conn, project_id) if apply else f"C{len(concepts) + 1}"
        entry = {"code": code, "label": label,
                 "description": str(proposal.get("description") or "").strip(),
                 "links": valid_links}
        concepts.append(entry)
        by_label[key] = entry
    if not apply:
        return {"project_id": project_id, "concepts": concepts, "rejected": rejected}
    try:
        if replace:
            conn.execute(
                "DELETE FROM note_concept_links WHERE concept_id IN"
                " (SELECT id FROM note_concepts WHERE project_id = ?)", [project_id])
            conn.execute("DELETE FROM note_concepts WHERE project_id = ?", [project_id])
        stored = []
        for entry in concepts:
            cur = conn.execute(
                "INSERT INTO note_concepts (project_id, code, label, description)"
                " VALUES (?, ?, ?, ?)",
                [project_id, entry["code"], entry["label"], entry["description"]])
            concept_id = cur.lastrowid
            for link in entry["links"]:
                conn.execute(
                    "INSERT OR IGNORE INTO note_concept_links"
                    " (concept_id, dossier_id, claim_code, note, page)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [concept_id, link["dossier_id"], link["claim_code"],
                     link["note"], link["page"]])
            stored.append({"id": concept_id, "code": entry["code"],
                           "label": entry["label"], "description": entry["description"],
                           "links": entry["links"]})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"project_id": project_id, "concepts": stored, "rejected": rejected}


def project_map(conn, project_id: int) -> dict:
    """Read-only UI view: documents, concepts, bridges and stats."""
    documents = []
    for dossier in conn.execute(
            "SELECT d.id AS dossier_id, d.entry_id,"
            " COALESCE(NULLIF(e.title, ''), d.title) AS title,"
            " e.authors AS authors, e.bib_year AS year, d.status AS status"
            " FROM note_dossiers d LEFT JOIN entries e ON e.id = d.entry_id"
            " WHERE d.project_id = ? ORDER BY d.id", [project_id]).fetchall():
        item = _row_dict(dossier)
        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM note_claims"
            " WHERE dossier_id = ? GROUP BY status", [item["dossier_id"]]).fetchall()
        by_status = {r["status"]: r["n"] for r in counts}
        documents.append({
            "dossier_id": item["dossier_id"], "entry_id": item["entry_id"],
            "title": item["title"] or "", "authors": item["authors"] or "",
            "year": item["year"] or "", "status": item["status"],
            "claims_total": sum(by_status.values()),
            "claims_verificat": by_status.get("verificat", 0)})
    concepts, bridges, bridged, total_links = [], [], 0, 0
    for concept in conn.execute(
            "SELECT * FROM note_concepts WHERE project_id = ? ORDER BY id",
            [project_id]).fetchall():
        item = _row_dict(concept)
        links = [_row_dict(r) for r in conn.execute(
            "SELECT l.dossier_id, l.claim_code, l.note, l.page,"
            " nc.statement, nc.page_printed FROM note_concept_links l"
            " JOIN note_claims nc ON nc.dossier_id = l.dossier_id"
            " AND nc.code = l.claim_code"
            " JOIN note_dossiers d ON d.id = l.dossier_id AND d.project_id = ?"
            " WHERE l.concept_id = ? ORDER BY l.id",
            [project_id, item["id"]]).fetchall()]
        total_links += len(links)
        dossier_ids = sorted({x["dossier_id"] for x in links})
        if len(dossier_ids) >= 2:
            bridged += 1
            bridges.append({"concept_code": item["code"], "dossier_ids": dossier_ids})
        concepts.append({"id": item["id"], "code": item["code"],
                         "label": item["label"], "description": item["description"],
                         "links": links})
    return {"documents": documents, "concepts": concepts, "bridges": bridges,
            "stats": {"documents": len(documents), "concepts": len(concepts),
                      "bridged_concepts": bridged, "links": total_links}}
