"""Evaluation: how well does FlowGuard heal broken workflows? (calls Muse Spark for real)

  .venv/bin/python eval.py

For each case we break the world with chaos switches, run the workflow (it fails),
and let FlowGuard diagnose + repair it with no human help. A case PASSES when:
  - fixable failures: a patch passed the sandbox AND the patched workflow then
    succeeds in a separate live run (an independent check, not the agent's own test)
  - unfixable failures (revoked token): FlowGuard escalates to a human instead
    of attempting a repair

The AI is never told which switches were flipped. Results are written to eval_results.json.
"""

import asyncio
import json
import time
from pathlib import Path

import llm
from agent.diagnose import diagnose
from agent.repair import repair
from engine.runner import load_workflow, mock_client, run_workflow
from mocks import chaos
from mocks.services import reset_data

PAYMENTS = ["pay_001", "pay_002", "pay_003"]

# (chaos switches, expected outcome, expected diagnosis category or None if mixed)
SCENARIOS = [
    (["schema_drift"], "healed", "field_mapping"),
    (["missing_data"], "healed", "missing_input_data"),
    (["rate_limit"], "healed", "rate_limiting"),
    (["auth_expired"], "needs_human", "authentication"),
]
COMBOS = [
    (["schema_drift", "missing_data"], "healed", None),
    (["schema_drift", "rate_limit"], "healed", None),
    (["missing_data", "rate_limit"], "healed", None),
]
CASES = [(s, outcome, cat, p) for s, outcome, cat in SCENARIOS for p in PAYMENTS] + \
        [(s, outcome, cat, "pay_001") for s, outcome, cat in COMBOS]


def run_once(workflow: dict, payment_id: str) -> dict:
    async def go():
        async with mock_client() as client:
            return await run_workflow(workflow, {"payment_id": payment_id}, client)
    return asyncio.run(go())


def evaluate_case(switches: list[str], expected: str, expected_category: str | None, payment_id: str) -> dict:
    chaos.reset()
    reset_data()
    for s in switches:
        chaos.set_switch(s, True)
    workflow = load_workflow("customer_onboarding")
    before = dict(llm.usage)
    start = time.time()

    run = run_once(workflow, payment_id)
    assert run["status"] == "failed", f"{switches} did not break the workflow"

    diagnosis = diagnose(workflow, run)
    result = {"switches": switches, "payment_id": payment_id, "expected": expected,
              "category": diagnosis["category"], "category_ok": expected_category in (None, diagnosis["category"]),
              "attempts": 0, "changes": []}

    if not diagnosis["fixable_by_workflow_change"]:
        result["outcome"] = "needs_human"
    else:
        r = repair(workflow, run, diagnosis)
        result["attempts"] = len(r["attempts"])
        if r["status"] == "awaiting_approval":
            result["changes"] = r["changes"]
            live = run_once(r["patched_workflow"], payment_id)  # independent check
            result["outcome"] = "healed" if live["status"] == "success" else "sandbox_only"
        else:
            result["outcome"] = "repair_failed"

    result["passed"] = result["outcome"] == expected
    result["seconds"] = round(time.time() - start, 1)
    spent = {k: llm.usage[k] - before[k] for k in llm.usage}
    result["llm_calls"] = spent["calls"]
    result["cost_usd"] = round(llm.usage_cost(spent), 5)
    return result


def main():
    results = []
    for i, case in enumerate(CASES, 1):
        r = evaluate_case(*case)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{i:2}/{len(CASES)}] {mark}  {'+'.join(r['switches']):<26} {r['payment_id']}  "
              f"-> {r['outcome']:<12} attempts={r['attempts']}  {r['seconds']}s  ${r['cost_usd']:.4f}", flush=True)

    passed = sum(r["passed"] for r in results)
    fixable = [r for r in results if r["expected"] == "healed"]
    summary = {
        "model": llm.MODEL,
        "cases": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 3),
        "healed": f"{sum(r['outcome'] == 'healed' for r in fixable)}/{len(fixable)}",
        "escalated_correctly": f"{sum(r['outcome'] == 'needs_human' for r in results if r['expected'] == 'needs_human')}"
                               f"/{sum(r['expected'] == 'needs_human' for r in results)}",
        "diagnosis_category_accuracy": round(sum(r["category_ok"] for r in results) / len(results), 3),
        "first_attempt_fix_rate": round(sum(r["attempts"] == 1 and r["outcome"] == "healed" for r in fixable) / len(fixable), 3),
        "avg_repair_attempts": round(sum(r["attempts"] for r in fixable) / len(fixable), 2),
        "avg_seconds_per_incident": round(sum(r["seconds"] for r in results) / len(results), 1),
        "avg_cost_usd_per_incident": round(sum(r["cost_usd"] for r in results) / len(results), 5),
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 4),
    }

    print("\n" + json.dumps(summary, indent=2))
    out = Path(__file__).parent / "eval_results.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(f"\nSaved to {out.name}")


if __name__ == "__main__":
    main()
