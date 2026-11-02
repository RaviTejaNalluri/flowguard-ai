"""API tests: run workflows through the server, flip chaos, and check runs are saved.

Run: .venv/bin/pytest -v
"""

import pytest
from fastapi.testclient import TestClient

import db
from main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Each test gets its own empty database file.
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def test_starter_workflow_is_loaded(client):
    workflows = client.get("/workflows").json()
    assert [w["id"] for w in workflows] == ["customer_onboarding"]
    assert client.get("/workflows/customer_onboarding/versions").json()[0]["version"] == 1


def test_healthy_run_is_saved(client):
    run = client.post("/workflows/customer_onboarding/run", json={"payment_id": "pay_002"}).json()

    assert run["status"] == "success"
    assert client.get(f"/runs/{run['id']}").json()["status"] == "success"
    assert client.get("/services/data").json()["crm_contacts"][0]["name"] == "Alan Turing"


def test_chaos_switch_causes_saved_failure(client):
    client.post("/chaos/schema_drift", json={"enabled": True})
    assert client.get("/chaos").json()["schema_drift"]["enabled"] is True

    run = client.post("/workflows/customer_onboarding/run", json={}).json()
    saved = client.get(f"/runs/{run['id']}").json()

    assert saved["status"] == "failed"
    assert saved["failed_step"] == "create_contact"
    assert saved["steps"][1]["response"]["status"] == 422


def test_run_history_newest_first(client):
    client.post("/workflows/customer_onboarding/run", json={})
    client.post("/chaos/auth_expired", json={"enabled": True})
    client.post("/workflows/customer_onboarding/run", json={})

    statuses = [r["status"] for r in client.get("/runs").json()]
    assert statuses == ["failed", "success"]


def test_saving_workflow_creates_new_version(client):
    # "Approve fix" will save workflows this way.
    workflow = client.get("/workflows/customer_onboarding").json()
    workflow["steps"][2]["retry"] = {"max_attempts": 3}
    db.save_workflow(workflow, note="Add retry to Slack step")

    assert client.get("/workflows/customer_onboarding").json()["version"] == 2
    versions = client.get("/workflows/customer_onboarding/versions").json()
    assert [v["note"] for v in versions] == ["Initial version", "Add retry to Slack step"]


def test_unknown_things_return_404(client):
    assert client.get("/workflows/nope").status_code == 404
    assert client.get("/runs/999").status_code == 404
    assert client.post("/chaos/nope", json={"enabled": True}).status_code == 404


# ---------- Diagnosis (the AI is replaced by a fake so tests are free and repeatable) ----------

FAKE_DIAGNOSIS = {
    "summary": "CRM field was renamed.", "root_cause": "email -> email_address", "category": "field_mapping",
    "evidence": ["422 Missing required field: 'email_address'"], "fixable_by_workflow_change": True,
    "confidence": 0.9,
}


@pytest.fixture
def fake_ai(monkeypatch):
    calls = []

    def fake_diagnose(workflow, run):
        calls.append(run["id"])
        return FAKE_DIAGNOSIS
    monkeypatch.setattr("main.diagnose", fake_diagnose)
    return calls


def test_diagnose_failed_run_creates_incident(client, fake_ai):
    client.post("/chaos/schema_drift", json={"enabled": True})
    run = client.post("/workflows/customer_onboarding/run", json={}).json()

    incident = client.post(f"/runs/{run['id']}/diagnose").json()
    assert incident["status"] == "diagnosed"
    assert incident["diagnosis"]["category"] == "field_mapping"

    detail = client.get(f"/incidents/{incident['id']}").json()
    assert detail["run"]["failed_step"] == "create_contact"
    assert [i["id"] for i in client.get("/incidents").json()] == [incident["id"]]


def test_diagnosing_twice_reuses_incident(client, fake_ai):
    client.post("/chaos/schema_drift", json={"enabled": True})
    run = client.post("/workflows/customer_onboarding/run", json={}).json()

    first = client.post(f"/runs/{run['id']}/diagnose").json()
    second = client.post(f"/runs/{run['id']}/diagnose").json()
    assert first["id"] == second["id"]
    assert len(fake_ai) == 1  # the AI was only asked once (saves credits)


def test_unfixable_diagnosis_needs_human(client, monkeypatch):
    monkeypatch.setattr("main.diagnose", lambda w, r: {**FAKE_DIAGNOSIS, "fixable_by_workflow_change": False})
    client.post("/chaos/auth_expired", json={"enabled": True})
    run = client.post("/workflows/customer_onboarding/run", json={}).json()

    assert client.post(f"/runs/{run['id']}/diagnose").json()["status"] == "needs_human"


def test_successful_run_cannot_be_diagnosed(client, fake_ai):
    run = client.post("/workflows/customer_onboarding/run", json={}).json()
    assert client.post(f"/runs/{run['id']}/diagnose").status_code == 400
    assert fake_ai == []


def test_demo_reset_starts_over(client):
    workflow = client.get("/workflows/customer_onboarding").json()
    db.save_workflow(workflow, note="changed")
    client.post("/chaos/rate_limit", json={"enabled": True})
    client.post("/workflows/customer_onboarding/run", json={})

    client.post("/demo/reset")
    assert client.get("/workflows/customer_onboarding").json()["version"] == 1
    assert client.get("/runs").json() == []
    assert client.get("/chaos").json()["rate_limit"]["enabled"] is False
    assert client.get("/services/data").json()["crm_contacts"] == []
