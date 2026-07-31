from fastapi.testclient import TestClient

from app.main import app


def test_theory_tree_and_evidence_counts():
    with TestClient(app) as client:
        root = client.post("/api/theories", json={"name": "Root", "description": "d", "status": "supported"}).json()
        child = client.post("/api/theories", json={"name": "Child", "parent_id": root["id"]}).json()
        s1 = client.post("/api/entries", json={"title": "Pro study", "content": "x"}).json()
        s2 = client.post("/api/entries", json={"title": "Contra study", "content": "y"}).json()
        client.post(f"/api/theories/{root['id']}/evidence", json={"entry_id": s1["id"], "stance": "pro"})
        client.post(f"/api/theories/{root['id']}/evidence", json={"entry_id": s2["id"], "stance": "contra"})

        tree = client.get("/api/theories/tree").json()
        assert len(tree) == 1
        node = tree[0]
        assert node["pro_count"] == 1 and node["contra_count"] == 1
        assert node["children"][0]["id"] == child["id"]
        assert node["children"][0]["parent_id"] == root["id"]


def test_self_parent_and_cycles_blocked():
    with TestClient(app) as client:
        a = client.post("/api/theories", json={"name": "A"}).json()
        b = client.post("/api/theories", json={"name": "B", "parent_id": a["id"]}).json()
        assert client.put(f"/api/theories/{a['id']}", json={"parent_id": a["id"]}).status_code == 400
        assert client.put(f"/api/theories/{a['id']}", json={"parent_id": b["id"]}).status_code == 400


def test_delete_theory_cascades_evidence_and_frees_children():
    with TestClient(app) as client:
        root = client.post("/api/theories", json={"name": "Root2"}).json()
        child = client.post("/api/theories", json={"name": "Child2", "parent_id": root["id"]}).json()
        entry = client.post("/api/entries", json={"title": "S", "content": "c"}).json()
        client.post(f"/api/theories/{root['id']}/evidence", json={"entry_id": entry["id"], "stance": "pro"})

        assert client.delete(f"/api/theories/{root['id']}").status_code == 204
        assert client.get(f"/api/theories/{root['id']}/evidence").status_code == 404
        tree = client.get("/api/theories/tree").json()
        assert any(t["id"] == child["id"] and t["parent_id"] is None for t in tree)


def test_all_evidence_endpoint_cross_references_theory_and_study():
    with TestClient(app) as client:
        theory = client.post("/api/theories", json={"name": "Cross ref"}).json()
        entry = client.post("/api/entries", json={"title": "Study X", "content": "c"}).json()
        client.post(f"/api/theories/{theory['id']}/evidence", json={"entry_id": entry["id"], "stance": "pro"})
        all_ev = client.get("/api/theories/evidence").json()
        assert any(e["theory_id"] == theory["id"] and e["entry_id"] == entry["id"] and e["theory_name"] == "Cross ref" for e in all_ev)
