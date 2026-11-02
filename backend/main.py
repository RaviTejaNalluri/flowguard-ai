"""FlowGuard API server. Run: .venv/bin/uvicorn main:app --reload

Then open http://127.0.0.1:8000/docs to try every endpoint in the browser.

How healing flows:
  run fails -> incident opened ("diagnosing"), plus any other runs that failed the same way
            -> Muse Spark diagnoses it      -> "diagnosed" or "needs_human"
            -> Muse Spark repairs it        -> "repairing" (attempts saved live) -> "awaiting_approval" / "repair_failed"
            -> a human approves             -> new workflow version, every affected run replayed -> "resolved"
With auto-heal on, a failed run starts this in the background with no button press.
"""

from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
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

settings = {"auto_heal": False}
_healing: set[int] = set()  # incidents with a background heal currently running


class RunRequest(BaseModel):
    payment_id: str = "pay_001"


class ChaosRequest(BaseModel):
    enabled: bool


class SettingsRequest(BaseModel):
    auto_heal: bool


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Settings ----------

@app.get("/settings")
def get_settings():
    return settings


@app.post("/settings")
def update_settings(req: SettingsRequest):
    settings["auto_heal"] = req.auto_heal
    return settings


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
async def run(workflow_id: str, req: RunRequest, background: BackgroundTasks):
    workflow = get_workflow(workflow_id)
    async with mock_client() as client:
        result = await run_workflow(workflow, {"payment_id": req.payment_id}, client)
    saved = db.save_run(result)

    if saved["status"] == "failed":
        if existing := db.find_open_incident(saved["workflow_id"], saved["failed_step"], saved["error"]):
            # Same failure as an incident we're already handling: group it, don't heal it twice.
            db.assign_runs([saved["id"]], existing["id"])
        elif settings["auto_heal"]:
            incident = _open_incident(saved)
            _schedule_heal(incident, background)
        saved = db.get_run(saved["id"])
    return saved


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


def _open_incident(run: dict) -> dict:
    """Return the run's incident, or open one that also claims every other run that failed the same way."""
    if run["status"] != "failed" or run["replay_of"]:
        raise HTTPException(400, "Only original failed runs can be healed")
    if run["incident_id"]:
        return db.get_incident(run["incident_id"])

    incident = db.create_incident(run, status="diagnosing")
    same = db.unassigned_failures(run["workflow_id"], run["failed_step"], run["error"])
    db.assign_runs([run["id"]] + [r["id"] for r in same if r["id"] != run["id"]], incident["id"])
    return incident


def _diagnose_step(incident_id: int) -> dict:
    incident = db.get_incident(incident_id)
    run = db.get_run(incident["run_id"])
    workflow = db.get_workflow_version(run["workflow_id"], run["workflow_version"])
    diagnosis = diagnose(workflow, run)
    status = "diagnosed" if diagnosis["fixable_by_workflow_change"] else "needs_human"
    return db.update_incident(incident_id, status=status, diagnosis=diagnosis)


def _repair_step(incident_id: int) -> dict:
    incident = db.get_incident(incident_id)
    run = db.get_run(incident["run_id"])
    workflow = db.get_workflow_version(run["workflow_id"], run["workflow_version"])

    def save_progress(attempts: list[dict]) -> None:
        db.update_incident(incident_id, repair={"status": "repairing", "attempts": attempts,
                                                "patched_workflow": None, "changes": []})

    save_progress([])
    db.update_incident(incident_id, status="repairing")
    result = repair(workflow, run, incident["diagnosis"], on_progress=save_progress)
    return db.update_incident(incident_id, status=result["status"], repair=result)


def _heal(incident_id: int) -> None:
    """Diagnose (if needed) then repair (if fixable). Errors are recorded on the incident."""
    _healing.add(incident_id)
    try:
        incident = db.get_incident(incident_id)
        if incident["status"] == "diagnosing":
            incident = _diagnose_step(incident_id)
        if incident["status"] == "diagnosed":
            _repair_step(incident_id)
    except Exception as e:  # e.g. the model API is unreachable
        db.update_incident(incident_id, status="error", error=str(e))
    finally:
        _healing.discard(incident_id)


