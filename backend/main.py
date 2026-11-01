"""FlowGuard API server. Run: uv run uvicorn main:app --reload"""

from fastapi import FastAPI

app = FastAPI(title="FlowGuard AI")


@app.get("/health")
def health():
    return {"status": "ok"}
