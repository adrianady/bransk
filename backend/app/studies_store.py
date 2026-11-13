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
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse

import anyio
import httpx

logger = logging.getLogger("chronokg.studies")

STUDIES_DIR = Path(os.getenv("CHRONOKG_STUDIES_DIR", str(Path(__file__).parent.parent / "data" / "studies")))
MAX_SIZE = 150 * 1024 * 1024  # 150 MB safety cap

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Bransk/0.1"}

# A publisher or repository answering with one of these is not providing the
# paper — it is refusing an automated client. 403/429 are decisive on their
# own; 503 only counts when the body carries a challenge marker, because a
# plain 503 is usually an outage.
_BOT_STATUS = frozenset({401, 403, 429})
# Challenge-page phrases. Generic words like "captcha" or "cloudflare" are
# deliberately absent: an article *about* CAPTCHAs is not a challenge page.
_BOT_MARKERS = (
    "i am not a robot",
    "i'm not a robot",
    "i am a robot",
    "verify you are human",
    "verify you are a human",
    "checking your browser",
    "just a moment",
    "attention required",
    "cf-challenge",
    "cf_chl_opt",
    "challenge-platform",
    "enable javascript and cookies to continue",
    "please turn javascript on",
    "unusual traffic",
    "request blocked",
    "bot detection",
    "ddos protection",
)


def detect_bot_challenge(status_code: int, content_type: str = "", body: str = "") -> str:
    """Return a short reason when a response is a bot challenge, else ``""``.

    Downloads that "succeed" with an HTML challenge page are the failure this
    guards against: the file is not a PDF, and the user needs a browser and a
    manual upload instead of a silent failure.
    """
    ctype = (content_type or "").lower()
    text = (body or "")[:20000].lower()
    marker = next((m for m in _BOT_MARKERS if m in text), "")
    if status_code in _BOT_STATUS:
        return marker or f"http_{status_code}"
    if status_code == 503 and marker:
        return marker
    if marker and ("html" in ctype or not ctype or "text" in ctype):
        return marker
    return ""



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


def _atomic_write(dest: Path, write) -> bool:
    """Write to a sibling temp file, then replace — never leave a half PDF."""
    tmp = dest.with_name(dest.name + ".part")
    try:
        write(tmp)
        if not is_valid_pdf_file(tmp):
            tmp.unlink(missing_ok=True)
            return False
        os.replace(tmp, dest)
        return True
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        logger.info("Could not store PDF at %s: %s", dest, exc)
        return False


def write_study_pdf_bytes(entry_id: int, title: str, data: bytes) -> str | None:
    """Store bytes uploaded from the browser as this entry's PDF.

    Returns the stored filename, or ``None`` when the upload is empty, too
    large, or not a PDF. This is the manual-upload counterpart of
    :func:`download_study_pdf`: the same folder, the same naming, the same
    magic-byte check, so the reader and RAG cannot tell them apart.
    """
    if not data or len(data) > MAX_SIZE or not data.startswith(b"%PDF-"):
        return None
    dest = ensure_dir() / pdf_filename(entry_id, title)
    if not _atomic_write(dest, lambda tmp: tmp.write_bytes(data)):
        return None
    logger.info("Stored uploaded PDF for entry %s: %s", entry_id, dest.name)
    return dest.name


def copy_study_pdf(entry_id: int, title: str, source) -> str | None:
    """Copy an existing PDF (e.g. from Zotero storage) into the studies folder."""
    src = Path(source)
    if not is_valid_pdf_file(src):
        return None
    if src.stat().st_size > MAX_SIZE:
        return None
    dest = ensure_dir() / pdf_filename(entry_id, title)
    try:
        if src.resolve() == dest.resolve():
            return dest.name
    except OSError:
        pass
    if not _atomic_write(dest, lambda tmp: shutil.copyfile(src, tmp)):
        return None
    logger.info("Copied PDF for entry %s from %s", entry_id, src)
    return dest.name


