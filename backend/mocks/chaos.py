"""Chaos switches: break the fake services on purpose.

Each switch simulates a real-world way that business automations break.
"""

SCENARIOS = {
    "schema_drift": "CRM API v2 renamed the contact field `email` to `email_address`",
    "missing_data": "Stripe sends payments where the customer name is empty",
    "rate_limit": "Slack rejects 2 out of every 3 messages with HTTP 429",
    "auth_expired": "Slack token was revoked, so every message gets HTTP 401",
}

_state = {name: False for name in SCENARIOS}
_slack_calls = 0


def get_all() -> dict:
    return {name: {"enabled": _state[name], "description": desc} for name, desc in SCENARIOS.items()}


def is_on(name: str) -> bool:
    return _state[name]


def set_switch(name: str, enabled: bool) -> None:
    if name not in SCENARIOS:
        raise KeyError(name)
    _state[name] = enabled


def reset() -> None:
    global _slack_calls
    for name in _state:
        _state[name] = False
    _slack_calls = 0


def next_slack_call() -> int:
    """Count Slack calls so rate limiting is predictable (calls 1 and 2 fail, 3 succeeds, ...)."""
    global _slack_calls
    _slack_calls += 1
    return _slack_calls
