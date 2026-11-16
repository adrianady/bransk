"""The AI item generator must survive messy cloud replies and never save zero items."""
from __future__ import annotations

import pytest

from app.routers.questionnaires import _parse_generated_questionnaire


def test_clean_json():
    data = _parse_generated_questionnaire(
        '{"description":"d","items":[{"text":"Îmi este ușor să vorbesc despre sentimente."}]}'
    )
    assert data["items"][0]["text"].startswith("Îmi este ușor")
    assert data["items"][0]["type"] == "likert"
    assert data["items"][0]["id"] == "g1"


def test_markdown_fence_with_prose():
    raw = 'Iată rezultatul:\n```JSON\n{"items":[{"text":"Item A"},{"text":"Item B"}]}\n```\nMulțumesc!'
    data = _parse_generated_questionnaire(raw)
    assert len(data["items"]) == 2


def test_alternative_items_key_and_normalisation():
    raw = '{"description":"d","questions":[{"question":"Item alternativ","reverse":true}]}'
    data = _parse_generated_questionnaire(raw)
    assert data["items"][0]["text"] == "Item alternativ"
    assert data["items"][0]["reverse"] is True


def test_empty_items_raise():
    with pytest.raises(ValueError, match="niciun item"):
        _parse_generated_questionnaire('{"description":"d","items":[]}')


def test_non_json_reply_raises():
    with pytest.raises(ValueError, match="JSON"):
        _parse_generated_questionnaire("Nu am putut genera itemii.")
