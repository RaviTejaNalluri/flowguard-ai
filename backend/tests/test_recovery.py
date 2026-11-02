"""Auto-heal, live progress, grouping identical failures, and recovering every affected run.

The AI is faked (free and repeatable). Note: Starlette's TestClient finishes background
tasks before returning, so a background heal is complete by the time the next call runs.
Run: .venv/bin/pytest -v
"""

import pytest
from fastapi.testclient import TestClient

import db
import main
from agent import repair as repair_module
from main import app
from mocks.services import contacts

FIXABLE = {"summary": "s", "root_cause": "r", "category": "field_mapping", "evidence": [],
           "fixable_by_workflow_change": True, "confidence": 0.9}
UNFIXABLE = {**FIXABLE, "category": "authentication", "fixable_by_workflow_change": False}
RENAME_EMAIL = {"op": "rename_field", "step_id": "create_contact", "from_field": "email", "to_field": "email_address"}
RETRY_SLACK = {"op": "add_retry", "step_id": "notify_slack", "max_attempts": 3}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setitem(main.settings, "auto_heal", False)
    with TestClient(app) as c:
        yield c


def fake_ai(monkeypatch, diagnosis=FIXABLE, patches=()):
    calls = {"diagnose": 0}

    def fake_diagnose(workflow, run):
        calls["diagnose"] += 1
        return diagnosis
    monkeypatch.setattr("main.diagnose", fake_diagnose)
    queue = [{"explanation": "test patch", "operations": ops} for ops in patches]
    monkeypatch.setattr(repair_module, "chat_json", lambda messages, name, schema: queue.pop(0))
    return calls


def run(client, payment_id="pay_001"):
    return client.post("/workflows/customer_onboarding/run", json={"payment_id": payment_id}).json()


def break_(client, *switches):
    for s in switches:
        client.post(f"/chaos/{s}", json={"enabled": True})


# ---------- Auto-heal ----------

def test_auto_heal_off_by_default(client, monkeypatch):
    calls = fake_ai(monkeypatch)
    break_(client, "schema_drift")
    failed = run(client)

    assert failed["incident_id"] is None
    assert calls["diagnose"] == 0


def test_auto_heal_starts_healing_without_a_click(client, monkeypatch):
    fake_ai(monkeypatch, patches=[[RENAME_EMAIL]])
    client.post("/settings", json={"auto_heal": True})
    break_(client, "schema_drift")
    failed = run(client)

    assert failed["incident_id"] is not None
    incident = client.get(f"/incidents/{failed['incident_id']}").json()
    assert incident["status"] == "awaiting_approval"


def test_demo_reset_turns_auto_heal_off(client):
    client.post("/settings", json={"auto_heal": True})
    client.post("/demo/reset")
    assert client.get("/settings").json() == {"auto_heal": False}


# ---------- Live progress ----------

def test_repair_attempts_are_saved_as_they_happen(client, monkeypatch):
    fake_ai(monkeypatch, patches=[[RETRY_SLACK], [RENAME_EMAIL]])
    snapshots = []
    original = db.update_incident

    def spy(incident_id, **fields):
        if "repair" in fields:
            snapshots.append(len(fields["repair"]["attempts"]))
        return original(incident_id, **fields)
    monkeypatch.setattr(db, "update_incident", spy)

    break_(client, "schema_drift")
    client.post(f"/runs/{run(client)['id']}/heal")
    assert snapshots == [0, 1, 2]  # empty, after attempt 1 (failed), final result


def test_model_errors_are_recorded_on_the_incident(client, monkeypatch):
    def broken(workflow, run):
        raise RuntimeError("Muse API unreachable")
    monkeypatch.setattr("main.diagnose", broken)
    break_(client, "schema_drift")
    started = client.post(f"/runs/{run(client)['id']}/heal").json()

    incident = client.get(f"/incidents/{started['id']}").json()
    assert incident["status"] == "error"
    assert "unreachable" in incident["error"]


