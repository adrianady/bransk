"""Slice 5 — claim hierarchy + stance edges."""
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


def make_theory(client, name="T", parent_id=None):
    payload = {"name": name}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    resp = client.post("/api/theories", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_entry(client, title="Study"):
    resp = client.post("/api/entries", json={"title": title, "content": "c"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def set_page_text(entry_id, page, text):
    conn = get_db()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO page_texts
               (entry_id, page_number, text, source, status, char_count)
               VALUES (?, ?, ?, 'manual', 'manual', ?)""",
            [entry_id, page, text, len(text)],
        )
        conn.commit()
    finally:
        conn.close()


def make_claim(client, parent_id, name="C", statement="X crește în condiția Z."):
    resp = client.post(
        "/api/claims",
        json={"parent_id": parent_id, "name": name, "statement": statement},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def propose(client, claim_id, entry_id, direction="supports", quote=None, page=1, **kw):
    payload = {"entry_id": entry_id, "direction": direction}
    if page is not None:
        payload["evidence_page"] = page
    if quote is not None:
        payload["evidence_quote"] = quote
    payload.update(kw)
    return client.post(f"/api/claims/{claim_id}/stances", json=payload)


def accept(client, stance_id):
    return client.patch(f"/api/claims/stances/{stance_id}", json={"action": "accept"})


def test_only_accepted_edges_affect_aggregate():
    with TestClient(app) as client:
        theory = make_theory(client)
        entry = make_entry(client)
        set_page_text(entry["id"], 1, "Rezultatele arată că X crește semnificativ în condiția Z.")
        claim = make_claim(client, theory["id"])

        stance = propose(client, claim["id"], entry["id"], "supports", "X crește semnificativ")
        assert stance.status_code == 201, stance.text
        assert stance.json()["status"] == "proposed"

        agg = client.get(f"/api/claims/{claim['id']}/aggregate").json()
        assert agg["per_entry"] == []
        assert agg["studies_accepted"] == 0
        assert agg["claim_verdict"] == "insufficient"

        assert accept(client, stance.json()["id"]).status_code == 200
        agg = client.get(f"/api/claims/{claim['id']}/aggregate").json()
        assert agg["studies_accepted"] == 1
        assert agg["per_entry"][0]["verdict"] == "supports"
        # One study is not enough to color the claim.
        assert agg["claim_verdict"] == "insufficient"


def test_quote_must_match_canonical_page_text():
    with TestClient(app) as client:
        theory = make_theory(client)
        entry = make_entry(client)
        set_page_text(entry["id"], 1, "Textul canonic al paginii unu.")
        claim = make_claim(client, theory["id"])

        bad = propose(client, claim["id"], entry["id"], "supports", "citat inventat de model")
        assert bad.status_code == 422
        assert "Citatul" in bad.json()["detail"]

        grouped = client.get(f"/api/claims/{claim['id']}/stances").json()
        assert grouped["proposed"] == []


def test_neutral_needs_no_quote_but_supports_does():
    with TestClient(app) as client:
        theory = make_theory(client)
        entry = make_entry(client)
        claim = make_claim(client, theory["id"])

        neutral = propose(client, claim["id"], entry["id"], "neutral", page=None)
        assert neutral.status_code == 201, neutral.text

        missing = propose(client, claim["id"], entry["id"], "supports", page=None)
        assert missing.status_code == 422
        assert "neutral" in missing.json()["detail"]


def test_second_accepted_edge_for_same_triple_conflicts():
    with TestClient(app) as client:
        theory = make_theory(client)
        entry = make_entry(client)
        set_page_text(entry["id"], 1, "X crește mult. Totuși Y scade vizibil aici.")
        claim = make_claim(client, theory["id"])

        first = propose(client, claim["id"], entry["id"], "supports", "X crește mult")
        assert accept(client, first.json()["id"]).status_code == 200

        second = propose(client, claim["id"], entry["id"], "contradicts", "Y scade vizibil")
        assert second.status_code == 201, second.text
        conflict = accept(client, second.json()["id"])
        assert conflict.status_code == 409
        assert "deja" in conflict.json()["detail"]

        grouped = client.get(f"/api/claims/{claim['id']}/stances").json()
        assert len(grouped["accepted"]) == 1
        assert len(grouped["proposed"]) == 1


def test_claim_cannot_be_parent():
    with TestClient(app) as client:
        theory = make_theory(client)
        claim = make_claim(client, theory["id"])
        resp = client.post(
            "/api/claims",
            json={"parent_id": claim["id"], "name": "Sub", "statement": "Y scade."},
        )
        assert resp.status_code == 422
        assert "copii" in resp.json()["detail"]


def test_aggregate_rules_and_threshold():
    with TestClient(app) as client:
        theory = make_theory(client)
        claim = make_claim(client, theory["id"])

        supporters = []
        for i in range(2):
            entry = make_entry(client, f"Pro {i}")
            set_page_text(entry["id"], 1, f"Studiul {i} confirmă că X crește clar.")
            stance = propose(client, claim["id"], entry["id"], "supports", "X crește clar")
            assert stance.status_code == 201, stance.text
            assert accept(client, stance.json()["id"]).status_code == 200
            supporters.append(entry)

        agg = client.get(f"/api/claims/{claim['id']}/aggregate").json()
        assert agg["studies_accepted"] == 2
        assert agg["claim_verdict"] == "supports"

        contra = make_entry(client, "Contra")
        set_page_text(contra["id"], 1, "Datele noastre arată că X scade de fapt.")
        stance = propose(client, claim["id"], contra["id"], "contradicts", "X scade de fapt")
        assert accept(client, stance.json()["id"]).status_code == 200

        agg = client.get(f"/api/claims/{claim['id']}/aggregate").json()
        assert agg["studies_accepted"] == 3
        assert agg["claim_verdict"] == "mixed"

        # Two accepted edges on the same study with different aspects mix per-entry.
        mixed_entry = make_entry(client, "Mixed aspects")
        set_page_text(mixed_entry["id"], 1, "X crește aici. Dar X scade dincolo.")
        a1 = propose(
            client, claim["id"], mixed_entry["id"], "supports", "X crește aici",
            aspect="efect principal",
        )
        assert accept(client, a1.json()["id"]).status_code == 200
        a2 = propose(
            client, claim["id"], mixed_entry["id"], "contradicts", "X scade dincolo",
            aspect="efect secundar",
        )
        assert a2.status_code == 201, a2.text
        assert accept(client, a2.json()["id"]).status_code == 200
        agg = client.get(f"/api/claims/{claim['id']}/aggregate").json()
        per = next(p for p in agg["per_entry"] if p["entry_id"] == mixed_entry["id"])
        assert per["verdict"] == "mixed"


def test_human_override_keeps_model_direction_once():
    with TestClient(app) as client:
        theory = make_theory(client)
        entry = make_entry(client)
        set_page_text(entry["id"], 1, "X crește semnificativ în eșantion.")
        claim = make_claim(client, theory["id"])

        stance = propose(client, claim["id"], entry["id"], "supports", "X crește semnificativ")
        sid = stance.json()["id"]

        first = client.patch(
            f"/api/claims/stances/{sid}", json={"direction": "contradicts"}
        )
        assert first.status_code == 200, first.text
        assert first.json()["reasoning"].startswith("[model: supports] ")

        second = client.patch(f"/api/claims/stances/{sid}", json={"direction": "mixed"})
        assert second.status_code == 200, second.text
        reasoning = second.json()["reasoning"]
        assert reasoning.count("[model: ") == 1
        assert reasoning.startswith("[model: supports] ")


def test_stances_grouped_by_status_with_entry_titles_and_analysis_panel():
    with TestClient(app) as client:
        theory = make_theory(client)
        entry = make_entry(client, "Panel study")
        set_page_text(entry["id"], 2, "Pagina doi spune că X crește lent.")
        claim = make_claim(client, theory["id"], name="Panel claim")

        stance = propose(
            client, claim["id"], entry["id"], "supports", "X crește lent",
            page=2, reasoning="Efect clar.", confidence=0.8,
        )
        assert stance.status_code == 201, stance.text
        body = stance.json()
        assert body["evidence_page"] == 2
        assert body["evidence_start"] is not None and body["evidence_end"] is not None

        grouped = client.get(f"/api/claims/{claim['id']}/stances").json()
        assert set(grouped) == {"proposed", "accepted", "rejected", "insufficient"}
        assert grouped["proposed"][0]["entry_title"] == "Panel study"

        filtered = client.get(
            f"/api/claims/{claim['id']}/stances", params={"status": "proposed"}
        ).json()
        assert set(filtered) == {"proposed"}

        analysis = client.get(f"/api/entries/{entry['id']}/analysis").json()
        assert analysis["entry_title"] == "Panel study"
        assert len(analysis["claims"]) == 1
        touched = analysis["claims"][0]
        assert touched["direction"] == "supports"
        assert touched["evidence_quote"] == "X crește lent"
        assert touched["evidence_page"] == 2
        assert touched["reasoning"] == "Efect clar."
        assert touched["confidence"] == 0.8
        assert analysis["bias"] == {"available": False, "reason": "not_implemented"}


def test_theory_tree_shape_unchanged_and_claims_opt_in():
    with TestClient(app) as client:
        root = make_theory(client, "Root")
        child = make_theory(client, "Child", parent_id=root["id"])
        entry = make_entry(client, "Pro study")
        client.post(
            f"/api/theories/{root['id']}/evidence",
            json={"entry_id": entry["id"], "stance": "pro"},
        )

        tree = client.get("/api/theories/tree").json()
        assert len(tree) == 1
        node = tree[0]
        assert set(node) == {
            "id", "name", "description", "parent_id", "status",
            "pro_count", "contra_count", "pro_weight", "contra_weight", "children",
        }
        assert node["pro_count"] == 1 and node["contra_count"] == 0
        assert node["children"][0]["id"] == child["id"]

        claim = make_claim(client, root["id"], name="Hidden claim")

        def ids(nodes):
            found = []
            for n in nodes:
                found.append(n["id"])
                found.extend(ids(n["children"]))
            return found

        assert claim["id"] not in ids(client.get("/api/theories/tree").json())
        assert claim["id"] in ids(
            client.get("/api/theories/tree", params={"include_claims": "true"}).json()
        )
        only_claims = client.get("/api/theories/tree", params={"node_type": "claim"}).json()
        assert only_claims and all(n["id"] == claim["id"] for n in only_claims)
