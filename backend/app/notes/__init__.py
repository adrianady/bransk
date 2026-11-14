"""Verified note dossiers — public surface of the ``app.notes`` package."""

from app.notes.engine import (
    SECTION_KINDS,
    STATUSES,
    collect_candidates,
    next_code,
    verify_claim,
    verify_dossier,
)
from app.notes.service import (
    capture,
    dossier_payload,
    get_or_create_dossier,
    list_dossiers,
)

__all__ = [
    "STATUSES",
    "SECTION_KINDS",
    "capture",
    "collect_candidates",
    "dossier_payload",
    "get_or_create_dossier",
    "list_dossiers",
    "next_code",
    "verify_claim",
    "verify_dossier",
]
