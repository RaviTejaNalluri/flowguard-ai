"""Fake Stripe, CRM and Slack APIs.

They behave like small versions of the real services, and the chaos switches in
chaos.py make them fail in realistic ways. Because we control them, the same
switch always causes the same failure, so the demo is reliable.
"""

from fastapi import FastAPI, HTTPException, Request

from mocks import chaos

app = FastAPI(title="FlowGuard mock services")

PAYMENTS = {
    "pay_001": {"id": "pay_001", "amount": 4900, "currency": "usd",
                "customer": {"name": "Ada Lovelace", "email": "ada@example.com"}},
    "pay_002": {"id": "pay_002", "amount": 12900, "currency": "usd",
                "customer": {"name": "Alan Turing", "email": "alan@example.com"}},
    "pay_003": {"id": "pay_003", "amount": 2500, "currency": "eur",
                "customer": {"name": "Grace Hopper", "email": "grace@example.com"}},
}

contacts: list[dict] = []
messages: list[dict] = []


def reset_data() -> None:
    contacts.clear()
    messages.clear()


# ---------- Stripe ----------

@app.get("/stripe/payments/{payment_id}")
def get_payment(payment_id: str):
    if payment_id not in PAYMENTS:
        raise HTTPException(404, f"No such payment: {payment_id}")
    payment = {**PAYMENTS[payment_id], "customer": dict(PAYMENTS[payment_id]["customer"])}
    if chaos.is_on("missing_data"):
        payment["customer"]["name"] = None
    return payment


# ---------- CRM ----------

@app.post("/crm/contacts", status_code=201)
async def create_contact(request: Request):
    body = await request.json()
    email_field = "email_address" if chaos.is_on("schema_drift") else "email"

    for field in ("name", email_field):
        if not body.get(field):
            raise HTTPException(422, f"Missing required field: '{field}'")

    contact = {"id": f"ct_{len(contacts) + 1:03d}", "name": body["name"], "email": body[email_field]}
    contacts.append(contact)
    return contact


# ---------- Slack ----------

@app.post("/slack/messages")
async def post_message(request: Request):
    if chaos.is_on("auth_expired"):
        raise HTTPException(401, "invalid_auth: token has been revoked")
    if chaos.is_on("rate_limit") and chaos.next_slack_call() % 3 != 0:
        raise HTTPException(429, "rate_limited: retry after 1 second")

    body = await request.json()
    for field in ("channel", "text"):
        if not body.get(field):
            raise HTTPException(422, f"Missing required field: '{field}'")

    message = {"ok": True, "ts": f"{len(messages) + 1}.000", "channel": body["channel"], "text": body["text"]}
    messages.append(message)
    return message