# ---------- Grouping identical failures ----------

def test_identical_failures_share_one_incident_and_one_diagnosis(client, monkeypatch):
    calls = fake_ai(monkeypatch, patches=[[RENAME_EMAIL]])
    break_(client, "schema_drift")
    first, second = run(client, "pay_001"), run(client, "pay_002")

    incident = client.post(f"/runs/{first['id']}/heal").json()
    third = run(client, "pay_003")  # fails the same way while the incident is open

    assert third["incident_id"] == incident["id"]
    affected = client.get(f"/incidents/{incident['id']}").json()["affected_runs"]
    assert [r["id"] for r in affected] == [first["id"], second["id"], third["id"]]
    assert calls["diagnose"] == 1


def test_different_failures_get_different_incidents(client, monkeypatch):
    fake_ai(monkeypatch, diagnosis=UNFIXABLE)
    break_(client, "auth_expired")
    a = client.post(f"/runs/{run(client)['id']}/heal").json()
    client.post("/chaos/reset")
    break_(client, "schema_drift")
    b = client.post(f"/runs/{run(client)['id']}/heal").json()

    assert a["id"] != b["id"]


# ---------- Recovery ----------

def test_approve_recovers_every_affected_customer(client, monkeypatch):
    fake_ai(monkeypatch, patches=[[RENAME_EMAIL]])
    break_(client, "schema_drift")
    runs = [run(client, p) for p in ("pay_001", "pay_002", "pay_003")]
    incident = client.post(f"/runs/{runs[0]['id']}/heal").json()

    resolved = client.post(f"/incidents/{incident['id']}/approve").json()
    assert resolved["status"] == "resolved"
    assert resolved["recovery"]["affected"] == 3
    assert resolved["recovery"]["recovered"] == 3
    assert sorted(c["name"] for c in contacts) == ["Ada Lovelace", "Alan Turing", "Grace Hopper"]

    by_id = {r["id"]: r for r in client.get("/runs").json()}
    for original in runs:
        replay = by_id[by_id[original["id"]]["recovered_by"]]
        assert replay["replay_of"] == original["id"]
        assert replay["status"] == "success"


def test_replaying_does_not_duplicate_contacts(client, monkeypatch):
    # Rate limit: the CRM contact IS created, then Slack fails. The replay must not create it again.
    fake_ai(monkeypatch, patches=[[RETRY_SLACK]])
    break_(client, "rate_limit")
    incident = client.post(f"/runs/{run(client)['id']}/heal").json()
    assert len(contacts) == 1

    client.post(f"/incidents/{incident['id']}/approve")
    assert len(contacts) == 1


def test_needs_human_can_be_replayed_after_the_human_fixes_it(client, monkeypatch):
    fake_ai(monkeypatch, diagnosis=UNFIXABLE)
    break_(client, "auth_expired")
    runs = [run(client, p) for p in ("pay_001", "pay_002")]
    incident = client.post(f"/runs/{runs[0]['id']}/heal").json()
    assert client.get(f"/incidents/{incident['id']}").json()["status"] == "needs_human"

    still_broken = client.post(f"/incidents/{incident['id']}/replay").json()
    assert still_broken["status"] == "needs_human"
    assert still_broken["recovery"]["recovered"] == 0

    client.post("/chaos/reset")  # the human reconnected Slack
    fixed = client.post(f"/incidents/{incident['id']}/replay").json()
    assert fixed["status"] == "resolved"
    assert fixed["recovery"]["recovered"] == 2


def test_replays_cannot_be_healed(client, monkeypatch):
    fake_ai(monkeypatch, diagnosis=UNFIXABLE)
    break_(client, "auth_expired")
    incident = client.post(f"/runs/{run(client)['id']}/heal").json()
    replayed = client.post(f"/incidents/{incident['id']}/replay").json()

    replay_id = replayed["recovery"]["replays"][0]["replay_run_id"]
    assert client.post(f"/runs/{replay_id}/heal").status_code == 400
