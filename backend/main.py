"""FlowGuard API server. Run: .venv/bin/uvicorn main:app --reload

Then open http://127.0.0.1:8000/docs to try every endpoint in the browser.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
from agent.diagnose import diagnose
from agent.repair import repair
from engine.runner import mock_client, run_workflow
from mocks import chaos
from mocks.services import contacts, messages, reset_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="FlowGuard AI", lifespan=lifespan)

# Let the Next.js dashboard (localhost:3000) call this server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    payment_id: str = "pay_001"


class ChaosRequest(BaseModel):
    enabled: bool


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Workflows ----------

@app.get("/workflows")
def list_workflows():
    return db.list_workflows()


@app.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    workflow = db.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(404, f"No such workflow: {workflow_id}")
    return workflow


@app.get("/workflows/{workflow_id}/versions")
def list_versions(workflow_id: str):
    get_workflow(workflow_id)
    return db.list_versions(workflow_id)


@app.post("/workflows/{workflow_id}/run")
async def run(workflow_id: str, req: RunRequest):
    workflow = get_workflow(workflow_id)
    async with mock_client() as client:
        result = await run_workflow(workflow, {"payment_id": req.payment_id}, client)
    return db.save_run(result)


# ---------- Runs ----------

@app.get("/runs")
def list_runs(workflow_id: str | None = None, limit: int = 50):
    return db.list_runs(workflow_id, limit)


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, f"No such run: {run_id}")
    return run


@app.post("/runs/{run_id}/diagnose")
def diagnose_run(run_id: int):
    """Ask Muse Spark why this run failed. Creates an incident (once per run)."""
    run = get_run(run_id)
    if run["status"] != "failed":
        raise HTTPException(400, "Only failed runs can be diagnosed")
    if existing := db.get_incident_for_run(run_id):
        return existing

    workflow = db.get_workflow_version(run["workflow_id"], run["workflow_version"])
    diagnosis = diagnose(workflow, run)
    status = "diagnosed" if diagnosis["fixable_by_workflow_change"] else "needs_human"
    return db.create_incident(run, status, diagnosis)


# ---------- Incidents ----------

@app.get("/incidents")
def list_incidents(limit: int = 50):
    return db.list_incidents(limit)


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: int):
    incident = db.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"No such incident: {incident_id}")
    resolution = db.get_run(incident["resolution_run_id"]) if incident["resolution_run_id"] else None
    return {**incident, "run": db.get_run(incident["run_id"]), "resolution_run": resolution}


def _require_status(incident: dict, *allowed: str) -> None:
    if incident["status"] not in allowed:
        raise HTTPException(409, f"Incident is '{incident['status']}', expected one of: {', '.join(allowed)}")


@app.post("/incidents/{incident_id}/repair")
def repair_incident(incident_id: int):
    """Muse Spark proposes patches; each is tested in the sandbox until one passes (max 3 tries)."""
    incident = get_incident(incident_id)
    _require_status(incident, "diagnosed", "repair_failed")
    run = incident["run"]
    workflow = db.get_workflow_version(run["workflow_id"], run["workflow_version"])

    result = repair(workflow, run, incident["diagnosis"])
    db.update_incident(incident_id, status=result["status"], repair=result)
    return get_incident(incident_id)


@app.post("/runs/{run_id}/heal")
def heal_run(run_id: int):
    """Diagnose + repair in one call. Stops at 'needs_human' or 'awaiting_approval'."""
    incident = diagnose_run(run_id)
    if incident["status"] == "diagnosed":
        return repair_incident(incident["id"])
    return get_incident(incident["id"])


@app.post("/incidents/{incident_id}/approve")
async def approve_incident(incident_id: int):
    """A human approved the fix: save it as a new workflow version, then re-run the failed trigger for real."""
    incident = get_incident(incident_id)
    _require_status(incident, "awaiting_approval")
    run = incident["run"]
    current = db.get_workflow(run["workflow_id"])
    if current["version"] != run["workflow_version"]:
        raise HTTPException(409, "The workflow changed since this fix was tested; heal a new failed run instead")

    changes = "; ".join(incident["repair"]["changes"])
    saved = db.save_workflow(incident["repair"]["patched_workflow"], note=f"FlowGuard fix (incident #{incident_id}): {changes}")
    async with mock_client() as client:
        result = await run_workflow(saved, run["trigger"], client)
    replay = db.save_run(result)

    db.update_incident(incident_id, status="resolved" if replay["status"] == "success" else "approved_but_failing",
                       resolution_run_id=replay["id"])
    return get_incident(incident_id)


@app.post("/incidents/{incident_id}/reject")
def reject_incident(incident_id: int):
    incident = get_incident(incident_id)
    _require_status(incident, "awaiting_approval")
    db.update_incident(incident_id, status="rejected")
    return get_incident(incident_id)


# ---------- Chaos switches ----------

@app.get("/chaos")
def get_chaos():
    return chaos.get_all()


@app.post("/chaos/reset")
def reset_chaos():
    chaos.reset()
    return chaos.get_all()


@app.post("/chaos/{name}")
def set_chaos(name: str, req: ChaosRequest):
    if name not in chaos.SCENARIOS:
        raise HTTPException(404, f"No such chaos switch: {name}")
    chaos.set_switch(name, req.enabled)
    return chaos.get_all()


# ---------- What the fake apps received (handy for the demo) ----------

@app.get("/services/data")
def services_data():
    return {"crm_contacts": contacts, "slack_messages": messages}


@app.post("/services/reset")
def services_reset():
    reset_data()
    return {"crm_contacts": contacts, "slack_messages": messages}


@app.post("/demo/reset")
def demo_reset():
    """Start the demo over: workflow back to v1, no runs or incidents, switches off, apps empty."""
    db.reset_all()
    chaos.reset()
    reset_data()
    return {"status": "reset"}
