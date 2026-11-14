"""STRUCTURE layer: fill a dossier's prose sections from verified claims.

Same product rule as the rest of the feature: the LLM proposes Romanian
prose, the server checks it. A section never invents a claim code or a
glossary/absence term that is not present in the source page text. Empty
prose is better than invented prose: with no verified claim nothing is
written at all.
"""

import json
import logging
import re

import anyio

from app.page_text import _normalize
from app.notes.service import dossier_payload

logger = logging.getLogger("chronokg.notes.compose")

#: Kinds composed here. ``fisa_sursei`` belongs to the service and
#: ``corectii`` is derived from claims by the UI, so neither is generated.
GENERATED_KINDS = (
    "ce_spune", "continut", "teza", "lipsea", "glosar", "formulari",
    "de_verificat",
)

#: Hard cap for one stored section body.
MAX_SECTION_CHARS = 6000

#: Claim codes look like ``A1``, ``A12``; only those may be cited.
_CODE_RE = re.compile(r"\bA(\d+)\b")

#: Minimum token length kept when checking a term against the page text.
_TERM_MIN_CHARS = 4

_API_NOTE = "Răspunde în limba română."


def _row_dict(row) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


def _clean_json(text: str):
    clean = re.sub(r"^```(?:json)?\s*", "", (text or "").strip())
    return json.loads(re.sub(r"\s*```$", "", clean))


def _coerce_sections(raw) -> dict:
    """Keep only string bodies in a plain ``{kind: body}`` mapping."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for kind, body in raw.items():
        if isinstance(body, str):
            out[str(kind).strip()] = body
    return out


async def _llm_sections(dossier_payload, page_samples: str, api_note="") -> dict:
    """Propose Romanian prose for the generated sections. Never raises.

    Returns ``{kind: body_md}`` or ``{}`` on any failure. Module-level so
    tests can monkeypatch it (same shape as ``map._llm_edges``).
    """
    from app.llm import complete

    claims = dossier_payload.get("claims", []) if isinstance(dossier_payload, dict) else []
    lines = "\n".join(
        f"{c.get('code')}: {c.get('statement')} [pag. {c.get('page_printed') or '?'}"
        f" | {c.get('status')}]"
        for c in claims if isinstance(c, dict))
    keys = ", ".join(GENERATED_KINDS)
    system = (
        "Ești un redactor academic. Compui secțiunile unui dosar de notițe "
        "DOAR din afirmațiile verificate și din pasajele sursei primite. "
        "Scrii în română, paragrafe scurte separate prin linii goale, fără "
        "markdown complicat. Nu inventezi citate, autori sau pagini; când "
        "citezi o afirmație folosești codul ei (A1, A2...). "
        "Răspunde DOAR cu un obiect JSON cu cheile: " + keys + ". "
        'Fiecare valoare este textul secțiunii (poate fi și gol). '
        "Secțiunile `glosar` și `lipsea` conțin doar termeni prezenți în "
        "pasajele sursei.")
    user = f"Afirmații:\n{lines}\n\nPasaje din sursă:\n{page_samples}"
    if api_note:
        user += f"\n\nNotă: {api_note}"
    try:
        _, _, text = await complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            json_mode=True, temperature=0)
    except Exception as exc:  # noqa: BLE001 - no prose on LLM failure
        logger.info("Section proposal failed: %s", exc)
        return {}
    try:
        return _coerce_sections(_clean_json(text))
    except Exception as exc:  # noqa: BLE001 - malformed JSON means no prose
        logger.info("Section proposal unparsable: %s", exc)
        return {}


def _claim_codes(payload) -> set[str]:
    """Every stable claim code present in the dossier (``A1``, ``A2``...)."""
    claims = payload.get("claims", []) if isinstance(payload, dict) else []
    return {str(c.get("code")) for c in claims
            if isinstance(c, dict) and c.get("code")}


def _terms(text: str) -> set[str]:
    return {tok for tok in re.findall(r"\w+", _normalize(text or ""))
            if len(tok) >= _TERM_MIN_CHARS}


def validate_section(
    kind: str, body: str, codes: set[str], page_text: str
) -> tuple[str, list[str]]:
    """Return ``(clean_body, rejected_lines)``. Pure and deterministic.

    * a kind outside :data:`GENERATED_KINDS` is rejected whole;
    * every line citing an ``A<n>`` code absent from *codes* is dropped;
    * ``glosar`` / ``lipsea`` lines must contain a term present in
      *page_text* (normalized, case-insensitive), else they are dropped;
    * the kept body is truncated to :data:`MAX_SECTION_CHARS`.
    """
    if kind not in GENERATED_KINDS:
        return "", ([body] if (body or "").strip() else [])
    rejected: list[str] = []
    kept: list[str] = []
    for line in (body or "").splitlines():
        mentioned = {f"A{n}" for n in _CODE_RE.findall(line)}
        if mentioned and not mentioned.issubset(codes):
            rejected.append(line)
            continue
        kept.append(line)
    if kind in ("glosar", "lipsea"):
        allowed = _terms(page_text)
        filtered: list[str] = []
        for line in kept:
            if not line.strip():
                filtered.append(line)
                continue
            if _terms(line) & allowed:
                filtered.append(line)
            else:
                rejected.append(line)
        kept = filtered
    clean = "\n".join(kept).strip()[:MAX_SECTION_CHARS]
    return clean, rejected


def _page_rows(entry_id: int) -> list[dict]:
    """Read the entry's page texts on a private connection (thread-safe)."""
    from app.db import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT page_number, text FROM page_texts"
            " WHERE entry_id = ? ORDER BY page_number", [entry_id],
        ).fetchall()
        return [_row_dict(r) for r in rows]
    finally:
        conn.close()


