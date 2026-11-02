"""FlowGuard API server. Run: .venv/bin/uvicorn main:app --reload

Then open http://127.0.0.1:8000/docs to try every endpoint in the browser.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
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
