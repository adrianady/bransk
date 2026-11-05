"""
Study identity — deciding whether two study entries describe the same paper.

The research pipeline inserts one entry per study it extracts. The model is not
consistent between runs: the same article comes back titled in full once and cut
short with an ellipsis the next time ("... and the brain: A systematic review
of ..."). Comparing normalized titles for equality let both reach the database,
which is how the real library ended up with duplicate pairs (ids 92/95, 93/96,
94/97) pointing at the same article.

Two rows are the same study when any of these holds:
  * their normalized titles are equal;
  * one title is a truncation of the other (ends in "..." or "…");
  * their titles are near-identical (a small edit distance);
  * they share a source URL *and* their titles are recognisably related.

A shared URL alone is NOT enough: one PDF link in the real library is attached
to six different papers, so URL equality by itself would merge unrelated work.
"""
import re
import unicodedata
from difflib import SequenceMatcher

# A title ending in an ellipsis was cut by the model, not finished by the author.
_TRUNCATION_MARKERS = ("...", "…")
_NEAR_IDENTICAL = 0.93   # titles this close are the same paper
_SAME_URL_SIMILAR = 0.55  # with an identical URL, a looser match is enough


def _fold(text: str) -> str:
    """Lowercase, strip diacritics, collapse punctuation and whitespace."""
    text = (text or "").replace("ş", "s").replace("ţ", "t")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_truncated(title: str) -> bool:
    return (title or "").strip().endswith(_TRUNCATION_MARKERS)


def normalize_title(title: str) -> str:
    """Comparison key for a title: folded, with any cut-off tail removed."""
    cleaned = (title or "").strip()
    for marker in _TRUNCATION_MARKERS:
        if cleaned.endswith(marker):
            cleaned = cleaned[: -len(marker)].rstrip(" .,-–—")
            break
    return _fold(cleaned)


def normalize_url(url: str) -> str:
    """Comparison key for a source URL: scheme-insensitive, no query or anchor."""
    url = (url or "").strip().lower()
    if not url:
        return ""
    url = re.sub(r"^https?://", "", url)
    url = re.split(r"[?#]", url, maxsplit=1)[0]
    url = url.replace("www.", "", 1) if url.startswith("www.") else url
    return url.rstrip("/")


def _words_match(short: str, long: str) -> bool:
    """`short` is the beginning of `long`, compared word by word.

    The last word of a truncated title may itself be cut in half ("systematic
    rev"), so it only has to be a prefix of its counterpart.
    """
    short_words, long_words = short.split(), long.split()
    if not short_words or len(short_words) > len(long_words):
        return False
    for index, word in enumerate(short_words):
        if index == len(short_words) - 1 and len(short_words) < len(long_words):
            if not long_words[index].startswith(word):
                return False
        elif word != long_words[index]:
            return False
    return True


def titles_match(title_a: str, title_b: str) -> bool:
    """Whether two study titles name the same work."""
    a, b = normalize_title(title_a), normalize_title(title_b)
    if not a or not b:
        return False
    if a == b:
        return True

    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    truncated = is_truncated(title_a) or is_truncated(title_b)
    # A truncation is either marked with an ellipsis or simply stops early —
    # the model sometimes cuts mid-sentence (even mid-word) with no marker at
    # all. Either way the short title must be the *beginning* of the long one;
    # four words is the floor, so "Mindfulness" alone cannot swallow a library.
    if shorter != longer and len(shorter.split()) >= 4 and _words_match(shorter, longer):
        if truncated or len(longer.split()) > len(shorter.split()):
            return True

    return SequenceMatcher(None, a, b).ratio() >= _NEAR_IDENTICAL


def same_study(title_a: str, url_a: str, title_b: str, url_b: str) -> bool:
    """Whether two rows describe the same paper, using title and source URL."""
    if titles_match(title_a, title_b):
        return True

    url = normalize_url(url_a)
    if not url or url != normalize_url(url_b):
        return False

    # Same URL: accept a looser title match, but never a blank or unrelated one.
    a, b = normalize_title(title_a), normalize_title(title_b)
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _SAME_URL_SIMILAR
