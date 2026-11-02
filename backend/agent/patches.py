"""The only edits FlowGuard's AI is allowed to make to a workflow.

Restricting the AI to three small, well-understood operations is a safety
feature: it cannot delete steps, call new URLs, or run arbitrary code. Every
patch is checked here before it is even tested.
"""

import copy

OPERATIONS_SCHEMA = {
    "anyOf": [
        {
            "type": "object",
            "description": "Rename a field in a step's request body (e.g. an API renamed it).",
            "properties": {
                "op": {"type": "string", "enum": ["rename_field"]},
                "step_id": {"type": "string"},
                "from_field": {"type": "string"},
                "to_field": {"type": "string"},
            },
            "required": ["op", "step_id", "from_field", "to_field"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "description": "Use a fallback value when a body field comes out empty.",
            "properties": {
                "op": {"type": "string", "enum": ["set_default"]},
                "step_id": {"type": "string"},
                "field": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["op", "step_id", "field", "value"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "description": "Retry the step on HTTP 429/5xx, up to max_attempts times in total (2-5).",
            "properties": {
                "op": {"type": "string", "enum": ["add_retry"]},
                "step_id": {"type": "string"},
                "max_attempts": {"type": "integer"},
            },
            "required": ["op", "step_id", "max_attempts"],
            "additionalProperties": False,
        },
    ]
}


class PatchError(ValueError):
    """The patch is invalid, so it is rejected before testing."""


def _find_step(workflow: dict, step_id: str) -> dict:
    for step in workflow["steps"]:
        if step["id"] == step_id:
            return step
    raise PatchError(f"No step named '{step_id}'")


def apply_patch(workflow: dict, operations: list[dict]) -> tuple[dict, list[str]]:
    """Return (patched copy of the workflow, human-readable list of changes). The original is untouched."""
    if not operations:
        raise PatchError("The patch has no operations")

    patched = copy.deepcopy(workflow)
    changes = []
    for op in operations:
        step = _find_step(patched, op["step_id"])
        body = step.get("body")

        if op["op"] == "rename_field":
            if not body or op["from_field"] not in body:
                raise PatchError(f"Step '{step['id']}' has no body field '{op['from_field']}'")
            if op["to_field"] in body:
                raise PatchError(f"Step '{step['id']}' already has a body field '{op['to_field']}'")
            body[op["to_field"]] = body.pop(op["from_field"])
            changes.append(f"{step['id']}: rename body field '{op['from_field']}' -> '{op['to_field']}'")

        elif op["op"] == "set_default":
            if not body or op["field"] not in body:
                raise PatchError(f"Step '{step['id']}' has no body field '{op['field']}'")
            if not op["value"].strip():
                raise PatchError("A default value cannot be empty")
            step.setdefault("defaults", {})[op["field"]] = op["value"]
            changes.append(f"{step['id']}: if '{op['field']}' is empty, use '{op['value']}'")

        elif op["op"] == "add_retry":
            if not 2 <= op["max_attempts"] <= 5:
                raise PatchError("max_attempts must be between 2 and 5")
            step["retry"] = {"max_attempts": op["max_attempts"]}
            changes.append(f"{step['id']}: retry up to {op['max_attempts']} attempts on HTTP 429/5xx")

        else:
            raise PatchError(f"Operation '{op['op']}' is not allowed")

    return patched, changes
