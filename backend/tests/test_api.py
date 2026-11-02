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
