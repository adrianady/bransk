"""Tests for checked statements (verified notes, slice 7)."""
from fastapi.testclient import TestClient

from app.main import app


def _study(client, title="Verified notes study"):
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201
    return res.json()


def _note(client, entry_id, content):
    res = client.post(
        f"/api/studies/{entry_id}/notes",
        json={"content": content, "course_context": "Test"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _make_pdf(path, pages):
    """A PDF whose pages carry a body line and a printed page number footer."""
    import pymupdf

    doc = pymupdf.open()
    for body, printed in pages:
        page = doc.new_page()
        page.insert_text((72, 72), body)
        if printed:
            page.insert_text((300, 760), str(printed))
    doc.save(str(path))
    doc.close()


# ── Task 1: printed page detection ──


def test_detect_printed_pages_consecutive():
    from app.page_text import detect_printed_pages

    pages = [
        (1, "Chapter opening\n\n794"),
        (2, "Body text of page two.\n795"),
        (3, "Body text of page three.\n— 796 —"),
    ]
    assert detect_printed_pages(pages) == {1: "794", 2: "795", 3: "796"}


def test_detect_printed_pages_every_second_page():
    from app.page_text import detect_printed_pages

    pages = []
    printed = 100
    for pdf_page in range(1, 7):
        if pdf_page % 2 == 0:
            pages.append((pdf_page, f"Body of numeric page {printed}.\n{printed}"))
            printed += 1
        else:
            pages.append((pdf_page, f"Body without a number {pdf_page}."))
    result = detect_printed_pages(pages)
    assert result == {1: "", 2: "100", 3: "", 4: "101", 5: "", 6: "102"}


def test_detect_printed_pages_ignores_year_in_body_text():
    from app.page_text import detect_printed_pages

    pages = [
        (1, "Some text.\nThe study was conducted in 2019."),
        (2, "More text.\nIn 2020 the sample was extended."),
    ]
    assert detect_printed_pages(pages) == {1: "", 2: ""}


def test_detect_printed_pages_page_without_number():
    from app.page_text import detect_printed_pages

    pages = [(1, "Only body text, no page number anywhere.")]
    assert detect_printed_pages(pages) == {1: ""}


# ── Task 3: deterministic support search ──


def _insert_page(conn, entry_id, page_number, text, printed=""):
    conn.execute(
        """INSERT OR REPLACE INTO page_texts
           (entry_id, page_number, text, source, status, char_count, printed_page)
           VALUES (?, ?, ?, 'native', 'native', ?, ?)""",
        [entry_id, page_number, text, len(text), printed],
    )
    conn.commit()


SENTENCE = (
    "Proximal processes are the primary engines of development and they "
    "depend on the child."
)


def test_find_support_exact_sentence_returns_page_quote():
    from app.db import get_db
    from app.note_check import find_support

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn, study["id"], 10,
                f"Opening line.\n{SENTENCE} More text follows.",
                printed="795",
            )
            support = find_support(conn, study["id"], SENTENCE)
        finally:
            conn.close()
    assert support is not None
    assert support["page_pdf"] == 10
    assert support["page_printed"] == "795"
    assert support["quote"] == SENTENCE
    assert support["method"] == "exact"
    assert support["coverage"] == 1.0


def test_find_support_reworded_edges_matches_eight_token_run():
    from app.db import get_db
    from app.note_check import find_support

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 3, f"Intro.\n{SENTENCE}", printed="41")
            reworded = (
                "These proximal processes are the primary engines of development "
                "and they depend on the child's context."
            )
            support = find_support(conn, study["id"], reworded)
        finally:
            conn.close()
    assert support is not None
    assert support["page_pdf"] == 3
    assert support["quote"] == SENTENCE


def test_find_support_missing_statement_returns_none():
    from app.db import get_db
    from app.note_check import find_support

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, SENTENCE)
            support = find_support(
                conn,
                study["id"],
                "Quantum entanglement determines the outcome of psychometric inventories.",
            )
        finally:
            conn.close()
    assert support is None


def test_find_support_first_matching_page_and_coverage():
    from app.db import get_db
    from app.note_check import find_support

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 5, f"Before. {SENTENCE}", printed="201")
            _insert_page(conn, study["id"], 9, f"{SENTENCE} After.", printed="205")
            statement = SENTENCE + " Teachers were observed."
            support = find_support(conn, study["id"], statement)
        finally:
            conn.close()
    assert support is not None
    assert support["page_pdf"] == 5
    assert 0.0 < support["coverage"] < 1.0


# ── Task 3/4: note checking, persistence and human override ──


