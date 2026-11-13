"""Per-page document parsing: classification, vision gates, failure isolation.

Hermetic: PDFs are synthesised in the test tmp dir, the vision call is
monkeypatched in ``app.document_parse`` and no provider is contacted. The
shared conftest already points ``CHRONOKG_STUDIES_DIR`` at a per-process folder,
so page images never touch the real library.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

NATIVE_TEXT = (
    "Această pagină are un strat de text nativ suficient de lung pentru a fi "
    "considerat utilizabil la ancorarea citatelor din teză."
)
LONG_TRANSCRIPT = "Some transcribed text. " * 20  # 460 chars, clean


def _cloud_reply(verdict="match", text=LONG_TRANSCRIPT, notes="") -> str:
    return json.dumps({"verdict": verdict, "text": text, "notes": notes})


def _patch_ocr(monkeypatch, text=LONG_TRANSCRIPT):
    """Replace the CPU Tesseract subprocess with a deterministic reading."""
    from app import document_parse

    def fake_ocr(png, lang=None):
        return {"text": (text or "").strip(), "lang": lang or "eng", "elapsed_ms": 1}

    monkeypatch.setattr(document_parse, "run_tesseract_ocr", fake_ocr)


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


def _study(client, title="Parse study") -> dict:
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201, res.text
    return res.json()


def _count_rows(entry_id: int) -> int:
    from app.db import get_db

    conn = get_db()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM page_texts WHERE entry_id = ?", [entry_id]
        ).fetchone()["n"]
    finally:
        conn.close()


def _row(entry_id: int, page_number: int):
    from app.db import get_db

    conn = get_db()
    try:
        return conn.execute(
            "SELECT text, source, status FROM page_texts"
            " WHERE entry_id = ? AND page_number = ?",
            [entry_id, page_number],
        ).fetchone()
    finally:
        conn.close()


# ── 1. Classification ────────────────────────────────────────────────────────


def test_detect_kind_classifies_native_scanned_empty(tmp_path):
    from app.document_parse import detect_kind

    pdf = tmp_path / "mixed.pdf"
    _make_pdf(pdf, [
        {"text": NATIVE_TEXT},             # native
        {"text": "x", "image": True},      # thin text + image -> scanned
        {},                                 # no text, no image -> empty
    ])
    info = detect_kind(pdf)
    assert info["pages"] == 3
    assert info["pages_native"] == 1
    assert info["pages_scanned"] == 1
    assert info["pages_empty"] == 1
    assert info["kind"] == "mixed"
    assert info["page_kinds"] == ["native", "scanned", "empty"]

    blank = tmp_path / "blank.pdf"
    _make_pdf(blank, [{}])
    info = detect_kind(blank)
    assert info["pages"] == 1
    assert info["kind"] == "empty"
    assert info["pages_empty"] == 1

    scanned_only = tmp_path / "scanned.pdf"
    _make_pdf(scanned_only, [{"image": True}, {"image": True}])
    assert detect_kind(scanned_only)["kind"] == "scanned"


# ── 2. Empty document: no model, no rows (entry 278's shape) ─────────────────


def test_empty_pdf_calls_no_vision_and_writes_nothing(tmp_path, monkeypatch):
    from app import document_parse

    def boom(*args, **kwargs):
        raise AssertionError("vision must not be called for an empty document")

    monkeypatch.setattr(document_parse, "complete_with_images", boom)

    with TestClient(app) as client:
        study = _study(client)
        pdf = tmp_path / "empty.pdf"
        _make_pdf(pdf, [{}])  # one blank page: no text, no images

        from app.db import get_db

        conn = get_db()
        try:
            result = document_parse.parse_document(conn, study["id"], pdf)
        finally:
            conn.close()

    assert result["status"] == "empty"
    assert result["kind"] == "empty"
    assert result["pages"] == []
    assert _count_rows(study["id"]) == 0


def test_broken_pdf_is_empty_not_vision(tmp_path, monkeypatch):
    """The real 278 file is malformed; it must never reach a model either."""
    from app import document_parse

    def boom(*args, **kwargs):
        raise AssertionError("broken PDF must not be sent to vision")

    monkeypatch.setattr(document_parse, "complete_with_images", boom)

    with TestClient(app) as client:
        study = _study(client, "Broken")
        pdf = tmp_path / "broken.pdf"
        pdf.write_bytes(b"%PDF-1.4\\n1 0 obj\\n<< /Type /Catalog >>\\nendobj")

        from app.db import get_db

        conn = get_db()
        try:
            result = document_parse.parse_document(conn, study["id"], pdf)
        finally:
            conn.close()

    assert result["status"] == "empty"
    assert _count_rows(study["id"]) == 0


# ── 3–5. Vision statuses ────────────────────────────────────────────────────


def _run_scanned(tmp_path, monkeypatch, reply, ocr_text=LONG_TRANSCRIPT) -> tuple[dict, dict]:
    from app import document_parse

    calls: list = []

    def fake_vision(messages, images, **kwargs):
        calls.append(kwargs)
        return reply(len(calls)) if callable(reply) else reply

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images", fake_vision)
    _patch_ocr(monkeypatch, ocr_text)

    with TestClient(app) as client:
        study = _study(client, "Scanned")
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])

        from app.db import get_db

        conn = get_db()
        try:
            result = document_parse.parse_document(
                conn, study["id"], pdf, provider="custom", model="vision-model",
                dpi=72,
            )
        finally:
            conn.close()
    return study, result


def test_scanned_page_clean_transcription_is_vision_ok(tmp_path, monkeypatch):
    study, result = _run_scanned(tmp_path, monkeypatch, _cloud_reply())
    assert result["status"] == "ok"
    assert result["vision"] == 1
    page = result["pages"][0]
    assert page["status"] == "vision-ok"
    assert page["chars"] == len(LONG_TRANSCRIPT.strip())
    # Cost evidence is reported per page, never stored in the DB.
    assert page["png_bytes"] > 0
    assert page["dpi"] == 72
    assert page["elapsed_s"] >= 0.0

    row = _row(study["id"], 1)
    assert row is not None
    assert row["source"] == "vision"
    assert row["status"] == "vision-ok"
    assert "Some transcribed text." in row["text"]


def test_uncertain_marker_is_recorded_as_uncertain(tmp_path, monkeypatch):
    reply = ("Pagina scanată conține un paragraf lung și lizibil, dar o zonă "
             "de la marginea de jos este neclară.\n[UNCERTAIN]\nSfârșitul paginii.")
    study, result = _run_scanned(
        tmp_path, monkeypatch, _cloud_reply(text=reply), ocr_text=reply,
    )

    page = result["pages"][0]
    assert page["status"] == "vision-uncertain"
    assert "uncertain_marker" in page["reason"]
    row = _row(study["id"], 1)
    assert row["status"] == "vision-uncertain"


def test_refusal_is_uncertain_and_not_stored_as_text(tmp_path, monkeypatch):
    study, result = _run_scanned(
        tmp_path, monkeypatch, "I'm sorry, I cannot read this image."
    )

    page = result["pages"][0]
    assert page["status"] == "vision-uncertain"
    assert "cloud_invalid_reply" in page["reason"]
    row = _row(study["id"], 1)
    assert row["status"] == "vision-uncertain"
    # Model meta must never be stored as if it were page content.
    assert "sorry" not in (row["text"] or "").lower()
    # The local OCR reading is preserved even when the cloud reply is unusable.
    assert row["text"] == LONG_TRANSCRIPT.strip()


# ── 6. Per-page failure isolation ───────────────────────────────────────────


def test_one_failing_page_does_not_abort_the_run(tmp_path, monkeypatch):
    from app import document_parse

    calls = {"n": 0}

    def flaky(messages, images, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _cloud_reply()
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images", flaky)
    _patch_ocr(monkeypatch)

    with TestClient(app) as client:
        study = _study(client, "Two pages")
        pdf = tmp_path / "two.pdf"
        _make_pdf(pdf, [{"image": True}, {"image": True}])

        from app.db import get_db

        conn = get_db()
        try:
            result = document_parse.parse_document(
                conn, study["id"], pdf, provider="custom", model="vision-model",
                dpi=72,
            )
        finally:
            conn.close()

    assert result["status"] == "ok"
    statuses = {p["page"]: p["status"] for p in result["pages"]}
    assert statuses[1] == "vision-ok"
    # A failed cloud verification is uncertainty, never an "ok" page.
    assert statuses[2] == "vision-uncertain"
    assert "cloud_unavailable" in result["pages"][1]["reason"]
    assert "provider exploded" in result["pages"][1]["reason"]
    assert result["vision"] == 1


# ── 7. Capability gate ──────────────────────────────────────────────────────


def test_text_only_model_is_rejected_before_any_call(tmp_path, monkeypatch):
    from app import document_parse
    from app.llm import VisionUnsupportedError

    def boom(*args, **kwargs):
        raise AssertionError("no vision call may happen for a text-only model")

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: False)
    monkeypatch.setattr(document_parse, "complete_with_images", boom)

    with TestClient(app) as client:
        study = _study(client, "Text only")
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])

        from app.db import get_db

        conn = get_db()
        try:
            with pytest.raises(VisionUnsupportedError) as excinfo:
                document_parse.parse_document(
                    conn, study["id"], pdf,
                    provider="custom", model="deepseek-v4.1-flash-1017",
                )
        finally:
            conn.close()

    message = str(excinfo.value)
    assert "custom" in message
    assert "deepseek-v4.1-flash-1017" in message
    assert "nu acceptă imagini" in message


def test_unknown_capability_proceeds(tmp_path, monkeypatch):
    """None means "provider did not publish modalities" — attempt the call."""
    from app import document_parse

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(
        document_parse, "complete_with_images",
        lambda messages, images, **kwargs: _cloud_reply(),
    )
    _patch_ocr(monkeypatch)

    with TestClient(app) as client:
        study = _study(client, "Unknown capability")
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])

        from app.db import get_db

        conn = get_db()
        try:
            result = document_parse.parse_document(
                conn, study["id"], pdf, provider="opencode_console",
                model="big-pickle", dpi=72,
            )
        finally:
            conn.close()

    assert result["pages"][0]["status"] == "vision-ok"


# ── Sparse front matter: informational, not uncertain ───────────────────────

COVER_TEXT = (
    "On Being Normal\nand Other Disorders\nA Manual for Clinical Psychodiagnostics"
    "\n\nPAUL VERHAEGHE\n\nROUTLEDGE"
)
PUBLISHER_TEXT = (
    "Taylor & Francis\nTaylor & Francis Group\nhttp://taylorandfrancis.com"
)


def test_cover_page_is_sparse_but_vision_ok(tmp_path, monkeypatch):
    """102-char cover: correct output, `sparse_page` note, not uncertainty."""
    assert len(COVER_TEXT) == 102
    study, result = _run_scanned(
        tmp_path, monkeypatch, _cloud_reply(text=COVER_TEXT), ocr_text=COVER_TEXT,
    )

    page = result["pages"][0]
    assert page["status"] == "vision-ok"
    assert "sparse_page" in page["reason"]
    row = _row(study["id"], 1)
    assert row["source"] == "vision"
    assert row["status"] == "vision-ok"
    assert row["text"] == COVER_TEXT


def test_publisher_page_is_sparse_but_vision_ok(tmp_path, monkeypatch):
    """67-char publisher page: same rule, the page really is that short."""
    assert len(PUBLISHER_TEXT) == 67
    study, result = _run_scanned(
        tmp_path, monkeypatch, _cloud_reply(text=PUBLISHER_TEXT),
        ocr_text=PUBLISHER_TEXT,
    )

    page = result["pages"][0]
    assert page["status"] == "vision-ok"
    assert "sparse_page" in page["reason"]
    assert _row(study["id"], 1)["status"] == "vision-ok"


def test_gibberish_short_output_stays_uncertain(tmp_path, monkeypatch):
    """Short and not well-formed: still a failed page, not a sparse one."""
    from app.document_parse import parse_scanned_page

    gibberish = "12 34 /// ###"
    monkeypatch.setattr(
        "app.document_parse.complete_with_images",
        lambda messages, images, **kwargs: _cloud_reply(text=gibberish),
    )
    _patch_ocr(monkeypatch, gibberish)

    parsed = parse_scanned_page(
        b"\x89PNG fake", provider="custom", model="m",
    )
    assert parsed["status"] == "vision-uncertain"
    assert "too_short" in parsed["reasons"]
    assert parsed["ocr_text"] == gibberish


def test_force_rewrites_uncertain_vision_row(tmp_path, monkeypatch):
    """--force must heal a stored vision-uncertain row, not only native ones."""
    from app import document_parse
    from app.db import get_db

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(
        "app.document_parse.complete_with_images",
        lambda messages, images, **kwargs: _cloud_reply(),
    )
    _patch_ocr(monkeypatch)

    with TestClient(app) as client:
        study = _study(client, "Force vision")
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])

        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO page_texts "
                "(entry_id, page_number, text, source, status, char_count) "
                "VALUES (?, 1, 'old uncertain text', 'vision', 'vision-uncertain', 19)",
                [study["id"]],
            )
            conn.commit()
            result = document_parse.parse_document(
                conn, study["id"], pdf, force=True, dpi=72,
                provider="custom", model="vision-model",
            )
            row = conn.execute(
                "SELECT text, source, status FROM page_texts "
                "WHERE entry_id = ? AND page_number = 1",
                [study["id"]],
            ).fetchone()
        finally:
            conn.close()

    assert result["rewritten"] == 1
    assert row["source"] == "vision"
    assert row["status"] == "vision-ok"
    assert row["text"] == LONG_TRANSCRIPT.strip()


def test_force_never_overwrites_manual_row(tmp_path, monkeypatch):
    """A human edit outranks the model even with --force."""
    from app import document_parse
    from app.db import get_db

    def boom(*args, **kwargs):
        raise AssertionError("manual pages must not reach OCR or the cloud")

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images", boom)
    monkeypatch.setattr(document_parse, "run_tesseract_ocr", boom)

    with TestClient(app) as client:
        study = _study(client, "Force manual")
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])

        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO page_texts "
                "(entry_id, page_number, text, source, status, char_count) "
                "VALUES (?, 1, 'EDITAT MANUAL', 'manual', 'manual', 13)",
                [study["id"]],
            )
            conn.commit()
            result = document_parse.parse_document(
                conn, study["id"], pdf, force=True, dpi=72,
                provider="custom", model="vision-model",
            )
            row = conn.execute(
                "SELECT text, source, status FROM page_texts "
                "WHERE entry_id = ? AND page_number = 1",
                [study["id"]],
            ).fetchone()
        finally:
            conn.close()

    assert result["rewritten"] == 0
    assert result["pages"][0]["status"] == "manual"
    assert row["text"] == "EDITAT MANUAL"
    assert row["source"] == "manual"


# ── Sparse-page calibration (real false alarms from glm-5.3-flash) ───────────
# (the calibration cases live above; the OCR/verification contract follows)


# ── OCR-first + mandatory cloud verification contract ────────────────────────


def _run_document(tmp_path, client, pdf, **kwargs):
    from app import document_parse
    from app.db import get_db

    study = _study(client, kwargs.pop("title", "Contract"))
    conn = get_db()
    try:
        result = document_parse.parse_document(conn, study["id"], pdf, **kwargs)
    finally:
        conn.close()
    return study, result


def test_native_page_never_calls_ocr_or_cloud(tmp_path, monkeypatch):
    from app import document_parse

    def boom(*args, **kwargs):
        raise AssertionError("native pages must not reach OCR or the cloud")

    monkeypatch.setattr(document_parse, "run_tesseract_ocr", boom)
    monkeypatch.setattr(document_parse, "complete_with_images", boom)

    with TestClient(app) as client:
        pdf = tmp_path / "native.pdf"
        _make_pdf(pdf, [{"text": NATIVE_TEXT}])
        study, result = _run_document(
            tmp_path, client, pdf, provider="custom", model="vision-model",
        )

    assert result["pages"][0]["status"] == "native"
    assert result["native"] == 1
    row = _row(study["id"], 1)
    assert row["source"] == "native"
    assert "strat de text nativ" in row["text"]


def test_scanned_page_calls_local_ocr_then_cloud(tmp_path, monkeypatch):
    from app import document_parse

    events: list[str] = []

    def fake_ocr(png, lang=None):
        events.append("ocr")
        return {"text": LONG_TRANSCRIPT, "lang": lang or "eng", "elapsed_ms": 3}

    calls: list[dict] = []

    def fake_vision(messages, images, **kwargs):
        events.append("cloud")
        calls.append({"messages": messages, "kwargs": kwargs})
        return _cloud_reply()

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(document_parse, "run_tesseract_ocr", fake_ocr)
    monkeypatch.setattr(document_parse, "complete_with_images", fake_vision)

    with TestClient(app) as client:
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])
        study, result = _run_document(
            tmp_path, client, pdf, provider="chosen-provider",
            model="chosen-model", dpi=72,
        )

    assert events == ["ocr", "cloud"]
    assert result["pages"][0]["status"] == "vision-ok"
    assert result["pages"][0]["verdict"] == "match"
    assert calls[0]["kwargs"]["provider"] == "chosen-provider"
    assert calls[0]["kwargs"]["model"] == "chosen-model"
    assert calls[0]["kwargs"]["json_mode"] is True
    prompt = calls[0]["messages"][0]["content"]
    assert LONG_TRANSCRIPT.strip() in prompt


def test_missing_tesseract_is_a_clear_error(tmp_path, monkeypatch):
    from app import document_parse

    monkeypatch.setattr(document_parse.shutil, "which", lambda name: None)

    def boom(*args, **kwargs):
        raise AssertionError("the cloud must not be called without local OCR")

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images", boom)

    with pytest.raises(document_parse.OcrUnavailableError) as excinfo:
        document_parse.run_tesseract_ocr(b"\x89PNG fake")

    message = str(excinfo.value)
    assert "tesseract" in message.lower()
    assert "nu a fost găsit" in message

    with TestClient(app) as client:
        study = _study(client, "No OCR")
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])
        from app.db import get_db

        conn = get_db()
        try:
            with pytest.raises(document_parse.OcrUnavailableError):
                document_parse.parse_document(
                    conn, study["id"], pdf, provider="custom",
                    model="vision-model", dpi=72,
                )
            assert _count_rows(study["id"]) == 0
        finally:
            conn.close()


def test_missing_language_pack_is_a_clear_error(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app import document_parse

    monkeypatch.setenv("CHRONOKG_OCR_LANG", "ron")
    monkeypatch.setattr(
        document_parse.shutil, "which", lambda name: "/usr/bin/tesseract"
    )
    monkeypatch.setattr(
        document_parse.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="List of available languages (1):\neng\n", stderr="", returncode=0,
        ),
    )

    with pytest.raises(document_parse.OcrUnavailableError) as excinfo:
        document_parse.run_tesseract_ocr(b"\x89PNG fake")

    assert "ron" in str(excinfo.value)
    assert "eng" in str(excinfo.value)


def test_no_explicit_cloud_keeps_page_uncertain(tmp_path, monkeypatch):
    from app import document_parse

    def boom(*args, **kwargs):
        raise AssertionError("no implicit cloud provider may be used")

    monkeypatch.setattr(document_parse, "complete_with_images", boom)
    _patch_ocr(monkeypatch)

    with TestClient(app) as client:
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])
        study, result = _run_document(tmp_path, client, pdf, dpi=72)

    page = result["pages"][0]
    assert page["status"] == "vision-uncertain"
    assert "cloud_not_selected" in page["reason"]
    row = _row(study["id"], 1)
    assert row["status"] == "vision-uncertain"
    # The local reading is kept, but it is not presented as verified.
    assert row["text"] == LONG_TRANSCRIPT.strip()


def test_cloud_failure_is_uncertain_and_preserves_ocr(tmp_path, monkeypatch):
    from app import document_parse

    def failing(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images", failing)
    _patch_ocr(monkeypatch)

    with TestClient(app) as client:
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])
        study, result = _run_document(
            tmp_path, client, pdf, provider="custom", model="vision-model",
            dpi=72,
        )

    page = result["pages"][0]
    assert page["status"] == "vision-uncertain"
    assert "cloud_unavailable" in page["reason"]
    row = _row(study["id"], 1)
    assert row["status"] == "vision-uncertain"
    assert row["text"] == LONG_TRANSCRIPT.strip()

    artifact = document_parse.page_verification_path(study["id"], 1)
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["ocr_text"] == LONG_TRANSCRIPT.strip()
    assert data["vision_text"] == ""
    assert data["status"] == "vision-uncertain"


def test_disagreement_preserves_both_readings(tmp_path, monkeypatch):
    from app import document_parse

    ocr_text = "Local reading says the sample was 40 adults in total. " * 4
    cloud_text = "Local reading says the sample was 140 adults in total. " * 4
    assert len(ocr_text) > 120 and len(cloud_text) > 120

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(
        document_parse, "complete_with_images",
        lambda messages, images, **kwargs: _cloud_reply(
            verdict="mismatch", text=cloud_text, notes="digit differs",
        ),
    )
    _patch_ocr(monkeypatch, ocr_text)

    with TestClient(app) as client:
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])
        study, result = _run_document(
            tmp_path, client, pdf, provider="custom", model="vision-model",
            dpi=72,
        )

    page = result["pages"][0]
    assert page["status"] == "vision-uncertain"
    assert "ocr_cloud_disagreement" in page["reason"]
    row = _row(study["id"], 1)
    assert row["status"] == "vision-uncertain"
    assert row["text"] == ocr_text.strip()

    data = json.loads(
        document_parse.page_verification_path(study["id"], 1)
        .read_text(encoding="utf-8")
    )
    assert data["ocr_text"] == ocr_text.strip()
    assert data["vision_text"] == cloud_text
    assert data["verdict"] == "mismatch"


def test_verbatim_match_needs_cloud_confirmation(tmp_path, monkeypatch):
    """Identical text is agreement evidence, not visual proof."""
    from app import document_parse

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(
        document_parse, "complete_with_images",
        lambda messages, images, **kwargs: _cloud_reply(
            verdict="uncertain", text=LONG_TRANSCRIPT,
        ),
    )
    _patch_ocr(monkeypatch)

    with TestClient(app) as client:
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])
        study, result = _run_document(
            tmp_path, client, pdf, provider="custom", model="vision-model",
            dpi=72,
        )

    page = result["pages"][0]
    assert page["status"] == "vision-uncertain"
    assert "cloud_uncertain" in page["reason"]
    assert _row(study["id"], 1)["status"] == "vision-uncertain"


def test_anchored_page_is_never_overwritten(tmp_path, monkeypatch):
    from app import document_parse
    from app.db import get_db

    def boom(*args, **kwargs):
        raise AssertionError("anchored pages must not be reparsed")

    monkeypatch.setattr(document_parse, "model_supports_images", lambda p, m: None)
    monkeypatch.setattr(document_parse, "complete_with_images", boom)
    monkeypatch.setattr(document_parse, "run_tesseract_ocr", boom)

    with TestClient(app) as client:
        study = _study(client, "Anchored")
        pdf = tmp_path / "scan.pdf"
        _make_pdf(pdf, [{"image": True}])

        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO page_texts "
                "(entry_id, page_number, text, source, status, char_count) "
                "VALUES (?, 1, 'TEXT ANCORAT', 'vision', 'vision-ok', 12)",
                [study["id"]],
            )
            conn.execute(
                "INSERT INTO pdf_highlights "
                "(entry_id, page_number, quote, start_offset, end_offset) "
                "VALUES (?, 1, 'ANCORAT', 5, 12)",
                [study["id"]],
            )
            conn.commit()
            result = document_parse.parse_document(
                conn, study["id"], pdf, force=True, dpi=72,
                provider="custom", model="vision-model",
            )
            row = conn.execute(
                "SELECT text, source, status FROM page_texts "
                "WHERE entry_id = ? AND page_number = 1",
                [study["id"]],
            ).fetchone()
        finally:
            conn.close()

    assert result["rewritten"] == 0
    assert result["pages"][0]["status"] == "vision-ok"
    assert "anchored" in result["pages"][0]["reason"]
    assert row["text"] == "TEXT ANCORAT"
    assert row["status"] == "vision-ok"

