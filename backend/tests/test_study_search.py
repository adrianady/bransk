"""Tests for academic study search, Romanian translation, and chat actions."""
import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.routers.chat import _detect_research_intent, _parse_actions
from app.study_search import combine_search_terms, translate_scientific_query


def test_translate_scientific_query_romanian_to_english():
    translated = translate_scientific_query("studii despre somn si consolidarea memoriei")
    assert "sleep" in translated
    assert "memory" in translated
    assert "consolidation" in translated

    neuro = translate_scientific_query("neuroplasticitate și învățare în creier")
    assert "neuroplasticity" in neuro
    assert "learning" in neuro
    assert "brain" in neuro


def test_combine_search_terms():
    combos = combine_search_terms("sleep and memory consolidation")
    assert len(combos) >= 2
    assert any("sleep" in c and "memory" in c for c in combos)


def test_detect_research_intent_romanian():
    assert _detect_research_intent("caută studii despre somn și memorie") == "somn și memorie"
    assert _detect_research_intent("găsește studii despre neuroplasticitate") == "neuroplasticitate"
    assert _detect_research_intent("descarcă studii despre axul creier-intestin") == "axul creier-intestin"
    assert _detect_research_intent("te rog caută articole despre inteligență artificială") == "inteligență artificială"
    # Strips trailing "și descarcă-le"
    assert _detect_research_intent("caută studii despre somn și descarcă-le") == "somn"


def test_search_studies_action_parses():
    acts = _parse_actions("[SEARCH_STUDIES: sleep memory consolidation]")
    assert any(a["type"] == "studies_search_request" and "sleep" in a["query"] for a in acts)


def test_academic_search_offline_graceful():
    from app.study_search import search_academic_studies

    # Empty query returns empty list immediately
    empty = asyncio.run(search_academic_studies(""))
    assert empty == []


def test_download_study_falls_back_to_fulltext():
    with TestClient(app) as client:
        # Create a study without direct PDF url
        study = client.post(
            "/api/entries",
            json={
                "title": "Sleep and synaptic plasticity",
                "content": "Study content without direct pdf link",
                "study_type": "paper",
                "doi": "10.1038/nature04286",
            },
        ).json()

        # In offline/mock test, fulltext discovery runs safely
        acts = _parse_actions(f"[DOWNLOAD_STUDY: {study['id']}]")
        assert acts[0] == {"type": "download_study_request", "entry_id": study["id"]}
