import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

import app.db as db_module
from app.db import get_db, init_db
from app.main import app
from app.study_import import (
    bulk_import_studies,
    bulk_delete_studies,
    extract_identifiers,
    find_duplicate_study,
    insert_study_entry,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_bulk_import.db")
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_conn(client):
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def test_bulk_import_persists_downloaded_pdf_and_indexes_it(client, tmp_path, monkeypatch):
    import app.studies_store as stores
    import app.study_import as study_import

    studies_dir = tmp_path / "studies"
    studies_dir.mkdir()
    monkeypatch.setattr(stores, "STUDIES_DIR", studies_dir)

    async def fake_download(entry_id, _url, _title):
        filename = f"{entry_id}-imported.pdf"
        (studies_dir / filename).write_bytes(b"%PDF-1.4\n")
        return filename

    monkeypatch.setattr(study_import, "download_study_pdf", fake_download)
    index_pdf = AsyncMock(return_value=4)
    monkeypatch.setattr(study_import, "index_pdf", index_pdf)

    result = asyncio.run(bulk_import_studies(
        [{"title": "Imported PDF study", "pdf_url": "https://example.org/paper.pdf"}],
        auto_download_pdfs=True,
    ))

    imported = result["imported"][0]
    assert result["pdfs_downloaded"] == 1
    assert imported["pdf_downloaded"] is True
    expected_path = studies_dir / f"{imported['id']}-imported.pdf"
    assert expected_path.read_bytes().startswith(b"%PDF-")
    conn = get_db()
    try:
        row = conn.execute("SELECT pdf_path FROM entries WHERE id = ?", [imported["id"]]).fetchone()
        assert row["pdf_path"] == expected_path.name
    finally:
        conn.close()
    index_pdf.assert_awaited_once_with(imported["id"], expected_path)


def test_bulk_import_reports_pdf_acquisition_failures(client, monkeypatch):
    import app.study_import as study_import
    import app.fulltext as fulltext

    monkeypatch.setattr(study_import, "download_study_pdf", AsyncMock(return_value=None))
    monkeypatch.setattr(fulltext, "fetch_fulltext", AsyncMock(return_value={"status": "none"}))

    result = asyncio.run(bulk_import_studies(
        [{"title": "Study without an open PDF", "pdf_url": "https://example.org/paper.pdf"}],
        auto_download_pdfs=True,
    ))

    assert result["total_imported"] == 1
    assert result["pdfs_downloaded"] == 0
    assert result["pdfs_failed"] == 1
    assert result["imported"][0]["pdf_downloaded"] is False


def test_bulk_import_keeps_saved_pdf_and_reports_indexing_failure(client, tmp_path, monkeypatch):
    import app.studies_store as stores
    import app.study_import as study_import

    studies_dir = tmp_path / "studies"
    studies_dir.mkdir()
    monkeypatch.setattr(stores, "STUDIES_DIR", studies_dir)

    async def fake_download(entry_id, _url, _title):
        filename = f"{entry_id}-index-failure.pdf"
        (studies_dir / filename).write_bytes(b"%PDF-1.4\n")
        return filename

    monkeypatch.setattr(study_import, "download_study_pdf", fake_download)
    monkeypatch.setattr(study_import, "index_pdf", AsyncMock(side_effect=RuntimeError("index failed")))

    result = asyncio.run(bulk_import_studies(
        [{"title": "Downloaded but not indexed", "pdf_url": "https://example.org/paper.pdf"}],
        auto_download_pdfs=True,
    ))

    imported = result["imported"][0]
    assert result["pdfs_downloaded"] == 1
    assert result["pdfs_failed"] == 0
    assert result["pdfs_index_failed"] == 1
    assert imported["pdf_downloaded"] is True
    assert imported["pdf_index_failed"] is True
    assert (studies_dir / f"{imported['id']}-index-failure.pdf").is_file()


def test_bulk_import_propagates_fallback_pdf_indexing_failure(client, monkeypatch):
    import app.study_import as study_import
    import app.fulltext as fulltext

    monkeypatch.setattr(study_import, "download_study_pdf", AsyncMock(return_value=None))
    monkeypatch.setattr(fulltext, "fetch_fulltext", AsyncMock(return_value={
        "status": "pdf",
        "index_error": True,
    }))

    result = asyncio.run(bulk_import_studies(
        [{"title": "Fallback PDF with index failure", "pdf_url": "https://example.org/paper.pdf"}],
        auto_download_pdfs=True,
    ))

    assert result["pdfs_downloaded"] == 1
    assert result["pdfs_failed"] == 0
    assert result["pdfs_index_failed"] == 1
    assert result["imported"][0]["pdf_index_failed"] is True


def test_extract_identifiers():
    text = """
    Check out these references:
    10.1038/s41586-020-2012-7
    https://doi.org/10.1016/j.neuron.2021.05.002;
    arXiv:2301.07067
    https://arxiv.org/abs/2104.08653
    https://pubmed.ncbi.nlm.nih.gov/32188939/
    PMID: 12345678
    PMC7144277
    https://example.com/some/article.pdf
    10.1038/s41586-020-2012-7
    """
    ids = extract_identifiers(text)
    assert "10.1038/s41586-020-2012-7" in ids
    assert "10.1016/j.neuron.2021.05.002" in ids
    assert "arXiv:2301.07067" in ids
    assert "arXiv:2104.08653" in ids
    assert "PMID:32188939" in ids
    assert "PMID:12345678" in ids
    assert "PMC7144277" in ids
    assert "https://example.com/some/article.pdf" in ids
    # Ensure deduplicated
    assert ids.count("10.1038/s41586-020-2012-7") == 1


def test_insert_and_duplicate_check(db_conn):
    study_data = {
        "title": "Sleep deprivation impairs memory consolidation in hippocampus",
        "authors": "Walker M.P., Stickgold R.",
        "year": 2017,
        "doi": "10.1038/nrn.2017.99",
        "study_type": "peer-reviewed paper",
        "quality": 5,
        "design": "experimental study",
        "effect_direction": "negative",
        "tags": "sleep, neuroscience",
        "snippet": "Deprivation significantly disrupts synaptic plasticity and recall.",
    }
    new_id = insert_study_entry(db_conn, study_data)
    assert new_id > 0

    # Verify fields in DB
    row = db_conn.execute("SELECT * FROM entries WHERE id = ?", [new_id]).fetchone()
    assert row["title"] == study_data["title"]
    assert row["doi"] == "10.1038/nrn.2017.99"
    assert row["entry_date"] == "2017-01-01"
    assert "external-import" in row["tags"]
    assert "study" in row["tags"]
    assert "Walker M.P." in row["content"]

    # Check duplicate by DOI
    dup1 = find_duplicate_study(db_conn, "Some other title", doi="10.1038/nrn.2017.99")
    assert dup1 is not None
    assert dup1["id"] == new_id

    # Check duplicate by title matching
    dup2 = find_duplicate_study(db_conn, "Sleep deprivation impairs memory consolidation in hippocampus...")
    assert dup2 is not None
    assert dup2["id"] == new_id

    # Non-duplicate
    dup3 = find_duplicate_study(db_conn, "Completely different paper on quantum computing", doi="10.1103/physrev.1.1")
    assert dup3 is None


def test_search_external_endpoint(client, db_conn):
    mock_results = [
        {
            "title": "Melatonin rhythm and circadian regulation",
            "authors": "Arendt J.",
            "year": 2018,
            "doi": "10.1016/j.cell.2018.01.001",
            "pdf_url": "https://example.com/melatonin.pdf",
            "source": "crossref",
            "snippet": "Circadian phase shifting by melatonin.",
            "is_oa": True,
        }
    ]
    with patch("app.routers.research.search_academic_studies", new=AsyncMock(return_value=mock_results)):
        resp = client.post("/api/studies/search-external", json={"query": "melatonina somn", "max_results": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["title"] == "Melatonin rhythm and circadian regulation"
        assert data["results"][0]["already_in_library"] is False


def test_resolve_doi_endpoint(client):
    mock_meta = {
        "title": "A landmark paper on synaptic transmission",
        "authors": "Katz B.",
        "year": 1969,
        "doi": "10.1113/jphysiol.1969.sp008888",
        "url": "https://doi.org/10.1113/jphysiol.1969.sp008888",
        "pdf_url": "",
        "abstract": "Release of neural transmitter packets at synapses.",
        "source": "crossref",
        "study_type": "peer-reviewed paper",
        "is_oa": False,
    }
    with patch("app.routers.research.resolve_identifier_metadata", new=AsyncMock(return_value=mock_meta)):
        resp = client.post("/api/studies/resolve-doi", json={"identifier": "10.1113/jphysiol.1969.sp008888"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "A landmark paper on synaptic transmission"
        assert data["year"] == 1969


def test_import_bulk_endpoint(client, db_conn):
    studies = [
        {
            "title": "Alpha oscillations reflect cortical inhibition",
            "authors": "Klimesch W.",
            "year": 2012,
            "doi": "10.1016/j.brainresrev.2011.11.002",
            "study_type": "peer-reviewed paper",
            "quality": 5,
            "snippet": "Alpha waves represent gating by inhibition.",
        },
        {
            "title": "Theta-gamma phase synchronization in hippocampus",
            "authors": "Lisman J.",
            "year": 2010,
            "doi": "10.1016/j.neuron.2010.05.011",
            "study_type": "peer-reviewed paper",
            "quality": 5,
            "snippet": "Dual frequency code for working memory.",
        },
    ]

    # First import
    resp1 = client.post(
        "/api/studies/import-bulk",
        json={"studies": studies, "auto_download_pdfs": False},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["total_imported"] == 2
    assert data1["total_skipped"] == 0

    # Second import with same items should skip them as duplicates
    resp2 = client.post(
        "/api/studies/import-bulk",
        json={"studies": studies, "auto_download_pdfs": False},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total_imported"] == 0
    assert data2["total_skipped"] == 2


def test_import_bulk_from_raw_text(client, db_conn):
    raw_text = """
    10.1016/j.neuron.2023.01.001
    arXiv:2301.09999
    """
    mock_res_1 = {
        "title": "Dopaminergic gating of reward prediction errors",
        "authors": "Schultz W.",
        "year": 2023,
        "doi": "10.1016/j.neuron.2023.01.001",
        "study_type": "peer-reviewed paper",
        "snippet": "Phasic dopamine encodes prediction errors.",
    }
    mock_res_2 = {
        "title": "Transformer architectures in working memory modelling",
        "authors": "Vaswani A.",
        "year": 2023,
        "doi": "10.48550/arXiv.2301.09999",
        "study_type": "peer-reviewed paper",
        "snippet": "Self-attention mechanism captures temporal associations.",
    }

    async def fake_resolve(ident, client=None):
        if "10.1016" in ident:
            return mock_res_1
        if "2301.09999" in ident:
            return mock_res_2
        return None

    with patch("app.study_import.resolve_identifier_metadata", side_effect=fake_resolve):
        resp = client.post(
            "/api/studies/import-bulk",
            json={"raw_text": raw_text, "auto_download_pdfs": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_imported"] == 2
        titles = [r["title"] for r in data["imported"]]
        assert "Dopaminergic gating of reward prediction errors" in titles
        assert "Transformer architectures in working memory modelling" in titles



def test_bulk_delete(client, db_conn):
    e1 = insert_study_entry(db_conn, {"title": "Study To Delete 1", "year": 2020})
    e2 = insert_study_entry(db_conn, {"title": "Study To Delete 2", "year": 2021})

    resp = client.post("/api/studies/bulk-delete", json={"entry_ids": [e1, e2]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2

    # Check they no longer exist
    rows = db_conn.execute("SELECT id FROM entries WHERE id IN (?, ?)", [e1, e2]).fetchall()
    assert len(rows) == 0
