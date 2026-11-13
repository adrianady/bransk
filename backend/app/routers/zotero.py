"""
Zotero integration — read the local library, import files, sync the Web API,
and export the Bransk library in a format Zotero understands.

The interesting part is the local database. Zotero keeps everything in
``zotero.sqlite`` plus a ``storage/<item key>/`` folder of attachments. Bransk
reads that database in read-only mode (copying it aside when Zotero holds a
write lock), turns each bibliographic item into a study entry, and copies its
PDF into ``data/studies`` so the existing reader/RAG pipeline takes over.

The import can use a different PDF folder (flat ZotFile/attanger layouts are
searched too, and files are matched by DOI or title when the attachment record
is stale) and checks duplicates by DOI and/or normalized title before inserting.

Endpoints:
  GET  /api/zotero/status        — is a local Zotero data directory present?
  POST /api/zotero/import/local  — import items (+ PDFs) from zotero.sqlite
  POST /api/zotero/import/file   — import BibTeX / RIS / CSL-JSON
  POST /api/zotero/sync/api      — import from the Zotero Web API
  GET  /api/zotero/export        — CSL-JSON / BibTeX / RIS for Zotero
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import quote, unquote

import anyio
import httpx
from fastapi import APIRouter, Body, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from app.db import get_db
from app.models import normalize_doi
from app.pdf_rag import extract_pdf_text, index_pdf
from app.routers.citations import _fetch_studies, _split_authors, _to_bibtex, _to_ris
from app.studies_store import copy_study_pdf, pdf_path_for
from app.study_dedup import normalize_title, titles_match
from app.study_import import insert_study_entry
from app import studytext

logger = logging.getLogger("chronokg.zotero")

router = APIRouter()

MAX_IMPORT_FILE = 20 * 1024 * 1024  # 20 MB of bibliography is already huge
ZOTERO_API_BASE = "https://api.zotero.org"

# Zotero item types that are containers/attachments, not papers.
_NON_PAPER_TYPES = {"attachment", "note", "annotation"}

# The PDF folder the user picked for Zotero imports, remembered between runs.
SETTING_PDF_FOLDER = "zotero_pdf_folder"
# The Zotero data directory / PDF folder the user picked in the UI.
SETTING_DATA_DIR = "zotero_custom_data_dir"
_PDF_SUFFIXES = {".pdf"}
_MAX_INDEXED_PDFS = 20000
_MAX_DB_SEARCH_DEPTH = 4
_MAX_DB_SEARCH_DIRS = 3000
_MAX_PDF_TEXT_CHARS = 12000
# Counts are probed repeatedly (status + import). Reading a locked Zotero
# database copies it first, so cache the count until the file actually changes.
_COUNT_CACHE: dict[tuple[str, int, int, int, int], int] = {}


# ── local data directory discovery ─────────────────────────────────────────


def _candidate_data_dirs() -> list[Path]:
    """Every place Zotero uses for its data directory, most likely first."""
    candidates: list[Path] = []
    configured = (os.getenv("CHRONOKG_ZOTERO_DIR") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    home = Path.home()
    candidates.append(home / "Zotero")
    candidates.append(home / "Documents" / "Zotero")
    profile = (os.getenv("USERPROFILE") or "").strip()
    if profile:
        candidates.append(Path(profile) / "Zotero")
        candidates.append(Path(profile) / "Documents" / "Zotero")
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


# Pasted paths often arrive wrapped in shell/list decorations: a leading
# ``-`` copied from ``ls -la`` output, ``>`` from a prompt, quotes, or a
# ``file://`` URL. All of these made a perfectly valid folder look missing.
_PATH_JUNK_PREFIX_RE = re.compile(r"^(?:[\s\-–—−•·*>`\"'“”‘’(\[])+")
_PATH_JUNK_SUFFIX_RE = re.compile(r"(?:[\s>`\"'“”‘’`)\]]+)$")


def _clean_user_path(value: str | Path | None) -> str:
    """Normalize a folder path typed/pasted by the user.

    Strips surrounding whitespace/quotes/dashes/bullets and unwraps a
    ``file://`` URI, so ``-/media/disk/Zotero`` and ``"/media/disk/Zotero"``
    both resolve to the real directory.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower().startswith("file://"):
        text = unquote(text[7:])
    text = _PATH_JUNK_PREFIX_RE.sub("", text)
    text = _PATH_JUNK_SUFFIX_RE.sub("", text)
    return text.strip()


def _existing_path(value: str | Path | None) -> Optional[Path]:
    """The path the user typed/selected, when it exists (file or folder)."""
    text = _clean_user_path(value)
    if not text:
        return None
    try:
        path = Path(text).expanduser()
    except (OSError, ValueError):
        return None
    try:
        return path if path.exists() else None
    except OSError:
        return None


def _locate_zotero_db(value: str | Path) -> tuple[Path | None, Path | None]:
    """Find ``zotero.sqlite`` for a user-selected folder.

    Zotero users pick very different folders: the data directory itself, its
    ``storage/`` child, a parent library folder that contains ``Zotero/``, or
    even the database file. Check the folder itself, its parent, then a
    depth-limited scan of subfolders.
    """
    try:
        path = Path(_clean_user_path(value)).expanduser()
    except (OSError, ValueError):
        return None, None
    try:
        if path.is_file():
            if path.name.lower() == "zotero.sqlite":
                return path.parent, path
            return None, None
        if not path.is_dir():
            return None, None

        direct = path / "zotero.sqlite"
        if direct.is_file():
            return path, direct

        parent_db = path.parent / "zotero.sqlite"
        if parent_db.is_file():
            return path.parent, parent_db

        # A library folder may hold the real data directory one or two levels
        # down (e.g. ``D:\\Research\\Zotero`` or a synced cloud folder).
        queue: list[tuple[Path, int]] = [(path, 0)]
        visited: set[str] = set()
        scanned = 0
        while queue and scanned < _MAX_DB_SEARCH_DIRS:
            folder, depth = queue.pop(0)
            try:
                key = str(folder.resolve())
            except OSError:
                key = str(folder)
            if key in visited:
                continue
            visited.add(key)
            scanned += 1
            try:
                children = list(folder.iterdir())
            except OSError:
                continue
            for child in children:
                try:
                    if child.is_file() and child.name.lower() == "zotero.sqlite":
                        return folder, child
                    if child.is_dir() and depth + 1 < _MAX_DB_SEARCH_DEPTH:
                        queue.append((child, depth + 1))
                except OSError:
                    continue
    except OSError:
        return None, None
    return None, None


def _probe_zotero_source(value: str | Path) -> dict:
    """Describe what a user-selected folder contains: DB, PDFs, or both."""
    probe: dict = {
        "requested_path": _clean_user_path(value),
        "path": "",
        "data_dir": "",
        "database_path": "",
        "storage_path": "",
        "item_count": 0,
        "pdf_count": 0,
        "has_database": False,
        "has_pdfs": False,
    }
    path = _existing_path(value)
    if path is None:
        return probe

    folder = path if path.is_dir() else path.parent
    data_dir, db_path = _locate_zotero_db(path)
    probe["path"] = str(data_dir or folder)
    if db_path:
        probe["has_database"] = True
        probe["data_dir"] = str(db_path.parent)
        probe["database_path"] = str(db_path)
        probe["item_count"] = _count_zotero_items(db_path)
        storage = db_path.parent / "storage"
        pdf_count = 0
        if storage.is_dir():
            pdf_count = len(_iter_storage_pdfs(storage))
        elif folder.is_dir():
            # A hand-picked folder of PDFs can sit next to (not inside) the DB.
            pdf_count = len(_iter_storage_pdfs(folder))
            if pdf_count:
                storage = folder
            else:
                storage = None
        else:
            storage = None
        if storage is not None:
            probe["storage_path"] = str(storage)
            probe["pdf_count"] = pdf_count
    else:
        probe["path"] = str(folder)
        count = len(_iter_storage_pdfs(folder)) if folder.is_dir() else 0
        probe["pdf_count"] = count
        probe["storage_path"] = str(folder) if count else ""
    probe["has_pdfs"] = probe["pdf_count"] > 0
    return probe


