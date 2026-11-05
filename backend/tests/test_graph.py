"""Graph endpoint: label normalisation, visibility rules, degrees."""
from fastapi.testclient import TestClient

from app.main import app
from app.routers.relationships import RELATION_KINDS, parse_relation_label


def _entry(client, title, **extra):
    return client.post("/api/entries", json={"title": title, "content": "x", **extra}).json()


def _link(client, a, b, label):
    return client.post("/api/relationships", json={"from_id": a, "to_id": b, "label": label})


# ── label normalisation (pure) ──────────────────────────────────────────────

def test_quoted_junk_labels_become_canonical():
    assert parse_relation_label('"precursor to"') == ("precedes", "")
    assert parse_relation_label('"alternative to"') == ("alternative_to", "")
    assert parse_relation_label('"extends"') == ("extends", "")
    assert parse_relation_label('"critiques"') == ("criticizes", "")


def test_free_text_label_keeps_detail_after_pipe():
    code, detail = parse_relation_label(
        "precedes | Tulving's episodic/semantic → Squire's multiple systems"
    )
    assert code == "precedes"
    assert detail == "Tulving's episodic/semantic → Squire's multiple systems"


def test_synonyms_map_to_canonical_codes():
    assert parse_relation_label("builds_on")[0] == "extends"
    assert parse_relation_label("chronologically_precedes")[0] == "precedes"
    assert parse_relation_label("preceded")[0] == "precedes"
    assert parse_relation_label('"formalized by"')[0] == "derives_from"
    assert parse_relation_label('"applied to AI in"')[0] == "applied_in"
    assert parse_relation_label("reviewed in")[0] == "reviewed_in"


def test_unknown_label_falls_back_to_relates_to_and_keeps_its_text():
    code, detail = parse_relation_label("something entirely new")
    assert code == "relates_to"
    # unrecognised free text must not vanish: it becomes the detail
    assert detail == "something entirely new"
    for c in {parse_relation_label(r)[0] for r in (
        '"precursor to"', "precedes | x", "builds_on", "derives_from", "supports",
    )}:
        assert c in RELATION_KINDS, c


def test_empty_label_is_relates_to():
    assert parse_relation_label(None) == ("relates_to", "")
    assert parse_relation_label("   ") == ("relates_to", "")


# ── graph payload ───────────────────────────────────────────────────────────

def test_graph_normalises_labels_and_counts_degrees():
    with TestClient(app) as client:
        a = _entry(client, "A")
        b = _entry(client, "B")
        c = _entry(client, "C")          # stays isolated
        _link(client, a["id"], b["id"], '"precursor to"')

        data = client.get("/api/relationships/graph").json()
        edge = data["edges"][0]
        assert edge["label"] == "precedes"
        assert edge["label_display"] == "precede"
        assert edge["color"].startswith("#")

        deg = {n["id"]: n for n in data["nodes"]}
        assert deg[a["id"]]["degree"] == 1
        assert deg[b["id"]]["degree"] == 1
        assert deg[c["id"]]["degree"] == 0
        assert deg[c["id"]]["isolated"] is True
        assert deg[a["id"]]["isolated"] is False


def test_archived_nodes_and_their_edges_disappear():
    with TestClient(app) as client:
        a = _entry(client, "A")
        b = _entry(client, "B")
        c = _entry(client, "C")
        _link(client, a["id"], b["id"], "supports")
        _link(client, a["id"], c["id"], "extends")
        client.put(f"/api/entries/{c['id']}", json={"archived": 1})

        data = client.get("/api/relationships/graph").json()
        assert c["id"] not in {n["id"] for n in data["nodes"]}
        assert len(data["edges"]) == 1
        assert {e["label"] for e in data["edges"]} == {"supports"}
        # the archived node's edge must not inflate A's degree
        deg = {n["id"]: n["degree"] for n in data["nodes"]}
        assert deg[a["id"]] == 1


def test_labels_endpoint_sums_to_visible_edges():
    with TestClient(app) as client:
        a, b, c = _entry(client, "A"), _entry(client, "B"), _entry(client, "C")
        _link(client, a["id"], b["id"], '"precursor to"')
        _link(client, b["id"], c["id"], "supports")
        _link(client, a["id"], c["id"], "precedes | note only")

        payload = client.get("/api/relationships/labels").json()
        counts = {item["code"]: item["count"] for item in payload["labels"]}
        assert counts == {"precedes": 2, "supports": 1}
        assert payload["total"] == 3
        assert payload["total"] == len(client.get("/api/relationships/graph").json()["edges"])


def test_create_normalises_label_and_blocks_self_links():
    with TestClient(app) as client:
        a, b = _entry(client, "A"), _entry(client, "B")
        created = _link(client, a["id"], b["id"], '"precursor to"')
        assert created.status_code == 201
        # stored canonical, so the duplicate check and the graph agree
        assert created.json()["label"] == "precedes"
        assert _link(client, a["id"], b["id"], "precedes").status_code == 409
        assert _link(client, a["id"], a["id"], "supports").status_code == 400
