"""Missing sources: the app's own model fills the gaps, Crossref verifies.

The work here is not the agent's — it is the model's. Two jobs, both reported
honestly: resolve a DOI for studies that have none (the model proposes, Crossref
decides), and draft a summary for studies that are only a title (written only
when the model says it knows the work, and always marked as a model summary).
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db
from app.sources import (
    fill_thin_studies,
    resolve_dois,
    source_state,
    studies_without_doi,
    thin_studies,
)

logger = logging.getLogger("chronokg.sources.api")
router = APIRouter()


class SourcesRun(BaseModel):
    limit: int | None = Field(None, ge=1, le=200)
    dry_run: bool = False


@router.get("/sources/status")
def sources_status():
    """What is missing from the library, counted — no model involved."""
    conn = get_db()
    try:
        state = source_state(conn)
        no_doi = studies_without_doi(conn)
        thin = thin_studies(conn)
    finally:
        conn.close()
    return {
        **state,
        "without_doi_titles": [
            {"id": r["id"], "title": r["title"], "authors": r["authors"] or "",
             "year": (r["entry_date"] or "")[:4]}
            for r in no_doi[:12]
        ],
        "thin_titles": [
            {"id": r["id"], "title": r["title"], "chars": len(r["content"] or "")}
            for r in thin[:12]
        ],
    }


@router.post("/sources/dois")
async def sources_resolve_dois(body: SourcesRun):
    """Let the model propose real titles; Crossref accepts or refuses each one."""
    result = await resolve_dois(limit=body.limit, dry_run=body.dry_run)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("note") or "Model indisponibil")
    return result


@router.post("/sources/summaries")
async def sources_fill_thin(body: SourcesRun):
    """Let the model draft what thin studies found, marked as a model summary."""
    result = await fill_thin_studies(limit=body.limit, dry_run=body.dry_run)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("note") or "Model indisponibil")
    return result