def _zotero_data_dir(custom_dir: str | Path | None = None) -> tuple[Path | None, Path | None]:
    """Return ``(data_dir, database_path)`` for the chosen or default install.

    ``custom_dir`` is authoritative: when the user picked a folder, only that
    folder (its parent/subfolders or a directly selected database) is used.
    Without it, remember the last validated folder, then scan the defaults.
    """
    if custom_dir:
        return _locate_zotero_db(custom_dir)

    best: tuple[int, Path, Path] | None = None

    def consider(value: str | Path | None) -> Optional[tuple[Path, Path]]:
        """Keep the richest database: a populated one beats an empty default."""
        nonlocal best
        if not value:
            return None
        try:
            data_dir, db_path = _locate_zotero_db(value)
        except OSError:
            return None
        if not db_path:
            return None
        rank = 2 if _count_zotero_items(db_path) > 0 else 1
        if best is None or rank > best[0]:
            best = (rank, data_dir, db_path)
            if rank == 2:
                return data_dir, db_path
        return None

    for configured in (
        (os.getenv("CHRONOKG_ZOTERO_DIR") or "").strip(),
        _get_setting(SETTING_DATA_DIR).strip(),
        (os.getenv("CHRONOKG_ZOTERO_PDF_DIR") or "").strip(),
        _get_setting(SETTING_PDF_FOLDER).strip(),
    ):
        found = consider(configured)
        if found:
            return found

    for data_dir in _candidate_data_dirs():
        found = consider(data_dir / "zotero.sqlite")
        if found:
            return found

    if best is not None:
        return best[1], best[2]
    return None, None


@contextmanager
def _zotero_connection(db_path: Path):
    """Read-only connection; copy the database aside if Zotero locks it.

    Zotero runs its own SQLite connection with WAL, so a plain read-only open
    can fail during a write. Copying the database (plus WAL/SHM) gives a
    consistent-enough snapshot without ever writing to the user's library.
    """
    conn: sqlite3.Connection | None = None
    tmpdir: str | None = None
    try:
        try:
            uri = db_path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=3.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=3000")
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.Error:
            if conn is not None:
                conn.close()
            tmpdir = tempfile.mkdtemp(prefix="chronokg-zotero-")
            snapshot = Path(tmpdir) / db_path.name
            shutil.copy2(db_path, snapshot)
            for suffix in ("-wal", "-shm"):
                side = Path(str(db_path) + suffix)
                if side.is_file():
                    shutil.copy2(side, Path(str(snapshot) + suffix))
            conn = sqlite3.connect(str(snapshot), timeout=3.0)
            conn.row_factory = sqlite3.Row
        yield conn
    finally:
        if conn is not None:
            conn.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", [name]
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    """Column names of ``name`` (empty when the table does not exist).

    Zotero 6/7 libraries and libraries migrated from older versions differ in
    a few optional columns (``fieldMode``, ``orderIndex``, ``contentType``);
    reading the real schema lets every one of them load without exceptions.
    """
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')}
    except sqlite3.Error:
        return set()


def _year_from_date(value: str) -> Optional[int]:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", value or "")
    return int(match.group(1)) if match else None


def _clean_doi(value: str) -> str:
    try:
        return normalize_doi(value or "")
    except ValueError:
        return ""


def _study_type_for(item_type: str) -> str:
    return {
        "journalArticle": "peer-reviewed paper",
        "book": "book",
        "bookSection": "book chapter",
        "conferencePaper": "conference paper",
        "thesis": "thesis",
        "report": "report",
        "preprint": "preprint",
        "manuscript": "preprint",
        "webpage": "webpage",
        "magazineArticle": "peer-reviewed paper",
        "newspaperArticle": "peer-reviewed paper",
    }.get(item_type or "", "peer-reviewed paper")


def _custom_storage_dir(value: str | Path | None) -> Optional[Path]:
    """The user-selected PDF folder, when it exists on disk."""
    text = _clean_user_path(value)
    if not text:
        return None
    try:
        path = Path(text).expanduser()
    except (OSError, ValueError):
        return None
    try:
        return path if path.is_dir() else None
    except OSError:
        return None


def _iter_storage_pdfs(storage_dir: Path) -> list[Path]:
    """Every PDF under ``storage_dir`` (flat folders included), bounded."""
    try:
        if not storage_dir.is_dir():
            return []
        found: list[Path] = []
        for path in storage_dir.rglob("*"):
            if len(found) >= _MAX_INDEXED_PDFS:
                break
            try:
                if path.is_file() and path.suffix.lower() in _PDF_SUFFIXES:
                    found.append(path)
            except OSError:
                continue
        return found
    except OSError:
        return []


def _pdf_index(storage_dir: Path, cache: dict[str, list[Path]]) -> list[Path]:
    """Cached PDF listing, so one import does not walk the folder per item."""
    if "files" not in cache:
        cache["files"] = _iter_storage_pdfs(storage_dir)
    return cache["files"]