def _assemble(page_rows: list[dict], sample_chars: int) -> tuple[str, str]:
    """Return ``(page_samples, full_page_text)`` skipping empty pages."""
    samples: list[str] = []
    full: list[str] = []
    budget = max(0, sample_chars)
    for row in page_rows:
        text = row.get("text") or ""
        if not text.strip():
            continue
        full.append(text)
        if budget > 0:
            clipped = text[:budget]
            budget -= len(clipped)
            samples.append(f"[p. {row.get('page_number')}]\n{clipped}")
    return "\n\n".join(samples), "\n".join(full)


async def compose_sections(
    conn, dossier_id: int, *, apply: bool = True, sample_chars: int = 12000
) -> dict:
    """Compose the generated sections from verified claims; never invents.

    The caller owns the transaction (nothing is committed here).
    """
    dossier = conn.execute(
        "SELECT * FROM note_dossiers WHERE id = ?", [dossier_id]).fetchone()
    if dossier is None:
        raise LookupError("Dosarul nu există.")
    payload = dossier_payload(conn, dossier_id)
    claims = payload.get("claims") or []
    verified = [c for c in claims if c.get("status") in
                ("verificat", "verificat_cu_nuanta")]
    if not claims or not verified:
        return {
            "dossier_id": dossier_id,
            "written": 0,
            "rejected": 0,
            "reason": "Nu există afirmații verificate din care să compun secțiunile.",
        }
    try:
        entry_id = int(payload["dossier"]["entry_id"])
    except (KeyError, TypeError, ValueError):
        return {
            "dossier_id": dossier_id,
            "written": 0,
            "rejected": 0,
            "reason": "Nu există afirmații verificate din care să compun secțiunile.",
        }
    page_rows = await anyio.to_thread.run_sync(_page_rows, entry_id)
    page_samples, page_text = _assemble(page_rows, sample_chars)
    proposed = await _llm_sections(payload, page_samples, _API_NOTE)
    codes = _claim_codes(payload)
    written_kinds: list[str] = []
    rejected_kinds: list[str] = []
    rejected_lines = 0
    for kind in GENERATED_KINDS:
        body = proposed.get(kind) if isinstance(proposed, dict) else None
        if not isinstance(body, str):
            continue
        clean, rejected = validate_section(kind, body, codes, page_text)
        rejected_lines += len(rejected)
        if rejected:
            rejected_kinds.append(kind)
        if not clean:
            continue
        if apply:
            conn.execute(
                "UPDATE note_sections SET body_md = ?, updated_at = datetime('now')"
                " WHERE dossier_id = ? AND kind = ?",
                [clean, dossier_id, kind])
        written_kinds.append(kind)
    if apply:
        conn.execute(
            "INSERT INTO note_rounds (dossier_id, summary, changed_json, open_json)"
            " VALUES (?, ?, '[]', ?)",
            [dossier_id,
             f"Structură: {len(written_kinds)} secțiuni scrise, "
             f"{rejected_lines} linii respinse.",
             json.dumps(rejected_kinds, ensure_ascii=False)])
    return {
        "dossier_id": dossier_id,
        "written": len(written_kinds),
        "rejected": rejected_lines,
        "sections": written_kinds,
    }
