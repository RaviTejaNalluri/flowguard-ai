"""Diagnosis agent: Muse Spark reads a failed run and explains the root cause.

The model only sees what a real on-call engineer would see: the workflow recipe
and the run record (what each step sent and got back). It is never told which
chaos switch was flipped, so it has to reason from the evidence.
"""

import json

from llm import chat_json

CATEGORIES = ["field_mapping", "missing_input_data", "rate_limiting", "authentication", "other"]

DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One plain-English sentence a non-engineer can understand."},
        "root_cause": {"type": "string", "description": "The technical root cause, naming the exact step and fields."},
        "category": {"type": "string", "enum": CATEGORIES},
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific facts from the run record that support the diagnosis.",
        },
        "fixable_by_workflow_change": {
            "type": "boolean",
            "description": "True if a workflow edit (mapping, defaults fallback, retry) can make it succeed "
                           "safely; false if a human must act (e.g. renew credentials).",
        },
        "confidence": {"type": "number", "description": "0.0 to 1.0"},
    },
    "required": ["summary", "root_cause", "category", "evidence", "fixable_by_workflow_change", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are FlowGuard, an expert site-reliability engineer for business automations.
A workflow run has failed. Diagnose the root cause using ONLY the evidence provided.

How workflows work:
- A workflow is a list of HTTP steps. Paths and bodies use {{templates}} that pull values from the
  trigger or from earlier steps' responses, e.g. {{steps.fetch_payment.customer.email}}.
  Text templates may use a formatting filter, e.g. {{steps.fetch_payment.amount | money}}.
- A step may have "defaults" (fill empty body fields) and "retry" (retry on HTTP 429/5xx).

Rules:
- Compare what the failing step SENT with what the service said it NEEDS.
- Look at earlier steps' responses to see whether the input data itself was bad.
- Quote concrete evidence (field names, status codes, error messages).
- Decide whether editing the workflow can make it succeed safely. The available workflow edits are:
  changing field names/mappings in a step body, adding a "defaults" fallback for an empty field,
  and adding a "retry" policy. Credential or permission problems cannot be fixed by editing the
  workflow; a human must act.
"""


def diagnose(workflow: dict, run: dict) -> dict:
    evidence = {
        "workflow": workflow,
        "failed_run": {
            "trigger": run["trigger"],
            "failed_step": run["failed_step"],
            "error": run["error"],
            "steps": run["steps"],
        },
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Diagnose this failure:\n" + json.dumps(evidence, indent=2)},
    ]
    return chat_json(messages, "Diagnosis", DIAGNOSIS_SCHEMA)