def _resolve_attachment_path(
    storage_dir: Path,
    item_key: str,
    raw_path: str,
    pdf_cache: Optional[dict[str, list[Path]]] = None,
) -> Path | None:
    """Resolve a Zotero ``itemAttachments.path`` value to a real file.

    Zotero stores ``storage:name.pdf`` under ``<storage>/<item key>/``; ZotFile
    and attanger move the same files into flat ``<folder>/name.pdf`` layouts.
    Both shapes are tried, then a filename lookup anywhere under the folder.
    """
    path = unquote((raw_path or "").strip())
    if not path:
        return None
    if path.startswith(("storage:", "attachments:")):
        path = path.split(":", 1)[1]
    relative = Path(path).expanduser()
    if relative.is_absolute():
        if relative.is_file():
            return relative
        # The library moved: look for the same file name in the chosen folder.
        relative = Path(relative.name)

    candidates = [
        storage_dir / item_key / relative,
        storage_dir / relative,
        storage_dir / relative.name,
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue

    # Last resort: the file lives somewhere under the selected folder.
    wanted = relative.name.casefold()
    for candidate in _pdf_index(storage_dir, {} if pdf_cache is None else pdf_cache):
        if candidate.name.casefold() == wanted:
            return candidate
    return None


def _title_in_filename(title: str, stem: str) -> bool:
    """Whether a PDF file name looks like it belongs to ``title``."""
    title_key = normalize_title(title)
    stem_key = normalize_title(stem)
    if len(title_key) < 20 or not stem_key:
        return False
    if titles_match(title, stem):
        return True
    # ZotFile names files "Author - Year - Title", so the title is a substring.
    if title_key in stem_key:
        return True
    return stem_key in title_key and len(stem_key) >= 20


def _fold_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _pdf_for_item(title: str, doi: str, files: list[Path]) -> Optional[Path]:
    """Match a paper to a PDF in a hand-picked folder by DOI then by title."""
    doi_key = _fold_name(_clean_doi(doi))
    for path in files:
        if doi_key and doi_key in _fold_name(path.stem):
            return path
    for path in files:
        if _title_in_filename(title, path.stem):
            return path
    return None


# ── PDF-folder import (no zotero.sqlite) ───────────────────────────────────

_DOI_IN_TEXT_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
_TITLE_NOISE_RE = re.compile(
    r"^(?:doi|https?://|www\.|issn|isbn|vol\.|volume|issue|journal|"
    r"received|accepted|published|available online|downloaded from|"
    r"preprint|copyright|©|all rights reserved)\b",
    re.IGNORECASE,
)


def _extract_doi_from_text(value: str) -> str:
    """First valid DOI found in a file name or an extracted first page."""
    for match in _DOI_IN_TEXT_RE.finditer(value or ""):
        candidate = match.group(0).rstrip(".,;:)]}\u00bb\u201d'\"")
        try:
            return normalize_doi(candidate)
        except ValueError:
            continue
    return ""


def _looks_like_title(value: str) -> bool:
    text = " ".join((value or "").split()).strip()
    if not (8 <= len(text) <= 320):
        return False
    if _TITLE_NOISE_RE.match(text) or _DOI_IN_TEXT_RE.search(text):
        return False
    words = [word for word in re.split(r"\s+", text) if re.search(r"[A-Za-zÀ-ž]", word)]
    return len(words) >= 2 or (len(words) == 1 and len(text) >= 15)


def _title_from_filename(stem: str) -> str:
    """Recover a readable paper title from a ZotFile/attanger file name."""
    text = unquote(stem or "").replace("_", " ").strip()
    text = re.sub(r"\s+", " ", text)
    # DOI-like prefixes: 10.1038-s41593-019-0123-4
    text = re.sub(r"^10\.\d{4,9}[\/ -]+", "", text)

    # "Author - 2019 - Title", "Author - Title", "2019 - Title".
    parts = [part.strip() for part in re.split(r"\s+[-–—]\s+", text) if part.strip()]
    if len(parts) > 1:
        for part in reversed(parts):
            if _looks_like_title(part):
                return part

    stripped = re.sub(r"^(?:19|20)\d{2}\s*[-–—]?\s*", "", text).strip()
    if _looks_like_title(stripped):
        return stripped
    if _looks_like_title(text):
        return text
    return text.strip()


def _title_from_pdf_text(text: str) -> str:
    """Best-effort title from the first plausible line of extracted text."""
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        if not _looks_like_title(line):
            continue
        # A long title often wraps on the next line; join when it looks open.
        if (
            len(line) < 90
            and index + 1 < len(lines)
            and _looks_like_title(lines[index + 1])
            and not re.search(r"[.!?:;]$", line)
            and len(line) + len(lines[index + 1]) <= 300
        ):
            return f"{line} {lines[index + 1]}".strip()
        return line
    return ""


def _pdf_file_to_item(path: Path) -> Optional[dict]:
    """Turn a standalone PDF into the same item shape the DB reader returns."""
    title = _title_from_filename(path.stem)
    doi = _extract_doi_from_text(unquote(path.stem))
    if not doi and "_" in path.stem:
        # Flat DOI names replace the slash with an underscore: 10.1038_s41593...
        doi = _extract_doi_from_text(path.stem.replace("_", "/", 1))

    text = ""
    if not doi or not _looks_like_title(title):
        try:
            text = extract_pdf_text(path, max_chars=_MAX_PDF_TEXT_CHARS)
        except Exception as exc:  # noqa: BLE001 - a broken PDF must not stop the scan
            logger.info("Could not extract text from %s: %s", path, exc)
            text = ""

    if not doi:
        doi = _extract_doi_from_text(text)
    if not _looks_like_title(title):
        title = _title_from_pdf_text(text) or title
    if not title:
        return None
    year = _year_from_date(text[:4000]) if text else None
    return {
        "zotero_key": "",
        "item_type": "journalArticle",
        "title": title,
        "authors": "",
        "year": year,
        "doi": doi,
        "abstract": "",
        "tags": [],
        "url": f"https://doi.org/{doi}" if doi else "",
        "pdf_path": str(path),
        "study_type": "peer-reviewed paper",
    }


def _scan_pdf_folder(folder: Path) -> list[dict]:
    """Every PDF in ``folder`` (recursively) as an importable study item."""
    items: list[dict] = []
    for path in _iter_storage_pdfs(folder):
        item = _pdf_file_to_item(path)
        if item:
            items.append(item)
    return items


def _read_zotero_items(
    db_path: Path,
    collection: str = "",
    storage_dir: str | Path | None = None,
) -> list[dict]:
    """Turn the Zotero SQLite schema into plain bibliographic dicts.

    ``storage_dir`` is the PDF folder the user picked; when it is a real
    directory it replaces Zotero's default ``<data dir>/storage`` and PDFs are
    also matched by DOI or title, which covers flat ZotFile/attanger folders.
    """
    custom = _custom_storage_dir(storage_dir)
    if custom is not None and custom.name.lower() != "storage":
        # The user selected the Zotero data directory itself; its PDFs live
        # under ``storage/<item key>/``.
        nested_storage = custom / "storage"
        if nested_storage.is_dir():
            custom = nested_storage
    storage = custom or (db_path.parent / "storage")
    pdf_cache: dict[str, list[Path]] = {}
    with _zotero_connection(db_path) as conn:
        if not _has_table(conn, "items"):
            return []
        items_columns = _table_columns(conn, "items")
        key_column = "key" if "key" in items_columns else "'' AS key"
        type_column = "itemTypeID" if "itemTypeID" in items_columns else "NULL AS itemTypeID"

        item_types: dict[int, str] = {}
        type_columns = _table_columns(conn, "itemTypes")
        if {"itemTypeID", "typeName"} <= type_columns:
            item_types = {
                row["itemTypeID"]: row["typeName"]
                for row in conn.execute("SELECT itemTypeID, typeName FROM itemTypes")
            }

        # ``itemData`` stores (item, field) -> valueID; the pairs live in
        # ``fields`` and the strings in ``itemDataValues``. Every table is
        # optional so a partially migrated library still yields its items.
        item_data: dict[int, dict[str, str]] = defaultdict(dict)
        field_columns = _table_columns(conn, "fields")
        value_columns = _table_columns(conn, "itemDataValues")
        data_columns = _table_columns(conn, "itemData")
        if (
            {"fieldID", "fieldName"} <= field_columns
            and {"valueID", "value"} <= value_columns
            and {"itemID", "fieldID", "valueID"} <= data_columns
        ):
            field_names = {
                row["fieldID"]: row["fieldName"]
                for row in conn.execute("SELECT fieldID, fieldName FROM fields")
            }
            data_values = {
                row["valueID"]: row["value"]
                for row in conn.execute("SELECT valueID, value FROM itemDataValues")
            }
            for row in conn.execute("SELECT itemID, fieldID, valueID FROM itemData"):
                name = field_names.get(row["fieldID"])
                value = data_values.get(row["valueID"])
                if name and value is not None:
                    item_data[row["itemID"]][name] = value

        deleted: set[int] = set()
        if _has_table(conn, "deletedItems"):
            deleted = {
                row[0] for row in conn.execute("SELECT itemID FROM deletedItems")
            }

        creators: dict[int, dict] = {}
        creator_columns = _table_columns(conn, "creators")
        if "creatorID" in creator_columns:
            first_expr = "firstName" if "firstName" in creator_columns else "''"
            last_expr = "lastName" if "lastName" in creator_columns else "''"
            mode_expr = "COALESCE(fieldMode, 0)" if "fieldMode" in creator_columns else "0"
            for row in conn.execute(
                f"SELECT creatorID, {first_expr} AS firstName, {last_expr} AS lastName,"
                f" {mode_expr} AS fieldMode FROM creators"
            ):
                creators[row["creatorID"]] = {
                    "first": row["firstName"] or "",
                    "last": row["lastName"] or "",
                    "mode": row["fieldMode"] or 0,
                }
        item_creators: dict[int, list[str]] = defaultdict(list)
        link_columns = _table_columns(conn, "itemCreators")
        if {"itemID", "creatorID"} <= link_columns:
            order_expr = "orderIndex" if "orderIndex" in link_columns else "0"
            for row in conn.execute(
                f"SELECT itemID, creatorID, {order_expr} AS orderIndex"
                " FROM itemCreators ORDER BY orderIndex"
            ):
                creator = creators.get(row["creatorID"])
                if not creator:
                    continue
                if creator["mode"] == 1:
                    name = creator["last"]
                else:
                    name = " ".join(
                        part for part in (creator["first"], creator["last"]) if part
                    ).strip()
                if name:
                    item_creators[row["itemID"]].append(name)

        item_tags: dict[int, list[str]] = defaultdict(list)
        tag_columns = _table_columns(conn, "tags")
        item_tag_columns = _table_columns(conn, "itemTags")
        if (
            {"tagID", "name"} <= tag_columns
            and {"itemID", "tagID"} <= item_tag_columns
        ):
            for row in conn.execute(
                "SELECT it.itemID, t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID"
            ):
                if row["name"]:
                    item_tags[row["itemID"]].append(row["name"])

        allowed: set[int] | None = None
        if collection:
            collection_columns = _table_columns(conn, "collections")
            collection_item_columns = _table_columns(conn, "collectionItems")
            if (
                {"collectionID", "collectionName"} <= collection_columns
                and {"collectionID", "itemID"} <= collection_item_columns
            ):
                ids = [
                    row[0]
                    for row in conn.execute(
                        "SELECT collectionID FROM collections WHERE lower(collectionName) = lower(?)",
                        [collection],
                    )
                ]
                if not ids:
                    return []
                placeholders = ",".join("?" for _ in ids)
                allowed = {
                    row[0]
                    for row in conn.execute(
                        f"SELECT itemID FROM collectionItems WHERE collectionID IN ({placeholders})",
                        ids,
                    )
                }
            else:
                return []

        attachments: dict[int, list[Path]] = defaultdict(list)
        attachment_columns = _table_columns(conn, "itemAttachments")
        if {"itemID", "parentItemID"} <= attachment_columns:
            path_expr = "a.path" if "path" in attachment_columns else "''"
            type_expr = "a.contentType" if "contentType" in attachment_columns else "''"
            if "key" in items_columns:
                attachment_sql = f"""SELECT a.parentItemID AS parent,
                                             i.key AS item_key,
                                             {path_expr} AS path,
                                             {type_expr} AS content_type
                                      FROM itemAttachments a
                                      LEFT JOIN items i ON i.itemID = a.itemID
                                      WHERE a.parentItemID IS NOT NULL"""
            else:
                attachment_sql = f"""SELECT a.parentItemID AS parent,
                                             '' AS item_key,
                                             {path_expr} AS path,
                                             {type_expr} AS content_type
                                      FROM itemAttachments a
                                      WHERE a.parentItemID IS NOT NULL"""
            for row in conn.execute(attachment_sql):
                content_type = (row["content_type"] or "").lower()
                raw = row["path"] or ""
                if (
                    "pdf" not in content_type
                    and not content_type
                    and ".pdf" not in raw.lower()
                ):
                    continue
                resolved = _resolve_attachment_path(
                    storage, row["item_key"] or "", raw, pdf_cache
                )
                if resolved:
                    attachments[row["parent"]].append(resolved)

        out: list[dict] = []
        claimed_pdfs: set[Path] = {
            path for paths in attachments.values() for path in paths
        }
        for row in conn.execute(
            f"SELECT itemID, {key_column}, {type_column} FROM items"
        ):
            try:
                item_id = row["itemID"]
                if item_id in deleted:
                    continue
                if allowed is not None and item_id not in allowed:
                    continue
                item_type = item_types.get(row["itemTypeID"] or 0, "")
                if item_type in _NON_PAPER_TYPES:
                    continue
                fields = item_data.get(item_id, {})
                title = (fields.get("title") or "").strip()
                if not title:
                    continue
                doi = _clean_doi(fields.get("DOI") or fields.get("doi") or "")
                url = (fields.get("url") or "").strip()
                source = url or (f"https://doi.org/{doi}" if doi else "")
                pdfs = attachments.get(item_id, [])
                if not pdfs and custom:
                    available = [
                        path
                        for path in _pdf_index(storage, pdf_cache)
                        if path not in claimed_pdfs
                    ]
                    matched = _pdf_for_item(title, doi, available)
                    if matched:
                        pdfs = [matched]
                        claimed_pdfs.add(matched)
                out.append({
                    "zotero_key": row["key"] or "",
                    "item_type": item_type,
                    "title": title,
                    "authors": ", ".join(item_creators.get(item_id, [])),
                    "year": _year_from_date(fields.get("date") or ""),
                    "doi": doi,
                    "abstract": (fields.get("abstractNote") or "").strip(),
                    "tags": item_tags.get(item_id, []),
                    "url": source,
                    "pdf_path": str(pdfs[0]) if pdfs else "",
                    "study_type": _study_type_for(item_type),
                })
            except Exception as exc:  # noqa: BLE001 - one bad row must not abort
                logger.warning("Skipping unreadable Zotero item %s: %s", row["itemID"], exc)
        return out


def _count_zotero_items(db_path: Path) -> int:
    """Number of bibliographic items, without materializing them.

    Probing a folder runs whenever the user types a path, so this must stay
    cheap: a counting query instead of a full read (the old implementation
    took seconds on a cold external drive and could time out the status call).
    The result is cached per database revision: opening a locked Zotero
    database requires copying it aside first.
    """
    try:
        stat = db_path.stat()
        wal = Path(str(db_path) + "-wal")
        try:
            wal_stat = wal.stat()
            signature = (
                int(stat.st_mtime_ns),
                stat.st_size,
                int(wal_stat.st_mtime_ns),
                wal_stat.st_size,
            )
        except OSError:
            signature = (int(stat.st_mtime_ns), stat.st_size, -1, -1)
    except OSError:
        return 0
    cache_key = (str(db_path), *signature)
    cached = _COUNT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    def _compute() -> int:
        with _zotero_connection(db_path) as conn:
            if not _has_table(conn, "items"):
                return 0
            sql = "SELECT COUNT(DISTINCT i.itemID) FROM items i"
            if _has_table(conn, "itemData") and _has_table(conn, "fields"):
                sql += (
                    " JOIN itemData d ON d.itemID = i.itemID"
                    " JOIN fields f ON f.fieldID = d.fieldID AND f.fieldName = 'title'"
                )
            if _has_table(conn, "deletedItems"):
                sql += " LEFT JOIN deletedItems di ON di.itemID = i.itemID"
                sql += " WHERE di.itemID IS NULL"
            else:
                sql += " WHERE 1 = 1"
            if _has_table(conn, "itemTypes"):
                sql += (
                    " AND i.itemTypeID NOT IN ("
                    "SELECT itemTypeID FROM itemTypes"
                    " WHERE typeName IN ('attachment', 'note', 'annotation'))"
                )
            row = conn.execute(sql).fetchone()
            return int(row[0] or 0) if row else 0

    try:
        count = _compute()
    except Exception:  # noqa: BLE001 - status must never fail the dialog
        try:
            count = len(_read_zotero_items(db_path))
        except Exception:  # noqa: BLE001
            return 0
    if len(_COUNT_CACHE) >= 64:
        _COUNT_CACHE.clear()
    _COUNT_CACHE[cache_key] = count
    return count


# ── dedup + insertion ──────────────────────────────────────────────────────


def _find_existing_entry(
    conn,
    title: str,
    doi: str = "",
    check_doi: bool = True,
    check_title: bool = True,
) -> Optional[dict]:
    """Existing Bransk row for this paper, by DOI then normalized title.

    Returns the row plus a ``match_type`` of ``"doi"`` or ``"title"`` so the
    import report can say why an item was treated as a duplicate.
    """
    doi_key = _clean_doi(doi) if check_doi else ""
    if doi_key:
        row = conn.execute(
            "SELECT id, title, doi FROM entries WHERE archived = 0 AND lower(doi) = lower(?)",
            [doi_key],
        ).fetchone()
        if row:
            return {**dict(row), "match_type": "doi"}

    title = " ".join((title or "").split()).strip()
    if not doi_key and (not title or not check_title):
        return None

    rows = list(conn.execute("SELECT id, title, doi FROM entries WHERE archived = 0"))
    if doi_key:
        # Legacy rows may still hold "https://doi.org/..." or mixed case.
        wanted = doi_key.lower()
        for row in rows:
            if _clean_doi(row["doi"] or "").lower() == wanted:
                return {**dict(row), "match_type": "doi"}
    if check_title and title:
        for row in rows:
            if titles_match(title, row["title"] or ""):
                return {**dict(row), "match_type": "title"}
    return None


def _item_tags(item: dict) -> str:
    tags = item.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,;]", tags) if t.strip()]
    return ",".join(["zotero", *[str(t).strip() for t in tags if str(t).strip()]])


