"""Layer 3 — hardcoded stance aggregation. Pure functions: no DB, no IO.

Rules come verbatim from ``docs/design/claim-stance-schema.md`` §3: only
``accepted`` edges vote, ``insufficient`` never votes, and a claim stays
uncoloured below ``min_studies`` accepted studies. Nothing here calls a model —
the verdict must be reproducible from the edge list alone.
"""
from collections.abc import Iterable

_DIRECTIONS = ("supports", "contradicts", "mixed", "neutral", "insufficient")


def study_verdict(directions: Iterable[str]) -> str:
    """Per-study verdict from its accepted directions.

    empty → insufficient; all supports → supports; all contradicts →
    contradicts; only neutral → neutral; any mix → mixed. A single direction
    repeated always wins, so all-``mixed`` stays ``mixed``.
    """
    dirs = [d for d in (directions or []) if d]
    if not dirs:
        return "insufficient"
    if all(d == "neutral" for d in dirs):
        return "neutral"
    pool = [d for d in dirs if d != "neutral"]
    if len(set(pool)) == 1:
        return pool[0]
    return "mixed"


def claim_rollup(per_study: Iterable[dict], min_studies: int = 2) -> dict:
    """Aggregate per-study verdicts into one claim verdict.

    ``per_study`` items are ``{entry_id, verdict, sides}``. Returns
    ``{verdict, studies_accepted, confident, distribution}``.

    Majority of the *decided* studies wins; a tie (no strict majority) becomes
    ``mixed``. Fewer than ``min_studies`` decided studies → ``insufficient`` and
    ``confident=False``. ``insufficient`` studies never vote and are only
    counted in the distribution.
    """
    items = list(per_study or [])
    distribution = {d: 0 for d in _DIRECTIONS}
    decided: list[str] = []
    for item in items:
        verdict = (item or {}).get("verdict") or "insufficient"
        if verdict not in distribution:
            verdict = "insufficient"
        distribution[verdict] += 1
        if verdict != "insufficient":
            decided.append(verdict)

    studies_accepted = len(decided)
    if studies_accepted < min_studies:
        return {
            "verdict": "insufficient",
            "studies_accepted": studies_accepted,
            "confident": False,
            "distribution": distribution,
        }

    counts: dict[str, int] = {}
    for verdict in decided:
        counts[verdict] = counts.get(verdict, 0) + 1
    winner = None
    for verdict, count in counts.items():
        if count * 2 > studies_accepted:
            winner = verdict
            break

    return {
        "verdict": winner or "mixed",
        "studies_accepted": studies_accepted,
        "confident": True,
        "distribution": distribution,
    }


_LABELS = {
    "supports": ("studiu susține", "studii susțin"),
    "contradicts": ("studiu contrazice", "studii contrazic"),
    "mixed": ("studiu e mixt", "studii sunt mixte"),
    "neutral": ("studiu e neutru", "studii sunt neutre"),
}

_VERDICT_WORDS = {
    "supports": "susținut",
    "contradicts": "contrazis",
    "mixed": "mixt",
    "neutral": "neutru",
    "insufficient": "insuficient",
}


def explain(verdict: str, per_study: Iterable[dict]) -> str:
    """One short Romanian sentence naming the counts, e.g. „3 studii susțin, 1 contrazice → mixt"."""
    distribution = {d: 0 for d in _DIRECTIONS}
    for item in per_study or []:
        value = (item or {}).get("verdict") or "insufficient"
        if value not in distribution:
            value = "insufficient"
        distribution[value] += 1

    parts = []
    for direction in ("supports", "contradicts", "mixed", "neutral"):
        count = distribution[direction]
        if not count:
            continue
        singular, plural = _LABELS[direction]
        parts.append(f"{count} {singular if count == 1 else plural}")
    if not parts:
        parts.append("niciun studiu acceptat")
    word = _VERDICT_WORDS.get(verdict, _VERDICT_WORDS["insufficient"])
    return ", ".join(parts) + " → " + word
