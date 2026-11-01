"""The workflow engine: runs a workflow's steps in order and records everything.

A workflow is a JSON "recipe" (see engine/workflows/). Each step is an HTTP call
whose path and body can use {{templates}} that pull values from the trigger or
from earlier steps' responses, e.g. {{steps.fetch_payment.customer.email}}.

Optional step settings (these are what FlowGuard's repairs will change):
  "defaults": {"name": "Unknown customer"}  -> fill a body field if it comes out empty
  "retry": {"max_attempts": 3}              -> retry on HTTP 429 / 5xx errors

Every request, response and error is written into the run record, because that
record is exactly what the AI reads to diagnose a failure.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from mocks.services import app as mock_app

WORKFLOWS_DIR = Path(__file__).parent / "workflows"
TEMPLATE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
RETRYABLE = {429, 500, 502, 503, 504}


def load_workflow(workflow_id: str) -> dict:
    return json.loads((WORKFLOWS_DIR / f"{workflow_id}.json").read_text())


def mock_client() -> httpx.AsyncClient:
    """An HTTP client wired straight to the fake services (no network needed)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_app), base_url="http://mocks")


def _lookup(context: dict, path: str):
    value = context
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _render(value, context: dict):
    """Fill in {{templates}}. A string that is only one template keeps the raw value type."""
    if isinstance(value, dict):
        return {k: _render(v, context) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    whole = TEMPLATE.fullmatch(value)
    if whole:
        return _lookup(context, whole.group(1))
    return TEMPLATE.sub(lambda m: "" if (v := _lookup(context, m.group(1))) is None else str(v), value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_workflow(workflow: dict, trigger: dict, client: httpx.AsyncClient) -> dict:
    run = {
        "workflow_id": workflow["id"],
        "workflow_version": workflow.get("version", 1),
        "trigger": trigger,
        "status": "running",
        "failed_step": None,
        "error": None,
        "steps": [],
        "started_at": _now(),
    }
    context = {"trigger": trigger, "steps": {}}

    for step in workflow["steps"]:
        path = _render(step["path"], context)
        body = _render(step.get("body"), context)
        for field, default in step.get("defaults", {}).items():
            if body is not None and body.get(field) in (None, ""):
                body[field] = default

        max_attempts = step.get("retry", {}).get("max_attempts", 1)
        for attempt in range(1, max_attempts + 1):
            resp = await client.request(step["method"], path, json=body)
            if resp.status_code not in RETRYABLE:
                break

        try:
            resp_body = resp.json()
        except ValueError:
            resp_body = resp.text

        record = {
            "id": step["id"],
            "service": step.get("service"),
            "request": {"method": step["method"], "path": path, "body": body},
            "response": {"status": resp.status_code, "body": resp_body},
            "attempts": attempt,
            "status": "success" if resp.is_success else "failed",
        }
        run["steps"].append(record)

        if not resp.is_success:
            detail = resp_body.get("detail", resp_body) if isinstance(resp_body, dict) else resp_body
            run["status"] = "failed"
            run["failed_step"] = step["id"]
            run["error"] = f"HTTP {resp.status_code}: {detail}"
            break

        context["steps"][step["id"]] = resp_body
    else:
        run["status"] = "success"

    run["finished_at"] = _now()
    return run
