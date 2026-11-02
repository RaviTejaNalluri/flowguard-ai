"""Repair agent: Muse Spark proposes a patch, the sandbox tests it, and failures are fed back.

The loop, at most MAX_ATTEMPTS times:
  1. Ask Muse Spark for a patch (only the safe operations in patches.py).
  2. Check the patch and apply it to a COPY of the workflow.
  3. Replay the exact failed trigger against the services in sandbox mode
     (nothing is really created in the CRM or posted to Slack).
  4. If it passes, stop and wait for a human to approve. If not, tell the
     model exactly what went wrong and ask for a different patch.
"""

import asyncio
import json
from collections.abc import Callable

from agent.patches import OPERATIONS_SCHEMA, PatchError, apply_patch
from engine.runner import mock_client, run_workflow
from llm import chat_json

MAX_ATTEMPTS = 3

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string", "description": "Why this patch fixes the root cause, in one or two sentences."},
        "operations": {"type": "array", "items": OPERATIONS_SCHEMA},
    },
    "required": ["explanation", "operations"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are FlowGuard's repair agent. A workflow run failed and has been diagnosed.
Propose the smallest patch that makes the workflow succeed again.

You may ONLY use these operations:
- rename_field: rename a key in a step's request body (keep its {{template}} value).
- set_default: a fallback value for a body field that comes out empty. Use an honest placeholder
  (e.g. "Unknown customer"), never invented personal data.
- add_retry: retry a step on HTTP 429/5xx (max_attempts 2-5 in total).

Your patch will be tested by replaying the failed run in a sandbox. If it fails, you will be shown
the result and asked to try again."""


def _sandbox_replay(workflow: dict, trigger: dict) -> dict:
    async def go():
        async with mock_client(sandbox=True) as client:
            return await run_workflow(workflow, trigger, client)
    return asyncio.run(go())


def repair(workflow: dict, run: dict, diagnosis: dict,
           on_progress: Callable[[list[dict]], None] | None = None) -> dict:
    """Run the repair loop. `on_progress(attempts)` is called after each attempt (for live UI updates)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(
            {"workflow": workflow, "failed_run": {k: run[k] for k in ("trigger", "failed_step", "error", "steps")},
             "diagnosis": diagnosis}, indent=2)},
    ]
    attempts = []

    for number in range(1, MAX_ATTEMPTS + 1):
        patch = chat_json(messages, "Patch", PATCH_SCHEMA)
        attempt = {"number": number, "patch": patch, "changes": [], "sandbox": None, "error": None}
        attempts.append(attempt)

        try:
            patched, attempt["changes"] = apply_patch(workflow, patch["operations"])
        except PatchError as e:
            attempt["error"] = f"Invalid patch: {e}"
        else:
            result = _sandbox_replay(patched, run["trigger"])
            attempt["sandbox"] = {k: result[k] for k in ("status", "failed_step", "error", "steps")}
            if result["status"] == "success":
                return {"status": "awaiting_approval", "attempts": attempts,
                        "patched_workflow": patched, "changes": attempt["changes"]}
            attempt["error"] = f"Sandbox replay failed at {result['failed_step']}: {result['error']}"

        if on_progress:
            on_progress(attempts)
        messages += [
            {"role": "assistant", "content": json.dumps(patch)},
            {"role": "user", "content": f"That patch did not work. {attempt['error']}\n"
                                        "Propose a different patch."},
        ]

    return {"status": "repair_failed", "attempts": attempts, "patched_workflow": None, "changes": []}
