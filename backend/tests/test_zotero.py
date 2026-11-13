"""Zotero integration, bibliography import, and bot-check browser fallback.

These tests exercise the paths that cannot be verified through the real
services: a synthetic ``zotero.sqlite`` (with a PDF in ``storage/``), uploaded
BibTeX/RIS/CSL-JSON, a mocked Zotero Web API, and a mocked publisher that
answers with a Cloudflare challenge.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

PDF_BYTES = b"%PDF-1.4\n" + b"0" * 2000 + b"\n%%EOF"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module
    import app.studies_store as stores
    import app.studytext as studytext

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "zotero.db")
    monkeypatch.setattr(stores, "STUDIES_DIR", tmp_path / "studies")
    monkeypatch.setattr(studytext, "TEXT_DIR", tmp_path / "studytext")
    monkeypatch.setenv("CHRONOKG_ZOTERO_DIR", str(tmp_path / "Zotero"))
    db_module.init_db()
    with TestClient(app) as test_client:
        yield test_client


def _create_entry(client: TestClient, **payload) -> dict:
    body = {"title": "A Study", "study_type": "study"}
    body.update(payload)
    response = client.post("/api/entries", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ── synthetic local Zotero installation ───────────────────────────────────


def _build_zotero_library(root, name="Zotero"):
    data_dir = root / name
    storage = data_dir / "storage" / "ATTACH01"
    storage.mkdir(parents=True)
    (storage / "paper.pdf").write_bytes(PDF_BYTES)

    conn = sqlite3.connect(data_dir / "zotero.sqlite")
    conn.executescript(
        """
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT, itemTypeID INTEGER);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT,
                               lastName TEXT, fieldMode INTEGER DEFAULT 0);
        CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER,
                                   creatorTypeID INTEGER, orderIndex INTEGER);
        CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE itemTags (itemID INTEGER, tagID INTEGER);
        CREATE TABLE itemAttachments (itemID INTEGER, parentItemID INTEGER,
                                      linkMode INTEGER, contentType TEXT, path TEXT);
        CREATE TABLE deletedItems (itemID INTEGER);
        """
    )
    conn.executemany(
        "INSERT INTO itemTypes VALUES (?, ?)",
        [(1, "journalArticle"), (2, "attachment")],
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?)",
        [(1, "title"), (2, "date"), (3, "DOI"), (4, "abstractNote"), (5, "url")],
    )
    conn.executemany(
        "INSERT INTO items VALUES (?, ?, ?)",
        [(1, "ABCD1234", 1), (2, "ATTACH01", 2), (3, "DELETED1", 1)],
    )
    conn.executemany(
        "INSERT INTO itemDataValues VALUES (?, ?)",
        [
            (1, "Memory consolidation during sleep"),
            (2, "2019-04-01 2019-04-01"),
            (3, "10.1038/s41593-019-0123-4"),
            (4, "A randomized study of sleep and memory."),
            (5, "https://example.org/paper"),
        ],
    )
    conn.executemany(
        "INSERT INTO itemData VALUES (?, ?, ?)",
        [(1, 1, 1), (1, 2, 2), (1, 3, 3), (1, 4, 4), (1, 5, 5)],
    )
    conn.execute("INSERT INTO creators VALUES (1, 'Ada', 'Lovelace', 0)")
    conn.execute("INSERT INTO itemCreators VALUES (1, 1, 1, 0)")
    conn.execute("INSERT INTO tags VALUES (1, 'sleep')")
    conn.execute("INSERT INTO itemTags VALUES (1, 1)")
    conn.execute(
        "INSERT INTO itemAttachments VALUES (2, 1, 0, 'application/pdf', 'storage:paper.pdf')"
    )
    conn.execute("INSERT INTO deletedItems VALUES (3)")
    conn.commit()
    conn.close()
    return data_dir


# ── status ────────────────────────────────────────────────────────────────


def test_status_without_zotero(client, tmp_path, monkeypatch):
    import app.routers.zotero as zotero

    monkeypatch.setattr(zotero, "_candidate_data_dirs", lambda: [tmp_path / "nope"])
    body = client.get("/api/zotero/status").json()
    assert body["installed"] is False
    assert body["item_count"] == 0
    assert "message" in body


def test_status_detects_local_library(client, tmp_path):
    _build_zotero_library(tmp_path)
    body = client.get("/api/zotero/status").json()
    assert body["installed"] is True
    assert body["item_count"] == 1
    assert body["storage_path"].endswith("storage")


def test_status_probes_custom_folder_and_remembers_it(client, tmp_path):
    custom = _build_zotero_library(tmp_path, name="CustomZotero")

    body = client.get("/api/zotero/status", params={"path": str(custom)}).json()
    assert body["installed"] is True
    assert body["has_database"] is True
    assert body["has_pdfs"] is True
    assert body["item_count"] == 1
    assert body["pdf_count"] == 1
    assert body["path"] == str(custom)

    # The validated path survives into the next status call.
    remembered = client.get("/api/zotero/status").json()
    assert remembered["installed"] is True
    assert remembered["path"] == str(custom)
    assert remembered["item_count"] == 1


def test_status_finds_database_in_parent_and_subfolder(client, tmp_path):
    data_dir = _build_zotero_library(tmp_path)

    # Selecting storage/ must find the database one level up.
    storage = client.get(
        "/api/zotero/status", params={"path": str(data_dir / "storage")}
    ).json()
    assert storage["has_database"] is True
    assert storage["path"] == str(data_dir)

    # Selecting a library root must find Zotero/zotero.sqlite below it.
    root = tmp_path / "LibraryRoot"
    _build_zotero_library(root)
    nested = client.get("/api/zotero/status", params={"path": str(root)}).json()
    assert nested["has_database"] is True
    assert nested["path"] == str(root / "Zotero")


def test_status_probes_database_file_path(client, tmp_path):
    data_dir = _build_zotero_library(tmp_path)
    body = client.get(
        "/api/zotero/status", params={"path": str(data_dir / "zotero.sqlite")}
    ).json()
    assert body["has_database"] is True
    assert body["item_count"] == 1
    assert body["path"] == str(data_dir)


def test_status_detects_pdf_only_folder(client, tmp_path):
    folder = tmp_path / "PDFs"
    folder.mkdir()
    (folder / "Memory consolidation during sleep.pdf").write_bytes(PDF_BYTES)
    (folder / "Attention and memory.pdf").write_bytes(PDF_BYTES)

    body = client.get("/api/zotero/status", params={"path": str(folder)}).json()
    assert body["installed"] is False
    assert body["has_database"] is False
    assert body["has_pdfs"] is True
    assert body["pdf_count"] == 2
    assert body["item_count"] == 0
    assert "PDF" in body["message"]

    remembered = client.get("/api/zotero/status").json()
    assert remembered["has_pdfs"] is True
    assert remembered["path"] == str(folder)


def test_status_rejects_missing_path(client, tmp_path):
    body = client.get(
        "/api/zotero/status", params={"path": str(tmp_path / "missing")}
    ).json()
    assert body["installed"] is False
    assert body["has_database"] is False
    assert body["has_pdfs"] is False
    assert "message" in body


def test_status_accepts_pasted_path_decorations(client, tmp_path):
    """A path copied as ``-/media/...``, quoted, or ``file://`` is normalized."""
    custom = _build_zotero_library(tmp_path, name="PastedZotero")

    for pasted in (
        f"-{custom}",
        f'"{custom}"',
        f"  {custom}  ",
        custom.as_uri(),
    ):
        body = client.get("/api/zotero/status", params={"path": pasted}).json()
        assert body["has_database"] is True, pasted
        assert body["path"] == str(custom)
        assert body["item_count"] == 1


