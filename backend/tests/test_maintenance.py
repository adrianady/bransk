import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def _run_analysis(client):
    resp = client.post("/api/maintenance/analyze")
    assert resp.status_code == 200
    report = None
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            parsed = json.loads(line[6:])
            if parsed.get("type") == "done":
                report = parsed["report"]
    assert report is not None
    return report


def test_maintenance_normalizes_entries_and_recomputes_theories():
    with TestClient(app) as client:
        client.post("/api/entries", json={
            "title": "  Messy Entry  ",
            "content": "x",
            "entry_date": "2016 some description",
            "tags": "alpha",
        })
        # Make the row dirty on purpose: the API normalizes tags on write now,
        # so only a pre-existing row can exercise the maintenance repair.
        from app.db import get_db

        conn = get_db()
        try:
            conn.execute("UPDATE entries SET tags = ? WHERE id = 1", ["2016-01-01 | alpha,beta, alpha"])
            conn.commit()
        finally:
            conn.close()
        theory = client.post("/api/theories", json={"name": "Theory T", "status": "emerging"}).json()
        study = client.post("/api/entries", json={"title": "Study S", "content": "c", "study_type": "paper"}).json()
        client.post(f"/api/theories/{theory['id']}/evidence", json={"entry_id": study["id"], "stance": "pro"})
        client.put(f"/api/theories/{theory['id']}", json={"status": "refuted"})

        report = _run_analysis(client)
        assert report["entries_fixed"] >= 1
        assert report["dates_fixed"] >= 1
        assert report["tags_fixed"] >= 1
        assert report["theories_updated"] >= 1

        entries = {e["id"]: e for e in client.get("/api/entries").json()}
        messy = entries[1]
        assert messy["entry_date"] == "2016-01-01"
        assert messy["tags"] == "alpha, beta"
        assert messy["title"] == "Messy Entry"
        # pro evidence -> supported
        assert client.get("/api/theories").json()[0]["status"] == "supported"


def test_maintenance_rereads_downloaded_pdf():
    with TestClient(app) as client:
        study = client.post("/api/entries", json={
            "title": "Sleep Study",
            "content": "original",
            "study_type": "paper",
            "design": "RCT",
            "effect_direction": "positive",
            "quality": 4,
        }).json()
        studies_dir = Path(os.environ["CHRONOKG_STUDIES_DIR"])
        studies_dir.mkdir(parents=True, exist_ok=True)
        pdf = studies_dir / f"{study['id']}-sleep-study.pdf"
        try:
            import pymupdf

            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Sleep consolidation memory N=100 positive effect")
            doc.save(str(pdf))
            doc.close()
        except Exception:
            pdf.write_bytes(b"%PDF-1.4 fake")  # fallback: no text layer -> unreadable path

        report = _run_analysis(client)
        assert report["pdfs_found"] >= 1
        entry = client.get(f"/api/entries/{study['id']}").json()
        assert "reanalysis" in entry["content"] or report["pdfs_unreadable"] == 1


def test_maintenance_classifies_tag_based_studies():
    with TestClient(app) as client:
        client.post("/api/entries", json={"title": "Mind paper", "content": "x", "tags": "mind,pdf"})
        report = _run_analysis(client)
        assert report["studies_classified"] >= 1
        entry = client.get("/api/entries").json()[0]
        assert entry["study_type"] == "study"
