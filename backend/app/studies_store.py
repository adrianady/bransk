"""
Studies store — a dedicated local folder where study PDFs are downloaded and
served back to the app.

Location is configurable via CHRONOKG_STUDIES_DIR (default:
backend/data/studies). Files are named `<entry_id>-<slug>.pdf` so a study's
entry links unambiguously to its downloaded paper.
"""
import logging
import ipaddress
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import anyio
import httpx

logger = logging.getLogger("chronokg.studies")

STUDIES_DIR = Path(os.getenv("CHRONOKG_STUDIES_DIR", str(Path(__file__).parent.parent / "data" / "studies")))
MAX_SIZE = 150 * 1024 * 1024  # 150 MB safety cap

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Bransk/0.1"}


def assert_public_download_url(url: str) -> None:
    """Raise ValueError when *url* could target a private/internal host.

    Study URLs come from LLM output and web search, so downloads are checked
    before every HTTP hop (initial request + redirects) to prevent SSRF-style
    fetches of localhost, LAN, or link-local addresses.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    if parsed.port is not None and parsed.port not in (80, 443):
        raise ValueError(f"Non-standard port: {parsed.port}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValueError(f"Host does not resolve: {host}") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"Refusing to download from non-public address: {address}")


def ensure_dir() -> Path:
    STUDIES_DIR.mkdir(parents=True, exist_ok=True)
    return STUDIES_DIR


def is_valid_pdf_file(path) -> bool:
    """True when *path* exists, is non-empty and starts with the PDF magic
    bytes. Many blocked/redirected downloads save an HTML error page with a
    .pdf name; this check rejects those so garbage never reaches the app."""
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return False
        with open(p, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


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
    try:
        await anyio.to_thread.run_sync(assert_public_download_url, url)
    except ValueError as exc:
        logger.info("Blocked unsafe download URL for entry %s: %s", entry_id, exc)
        return None

    fname = pdf_filename(entry_id, title)
    dest = ensure_dir() / fname
    if is_valid_pdf_file(dest):
        return fname
    # A stale/blocked download may have left an HTML page named .pdf — replace it.
    if dest.exists():
        dest.unlink(missing_ok=True)

    try:
        async def _guard_hop(response: httpx.Response) -> None:
            try:
                await anyio.to_thread.run_sync(assert_public_download_url, str(response.url))
            except ValueError as exc:
                raise httpx.HTTPError(f"Blocked redirect to non-public URL: {exc}") from exc

        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            headers=_UA,
            event_hooks={"response": [_guard_hop]},
            max_redirects=8,
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").lower()
                size = 0
                with open(dest, "wb") as out:
                    async for chunk in resp.aiter_bytes(65536):
                        size += len(chunk)
                        if size > MAX_SIZE:
                            break
                        out.write(chunk)
        valid = is_valid_pdf_file(dest) and size <= MAX_SIZE
        if not valid:
            dest.unlink(missing_ok=True)
            logger.info(
                "Rejected download for entry %s (%s bytes, ctype=%s, not a valid PDF)",
                entry_id, size, ctype,
            )
            return None
        logger.info("Downloaded study PDF for entry %s: %s", entry_id, fname)
        return fname
    except Exception as exc:
        logger.info("PDF download failed for entry %s: %s", entry_id, type(exc).__name__)
        dest.unlink(missing_ok=True)
        return None