async def _attach_pdf(entry_id: int, title: str, source_pdf: str) -> bool:
    """Copy a Zotero attachment into data/studies and index it."""
    fname = await anyio.to_thread.run_sync(copy_study_pdf, entry_id, title, source_pdf)
    if not fname:
        return False
    conn = get_db()
    try:
        conn.execute(
            "UPDATE entries SET pdf_path = ?, updated_at = datetime('now') WHERE id = ?",
            [fname, entry_id],
        )
        try:
            from app.studies_store import store_entry_pdf_hash

            store_entry_pdf_hash(conn, entry_id)
        except Exception:  # noqa: BLE001 - hashing is best-effort
            pass
        conn.commit()
    finally:
        conn.close()
    path = pdf_path_for(entry_id)
    try:
        await index_pdf(entry_id, path)
    except Exception as exc:  # noqa: BLE001 - the copied PDF is still useful
        logger.warning("Indexing Zotero PDF for #%s failed: %s", entry_id, exc)
    try:
        text = await anyio.to_thread.run_sync(extract_pdf_text, path)
        if text.strip():
            await anyio.to_thread.run_sync(studytext.save_text, entry_id, title, text)
    except Exception as exc:  # noqa: BLE001 - chunks are already indexed
        logger.info("Saving Zotero PDF text for #%s failed: %s", entry_id, exc)
    return True