def test_local_import_accepts_dashed_data_dir(client, tmp_path):
    _build_zotero_library(tmp_path, name="DashedZotero")
    source = tmp_path / "DashedZotero"

    body = client.post(
        "/api/zotero/import/local", json={"data_dir": f"-{source}"}
    ).json()
    assert body["imported_count"] == 1
    assert body["pdfs_copied"] == 1


def test_status_prefers_populated_library_over_empty_default(client, tmp_path):
    """An empty ~/Zotero must not hide the real library selected by the user."""
    empty = _build_zotero_library(tmp_path, name="Zotero")
    conn = sqlite3.connect(empty / "zotero.sqlite")
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM itemAttachments")
    conn.execute("DELETE FROM itemData")
    conn.commit()
    conn.close()

    real = _build_zotero_library(tmp_path / "media", name="Zotero")
    probed = client.get("/api/zotero/status", params={"path": str(real)}).json()
    assert probed["item_count"] == 1

    body = client.get("/api/zotero/status").json()
    assert body["path"] == str(real)
    assert body["item_count"] == 1


def test_local_import_prefers_populated_storage_over_empty_data_dir(client, tmp_path):
    """The UI may send the empty default data dir plus the real storage path."""
    empty = _build_zotero_library(tmp_path, name="EmptyZotero")
    conn = sqlite3.connect(empty / "zotero.sqlite")
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM itemAttachments")
    conn.execute("DELETE FROM itemData")
    conn.commit()
    conn.close()

    real = _build_zotero_library(tmp_path / "media", name="Zotero")
    body = client.post(
        "/api/zotero/import/local",
        json={"data_dir": str(empty), "storage_dir": str(real)},
    ).json()
    assert body["mode"] == "database"
    assert body["imported_count"] == 1
    assert body["pdfs_copied"] == 1
    assert body["source"] == "zotero-local"

    remembered = client.get("/api/zotero/status").json()
    assert remembered["path"] == str(real)
    assert remembered["item_count"] == 1


