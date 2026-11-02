"""FlowGuard's memory: a small SQLite database file (flowguard.db).

Stores:
  workflows          the current version of each workflow recipe
  workflow_versions  every past version, so we can show "v1 -> v2 after the fix"
  runs               every workflow run with its full step-by-step record
  incidents          a failed run plus FlowGuard's diagnosis (and later its repair)
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from engine.runner import WORKFLOWS_DIR

DB_PATH = os.getenv("FLOWGUARD_DB", str(Path(__file__).parent / "flowguard.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id          TEXT PRIMARY KEY,
    definition  TEXT NOT NULL,
    version     INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_versions (
    workflow_id TEXT NOT NULL,
    version     INTEGER NOT NULL,
    definition  TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (workflow_id, version)
);
CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id      TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    status           TEXT NOT NULL,
    failed_step      TEXT,
    error            TEXT,
    trigger          TEXT NOT NULL,
    steps            TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    status      TEXT NOT NULL,
    diagnosis   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """Create tables and load the starter workflows from engine/workflows/ (first time only)."""
    with _connect() as conn:
        conn.executescript(SCHEMA)
        for path in sorted(WORKFLOWS_DIR.glob("*.json")):
            definition = json.loads(path.read_text())
            exists = conn.execute("SELECT 1 FROM workflows WHERE id = ?", (definition["id"],)).fetchone()
            if not exists:
                _insert_version(conn, definition, note="Initial version")


def _insert_version(conn: sqlite3.Connection, definition: dict, note: str) -> None:
    text = json.dumps(definition)
    now = _now()
    conn.execute(
        "INSERT OR REPLACE INTO workflows (id, definition, version, updated_at) VALUES (?, ?, ?, ?)",
        (definition["id"], text, definition["version"], now),
    )
    conn.execute(
        "INSERT INTO workflow_versions (workflow_id, version, definition, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (definition["id"], definition["version"], text, note, now),
    )


# ---------- Workflows ----------

def list_workflows() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT definition FROM workflows ORDER BY id").fetchall()
    return [json.loads(r["definition"]) for r in rows]


def get_workflow(workflow_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT definition FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
    return json.loads(row["definition"]) if row else None


def save_workflow(definition: dict, note: str) -> dict:
    """Save a changed workflow as a new version (e.g. after an approved fix)."""
    current = get_workflow(definition["id"])
    definition = {**definition, "version": (current["version"] if current else 0) + 1}
    with _connect() as conn:
        _insert_version(conn, definition, note)
    return definition


def get_workflow_version(workflow_id: str, version: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT definition FROM workflow_versions WHERE workflow_id = ? AND version = ?",
            (workflow_id, version),
        ).fetchone()
    return json.loads(row["definition"]) if row else None


def list_versions(workflow_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT version, note, created_at, definition FROM workflow_versions "
            "WHERE workflow_id = ? ORDER BY version",
            (workflow_id,),
        ).fetchall()
    return [{**dict(r), "definition": json.loads(r["definition"])} for r in rows]


# ---------- Runs ----------

def save_run(run: dict) -> dict:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (workflow_id, workflow_version, status, failed_step, error, trigger, steps, "
            "started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run["workflow_id"], run["workflow_version"], run["status"], run["failed_step"], run["error"],
             json.dumps(run["trigger"]), json.dumps(run["steps"]), run["started_at"], run.get("finished_at")),
        )
    return {**run, "id": cur.lastrowid}


def _row_to_run(row: sqlite3.Row) -> dict:
    run = dict(row)
    run["trigger"] = json.loads(run["trigger"])
    run["steps"] = json.loads(run["steps"])
    return run


def get_run(run_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(workflow_id: str | None = None, limit: int = 50) -> list[dict]:
    query, params = "SELECT * FROM runs", []
    if workflow_id:
        query += " WHERE workflow_id = ?"
        params.append(workflow_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_run(r) for r in rows]


# ---------- Incidents ----------

def _row_to_incident(row: sqlite3.Row) -> dict:
    incident = dict(row)
    incident["diagnosis"] = json.loads(incident["diagnosis"]) if incident["diagnosis"] else None
    return incident


def create_incident(run: dict, status: str, diagnosis: dict) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO incidents (run_id, workflow_id, status, diagnosis, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run["id"], run["workflow_id"], status, json.dumps(diagnosis), now, now),
        )
    return get_incident(cur.lastrowid)


def get_incident(incident_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return _row_to_incident(row) if row else None


def get_incident_for_run(run_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE run_id = ?", (run_id,)).fetchone()
    return _row_to_incident(row) if row else None


def list_incidents(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_incident(r) for r in rows]
