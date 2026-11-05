"""The AI must be able to build the graph, timeline and theory tree correctly.

These tests pin the three things that were missing before: a closed label
vocabulary in the prompt, ISO dates for anything the model writes, and a
theory status that the tree can actually render.
"""
from app.routers.chat import _build_system_prompt, _iso_entry_date
from app.routers.relationships import RELATION_KINDS


def test_prompt_lists_every_relationship_label():
    prompt = _build_system_prompt([], "mock", "mock")
    for code in RELATION_KINDS:
        assert f"`{code}`" in prompt, f"prompt never names the label {code}"


def test_prompt_marks_relates_to_as_last_resort_and_warns_about_duplicates():
    prompt = _build_system_prompt([], "mock", "mock")
    assert "relates_to" in prompt
    assert "LAST RESORT" in prompt
    assert "duplicate" in prompt.lower()
    assert "BEFORE creating an entry" in prompt


def test_prompt_states_iso_dates_and_theory_rules():
    prompt = _build_system_prompt([], "mock", "mock")
    assert "YYYY-MM-DD" in prompt
    assert "LINK_STUDY" in prompt
    # theory tree: parent required unless it is a root
    assert "parent" in prompt.lower()
    assert "root" in prompt.lower()


def test_iso_entry_date_accepts_and_normalises():
    assert _iso_entry_date("1973-04-01") == ("1973-04-01", None)
    assert _iso_entry_date("1973") == ("1973-01-01", None)
    assert _iso_entry_date("0350") == ("0350-01-01", None)
    assert _iso_entry_date("12.03.1999") == ("1999-03-12", None)


def test_iso_entry_date_blank_is_allowed():
    assert _iso_entry_date("") == (None, None)
    assert _iso_entry_date("-") == (None, None)


def test_iso_entry_date_refuses_free_text_with_a_retry_hint():
    value, error = _iso_entry_date("în jurul anului 300 î.Hr.")
    assert value is None
    assert error and "YYYY-MM-DD" in error and "CREATE_ENTRY" in error
