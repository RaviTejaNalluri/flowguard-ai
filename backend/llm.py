"""The only file that talks to Meta's Muse API.

Every other part of FlowGuard calls `chat()` or `chat_json()` from here, so if the
API details change we only fix this one file.

Docs: https://dev.meta.ai/docs/api-reference/chat-completions/create-chat-completion
"""

import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("MODEL_API_KEY", "")
BASE_URL = os.getenv("MUSE_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
MODEL = os.getenv("MUSE_MODEL", "muse-spark-1.3")


def _post(body: dict) -> dict:
    if not API_KEY or API_KEY == "your-key-here":
        raise RuntimeError("Put your real MODEL_API_KEY in backend/.env")

    resp = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": MODEL, **body},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def chat(messages: list[dict]) -> str:
    """Send chat messages to Muse and return the reply text."""
    return _post({"messages": messages})["choices"][0]["message"]["content"]


def chat_json(messages: list[dict], name: str, schema: dict) -> dict:
    """Ask Muse for a reply that is guaranteed to match `schema` (JSON Schema).

    Uses Meta's structured output: decoding itself is constrained, so the reply
    always parses and has the fields we asked for.
    """
    data = _post({
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        },
    })
    return json.loads(data["choices"][0]["message"]["content"])