def test_check_note_without_apply_writes_nothing():
    from app.db import get_db
    from app.note_check import check_note

    with TestClient(app) as client:
        study = _study(client)
        note = _note(
            client,
            study["id"],
            SENTENCE + " A second statement that is absent from the source document.",
        )
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 2, f"{SENTENCE}", printed="12")
            result = check_note(conn, note["id"], apply=False)
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM note_checks WHERE note_id = ?",
                [note["id"]],
            ).fetchone()["n"]
        finally:
            conn.close()
    assert result["summary"]["total"] == 2
    assert result["summary"]["verificat"] == 1
    assert result["summary"]["neverificat"] == 1
    assert count == 0


def test_check_note_apply_replaces_instead_of_appending():
    from app.db import get_db
    from app.note_check import check_note

    with TestClient(app) as client:
        study = _study(client)
        note = _note(
            client,
            study["id"],
            SENTENCE + " Another claim that the source does not contain at all.",
        )
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 2, f"{SENTENCE}", printed="12")
            check_note(conn, note["id"], apply=True)
            first = conn.execute(
                "SELECT COUNT(*) AS n FROM note_checks WHERE note_id = ?",
                [note["id"]],
            ).fetchone()["n"]
            check_note(conn, note["id"], apply=True)
            second = conn.execute(
                "SELECT COUNT(*) AS n FROM note_checks WHERE note_id = ?",
                [note["id"]],
            ).fetchone()["n"]
            verified = conn.execute(
                "SELECT page_printed, quote, method FROM note_checks"
                " WHERE note_id = ? AND status = 'verificat'",
                [note["id"]],
            ).fetchone()
        finally:
            conn.close()
    assert first == 2
    assert second == 2
    assert verified["page_printed"] == "12"
    assert verified["quote"] == SENTENCE
    assert verified["method"] == "exact"


def test_patch_note_check_sets_manual_and_stores_correction():
    from app.db import get_db
    from app.note_check import check_note

    with TestClient(app) as client:
        study = _study(client)
        note = _note(client, study["id"], SENTENCE)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 2, f"{SENTENCE}", printed="12")
            check_note(conn, note["id"], apply=True)
            check_id = conn.execute(
                "SELECT id FROM note_checks WHERE note_id = ?", [note["id"]]
            ).fetchone()["id"]
        finally:
            conn.close()

        res = client.patch(
            f"/api/note-checks/{check_id}",
            json={
                "status": "corectat",
                "quote": "Proximal processes are the engines of development.",
                "correction": "Ce notasem: factori unici. Cum este de fapt: procese proximale.",
                "page_printed": "13",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["method"] == "manual"
        assert body["status"] == "corectat"
        assert body["quote"] == "Proximal processes are the engines of development."
        assert "procese proximale" in body["correction"]
        assert body["page_printed"] == "13"

        bad = client.patch(f"/api/note-checks/{check_id}", json={"status": "nonsense"})
        assert bad.status_code == 422
        assert "Status invalid" in bad.json()["detail"]


def test_verify_summary_counts_and_printed_pages(tmp_path):
    from app.db import get_db
    from app.note_check import check_note
    from app.page_text import ensure_page_texts

    body = "The sample included two hundred teachers from rural schools."
    with TestClient(app) as client:
        study = _study(client)
        note = _note(client, study["id"], body)
        pdf = tmp_path / "printed.pdf"
        _make_pdf(pdf, [(body, 794), (body, 795), (body, 796)])
        conn = get_db()
        try:
            written = ensure_page_texts(conn, study["id"], pdf)
            assert written == 3
            check_note(conn, note["id"], apply=True)
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM note_checks WHERE entry_id = ?",
                [study["id"]],
            ).fetchone()["n"]
        finally:
            conn.close()

        res = client.get(f"/api/studies/{study['id']}/verify/summary")
        assert res.status_code == 200, res.text
        summary = res.json()
        assert summary["pages_total"] == 3
        assert summary["pages_with_printed_numbers"] == 3
        assert summary["statements"] == rows
        assert summary["statements"] == 1
        assert summary["by_status"]["verificat"] == 1
        assert summary["notes_total"] == 1
        assert summary["notes_fully_verified"] == 1

        listed = client.get(f"/api/studies/{study['id']}/note-checks")
        assert listed.status_code == 200
        payload = listed.json()
        assert len(payload["statements"]) == 1
        assert payload["statements"][0]["note_content"] == body
        assert payload["statements"][0]["page_printed"] == "794"
        assert payload["by_status"]["verificat"][0]["statement"] == body
