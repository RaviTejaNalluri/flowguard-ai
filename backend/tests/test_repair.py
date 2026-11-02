"""Repair tests: patch safety checks, the sandbox retry loop, and approve/reject.

The AI is replaced by a scripted fake that returns patches in order, so these
tests are free, fast and always give the same result.
Run: .venv/bin/pytest -v
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import db
from agent import repair as repair_module
from agent.patches import PatchError, apply_patch
from engine.runner import load_workflow, mock_client, run_workflow
from main import app
from mocks import chaos
from mocks.services import contacts

RENAME_EMAIL = {"op": "rename_field", "step_id": "create_contact", "from_field": "email", "to_field": "email_address"}
DEFAULT_NAME = {"op": "set_default", "step_id": "create_contact", "field": "name", "value": "Unknown customer"}
RETRY_SLACK = {"op": "add_retry", "step_id": "notify_slack", "max_attempts": 3}


def scripted_ai(monkeypatch, *patches):
    """Make the repair agent's AI return these patches, one per call."""
    queue = [{"explanation": "test patch", "operations": ops} for ops in patches]
    monkeypatch.setattr(repair_module, "chat_json", lambda messages, name, schema: queue.pop(0))
    return queue


def failed_run(*switches):
    for s in switches:
        chaos.set_switch(s, True)
    workflow = load_workflow("customer_onboarding")

    async def go():
        async with mock_client() as client:
            return await run_workflow(workflow, {"payment_id": "pay_001"}, client)
    return workflow, asyncio.run(go())


# ---------- Patch safety checks ----------

def test_patch_does_not_modify_original():
    workflow = load_workflow("customer_onboarding")
    patched, changes = apply_patch(workflow, [RENAME_EMAIL])

    assert "email" in workflow["steps"][1]["body"]
    assert "email_address" in patched["steps"][1]["body"]
    assert changes == ["create_contact: rename body field 'email' -> 'email_address'"]


@pytest.mark.parametrize("bad_op, message", [
    ({**RENAME_EMAIL, "step_id": "delete_everything"}, "No step"),
    ({**RENAME_EMAIL, "from_field": "phone"}, "no body field"),
    ({**RENAME_EMAIL, "to_field": "name"}, "already has"),
    ({**DEFAULT_NAME, "value": "  "}, "cannot be empty"),
    ({**RETRY_SLACK, "max_attempts": 50}, "between 2 and 5"),
    ({"op": "run_shell", "step_id": "notify_slack"}, "not allowed"),
])
def test_unsafe_or_invalid_patches_are_rejected(bad_op, message):
    with pytest.raises(PatchError, match=message):
        apply_patch(load_workflow("customer_onboarding"), [bad_op])


# ---------- Sandbox retry loop ----------

def test_good_patch_passes_on_first_attempt(monkeypatch):
    scripted_ai(monkeypatch, [RENAME_EMAIL])
    workflow, run = failed_run("schema_drift")

    result = repair_module.repair(workflow, run, diagnosis={})
    assert result["status"] == "awaiting_approval"
    assert len(result["attempts"]) == 1
    assert contacts == []  # the sandbox test did not create a real contact


def test_failed_sandbox_feeds_back_and_second_patch_wins(monkeypatch):
    # Two problems at once: the first patch only fixes one of them.
    scripted_ai(monkeypatch, [RENAME_EMAIL], [RENAME_EMAIL, DEFAULT_NAME])
    workflow, run = failed_run("schema_drift", "missing_data")

    result = repair_module.repair(workflow, run, diagnosis={})
    assert result["status"] == "awaiting_approval"
    assert "Sandbox replay failed at create_contact" in result["attempts"][0]["error"]
    assert result["attempts"][1]["error"] is None


def test_invalid_patch_is_rejected_then_retried(monkeypatch):
    scripted_ai(monkeypatch, [{**RENAME_EMAIL, "from_field": "phone"}], [RENAME_EMAIL])
    workflow, run = failed_run("schema_drift")

    result = repair_module.repair(workflow, run, diagnosis={})
    assert result["attempts"][0]["error"].startswith("Invalid patch")
    assert result["status"] == "awaiting_approval"


def test_gives_up_after_max_attempts(monkeypatch):
    scripted_ai(monkeypatch, [RETRY_SLACK], [RETRY_SLACK], [RETRY_SLACK])
    workflow, run = failed_run("auth_expired")

    result = repair_module.repair(workflow, run, diagnosis={})
    assert result["status"] == "repair_failed"
    assert len(result["attempts"]) == repair_module.MAX_ATTEMPTS


# ---------- Heal + approve / reject through the server ----------

FIXABLE = {"summary": "s", "root_cause": "r", "category": "field_mapping", "evidence": [],
           "fixable_by_workflow_change": True, "confidence": 0.9}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("main.diagnose", lambda workflow, run: FIXABLE)
    with TestClient(app) as c:
        yield c


def heal_schema_drift(client, monkeypatch):
    scripted_ai(monkeypatch, [RENAME_EMAIL])
    client.post("/chaos/schema_drift", json={"enabled": True})
    run = client.post("/workflows/customer_onboarding/run", json={}).json()
    return client.post(f"/runs/{run['id']}/heal").json()


def test_heal_stops_at_awaiting_approval(client, monkeypatch):
    incident = heal_schema_drift(client, monkeypatch)

    assert incident["status"] == "awaiting_approval"
    assert incident["repair"]["changes"] == ["create_contact: rename body field 'email' -> 'email_address'"]
    assert client.get("/workflows/customer_onboarding").json()["version"] == 1  # nothing applied yet


def test_approve_saves_new_version_and_recovers_the_customer(client, monkeypatch):
    incident = heal_schema_drift(client, monkeypatch)
    approved = client.post(f"/incidents/{incident['id']}/approve").json()

    assert approved["status"] == "resolved"
    assert approved["resolution_run"]["status"] == "success"
    assert client.get("/workflows/customer_onboarding").json()["version"] == 2
    assert client.get("/services/data").json()["crm_contacts"][0]["email"] == "ada@example.com"

    # And the next normal run works too.
    assert client.post("/workflows/customer_onboarding/run", json={}).json()["status"] == "success"


def test_reject_leaves_workflow_unchanged(client, monkeypatch):
    incident = heal_schema_drift(client, monkeypatch)
    rejected = client.post(f"/incidents/{incident['id']}/reject").json()

    assert rejected["status"] == "rejected"
    assert client.get("/workflows/customer_onboarding").json()["version"] == 1
    assert client.post(f"/incidents/{incident['id']}/approve").status_code == 409


def test_cannot_approve_twice(client, monkeypatch):
    incident = heal_schema_drift(client, monkeypatch)
    client.post(f"/incidents/{incident['id']}/approve")
    assert client.post(f"/incidents/{incident['id']}/approve").status_code == 409