async def _import_items(
    items: list[dict],
    *,
    attach_pdfs: bool,
    origin: str,
    check_doi: bool = True,
    check_title: bool = True,
) -> dict:
    """Shared import path for local DB, file uploads and the Web API.

    Duplicates are detected by DOI and/or normalized title (both on by
    default). A duplicate is never inserted again, but a Zotero PDF is still
    attached to the existing entry when it had none.
    """
    imported: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []
    pdfs_copied = 0

    conn = get_db()
    try:
        for item in items:
            title = " ".join(str(item.get("title") or "").split()).strip()
            if not title:
                continue
            doi = _clean_doi(str(item.get("doi") or ""))
            pdf = (item.get("pdf_path") or "").strip()

            existing = _find_existing_entry(
                conn, title, doi, check_doi=check_doi, check_title=check_title
            )
            if existing:
                entry_id = existing["id"]
                match_type = existing.get("match_type") or "title"
                pdf_attached = False
                if attach_pdfs and pdf and not pdf_path_for(entry_id):
                    pdf_attached = await _attach_pdf(
                        entry_id, existing.get("title") or title, pdf
                    )
                    if pdf_attached:
                        pdfs_copied += 1
                skipped.append({
                    "title": title,
                    "doi": doi,
                    "existing_id": entry_id,
                    "match_type": match_type,
                    "pdf_attached": pdf_attached,
                    "reason": f"Dublură detectată după {match_type.upper()}",
                })
                continue

            entry_data = {
                "title": title,
                "authors": item.get("authors") or "",
                "year": item.get("year"),
                "doi": doi,
                "url": item.get("url") or "",
                "source": item.get("url") or (f"https://doi.org/{doi}" if doi else ""),
                "abstract": item.get("abstract") or "",
                "tags": _item_tags(item),
                "study_type": item.get("study_type") or "peer-reviewed paper",
                "notes": f"Imported from Zotero ({origin}).",
            }
            try:
                entry_id = insert_study_entry(conn, entry_data)
            except Exception as exc:  # noqa: BLE001 - report one bad item, keep going
                errors.append(f"{title[:120]}: {exc}")
                continue

            imported.append({
                "id": entry_id,
                "title": title,
                "doi": doi,
                "zotero_key": item.get("zotero_key") or "",
            })

            if attach_pdfs and pdf:
                if await _attach_pdf(entry_id, title, pdf):
                    pdfs_copied += 1
    finally:
        conn.close()

    duplicates = [entry for entry in skipped if entry.get("match_type")]
    return {
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "pdfs_copied": pdfs_copied,
        "duplicates": duplicates,
        "errors": errors,
        "imported": imported,
        "skipped": skipped,
        "source": origin,
    }


# ── bibliography file parsers ──────────────────────────────────────────────


def _latex_clean(value: str) -> str:
    """Enough LaTeX cleanup for BibTeX titles and author names."""
    accents = {
        "'": {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "y": "ý", "c": "ć", "n": "ń"},
        "`": {"a": "à", "e": "è", "i": "ì", "o": "ò", "u": "ù"},
        '"': {"a": "ä", "e": "ë", "i": "ï", "o": "ö", "u": "ü", "y": "ÿ"},
        "^": {"a": "â", "e": "ê", "i": "î", "o": "ô", "u": "û"},
        "~": {"a": "ã", "n": "ñ", "o": "õ"},
        "c": {"c": "ç", "s": "ş", "t": "ţ"},
    }

    def accent(match):
        mark, letter = match.group(1), match.group(2).lower()
        return accents.get(mark, {}).get(letter, letter)

    value = re.sub(r"\\([`'\"^~c])\s*\{?([A-Za-z])\}?", accent, value or "")
    value = re.sub(r"\\([&%$#_{}])", r"\1", value)
    value = re.sub(r"\\[a-zA-Z]+\s*", "", value)
    value = value.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", value).strip()


def _read_bibtex_value(body: str, start: int) -> tuple[str, int]:
    """Read one BibTeX value (braced, quoted or bare) at ``body[start:]``."""
    pos = start
    while pos < len(body) and body[pos].isspace():
        pos += 1
    if pos >= len(body):
        return "", pos
    parts: list[str] = []
    while pos < len(body):
        while pos < len(body) and body[pos].isspace():
            pos += 1
        if pos >= len(body):
            break
        char = body[pos]
        if char == "{":
            depth, end = 1, pos + 1
            while end < len(body) and depth:
                if body[end] == "{":
                    depth += 1
                elif body[end] == "}":
                    depth -= 1
                end += 1
            parts.append(body[pos + 1:end - 1])
            pos = end
        elif char == '"':
            end = pos + 1
            while end < len(body) and body[end] != '"':
                if body[end] == "\\":
                    end += 1
                end += 1
            parts.append(body[pos + 1:end])
            pos = end + 1
        else:
            end = pos
            while end < len(body) and body[end] not in ",\n":
                end += 1
            parts.append(body[pos:end].strip())
            pos = end
        while pos < len(body) and body[pos].isspace():
            pos += 1
        if pos < len(body) and body[pos] == "#":
            pos += 1
            continue
        break
    if pos < len(body) and body[pos] == ",":
        pos += 1
    return "".join(parts), pos


