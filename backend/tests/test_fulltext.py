"""The full-text hunt: source order, parsing, and the disk layout.

No network here. Every HTTP call is served by `httpx.MockTransport`, so the
tests state the behaviour we promise — which source is tried when, what counts
as an article, and what is refused — without depending on a publisher.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app import fulltext, studytext
from app.fulltext import (
    Candidate,
    clean_title,
    find_fulltext,
    html_to_text,
    title_similarity,
)

PDF_BYTES = b"%PDF-1.4\n" + b"0" * 2000 + b"\n%%EOF"


@pytest.fixture()
def text_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(studytext, "TEXT_DIR", tmp_path / "studytext")
    return tmp_path / "studytext"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


# ── text helpers ───────────────────────────────────────────────────────────

def test_clean_title_strips_catalogue_decorations():
    assert clean_title("Friston - Free Energy Principle (2010)") == "Free Energy Principle"
    assert clean_title("Machine Theory of Mind (DeepMind)") == "Machine Theory of Mind"


def test_title_similarity_rejects_a_different_paper():
    same = title_similarity("The Default Mode Network", "The default mode network.")
    other = title_similarity("The Default Mode Network", "Sleep and memory consolidation")
    assert same > 0.9
    assert other < 0.5


def test_html_to_text_prefers_the_article_and_drops_scripts():
    page = (
        "<html><head><script>evil()</script></head><body>"
        "<nav>menu</nav><article><h1>Title</h1><p>First para.</p><p>Second &amp; last.</p></article>"
        "</body></html>"
    )
    text = html_to_text(page)
    assert "First para." in text and "Second & last." in text
    assert "evil" not in text and "menu" not in text


# ── discovery order ────────────────────────────────────────────────────────

def test_discovery_falls_through_to_unpaywall_and_pmc(monkeypatch):
    async def _run():
        monkeypatch.setattr(fulltext, "CONTACT_EMAIL", "owner@example.com")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "openalex" in url:
                # No open-access PDF from OpenAlex for this DOI.
                return httpx.Response(200, json={"open_access": {"is_oa": False}, "locations": []})
            if "unpaywall" in url:
                return httpx.Response(200, json={"oa_locations": [
                    {"url_for_pdf": "https://repository.example/paper.pdf", "host_type": "repository"},
                ]})
            if "semanticscholar" in url:
                return httpx.Response(200, json={"openAccessPdf": {"url": "https://s2.example/p.pdf"}})
            if "europepmc" in url:
                return httpx.Response(200, json={"resultList": {"result": [{"pmcid": "PMC123", "pmid": "9"}]}})
            if "arxiv" in url:
                return httpx.Response(200, text="<feed></feed>")
            return httpx.Response(404)

        entry = {"title": "Some Paper", "doi": "10.1000/xyz", "source": "", "content": ""}
        async with _client(handler) as client:
            cands = await find_fulltext(entry, client)
        sources = [c.source for c in cands]
        assert "unpaywall" in sources
        assert "europepmc" in sources
        # The PMC PDF is a challenge page, the HTML full text is the useful one.
        assert any(c.kind == "html" for c in cands if c.source == "europepmc")


    asyncio.run(_run())
def test_unpaywall_is_skipped_without_a_contact_email(monkeypatch):
    async def _run():
        monkeypatch.setattr(fulltext, "CONTACT_EMAIL", "")
        called: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            called.append(str(request.url))
            return httpx.Response(200, json={"open_access": {}, "locations": []})

        entry = {"title": "Some Paper", "doi": "10.1000/xyz", "source": "", "content": ""}
        async with _client(handler) as client:
            await find_fulltext(entry, client)
        assert not any("unpaywall" in url for url in called)


    asyncio.run(_run())
def test_arxiv_is_only_taken_on_a_close_title_match(monkeypatch):
    async def _run():
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "openalex" in url:
                return httpx.Response(200, json={"results": []})
            if "arxiv" in url:
                return httpx.Response(200, text=(
                    "<feed><entry><title>Unrelated Paper About Mice</title>"
                    "<id>http://arxiv.org/abs/1234.5678</id></entry></feed>"
                ))
            return httpx.Response(404)

        entry = {"title": "Consciousness and the Brain", "doi": "", "source": "", "content": ""}
        async with _client(handler) as client:
            cands = await find_fulltext(entry, client)
        assert not [c for c in cands if c.source == "arxiv"]


    asyncio.run(_run())
# ── refusal that must stay a refusal ──────────────────────────────────────

def test_fulltext_never_returns_a_scihub_candidate():
    async def _run():
        entry = {"title": "Anything", "doi": "10.1000/xyz", "source": "https://sci-hub.ru/10.1000/xyz", "content": ""}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"open_access": {}, "locations": [], "results": []})

        async with _client(handler) as client:
            cands = await find_fulltext(entry, client)
        assert all("sci-hub" not in c.url for c in cands)
        assert fulltext.HONEST_SOURCES_ONLY is True


    asyncio.run(_run())
def test_ssrf_guard_blocks_a_private_html_source(monkeypatch):
    async def _run():
        async def fake_find(entry, client=None):
            return [Candidate(url="http://127.0.0.1:8000/secret", source="test", kind="html")]

        monkeypatch.setattr(fulltext, "find_fulltext", fake_find)
        monkeypatch.setattr(fulltext, "_entry_row", lambda entry_id: {
            "id": entry_id, "title": "T", "doi": "", "source": "", "content": "",
        })
        result = await fulltext.fetch_fulltext(1)
        assert result["status"] == "none"
        assert "blocked" in json.dumps(result["tried"]).lower()


    asyncio.run(_run())


def test_pdf_fulltext_reports_indexing_failure_without_discarding_the_pdf(monkeypatch, tmp_path):
    async def _run():
        async def fake_find(entry, client=None):
            return [Candidate(url="https://example.org/paper.pdf", source="test")]

        monkeypatch.setattr(fulltext, "find_fulltext", fake_find)
        monkeypatch.setattr(fulltext, "_entry_row", lambda entry_id: {
            "id": entry_id, "title": "Paper", "doi": "", "source": "", "content": "",
        })
        monkeypatch.setattr(fulltext, "download_study_pdf", AsyncMock(return_value="9-paper.pdf"))
        monkeypatch.setattr(fulltext, "pdf_path_for", lambda _entry_id: tmp_path / "9-paper.pdf")
        monkeypatch.setattr(fulltext, "is_valid_pdf_file", lambda _path: True)
        monkeypatch.setattr(
            "app.pdf_rag.index_pdf",
            AsyncMock(side_effect=RuntimeError("index unavailable")),
        )

        result = await fulltext.fetch_fulltext(9)
        assert result["status"] == "pdf"
        assert result["index_error"] is True

    asyncio.run(_run())


# ── storing what was found ─────────────────────────────────────────────────

def test_study_text_lands_on_disk_and_keeps_the_longer_version(text_dir):
    short = studytext.save_text(7, "A Study Title", "x" * 100)
    longer = studytext.save_text(7, "A Study Title", "y" * 5000)
    assert short == longer
    assert studytext.has_text(7)
    assert len(studytext.read_text(7)) == 5000
    assert studytext.text_path_for(7).parent == text_dir


def test_html_fulltext_is_saved_to_disk_not_the_database(monkeypatch, text_dir):
    async def _run():
        article = "<html><body><article><p>" + ("word " * 600) + "</p></article></body></html>"

        async def fake_find(entry, client=None):
            return [Candidate(url="https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/", source="europepmc", kind="html")]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=article, headers={"content-type": "text/html; charset=utf-8"})

        updates: list[tuple] = []

        class FakeConn:
            def execute(self, sql, params=None):
                updates.append((sql, params))
                return self

            def commit(self):
                return None

            def close(self):
                return None

        monkeypatch.setattr(fulltext, "find_fulltext", fake_find)
        monkeypatch.setattr(fulltext, "get_db", lambda: FakeConn())
        monkeypatch.setattr(fulltext, "_entry_row", lambda entry_id: {
            "id": entry_id, "title": "Memory Paper", "doi": "", "source": "", "content": "",
        })

        original_client = httpx.AsyncClient

        def patched_client(*args, **kwargs):
            return original_client(transport=httpx.MockTransport(handler), timeout=5.0)

        monkeypatch.setattr(fulltext.httpx, "AsyncClient", patched_client)
        result = await fulltext.fetch_fulltext(5, index=False)

        assert result["status"] == "html"
        assert studytext.has_text(5)
        # The database only receives a bounded excerpt, never the whole paper.
        excerpt = [params for sql, params in updates if "entry_id" not in str(params)]
        assert excerpt, "an excerpt update was expected"
        assert all(len(str(p[0])) <= 8000 for p in excerpt if isinstance(p, (list, tuple)))


    asyncio.run(_run())
def test_no_open_source_leaves_the_study_untouched(monkeypatch, text_dir):
    async def _run():
        async def fake_find(entry, client=None):
            return []

        def failing(entry_id):
            raise AssertionError("the database must not be touched when nothing was found")

        monkeypatch.setattr(fulltext, "find_fulltext", fake_find)
        monkeypatch.setattr(fulltext, "get_db", failing)
        monkeypatch.setattr(fulltext, "_entry_row", lambda entry_id: {
            "id": entry_id, "title": "T", "doi": "", "source": "", "content": "",
        })

        result = await fulltext.fetch_fulltext(9, index=False)
        assert result["status"] == "none"
        assert not studytext.has_text(9)

    asyncio.run(_run())
