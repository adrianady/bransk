"""The tag vocabulary must stay a list of subjects.

Two defects met here: the research pipeline split a topic into its first four
words (so "the structure of mind" produced the tags "the", "of"), and rows
carried a bare date among their tags. Both made the tag filter useless.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routers.research import _topic_tags  # noqa: E402
from scripts.clean_tags import clean_tags, is_noise  # noqa: E402


def test_topic_tags_drop_stop_words():
    assert _topic_tags("the structure of mind") == "structure,mind"
    assert _topic_tags("modularity of mind") == "modularity,mind"
    assert _topic_tags("adverse childhood experiences and resilience") == \
        "adverse,childhood,experiences,resilience"


def test_topic_tags_keep_meaningful_short_words():
    # "AI" is a subject; a length filter would have thrown it away.
    assert _topic_tags("AI") == "ai"


def test_topic_tags_cap_the_number_of_words():
    tags = _topic_tags("one two three four five six seven")
    assert len(tags.split(",")) == 4


def test_clean_tags_removes_dates_and_stop_words():
    assert clean_tags("1973-01-01,study,the,neurobiology,pdf") == "study,neurobiology,pdf"
    assert clean_tags("-0350-01-01, philosophy, of, mind") == "philosophy,mind"


def test_clean_tags_keeps_rare_real_tags():
    # An author's name is a subject someone chose on purpose.
    assert clean_tags("Carey, Fodor, modularity") == "Carey,Fodor,modularity"


def test_clean_tags_drops_duplicates_but_keeps_the_first_spelling():
    assert clean_tags("Mind, mind, MIND, memory") == "Mind,memory"


def test_clean_tags_of_empty_is_empty():
    assert clean_tags("") == ""
    assert clean_tags(None) == ""
    assert clean_tags(" , , ") == ""


def test_is_noise_recognises_dates():
    assert is_noise("1973-01-01")
    assert is_noise("-0350-01-01")
    assert not is_noise("memory")
    assert not is_noise("2023")  # a bare year is not a full date