def _parse_bibtex_fields(body: str) -> dict[str, str]:
    """Parse ``key, field = value, ...`` into a lowercase-keyed dict."""
    fields: dict[str, str] = {}
    pos = 0
    # Skip the citation key up to the first top-level comma.
    depth = 0
    while pos < len(body):
        char = body[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "," and depth == 0:
            pos += 1
            break
        pos += 1
    while pos < len(body):
        match = re.compile(r"\s*([A-Za-z][\w-]*)\s*=\s*").match(body, pos)
        if not match:
            break
        name = match.group(1).lower()
        value, pos = _read_bibtex_value(body, match.end())
        fields[name] = _latex_clean(value)
    return fields


def _parse_bibtex(text: str) -> list[dict]:
    items: list[dict] = []
    head = re.compile(r"@(\w+)\s*\{", re.IGNORECASE)
    pos = 0
    while True:
        match = head.search(text, pos)
        if not match:
            break
        entry_type = match.group(1).lower()
        start = match.end()
        depth, end = 1, start
        while end < len(text) and depth:
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
            end += 1
        body = text[start:end - 1]
        pos = end
        if entry_type in ("comment", "preamble", "string"):
            continue
        fields = _parse_bibtex_fields(body)
        title = fields.get("title") or ""
        if not title:
            continue
        authors = fields.get("author") or fields.get("editor") or ""
        names = []
        for raw in re.split(r"\s+and\s+", authors, flags=re.IGNORECASE):
            if not raw.strip():
                continue
            if "," in raw:
                last, first = [part.strip() for part in raw.split(",", 1)]
                names.append(f"{first} {last}".strip())
            else:
                names.append(raw.strip())
        year = fields.get("year") or ""
        year_match = re.search(r"\d{4}", year)
        items.append({
            "title": title,
            "authors": ", ".join(names),
            "year": int(year_match.group(0)) if year_match else None,
            "doi": _clean_doi(fields.get("doi") or ""),
            "abstract": fields.get("abstract") or "",
            "tags": [t.strip() for t in re.split(r"[,;]", fields.get("keywords") or "") if t.strip()],
            "url": fields.get("url") or "",
            "study_type": "book" if entry_type == "book" else "peer-reviewed paper",
        })
    return items


def _parse_ris(text: str) -> list[dict]:
    items: list[dict] = []
    current: dict[str, list[str]] = defaultdict(list)
    type_map = {
        "JOUR": "peer-reviewed paper",
        "JFULL": "peer-reviewed paper",
        "EJOUR": "peer-reviewed paper",
        "BOOK": "book",
        "CHAP": "book chapter",
        "CONF": "conference paper",
        "CPAPER": "conference paper",
        "THES": "thesis",
        "RPRT": "report",
        "UNPB": "preprint",
    }

    def flush():
        title = " ".join(current.get("TI") or current.get("T1") or []).strip()
        if not title:
            current.clear()
            return
        authors = [
            name
            for key in ("AU", "A1")
            for name in current.get(key, [])
            if name.strip()
        ]
        year = ""
        for key in ("PY", "Y1", "DA"):
            if current.get(key):
                year = current[key][0]
                break
        year_match = re.search(r"\d{4}", year)
        tags = [t.strip() for t in current.get("KW", []) if t.strip()]
        doi = _clean_doi((current.get("DO") or [""])[0])
        typ = (current.get("TY") or ["JOUR"])[0].upper()
        items.append({
            "title": title,
            "authors": ", ".join(authors),
            "year": int(year_match.group(0)) if year_match else None,
            "doi": doi,
            "abstract": " ".join(current.get("AB") or current.get("N2") or []).strip(),
            "tags": tags,
            "url": (current.get("UR") or [""])[0],
            "study_type": type_map.get(typ, "peer-reviewed paper"),
        })
        current.clear()

    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9])\s{1,2}-\s?(.*)$", line)
        if not match:
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "TY":
            current.clear()
            current[tag].append(value)
        elif tag == "ER":
            flush()
        else:
            current[tag].append(value)
    if current:
        flush()
    return items