def test_reader_tolerates_older_zotero_schema(tmp_path):
    """Optional Zotero columns/tables may be absent without raising."""
    data_dir = tmp_path / "OldZotero"
    storage = data_dir / "storage" / "OLDITEM1"
    storage.mkdir(parents=True)
    (storage / "old-paper.pdf").write_bytes(PDF_BYTES)

    conn = sqlite3.connect(data_dir / "zotero.sqlite")
    conn.executescript(
        """
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT, itemTypeID INTEGER);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT,
                               lastName TEXT);
        CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER);
        CREATE TABLE itemAttachments (itemID INTEGER, parentItemID INTEGER, path TEXT);
        """
    )
    conn.executemany("INSERT INTO itemTypes VALUES (?, ?)", [(1, "journalArticle")])
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?)", [(1, "title"), (2, "date")]
    )
    conn.executemany(
        "INSERT INTO itemDataValues VALUES (?, ?)",
        [(1, "Older Zotero paper"), (2, "2005")],
    )
    conn.executemany("INSERT INTO itemData VALUES (?, ?, ?)", [(1, 1, 1), (1, 2, 2)])
    conn.execute("INSERT INTO items VALUES (1, 'OLDITEM1', 1)")
    conn.execute("INSERT INTO creators VALUES (1, 'Grace', 'Hopper')")
    conn.execute("INSERT INTO itemCreators VALUES (1, 1)")
    conn.execute(
        "INSERT INTO itemAttachments VALUES (2, 1, 'storage:old-paper.pdf')"
    )
    conn.commit()
    conn.close()

    from app.routers.zotero import _read_zotero_items

    items = _read_zotero_items(data_dir / "zotero.sqlite")
    assert len(items) == 1
    assert items[0]["title"] == "Older Zotero paper"
    assert items[0]["authors"] == "Grace Hopper"
    assert items[0]["year"] == 2005
    assert items[0]["pdf_path"].endswith("old-paper.pdf")


# ── local import ──────────────────────────────────────────────────────────


