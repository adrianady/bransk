"""
Studies store — a dedicated local folder where study PDFs are downloaded and
served back to the app.

Location is configurable via CHRONOKG_STUDIES_DIR (default:
backend/data/studies). Files are named `<entry_id>-<slug>.pdf` so a study's
entry links unambiguously to its downloaded paper.
"""
import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger("chronokg.studies")

STUDIES_DIR = Path(os.getenv("CHRONOKG_STUDIES_DIR", str(Path(__file__).parent.parent / "data" / "studies")))
MAX_SIZE = 150 * 1024 * 1024  # 150 MB safety cap

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ChronoKG/0.1"}


def ensure_dir() -> Path:
    STUDIES_DIR.mkdir(parents=True, exist_ok=True)
    return STUDIES_DIR


def _slugify(title: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^\w\s-]", "", title or "").strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:max_len] or "study"


def pdf_filename(entry_id: int, title: str) -> str:
    return f"{entry_id}-{_slugify(title)}.pdf"


def pdf_path_for(entry_id: int):
    """Return the downloaded PDF path for an entry, if any."""
    try:
        matches = sorted(ensure_dir().glob(f"{entry_id}-*.pdf"))
    except OSError:
        return None
    return matches[0] if matches else None


def extract_pdf_url(entry: dict) -> str:
    """Best PDF URL for a study: `source` column or a .pdf link inside content."""
    url = (entry.get("source") or "").strip()
    if url:
        return url
    match = re.search(r"https?://\S+\.pdf\S*", entry.get("content") or "")
    return match.group(0) if match else ""


async def download_study_pdf(entry_id: int, url: str, title: str):
    """Download a study PDF into the studies folder. Returns the filename or None."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None

    fname = pdf_filename(entry_id, title)
    dest = ensure_dir() / fname
    if dest.exists() and dest.stat().st_size > 0:
        return fname

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=_UA) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").lower()
                looks_like_pdf = ".pdf" in url.lower() or "pdf" in ctype or "octet-stream" in ctype
                size = 0
                with open(dest, "wb") as out:
                    async for chunk in resp.aiter_bytes(65536):
                        size += len(chunk)
                        if size > MAX_SIZE:
                            break
                        out.write(chunk)
        if size == 0 or size > MAX_SIZE or (not looks_like_pdf and size < 200 * 1024):
            dest.unlink(missing_ok=True)
            logger.info("Rejected download for entry %s (%s bytes, %s)", entry_id, size, ctype)
            return None
        logger.info("Downloaded study PDF for entry %s: %s", entry_id, fname)
        return fname
    except Exception as exc:
        logger.info("PDF download failed for entry %s: %s", entry_id, type(exc).__name__)
        dest.unlink(missing_ok=True)
        return None
