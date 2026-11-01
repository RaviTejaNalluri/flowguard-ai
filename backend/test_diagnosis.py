"""Day 1 check: can Muse return a structured diagnosis? Run: .venv/bin/python test_diagnosis.py"""

from llm import chat_json

# The shape we require the answer to have (JSON Schema).
DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["root_cause", "confidence"],
    "additionalProperties": False,
}

error = (
    "A workflow step failed with KeyError: 'email'. "
    "The CRM API now returns contact.email_address. Diagnose it."
)

result = chat_json([{"role": "user", "content": error}], "Diagnosis", DIAGNOSIS_SCHEMA)
print("Root cause:", result["root_cause"])
print("Confidence:", result["confidence"])