def _parse_csl_json(text: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid CSL-JSON: {exc}") from exc
    if isinstance(payload, dict):
        items = payload.get("items") if "items" in payload else [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("CSL-JSON must be an object or an array")
    out: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title") or ""
        if not title:
            continue
        authors = []
        for author in raw.get("author") or []:
            if not isinstance(author, dict):
                continue
            if author.get("literal"):
                authors.append(str(author["literal"]))
            else:
                name = " ".join(
                    part for part in (author.get("given"), author.get("family")) if part
                ).strip()
                if name:
                    authors.append(name)
        year = None
        issued = raw.get("issued")
        if isinstance(issued, dict):
            parts = issued.get("date-parts")
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                try:
                    year = int(parts[0][0])
                except (TypeError, ValueError):
                    year = None
        if year is None:
            year = _year_from_date(str(issued or raw.get("date") or ""))
        keywords = raw.get("keyword") or raw.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in re.split(r"[,;]", keywords) if k.strip()]
        out.append({
            "title": title,
            "authors": ", ".join(authors),
            "year": year,
            "doi": _clean_doi(raw.get("DOI") or raw.get("doi") or ""),
            "abstract": raw.get("abstract") or "",
            "tags": [str(k) for k in keywords if str(k).strip()],
            "url": raw.get("URL") or raw.get("url") or "",
            "study_type": _study_type_for(
                {
                    "book": "book",
                    "chapter": "book chapter",
                    "paper-conference": "conference paper",
                    "thesis": "thesis",
                    "report": "report",
                }.get(raw.get("type") or "", "article-journal")
            ),
        })
    return out


def _decode_bibliography(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _detect_bibliography_format(filename: str, text: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".bib":
        return "bibtex"
    if suffix == ".ris":
        return "ris"
    if suffix == ".json":
        return "csl-json"
    stripped = text.lstrip()
    if stripped.startswith("@") or re.search(r"@\w+\s*\{", text):
        return "bibtex"
    if re.search(r"^TY\s{1,2}-\s?", text, re.MULTILINE):
        return "ris"
    if stripped.startswith(("{", "[")):
        return "csl-json"
    return "bibtex"


# ── Zotero Web API ─────────────────────────────────────────────────────────


def _zotero_api_payload_to_item(payload: dict) -> Optional[dict]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    item_type = data.get("itemType") or ""
    if item_type in _NON_PAPER_TYPES:
        return None
    title = (data.get("title") or "").strip()
    if not title:
        return None
    authors = []
    for creator in data.get("creators") or []:
        if not isinstance(creator, dict):
            continue
        if creator.get("name"):
            authors.append(str(creator["name"]))
        else:
            name = " ".join(
                part for part in (creator.get("firstName"), creator.get("lastName")) if part
            ).strip()
            if name:
                authors.append(name)
    tags = [
        str(tag.get("tag"))
        for tag in data.get("tags") or []
        if isinstance(tag, dict) and tag.get("tag")
    ]
    doi = _clean_doi(data.get("DOI") or "")
    url = (data.get("url") or "").strip() or (f"https://doi.org/{doi}" if doi else "")
    return {
        "zotero_key": data.get("key") or payload.get("key") or "",
        "item_type": item_type,
        "title": title,
        "authors": ", ".join(authors),
        "year": _year_from_date(data.get("date") or ""),
        "doi": doi,
        "abstract": (data.get("abstractNote") or "").strip(),
        "tags": tags,
        "url": url,
        "pdf_path": "",
        "study_type": _study_type_for(item_type),
    }


async def _fetch_zotero_api_items(
    client: httpx.AsyncClient, url: str, api_key: str, limit: int = 100
) -> list[dict]:
    headers = {
        "Zotero-API-Version": "3",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    collected: list[dict] = []
    start = 0
    while True:
        response = await client.get(
            url,
            params={"format": "json", "limit": limit, "start": start},
            headers=headers,
        )
        if response.status_code in (401, 403):
            raise HTTPException(401, "Cheia API Zotero a fost respinsă.")
        if response.status_code == 404:
            raise HTTPException(404, "Biblioteca Zotero nu a fost găsită.")
        if response.status_code == 429:
            raise HTTPException(429, "Zotero API a limitat cererile. Încearcă din nou.")
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break
        collected.extend(batch)
        total = 0
        try:
            total = int(response.headers.get("Total-Results") or 0)
        except ValueError:
            total = 0
        start += len(batch)
        if len(batch) < limit or (total and start >= total) or start >= 10000:
            break
    return collected


# ── request models ─────────────────────────────────────────────────────────


class ZoteroLocalImportRequest(BaseModel):
    collection: str = ""
    # Folder holding the Zotero data directory (or the database file itself).
    data_dir: str = ""
    folder_path: str = ""
    # Folder holding the PDFs; also accepted as an import source when it has
    # no database but does contain PDFs.
    storage_dir: str = ""
    pdf_folder: str = ""
    dedup_by_doi: bool = True
    dedup_by_title: bool = True
    # "auto": database when found, otherwise PDFs; "database"/"pdfs": strict.
    import_mode: Literal["auto", "database", "pdfs"] = "auto"


class ZoteroApiSyncRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    user_id: str = ""
    library_id: str = ""
    library_type: str = Field("user", pattern="^(user|group)$")
    limit: int = Field(100, ge=1, le=100)


# ── endpoints ──────────────────────────────────────────────────────────────


def _get_setting(key: str) -> str:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", [key]
        ).fetchone()
        return (row["value"] if row else "") or ""
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


def _set_setting(key: str, value: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')""",
            [key, value, value],
        )
        conn.commit()
    except sqlite3.Error as exc:  # noqa: BLE001 - the import must still run
        logger.warning("Could not save %s: %s", key, exc)
    finally:
        conn.close()


def _configured_pdf_folder() -> Optional[Path]:
    """The PDF folder remembered from a previous import, if still valid."""
    for value in (os.getenv("CHRONOKG_ZOTERO_PDF_DIR") or "", _get_setting(SETTING_PDF_FOLDER)):
        custom = _custom_storage_dir(value)
        if custom:
            return custom
    return None


def _probe_rank(probe: dict) -> int:
    """How useful a probed folder is: populated DB > PDFs > empty DB."""
    if probe.get("has_database"):
        return 2 if (probe.get("item_count") or 0) > 0 else 1
    return 1 if probe.get("has_pdfs") else 0


def _configured_probe() -> Optional[dict]:
    """Probe the configured folders and return the most useful one.

    The plain ``~/Zotero`` default can be a freshly created, empty library
    while the user's real library sits on another drive. A populated database
    or a PDF folder must therefore win over a valid-but-empty one.
    """
    best: Optional[dict] = None
    best_rank = 0
    for value in (
        (os.getenv("CHRONOKG_ZOTERO_DIR") or "").strip(),
        _get_setting(SETTING_DATA_DIR).strip(),
        (os.getenv("CHRONOKG_ZOTERO_PDF_DIR") or "").strip(),
        _get_setting(SETTING_PDF_FOLDER).strip(),
    ):
        if not value:
            continue
        try:
            probe = _probe_zotero_source(value)
        except OSError:
            continue
        rank = _probe_rank(probe)
        if rank > best_rank:
            best, best_rank = probe, rank
            if rank == 2:
                break
    return best if best_rank else None


def _status_message(probe: dict) -> str:
    count = probe.get("item_count") or 0
    pdfs = probe.get("pdf_count") or 0
    if probe.get("has_database") and probe.get("has_pdfs"):
        return f"Zotero găsit: {count} itemi bibliografici și {pdfs} PDF-uri disponibile."
    if probe.get("has_database"):
        return f"Zotero găsit: {count} de itemi bibliografici."
    if probe.get("has_pdfs"):
        return (
            f"Folder PDF detectat: {pdfs} fișiere. Nu există zotero.sqlite, "
            "dar PDF-urile pot fi importate direct."
        )
    return (
        "Nu am găsit zotero.sqlite sau PDF-uri în folderul selectat. "
        "Alege folderul care conține zotero.sqlite (sau folderul de PDF-uri)."
    )


def _status_payload(probe: dict, custom: Optional[Path], *, prefer_probe: bool = False) -> dict:
    storage = probe.get("storage_path") or ""
    if custom and not (prefer_probe and storage):
        storage = str(custom)
    return {
        # ``installed`` keeps its historical meaning (a Zotero database was
        # found); a plain PDF folder is reported through ``has_pdfs``.
        "installed": bool(probe.get("has_database")),
        "path": probe.get("path") or probe.get("requested_path") or "",
        "storage_path": storage,
        "custom_storage_path": str(custom) if custom else "",
        "custom_data_dir": probe.get("data_dir") or "",
        "database_path": probe.get("database_path") or "",
        "item_count": probe.get("item_count") or 0,
        "pdf_count": probe.get("pdf_count") or 0,
        "has_database": bool(probe.get("has_database")),
        "has_pdfs": bool(probe.get("has_pdfs")),
        "message": _status_message(probe),
    }


@router.get("/zotero/status")
def zotero_status(path: Optional[str] = Query(None, description="User-selected Zotero/PDF folder")):
    """Report whether a local Zotero installation or PDF folder can be imported.

    ``?path=`` probes a user-selected folder (the data directory, its
    ``storage/`` child, a parent library folder, or a plain PDF folder) and
    remembers it when it is usable. Without ``path`` the last validated folder
    and then the usual Zotero locations are checked.
    """
    custom = _configured_pdf_folder()
    requested = _clean_user_path(path)
    if requested:
        probe = _probe_zotero_source(requested)
        if probe["has_database"] or probe["has_pdfs"]:
            _set_setting(SETTING_DATA_DIR, probe["path"])
        return _status_payload(probe, custom, prefer_probe=True)

    probe = _configured_probe()
    if probe is not None:
        return _status_payload(probe, custom)

    _, db_path = _zotero_data_dir()
    if db_path:
        return _status_payload(_probe_zotero_source(db_path), custom)

    return {
        "installed": False,
        "path": "",
        "storage_path": str(custom) if custom else "",
        "custom_storage_path": str(custom) if custom else "",
        "custom_data_dir": "",
        "database_path": "",
        "item_count": 0,
        "pdf_count": 0,
        "has_database": False,
        "has_pdfs": False,
        "message": (
            "Nu am găsit ~/Zotero/zotero.sqlite. Instalează Zotero, selectează "
            "folderul bibliotecii sau un folder cu PDF-uri."
        ),
    }


@router.post("/zotero/import/local")
async def import_zotero_local(
    payload: ZoteroLocalImportRequest | None = Body(default=None),
):
    """Import items/PDFs from a Zotero database or a folder of PDFs.

    ``import_mode="auto"`` (default) uses ``zotero.sqlite`` when the selected
    folder contains one (directly, in its parent, or in a subfolder) and falls
    back to scanning the folder's PDFs. A folder without a database can always
    be imported in PDF mode, with DOI/title recovered from the PDF text or the
    file name.
    """
    request = payload or ZoteroLocalImportRequest()
    mode = request.import_mode
    collection = (request.collection or "").strip()

    source_value = _clean_user_path(request.data_dir or request.folder_path or "")
    storage_value = _clean_user_path(request.storage_dir or request.pdf_folder or "")

    explicit_source: Optional[Path] = None
    if source_value:
        explicit_source = _existing_path(source_value)
        if explicit_source is None:
            raise HTTPException(404, f"Folderul selectat nu există: {source_value}")

    storage_hint: Optional[Path] = None
    picked: Optional[Path] = None
    if storage_value:
        storage_hint = _existing_path(storage_value)
        if storage_hint is None:
            raise HTTPException(404, f"Folderul selectat nu există: {storage_value}")
        picked = _custom_storage_dir(storage_value)
        if picked:
            _set_setting(SETTING_PDF_FOLDER, str(picked))
    else:
        picked = _configured_pdf_folder()

    data_dir: Optional[Path] = None
    db_path: Optional[Path] = None
    if mode != "pdfs":
        best_dir: Optional[Path] = None
        best_db: Optional[Path] = None
        best_rank = 0
        for candidate in (explicit_source, storage_hint):
            if candidate is None:
                continue
            candidate_dir, candidate_db = _locate_zotero_db(candidate)
            if not candidate_db:
                continue
            rank = 2 if _count_zotero_items(candidate_db) > 0 else 1
            if rank > best_rank:
                best_dir, best_db, best_rank = candidate_dir, candidate_db, rank
                if rank == 2:
                    break
        data_dir, db_path = best_dir, best_db
        # A storage/PDF hint must not hide an otherwise standard installation;
        # a chosen data directory is authoritative only when it has items.
        if db_path is None and not source_value:
            data_dir, db_path = _zotero_data_dir()
        if db_path is not None and data_dir is not None and (source_value or storage_value):
            _set_setting(SETTING_DATA_DIR, str(data_dir))

    if mode == "database" and db_path is None:
        raise HTTPException(
            404,
            "Nu am găsit zotero.sqlite în folderul selectat (nici în folderul "
            "părinte sau subfoldere). Alege folderul bibliotecii Zotero.",
        )

    if mode == "pdfs" or db_path is None:
        pdf_folder: Optional[Path] = None
        if explicit_source is not None:
            pdf_folder = explicit_source if explicit_source.is_dir() else explicit_source.parent
        elif picked is not None:
            pdf_folder = picked
        else:
            probe = _configured_probe()
            if probe and probe.get("has_pdfs"):
                pdf_folder = Path(probe["storage_path"] or probe["path"])
        if pdf_folder is None:
            default_dir, _ = _zotero_data_dir()
            if default_dir and (default_dir / "storage").is_dir():
                pdf_folder = default_dir / "storage"
        if pdf_folder is None:
            raise HTTPException(
                404,
                "Nu am găsit zotero.sqlite sau un folder cu PDF-uri. "
                "Selectează folderul bibliotecii ori folderul de PDF-uri.",
            )
        if not pdf_folder.is_dir():
            pdf_folder = pdf_folder.parent

        items = await anyio.to_thread.run_sync(_scan_pdf_folder, pdf_folder)
        if not items:
            raise HTTPException(
                404, f"Nu am găsit niciun PDF în folderul selectat: {pdf_folder}"
            )
        _set_setting(SETTING_DATA_DIR, str(pdf_folder))
        result = await _import_items(
            items,
            attach_pdfs=True,
            origin="zotero-pdfs",
            check_doi=request.dedup_by_doi,
            check_title=request.dedup_by_title,
        )
        result.update({
            "mode": "pdfs",
            "scanned_count": len(items),
            "pdf_folder": str(pdf_folder),
            "storage_path": str(pdf_folder),
        })
        return result

    storage = picked
    if storage is None and db_path is not None:
        default_storage = db_path.parent / "storage"
        if default_storage.is_dir():
            storage = default_storage
        elif explicit_source is not None and explicit_source.is_dir():
            storage = explicit_source

    items = await anyio.to_thread.run_sync(
        _read_zotero_items, db_path, collection, storage
    )
    if collection and not items:
        return {
            "imported_count": 0,
            "skipped_count": 0,
            "pdfs_copied": 0,
            "duplicates": [],
            "errors": [f"Colecția „{collection}” nu există sau este goală."],
            "imported": [],
            "skipped": [],
            "source": "zotero-local",
            "mode": "database",
            "scanned_count": 0,
            "storage_path": str(storage) if storage else "",
        }
    result = await _import_items(
        items,
        attach_pdfs=True,
        origin="zotero-local",
        check_doi=request.dedup_by_doi,
        check_title=request.dedup_by_title,
    )
    result.update({
        "mode": "database",
        "scanned_count": len(items),
        "storage_path": str(storage) if storage else "",
    })
    return result


@router.post("/zotero/import/file")
async def import_zotero_file(file: UploadFile = File(...)):
    """Import a Zotero-exported BibTeX, RIS or CSL-JSON file."""
    raw = await file.read(MAX_IMPORT_FILE + 1)
    if len(raw) > MAX_IMPORT_FILE:
        raise HTTPException(413, "Fișierul bibliografic depășește 20 MB.")
    if not raw:
        raise HTTPException(400, "Fișierul este gol.")
    text = _decode_bibliography(raw)
    fmt = _detect_bibliography_format(file.filename or "", text)
    try:
        if fmt == "ris":
            items = _parse_ris(text)
        elif fmt == "csl-json":
            items = _parse_csl_json(text)
        else:
            items = _parse_bibtex(text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not items:
        raise HTTPException(400, "Nu am găsit niciun item bibliografic în fișier.")
    result = await _import_items(items, attach_pdfs=False, origin=f"zotero-file:{fmt}")
    result["format"] = fmt
    result["total_parsed"] = len(items)
    return result


@router.post("/zotero/sync/api")
async def sync_zotero_api(payload: ZoteroApiSyncRequest):
    """Import top-level items from a Zotero Web API library."""
    target = (payload.library_id or payload.user_id).strip()
    if not target:
        raise HTTPException(400, "user_id sau library_id este obligatoriu.")
    kind = "groups" if payload.library_type == "group" else "users"
    url = f"{ZOTERO_API_BASE}/{kind}/{quote(target, safe='')}/items/top"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            raw_items = await _fetch_zotero_api_items(
                client, url, payload.api_key, payload.limit
            )
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Zotero API nu a răspuns: {exc}") from exc

    items = [
        item
        for item in (_zotero_api_payload_to_item(raw) for raw in raw_items)
        if item
    ]
    result = await _import_items(items, attach_pdfs=False, origin="zotero-api")
    result["total_fetched"] = len(raw_items)
    return result


def _abstract_from_content(content: str) -> str:
    match = re.search(r"###\s*Rezumat\s*\n(.*?)(?:\n###|\Z)", content or "", re.DOTALL)
    return (match.group(1).strip()[:4000] if match else "")


def _csl_item(entry: dict) -> dict:
    """One Zotero-importable CSL-JSON item from a Bransk entry row."""
    doi = (entry.get("doi") or "").strip()
    year = (entry.get("bib_year") or str(entry.get("entry_date") or "")[:4]).strip()
    authors = []
    for name in _split_authors(entry.get("bib_authors") or entry.get("authors") or ""):
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            authors.append({"family": parts[1], "given": parts[0]})
        elif parts[0]:
            authors.append({"literal": parts[0]})
    study_type = (entry.get("study_type") or "").lower()
    item_type = "book" if "book" in study_type else (
        "thesis" if "thesis" in study_type else "article-journal"
    )
    source = (entry.get("source") or "").strip()
    item = {
        "id": f"bransk-{entry['id']}",
        "type": item_type,
        "title": (entry.get("bib_title") or entry.get("title") or "").strip(),
        "author": authors,
        "URL": source if source.startswith(("http://", "https://")) else (
            f"https://doi.org/{doi}" if doi else ""
        ),
        "note": f"Bransk entry #{entry['id']}",
    }
    if doi:
        item["DOI"] = doi
    if year.isdigit():
        item["issued"] = {"date-parts": [[int(year)]]}
    abstract = _abstract_from_content(entry.get("content") or "")
    if abstract:
        item["abstract"] = abstract
    tags = [t.strip() for t in (entry.get("tags") or "").split(",") if t.strip()]
    if tags:
        item["keyword"] = ", ".join(tags)
    return {key: value for key, value in item.items() if value not in ("", [], None)}


@router.get("/zotero/export")
def export_to_zotero(
    format: str = Query("csl-json", pattern="^(csl-json|bibtex|ris)$"),
    ids: Optional[str] = Query(None, description="Comma-separated entry ids; empty = whole library"),
    doi_only: bool = False,
):
    """Export the library in a format Zotero imports directly."""
    id_list = None
    if ids:
        try:
            id_list = [int(part) for part in ids.split(",") if part.strip()]
        except ValueError:
            raise HTTPException(400, "ids must be a comma-separated list of integers")
        if not id_list:
            raise HTTPException(400, "ids must not be empty")

    conn = get_db()
    try:
        rows = _fetch_studies(conn, id_list, doi_only)
        # `_fetch_studies` deliberately avoids the big `content` column; the
        # CSL export needs it only to recover the abstract paragraph.
        if rows:
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            contents = {
                row["id"]: row["content"] or ""
                for row in conn.execute(
                    f"SELECT id, content FROM entries WHERE id IN ({placeholders})",
                    ids,
                )
            }
            for row in rows:
                row["content"] = contents.get(row["id"], "")
    finally:
        conn.close()
    if not rows:
        raise HTTPException(404, "No studies match this export")

    if format == "bibtex":
        used: set = set()
        body = "\n\n".join(_to_bibtex(row, used) for row in rows) + "\n"
        return Response(
            content=body,
            media_type="application/x-bibtex; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="bransk-zotero.bib"'},
        )
    if format == "ris":
        body = "\n".join(_to_ris(row) for row in rows) + "\n"
        return Response(
            content=body,
            media_type="application/x-research-info-systems; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="bransk-zotero.ris"'},
        )

    body = json.dumps([_csl_item(row) for row in rows], ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bransk-zotero.json"'},
    )
