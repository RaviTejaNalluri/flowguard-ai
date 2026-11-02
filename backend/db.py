"""FlowGuard's memory: a small SQLite database file (flowguard.db).

Stores:
  workflows          the current version of each workflow recipe
  workflow_versions  every past version, so we can show "v1 -> v2 after the fix"
  runs               every workflow run with its full step-by-step record
  incidents          a failure (one or more runs with the same error) plus FlowGuard's
                     diagnosis, repair attempts, and the recovery of the affected runs
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
    finished_at      TEXT,
    incident_id      INTEGER,
    replay_of        INTEGER
);
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    status      TEXT NOT NULL,
    diagnosis   TEXT,
    repair      TEXT,
    resolution_run_id INTEGER,
    recovery    TEXT,
    error       TEXT,
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
        # Databases created by earlier versions of FlowGuard are missing these columns.
        _add_missing_columns(conn, "incidents", {"repair": "TEXT", "resolution_run_id": "INTEGER",
                                                 "recovery": "TEXT", "error": "TEXT"})
        _add_missing_columns(conn, "runs", {"incident_id": "INTEGER", "replay_of": "INTEGER"})
        for path in sorted(WORKFLOWS_DIR.glob("*.json")):
            definition = json.loads(path.read_text())
            exists = conn.execute("SELECT 1 FROM workflows WHERE id = ?", (definition["id"],)).fetchone()
            if not exists:
                _insert_version(conn, definition, note="Initial version")


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for column, kind in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def reset_all() -> None:
    """Delete everything and reload the starter workflows (used by the demo's Reset button)."""
    with _connect() as conn:
        conn.executescript(
            "DROP TABLE IF EXISTS incidents; DROP TABLE IF EXISTS runs; "
            "DROP TABLE IF EXISTS workflow_versions; DROP TABLE IF EXISTS workflows;"
        )
    init_db()


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
    """Save a run. Replays of a failed run set `replay_of` to the original run's id."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (workflow_id, workflow_version, status, failed_step, error, trigger, steps, "
            "started_at, finished_at, incident_id, replay_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run["workflow_id"], run["workflow_version"], run["status"], run["failed_step"], run["error"],
             json.dumps(run["trigger"]), json.dumps(run["steps"]), run["started_at"], run.get("finished_at"),
             run.get("incident_id"), run.get("replay_of")),
        )
    return get_run(cur.lastrowid)


# A failed run counts as recovered once a replay of it succeeded.
RUN_COLUMNS = """*, (SELECT r2.id FROM runs r2 WHERE r2.replay_of = runs.id AND r2.status = 'success'
                    ORDER BY r2.id LIMIT 1) AS recovered_by"""


def _row_to_run(row: sqlite3.Row) -> dict:
    run = dict(row)
    run["trigger"] = json.loads(run["trigger"])
    run["steps"] = json.loads(run["steps"])
    return run


def get_run(run_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(f"SELECT {RUN_COLUMNS} FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(workflow_id: str | None = None, limit: int = 50) -> list[dict]:
    query, params = f"SELECT {RUN_COLUMNS} FROM runs", []
    if workflow_id:
        query += " WHERE workflow_id = ?"
        params.append(workflow_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_run(r) for r in rows]


def unassigned_failures(workflow_id: str, failed_step: str, error: str) -> list[dict]:
    """Original (non-replay) failed runs with this exact error that no incident has claimed yet."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {RUN_COLUMNS} FROM runs WHERE workflow_id = ? AND status = 'failed' AND failed_step = ? "
            "AND error = ? AND incident_id IS NULL AND replay_of IS NULL ORDER BY id",
            (workflow_id, failed_step, error),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def assign_runs(run_ids: list[int], incident_id: int) -> None:
    with _connect() as conn:
        conn.executemany("UPDATE runs SET incident_id = ? WHERE id = ?", [(incident_id, r) for r in run_ids])


def affected_runs(incident_id: int) -> list[dict]:
    """The original failed runs grouped under an incident (not the replays)."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {RUN_COLUMNS} FROM runs WHERE incident_id = ? AND replay_of IS NULL ORDER BY id",
            (incident_id,),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


# ---------- Incidents ----------

# Incidents in these states still "own" their error: new identical failures are grouped into them.
OPEN_STATUSES = ("diagnosing", "diagnosed", "repairing", "awaiting_approval", "repair_failed", "needs_human")

def _row_to_incident(row: sqlite3.Row) -> dict:
    incident = dict(row)
    for field in ("diagnosis", "repair", "recovery"):
        incident[field] = json.loads(incident[field]) if incident[field] else None
    return incident


def create_incident(run: dict, status: str, diagnosis: dict | None = None) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO incidents (run_id, workflow_id, status, diagnosis, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run["id"], run["workflow_id"], status, json.dumps(diagnosis) if diagnosis else None, now, now),
        )
    return get_incident(cur.lastrowid)


def update_incident(incident_id: int, **fields) -> dict:
    """Update any incident columns, e.g. status, diagnosis, repair, recovery, error."""
    for field in ("diagnosis", "repair", "recovery"):
        if field in fields:
            fields[field] = json.dumps(fields[field])
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE incidents SET {assignments}, updated_at = ? WHERE id = ?",
                     (*fields.values(), _now(), incident_id))
    return get_incident(incident_id)


def get_incident(incident_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return _row_to_incident(row) if row else None


def get_incident_for_run(run_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE run_id = ?", (run_id,)).fetchone()
    return _row_to_incident(row) if row else None


def find_open_incident(workflow_id: str, failed_step: str, error: str) -> dict | None:
    """An unresolved incident whose original run failed the same way (used to group failures)."""
    placeholders = ", ".join("?" for _ in OPEN_STATUSES)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT incidents.* FROM incidents JOIN runs ON runs.id = incidents.run_id "
            f"WHERE incidents.workflow_id = ? AND runs.failed_step = ? AND runs.error = ? "
            f"AND incidents.status IN ({placeholders}) ORDER BY incidents.id DESC LIMIT 1",
            (workflow_id, failed_step, error, *OPEN_STATUSES),
        ).fetchone()
    return _row_to_incident(row) if row else None


def list_incidents(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_incident(r) for r in rows]
