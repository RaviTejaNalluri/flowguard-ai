"""Engine tests: healthy runs succeed, each chaos switch breaks the expected step,
and the kinds of fixes FlowGuard will propose really do repair them.

Run: .venv/bin/pytest -v
"""

import asyncio
import copy

from engine.runner import load_workflow, mock_client, run_workflow
from mocks import chaos
from mocks.services import contacts, messages


def run(workflow, payment_id="pay_001"):
    async def go():
        async with mock_client() as client:
            return await run_workflow(workflow, {"payment_id": payment_id}, client)
    return asyncio.run(go())


def step(workflow, step_id):
    return next(s for s in workflow["steps"] if s["id"] == step_id)


# ---------- Healthy ----------

def test_healthy_run_succeeds():
    result = run(load_workflow("customer_onboarding"))

    assert result["status"] == "success"
    assert [s["status"] for s in result["steps"]] == ["success"] * 3
    assert contacts[0]["email"] == "ada@example.com"
    assert messages[0]["text"] == "New customer Ada Lovelace paid 4900 usd"


# ---------- Each chaos switch breaks the expected step ----------

def test_schema_drift_breaks_crm_step():
    chaos.set_switch("schema_drift", True)
    result = run(load_workflow("customer_onboarding"))

    assert result["status"] == "failed"
    assert result["failed_step"] == "create_contact"
    assert "email_address" in result["error"]


def test_missing_data_breaks_crm_step():
    chaos.set_switch("missing_data", True)
    result = run(load_workflow("customer_onboarding"))

    assert result["failed_step"] == "create_contact"
    assert "'name'" in result["error"]


def test_rate_limit_breaks_slack_step():
    chaos.set_switch("rate_limit", True)
    result = run(load_workflow("customer_onboarding"))

    assert result["failed_step"] == "notify_slack"
    assert result["error"].startswith("HTTP 429")


def test_auth_expired_breaks_slack_step():
    chaos.set_switch("auth_expired", True)
    result = run(load_workflow("customer_onboarding"))

    assert result["failed_step"] == "notify_slack"
    assert result["error"].startswith("HTTP 401")


# ---------- The fixes FlowGuard will propose actually work ----------

def test_renaming_field_fixes_schema_drift():
    chaos.set_switch("schema_drift", True)
    wf = copy.deepcopy(load_workflow("customer_onboarding"))
    body = step(wf, "create_contact")["body"]
    body["email_address"] = body.pop("email")

    assert run(wf)["status"] == "success"


def test_default_value_fixes_missing_data():
    chaos.set_switch("missing_data", True)
    wf = copy.deepcopy(load_workflow("customer_onboarding"))
    step(wf, "create_contact")["defaults"] = {"name": "Unknown customer"}

    assert run(wf)["status"] == "success"
    assert contacts[0]["name"] == "Unknown customer"


def test_retry_fixes_rate_limit():
    chaos.set_switch("rate_limit", True)
    wf = copy.deepcopy(load_workflow("customer_onboarding"))
    step(wf, "notify_slack")["retry"] = {"max_attempts": 3}

    result = run(wf)
    assert result["status"] == "success"
    assert result["steps"][-1]["attempts"] == 3


def test_retry_does_not_fix_expired_auth():
    chaos.set_switch("auth_expired", True)
    wf = copy.deepcopy(load_workflow("customer_onboarding"))
    step(wf, "notify_slack")["retry"] = {"max_attempts": 3}

    assert run(wf)["status"] == "failed"