async def download_study_pdf(entry_id: int, url: str, title: str, *, details: dict | None = None):
    """Download a study PDF into the studies folder. Returns the filename or None.

    ``details``, when supplied, is filled with ``status_code``, ``content_type``
    and ``bot_challenge`` so callers can distinguish "no PDF exists" from "the
    publisher wants a human in a browser".
    """
    if details is not None:
        details.clear()
        details.update({"status_code": 0, "content_type": "", "bot_challenge": ""})
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
                ctype = resp.headers.get("content-type", "").lower()
                if details is not None:
                    details["status_code"] = resp.status_code
                    details["content_type"] = ctype
                if resp.status_code >= 400:
                    head = b""
                    try:
                        async for chunk in resp.aiter_bytes(8192):
                            head += chunk
                            break
                    except Exception:  # noqa: BLE001 - the status is the signal
                        pass
                    reason = detect_bot_challenge(
                        resp.status_code, ctype, head.decode("utf-8", "ignore")
                    )
                    if details is not None:
                        details["bot_challenge"] = reason
                    if reason:
                        # Don't stream a challenge body to disk as if it were a PDF.
                        logger.info(
                            "Bot challenge for entry %s at %s (%s)",
                            entry_id, url[:200], reason,
                        )
                        return None
                    resp.raise_for_status()
                size = 0
                head = b""
                with open(dest, "wb") as out:
                    async for chunk in resp.aiter_bytes(65536):
                        if len(head) < 8192:
                            head += chunk[: 8192 - len(head)]
                        size += len(chunk)
                        if size > MAX_SIZE:
                            break
                        out.write(chunk)
        valid = is_valid_pdf_file(dest) and size <= MAX_SIZE
        if not valid:
            reason = detect_bot_challenge(
                details.get("status_code", 0) if details else 0,
                ctype,
                head.decode("utf-8", "ignore"),
            )
            if details is not None:
                details["bot_challenge"] = reason
            dest.unlink(missing_ok=True)
            logger.info(
                "Rejected download for entry %s (%s bytes, ctype=%s, not a valid PDF%s)",
                entry_id, size, ctype, f", bot challenge: {reason}" if reason else "",
            )
            return None
        logger.info("Downloaded study PDF for entry %s: %s", entry_id, fname)
        return fname
    except Exception as exc:
        logger.info("PDF download failed for entry %s: %s", entry_id, type(exc).__name__)
        dest.unlink(missing_ok=True)
        return None


def compute_pdf_hash(file_bytes: bytes) -> str:
    """SHA256 hex digest of PDF bytes (empty input -> empty string)."""
    import hashlib

    if not file_bytes:
        return ""
    return hashlib.sha256(file_bytes).hexdigest()


def compute_pdf_file_hash(path) -> str:
    """SHA256 hex digest of a stored PDF file; ``""`` when unreadable."""
    import hashlib

    try:
        h = hashlib.sha256()
        with open(str(path), "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def find_entry_by_pdf_hash(conn, pdf_hash: str, exclude_id: int | None = None) -> dict | None:
    """Return the live entry already storing *pdf_hash*, if any."""
    if not (pdf_hash or "").strip():
        return None
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}
    if "pdf_hash" not in cols:
        return None
    if exclude_id is not None:
        row = conn.execute(
            "SELECT * FROM entries WHERE pdf_hash = ? AND id != ? AND archived = 0",
            [pdf_hash, exclude_id],
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM entries WHERE pdf_hash = ? AND archived = 0",
            [pdf_hash],
        ).fetchone()
    return dict(row) if row else None


def store_entry_pdf_hash(conn, entry_id: int, pdf_path=None) -> str:
    """(Re)compute the SHA256 of the stored PDF and persist it on the entry.

    Returns the digest (``""`` when no readable PDF). Never raises: a missing
    column (pre-migration DB) or missing file simply yields ``""``.
    """
    try:
        path = pdf_path or pdf_path_for(entry_id)
        digest = compute_pdf_file_hash(path) if path else ""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}
        if "pdf_hash" in cols:
            conn.execute(
                "UPDATE entries SET pdf_hash = ? WHERE id = ?",
                [digest, entry_id],
            )
        return digest
    except Exception:  # noqa: BLE001 - hashing must never break an upload
        return ""