def test_local_import_copies_pdf_and_deduplicates(client, tmp_path):
    import app.studies_store as stores

    _build_zotero_library(tmp_path)
    response = client.post("/api/zotero/import/local", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported_count"] == 1
    assert body["pdfs_copied"] == 1
    assert body["errors"] == []

    entries = client.get("/api/entries").json()
    assert len(entries) == 1
    entry = client.get(f"/api/entries/{entries[0]['id']}").json()
    assert entry["title"] == "Memory consolidation during sleep"
    assert entry["doi"] == "10.1038/s41593-019-0123-4"
    assert entry["authors"] == "Ada Lovelace"
    assert entry["pdf_path"]
    assert (stores.STUDIES_DIR / entry["pdf_path"]).is_file()

    # Second run must not duplicate the paper.
    again = client.post("/api/zotero/import/local", json={}).json()
    assert again["imported_count"] == 0
    assert again["skipped_count"] == 1
    assert again["duplicates"][0]["match_type"] == "doi"


def _move_pdf_to_custom_folder(tmp_path):
    """Give the synthetic library a flat PDF folder instead of storage/."""
    custom = tmp_path / "PDFs"
    custom.mkdir()
    source = tmp_path / "Zotero" / "storage" / "ATTACH01" / "paper.pdf"
    target = custom / "paper.pdf"
    target.write_bytes(source.read_bytes())
    source.unlink()
    return custom


def test_local_import_reads_pdfs_from_selected_folder(client, tmp_path):
    import app.studies_store as stores

    _build_zotero_library(tmp_path)
    custom = _move_pdf_to_custom_folder(tmp_path)

    response = client.post(
        "/api/zotero/import/local", json={"pdf_folder": str(custom)}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported_count"] == 1
    assert body["pdfs_copied"] == 1
    assert body["storage_path"] == str(custom)

    entry = client.get(f"/api/entries/{body['imported'][0]['id']}").json()
    assert entry["pdf_path"]
    assert (stores.STUDIES_DIR / entry["pdf_path"]).is_file()

    status = client.get("/api/zotero/status").json()
    assert status["custom_storage_path"] == str(custom)
    assert status["storage_path"] == str(custom)


def test_selected_folder_matches_pdf_by_title_without_attachment(client, tmp_path):
    _build_zotero_library(tmp_path)
    db = sqlite3.connect(tmp_path / "Zotero" / "zotero.sqlite")
    db.execute("DELETE FROM itemAttachments")
    db.commit()
    db.close()

    custom = tmp_path / "PDFs"
    custom.mkdir()
    (custom / "Lovelace 2019 - Memory consolidation during sleep.pdf").write_bytes(
        PDF_BYTES
    )

    body = client.post(
        "/api/zotero/import/local", json={"storage_dir": str(custom)}
    ).json()
    assert body["imported_count"] == 1
    assert body["pdfs_copied"] == 1


def test_dedup_by_doi_prevents_duplicate(client, tmp_path):
    _build_zotero_library(tmp_path)
    existing = _create_entry(
        client,
        title="Sleep and Memory Revisited",
        doi="https://doi.org/10.1038/S41593-019-0123-4",
    )

    body = client.post(
        "/api/zotero/import/local", json={"dedup_by_title": False}
    ).json()
    assert body["imported_count"] == 0
    assert body["skipped_count"] == 1
    duplicate = body["duplicates"][0]
    assert duplicate["match_type"] == "doi"
    assert duplicate["existing_id"] == existing["id"]
    assert "DOI" in duplicate["reason"]
    assert len(client.get("/api/entries").json()) == 1


def test_dedup_by_title_prevents_duplicate(client, tmp_path):
    _build_zotero_library(tmp_path)
    existing = _create_entry(client, title="Memory consolidation during sleep")

    body = client.post(
        "/api/zotero/import/local", json={"dedup_by_doi": False}
    ).json()
    assert body["imported_count"] == 0
    assert body["skipped_count"] == 1
    duplicate = body["duplicates"][0]
    assert duplicate["match_type"] == "title"
    assert duplicate["existing_id"] == existing["id"]
    assert "TITLE" in duplicate["reason"]


def test_dedup_can_be_disabled(client, tmp_path):
    _build_zotero_library(tmp_path)
    _create_entry(
        client,
        title="Memory consolidation during sleep",
        doi="10.1038/s41593-019-0123-4",
    )

    body = client.post(
        "/api/zotero/import/local",
        json={"dedup_by_doi": False, "dedup_by_title": False},
    ).json()
    assert body["imported_count"] == 1
    assert body["skipped_count"] == 0
    assert body["duplicates"] == []
    assert len(client.get("/api/entries").json()) == 2


def test_duplicate_gets_pdf_attached_when_missing(client, tmp_path):
    import app.studies_store as stores

    _build_zotero_library(tmp_path)
    existing = _create_entry(
        client,
        title="Memory consolidation during sleep",
        doi="10.1038/s41593-019-0123-4",
    )

    body = client.post("/api/zotero/import/local", json={}).json()
    assert body["imported_count"] == 0
    assert body["skipped_count"] == 1
    assert body["pdfs_copied"] == 1
    assert body["duplicates"][0]["pdf_attached"] is True

    detail = client.get(f"/api/entries/{existing['id']}").json()
    assert detail["pdf_path"]
    assert (stores.STUDIES_DIR / detail["pdf_path"]).is_file()


def test_local_import_uses_custom_data_dir(client, tmp_path):
    import app.studies_store as stores

    custom = _build_zotero_library(tmp_path, name="CustomZotero")

    body = client.post(
        "/api/zotero/import/local", json={"data_dir": str(custom)}
    ).json()
    assert body["mode"] == "database"
    assert body["imported_count"] == 1
    assert body["pdfs_copied"] == 1
    assert body["storage_path"] == str(custom / "storage")

    entry = client.get(f"/api/entries/{body['imported'][0]['id']}").json()
    assert entry["title"] == "Memory consolidation during sleep"
    assert (stores.STUDIES_DIR / entry["pdf_path"]).is_file()

    # The selected data dir is remembered for status and the next import.
    status = client.get("/api/zotero/status").json()
    assert status["path"] == str(custom)
    assert status["custom_data_dir"] == str(custom)


def _write_pdf_library(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Memory consolidation during sleep.pdf").write_bytes(PDF_BYTES)
    (folder / "Attention and memory.pdf").write_bytes(PDF_BYTES)
    return folder


def test_pdf_folder_import_extracts_doi_and_deduplicates(client, tmp_path, monkeypatch):
    import app.routers.zotero as zotero

    folder = _write_pdf_library(tmp_path / "PDFs")

    def fake_extract(path, max_chars=12000):
        name = Path(path).name
        if name.startswith("Memory"):
            return (
                "Memory consolidation during sleep\n"
                "Journal of Sleep Research\n"
                "DOI: 10.1038/s41593-019-0123-4\n"
            )
        return "Attention and memory\n"

    monkeypatch.setattr(zotero, "extract_pdf_text", fake_extract)

    body = client.post(
        "/api/zotero/import/local",
        json={"folder_path": str(folder), "import_mode": "auto"},
    ).json()
    assert body["mode"] == "pdfs"
    assert body["scanned_count"] == 2
    assert body["imported_count"] == 2
    assert body["pdfs_copied"] == 2
    assert body["errors"] == []

    entries = {entry["title"]: entry for entry in client.get("/api/entries").json()}
    assert set(entries) == {"Memory consolidation during sleep", "Attention and memory"}
    assert entries["Memory consolidation during sleep"]["doi"] == "10.1038/s41593-019-0123-4"

    # A second run finds the same PDFs as duplicates (title after DOI).
    again = client.post(
        "/api/zotero/import/local",
        json={"folder_path": str(folder), "import_mode": "pdfs"},
    ).json()
    assert again["imported_count"] == 0
    assert again["skipped_count"] == 2
    assert len(again["duplicates"]) == 2


def test_pdf_folder_import_uses_filename_without_text(client, tmp_path, monkeypatch):
    import app.routers.zotero as zotero

    folder = tmp_path / "PDFs"
    folder.mkdir()
    (folder / "Memory consolidation during sleep.pdf").write_bytes(PDF_BYTES)
    monkeypatch.setattr(zotero, "extract_pdf_text", lambda *args, **kwargs: "")

    body = client.post(
        "/api/zotero/import/local",
        json={"storage_dir": str(folder), "import_mode": "pdfs"},
    ).json()
    assert body["imported_count"] == 1
    assert client.get("/api/entries").json()[0]["title"] == "Memory consolidation during sleep"


def test_pdf_item_recovers_doi_from_flat_filename(tmp_path, monkeypatch):
    import app.routers.zotero as zotero

    path = tmp_path / "10.1038_s41593-019-0123-5.pdf"
    path.write_bytes(PDF_BYTES)
    monkeypatch.setattr(zotero, "extract_pdf_text", lambda *args, **kwargs: "")

    item = zotero._pdf_file_to_item(path)
    assert item is not None
    assert item["doi"] == "10.1038/s41593-019-0123-5"
    assert item["pdf_path"] == str(path)


def test_import_mode_database_requires_a_database(client, tmp_path):
    folder = _write_pdf_library(tmp_path / "PDFs")
    response = client.post(
        "/api/zotero/import/local",
        json={"folder_path": str(folder), "import_mode": "database"},
    )
    assert response.status_code == 404
    assert "zotero.sqlite" in response.json()["detail"]


def test_local_import_rejects_missing_selected_folder(client, tmp_path):
    response = client.post(
        "/api/zotero/import/local",
        json={"data_dir": str(tmp_path / "does-not-exist")},
    )
    assert response.status_code == 404
    assert "nu există" in response.json()["detail"]


# ── file import ───────────────────────────────────────────────────────────


BIBTEX = """
@article{lovelace2019,
  author = {Lovelace, Ada and Turing, Alan},
  title = {Memory consolidation during sleep},
  year = {2019},
  doi = {10.1038/s41593-019-0123-4},
  abstract = {A randomized study.},
  keywords = {sleep, memory}
}

@book{hoelderlin2020,
  author = {H{\\"o}lderlin, Friedrich},
  title = {On Memory},
  year = {2020},
  publisher = {Example Press}
}
"""

RIS = """TY  - JOUR
TI  - Working memory capacity
AU  - Miller, George
PY  - 1956
DO  - 10.1037/h0043158
KW  - memory
ER  - 
"""

CSL = """[
  {"type": "article-journal", "title": "Attention and memory",
   "author": [{"family": "Posner", "given": "Michael"}],
   "issued": {"date-parts": [[1980]]}, "DOI": "10.1037/0033-295X.87.1.1",
   "keyword": "attention"}
]
"""


def test_bibtex_file_import_dedups_existing_entries(client):
    _create_entry(client, title="Memory consolidation during sleep")
    response = client.post(
        "/api/zotero/import/file",
        files={"file": ("library.bib", BIBTEX, "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "bibtex"
    assert body["total_parsed"] == 2
    assert body["imported_count"] == 1
    assert body["skipped_count"] == 1

    titles = {e["title"] for e in client.get("/api/entries").json()}
    assert "On Memory" in titles  # LaTeX accent cleaned in the author, title kept


def test_ris_and_csl_json_import(client):
    ris = client.post(
        "/api/zotero/import/file",
        files={"file": ("library.ris", RIS, "text/plain")},
    )
    assert ris.status_code == 200, ris.text
    assert ris.json()["imported_count"] == 1

    csl = client.post(
        "/api/zotero/import/file",
        files={"file": ("library.json", CSL, "application/json")},
    )
    assert csl.status_code == 200, csl.text
    assert csl.json()["imported_count"] == 1

    titles = {e["title"] for e in client.get("/api/entries").json()}
    assert titles == {"Working memory capacity", "Attention and memory"}


def test_invalid_bibliography_is_rejected(client):
    response = client.post(
        "/api/zotero/import/file",
        files={"file": ("empty.bib", "not a bibliography", "text/plain")},
    )
    assert response.status_code == 400


# ── Web API sync ──────────────────────────────────────────────────────────


def test_api_sync_uses_mocked_zotero(client, monkeypatch):
    import app.routers.zotero as zotero

    async def fake_fetch(client_, url, api_key, limit=100):
        assert url.endswith("/users/42/items/top")
        assert api_key == "secret"
        return [{
            "data": {
                "key": "WEB1",
                "itemType": "journalArticle",
                "title": "Sleep and synaptic plasticity",
                "creators": [{"firstName": "Gina", "lastName": "Turrigiano"}],
                "date": "2018-01-01",
                "DOI": "10.1016/j.neuron.2018.01.001",
                "abstractNote": "Synapses and sleep.",
                "tags": [{"tag": "plasticity"}],
                "url": "https://example.org/sleep",
            }
        }]

    monkeypatch.setattr(zotero, "_fetch_zotero_api_items", fake_fetch)
    response = client.post(
        "/api/zotero/sync/api",
        json={"api_key": "secret", "user_id": "42"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported_count"] == 1
    assert body["total_fetched"] == 1
    assert client.get("/api/entries").json()[0]["title"] == "Sleep and synaptic plasticity"


# ── export ────────────────────────────────────────────────────────────────


def test_export_formats_for_zotero(client):
    _create_entry(
        client,
        title="Exported Paper",
        authors="Ada Lovelace, Alan Turing",
        doi="10.1000/xyz",
        entry_date="2019-01-01",
        content="# Exported Paper\n\n### Rezumat\nAbstract text.",
    )

    csl = client.get("/api/zotero/export")
    assert csl.status_code == 200
    items = csl.json()
    assert items[0]["DOI"] == "10.1000/xyz"
    assert items[0]["author"][0]["family"] == "Lovelace"
    assert items[0]["abstract"] == "Abstract text."

    bibtex = client.get("/api/zotero/export", params={"format": "bibtex"})
    assert bibtex.status_code == 200
    assert "@article" in bibtex.text and "10.1000/xyz" in bibtex.text

    ris = client.get("/api/zotero/export", params={"format": "ris"})
    assert ris.status_code == 200
    assert "TY  - JOUR" in ris.text


# ── bot challenge / browser fallback ─────────────────────────────────────


def test_detect_bot_challenge_signatures():
    from app.studies_store import detect_bot_challenge

    assert detect_bot_challenge(403, "text/html", "") == "http_403"
    assert detect_bot_challenge(
        503, "text/html", "<title>Just a moment...</title> cloudflare"
    ) == "just a moment"
    assert detect_bot_challenge(200, "application/pdf", b"%PDF-".decode()) == ""


def test_fulltext_returns_browser_fallback_on_bot_challenge(client, monkeypatch):
    import app.fulltext as fulltext

    entry = _create_entry(
        client,
        title="Blocked Paper",
        doi="10.1234/blocked",
        source="https://publisher.example/paper",
    )

    async def fake_find(entry_row, client=None):
        return [fulltext.Candidate("https://publisher.example/paper", "publisher", "html")]

    async def fake_fetch_html(client_, url):
        raise fulltext.BotChallenge(url, 403, "http_403")

    monkeypatch.setattr(fulltext, "find_fulltext", fake_find)
    monkeypatch.setattr(fulltext, "_fetch_html_text", fake_fetch_html)

    result = asyncio.run(fulltext.fetch_fulltext(entry["id"]))
    assert result["status"] == "none"
    assert result["bot_challenge"] is True
    assert result["manual_upload_needed"] is True
    assert result["browser_download_url"] == "https://doi.org/10.1234/blocked"
    assert result["browser_download_url_alt"] == "https://sci-hub.se/10.1234/blocked"
    assert any(item.get("error") == "bot_challenge" for item in result["tried"])


def test_bulk_download_reports_manual_upload(client, monkeypatch):
    import app.fulltext as fulltext
    import app.study_import as study_import

    entry = _create_entry(client, title="Blocked Again", doi="10.1234/again")

    async def fake_fetch(entry_id, index=True):
        return {
            "status": "none",
            "bot_challenge": True,
            "browser_download_url": "https://doi.org/10.1234/again",
            "browser_download_url_alt": "https://sci-hub.se/10.1234/again",
            "manual_upload_needed": True,
        }

    monkeypatch.setattr(fulltext, "fetch_fulltext", fake_fetch)
    result = asyncio.run(study_import.bulk_download_studies([entry["id"]]))
    assert result["manual_upload_needed"] == 1
    assert result["browser_fallback"][0]["id"] == entry["id"]
    assert result["browser_fallback"][0]["upload_endpoint"] == f"/api/entries/{entry['id']}/pdf"


def test_manual_pdf_upload_indexes_and_sets_pdf_path(client, tmp_path):
    import app.studies_store as stores

    entry = _create_entry(client, title="Manual Upload")
    response = client.post(
        f"/api/entries/{entry['id']}/pdf",
        files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pdf_path"].endswith(".pdf")
    assert (stores.STUDIES_DIR / body["pdf_path"]).is_file()

    detail = client.get(f"/api/entries/{entry['id']}").json()
    assert detail["pdf_path"] == body["pdf_path"]


def test_manual_pdf_upload_rejects_html_disguised_as_pdf(client):
    entry = _create_entry(client, title="Not A PDF")
    response = client.post(
        f"/api/entries/{entry['id']}/pdf",
        files={"file": ("paper.pdf", b"<html>challenge</html>", "application/pdf")},
    )
    assert response.status_code == 400
