"""HTTP layer for per-page document parsing (slice 4).

Hermetic: PDFs are synthesised in the test tmp dir, ``complete_with_images``
is monkeypatched in ``app.document_parse`` — no real provider calls. The
shared conftest already points ``CHRONOKG_STUDIES_DIR`` at a per-process
folder, so page images never touch the real library.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

NATIVE_TEXT = (
    "Această pagină are un strat de text nativ suficient de lung pentru a fi "
    "considerat utilizabil la ancorarea citatelor din teză."
)
LONG_TRANSCRIPT = "Some transcribed text. " * 20  # 460 chars, clean


def _tiny_png() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=40, height=40)
    png = page.get_pixmap().tobytes("png")
    doc.close()
    return png


def _make_pdf(path, pages) -> None:
    """Build a PDF from page specs: text / image / blank (all optional)."""
    import pymupdf

    png = _tiny_png()
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=612, height=792)
        if spec.get("text"):
            page.insert_text((72, 72), spec["text"])
        if spec.get("image"):
            page.insert_image(pymupdf.Rect(60, 120, 360, 420), stream=png)
    doc.save(str(path))
    doc.close()


def _study(client, title="Parse HTTP study") -> dict:
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201, res.text
    return res.json()


def _attach_pdf(entry_id: int, pdf: Path, name: str = "test.pdf") -> None:
    """Store *pdf* where ``pdf_path_for`` finds it and link it on the entry."""
    from app.db import get_db

    studies_dir = Path(os.environ["CHRONOKG_STUDIES_DIR"])
    studies_dir.mkdir(parents=True, exist_ok=True)
    dest = studies_dir / f"{entry_id}-{name}"
    dest.write_bytes(pdf.read_bytes())
    conn = get_db()
    try:
        conn.execute("UPDATE entries SET pdf_path = ? WHERE id = ?",
                     [dest.name, entry_id])
        conn.commit()
    finally:
        conn.close()


def _events(res) -> list[dict]:
    out = []
    for chunk in res.text.split("data: "):
        line = chunk.strip().split("\n")[0].strip()
        if not line or line == "[DONE]":
            continue
        out.append(json.loads(line))
    return out


def _cloud_json(verdict="match", text=LONG_TRANSCRIPT) -> str:
    return json.dumps({"verdict": verdict, "text": text, "notes": ""})


def _patch_ocr(monkeypatch, text=LONG_TRANSCRIPT):
    from app import document_parse

    monkeypatch.setattr(
        document_parse, "run_tesseract_ocr",
        lambda png, lang=None: {"text": text, "lang": lang or "eng", "elapsed_ms": 1},
    )


def _ok_vision(monkeypatch, reply=None):
    from app import document_parse

    monkeypatch.setattr(document_parse, "model_supports_images",
                        lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images",
                        lambda messages, images, **kwargs: reply or _cloud_json())
    _patch_ocr(monkeypatch)


# ── 1. Summary on a synthesized mixed PDF ────────────────────────────────────

def test_summary_mixed_counts_and_missing(tmp_path):
    with TestClient(app) as client:
        study = _study(client)
        pdf = tmp_path / "mixed.pdf"
        _make_pdf(pdf, [
            {"text": NATIVE_TEXT},            # native
            {"text": "x", "image": True},     # thin text + image -> scanned
            {},                               # no text, no image -> empty
        ])
        _attach_pdf(study["id"], pdf)

        res = client.get(f"/api/studies/{study['id']}/parse/summary")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["entry_id"] == study["id"]
        assert data["kind"] == "mixed"
        assert data["pages"] == 3
        assert data["pages_native"] == 1
        assert data["pages_scanned"] == 1
        assert data["pages_empty"] == 1
        # No parse ran yet: every page is missing a row.
        assert data["parsed"]["missing"] == 3
        assert len(data["per_page"]) == 3
        assert [p["kind"] for p in data["per_page"]] == [
            "native", "scanned", "empty"]
        assert all(p["source"] == "" and p["chars"] == 0
                   for p in data["per_page"])


# ── 2. Summary errors: no PDF → 404, corrupt PDF → 400 with reason ───────────

def test_summary_without_pdf_is_404():
    with TestClient(app) as client:
        study = _study(client, "No PDF study")
        res = client.get(f"/api/studies/{study['id']}/parse/summary")
        assert res.status_code == 404


def test_summary_corrupt_pdf_is_400_with_reason(tmp_path):
    with TestClient(app) as client:
        study = _study(client, "Broken")
        pdf = tmp_path / "broken.pdf"
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj")
        _attach_pdf(study["id"], pdf)

        res = client.get(f"/api/studies/{study['id']}/parse/summary")
        assert res.status_code == 400, res.text
        # detect_kind's reason, verbatim (pymupdf reports this file as
        # unreadable or as having no pages, depending on version).
        assert ("unreadable pdf" in res.text) or ("no pages" in res.text)


# ── 3. Stream on a 2-page scanned PDF: start → progress×2 → done ─────────────

def test_stream_two_scanned_pages_in_order(tmp_path, monkeypatch):
    _ok_vision(monkeypatch)
    with TestClient(app) as client:
        study = _study(client, "Two scans")
        pdf = tmp_path / "two.pdf"
        _make_pdf(pdf, [{"image": True}, {"image": True}])
        _attach_pdf(study["id"], pdf)

        res = client.get(f"/api/studies/{study['id']}/parse/stream")
        assert res.status_code == 200, res.text
        events = _events(res)
        kinds = [e.get("event") for e in events]
        assert kinds[0] == "start"
        assert kinds[-1] == "done"
        assert kinds.count("progress") == 2
        assert "error" not in kinds

        progresses = [e for e in events if e.get("event") == "progress"]
        assert [p["page"] for p in progresses] == [1, 2]
        for p in progresses:
            assert p["status"] == "vision-ok"
            assert p["chars"] > 0

        # The final done carries the same aggregate as the summary.
        done = events[-1]
        summary = client.get(
            f"/api/studies/{study['id']}/parse/summary").json()
        for key in ("entry_id", "kind", "pages", "pages_native",
                    "pages_scanned", "pages_empty", "parsed", "per_page"):
            assert done[key] == summary[key]


# ── 4. A failing page does not abort the stream ──────────────────────────────

def test_stream_failing_page_still_ends_with_done(tmp_path, monkeypatch):
    from app import document_parse

    calls = {"n": 0}

    def flaky(messages, images, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _cloud_json()
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(document_parse, "model_supports_images",
                        lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images", flaky)
    _patch_ocr(monkeypatch)

    with TestClient(app) as client:
        study = _study(client, "Flaky scans")
        pdf = tmp_path / "two.pdf"
        _make_pdf(pdf, [{"image": True}, {"image": True}])
        _attach_pdf(study["id"], pdf)

        res = client.get(f"/api/studies/{study['id']}/parse/stream")
        assert res.status_code == 200, res.text
        events = _events(res)
        kinds = [e.get("event") for e in events]
        assert kinds[0] == "start"
        assert kinds[-1] == "done"
        # A failed cloud verification is uncertainty, not a page error.
        assert "error" not in kinds
        progress = {e["page"]: e["status"] for e in events
                    if e.get("event") == "progress"}
        assert progress[1] == "vision-ok"
        assert progress[2] == "vision-uncertain"


# ── 5. A second concurrent stream gets 409 ───────────────────────────────────

def test_concurrent_second_stream_is_409(tmp_path, monkeypatch):
    from app import document_parse

    def slow(messages, images, **kwargs):
        time.sleep(1.0)
        return _cloud_json()

    monkeypatch.setattr(document_parse, "model_supports_images",
                        lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images", slow)
    _patch_ocr(monkeypatch)

    with TestClient(app) as client:
        study = _study(client, "Slow scans")
        pdf = tmp_path / "two.pdf"
        _make_pdf(pdf, [{"image": True}, {"image": True}])
        _attach_pdf(study["id"], pdf)
        url = f"/api/studies/{study['id']}/parse/stream"

        results: dict = {}

        def first():
            results["first"] = client.get(url)

        thread = threading.Thread(target=first, daemon=True)
        thread.start()
        time.sleep(0.5)
        # A second, lifespan-less client: tables already exist.
        second = TestClient(app).get(url)
        assert second.status_code == 409, second.text
        assert "deja în curs" in second.text
        thread.join(timeout=60)
        assert results["first"].status_code == 200


# ── 6. VisionUnsupportedError → error event naming model/provider ────────────

def test_stream_vision_unsupported_is_error_event(tmp_path, monkeypatch):
    from app import document_parse

    monkeypatch.setattr(document_parse, "model_supports_images",
                        lambda p, m: False)

    with TestClient(app) as client:
        study = _study(client, "Text-only model")
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])
        _attach_pdf(study["id"], pdf)

        res = client.get(
            f"/api/studies/{study['id']}/parse/stream"
            "?provider=custom&model=x-model")
        assert res.status_code == 200, res.text
        events = _events(res)
        errors = [e for e in events if e.get("event") == "error"]
        assert errors, events
        message = errors[0].get("message", "")
        assert "custom" in message
        assert "x-model" in message


# ── 7. Page text: parsed page 200, unparsed page 404 ─────────────────────────

def test_page_text_endpoint(tmp_path):
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client, "Text rows")
        eid = study["id"]
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO page_texts "
                "(entry_id, page_number, text, source, status, char_count) "
                "VALUES (?, 1, 'Textul paginii unu.', 'native', 'native', 18)",
                [eid],
            )
            conn.commit()
        finally:
            conn.close()

        res = client.get(f"/api/studies/{eid}/pages/1/text")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["page"] == 1
        assert data["text"] == "Textul paginii unu."
        assert data["source"] == "native"
        assert data["status"] == "native"

        missing = client.get(f"/api/studies/{eid}/pages/2/text")
        assert missing.status_code == 404


# ── 8. Page image: PNG bytes, 404 beyond the page count ──────────────────────

def test_page_image_endpoint(tmp_path):
    with TestClient(app) as client:
        study = _study(client, "Renders")
        pdf = tmp_path / "one.pdf"
        _make_pdf(pdf, [{"image": True}])
        _attach_pdf(study["id"], pdf)

        res = client.get(f"/api/studies/{study['id']}/pages/1/image")
        assert res.status_code == 200, res.text[:200]
        assert res.headers["content-type"] == "image/png"
        assert len(res.content) > 0

        beyond = client.get(f"/api/studies/{study['id']}/pages/99/image")
        assert beyond.status_code == 404