def _schedule_heal(incident: dict, background: BackgroundTasks) -> None:
    if incident["id"] not in _healing and incident["status"] in ("diagnosing", "diagnosed"):
        _healing.add(incident["id"])
        background.add_task(_heal, incident["id"])


@app.post("/runs/{run_id}/heal")
def heal_run(run_id: int, background: BackgroundTasks):
    """Start healing in the background and return the incident right away (poll it for progress)."""
    incident = _open_incident(get_run(run_id))
    _schedule_heal(incident, background)
    return incident


@app.post("/runs/{run_id}/diagnose")
def diagnose_run(run_id: int):
    """Diagnose only, and wait for the answer. Each incident is diagnosed once."""
    incident = _open_incident(get_run(run_id))
    if incident["status"] == "diagnosing" and incident["id"] not in _healing:
        incident = _diagnose_step(incident["id"])
    return incident


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
    return {**incident, "run": db.get_run(incident["run_id"]), "resolution_run": resolution,
            "affected_runs": db.affected_runs(incident_id)}


def _require_status(incident: dict, *allowed: str) -> None:
    if incident["status"] not in allowed:
        raise HTTPException(409, f"Incident is '{incident['status']}', expected one of: {', '.join(allowed)}")


@app.post("/incidents/{incident_id}/repair")
def repair_incident(incident_id: int):
    """Muse Spark proposes patches; each is tested in the sandbox until one passes (max 3 tries). Waits for the result."""
    _require_status(get_incident(incident_id), "diagnosed", "repair_failed")
    _repair_step(incident_id)
    return get_incident(incident_id)


async def _recover(incident: dict, workflow: dict) -> dict:
    """Replay every affected run that hasn't been recovered yet, using `workflow`."""
    replays = []
    async with mock_client() as client:
        for original in db.affected_runs(incident["id"]):
            if original["recovered_by"]:
                continue
            result = await run_workflow(workflow, original["trigger"], client)
            replay = db.save_run({**result, "incident_id": incident["id"], "replay_of": original["id"]})
            replays.append({"run_id": original["id"], "replay_run_id": replay["id"],
                            "payment_id": original["trigger"]["payment_id"], "status": replay["status"]})

    affected = db.affected_runs(incident["id"])
    recovery = {"affected": len(affected), "recovered": sum(1 for r in affected if r["recovered_by"]),
                "replays": (incident.get("recovery") or {}).get("replays", []) + replays}
    primary = next((r for r in replays if r["run_id"] == incident["run_id"]), None)
    fields = {"recovery": recovery}
    if primary:
        fields["resolution_run_id"] = primary["replay_run_id"]
    return db.update_incident(incident["id"], **fields)


@app.post("/incidents/{incident_id}/approve")
async def approve_incident(incident_id: int):
    """A human approved the fix: save it as a new workflow version, then replay every affected run for real."""
    incident = get_incident(incident_id)
    _require_status(incident, "awaiting_approval")
    run = incident["run"]
    current = db.get_workflow(run["workflow_id"])
    if current["version"] != run["workflow_version"]:
        raise HTTPException(409, "The workflow changed since this fix was tested; heal a new failed run instead")

    changes = "; ".join(incident["repair"]["changes"])
    saved = db.save_workflow(incident["repair"]["patched_workflow"], note=f"FlowGuard fix (incident #{incident_id}): {changes}")
    incident = await _recover(incident, saved)
    all_ok = incident["recovery"]["recovered"] == incident["recovery"]["affected"]
    db.update_incident(incident_id, status="resolved" if all_ok else "approved_but_failing")
    return get_incident(incident_id)


@app.post("/incidents/{incident_id}/replay")
async def replay_incident(incident_id: int):
    """After a human fixed the cause outside FlowGuard (e.g. reconnected Slack), replay the affected runs."""
    incident = get_incident(incident_id)
    _require_status(incident, "needs_human")
    incident = await _recover(incident, db.get_workflow(incident["workflow_id"]))
    if incident["recovery"]["recovered"] == incident["recovery"]["affected"]:
        db.update_incident(incident_id, status="resolved")
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
    """Start the demo over: workflow back to v1, no runs or incidents, switches off, apps empty, auto-heal off."""
    db.reset_all()
    chaos.reset()
    reset_data()
    settings["auto_heal"] = False
    return {"status": "reset"}
