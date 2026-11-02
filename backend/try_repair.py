"""Try the full heal loop for real: break -> run -> diagnose -> repair -> sandbox test.
Calls Muse Spark (a few cents per scenario). Nothing is approved or saved.

  .venv/bin/python try_repair.py schema_drift
  .venv/bin/python try_repair.py all
"""

import asyncio
import sys

from agent.diagnose import diagnose
from agent.repair import repair
from engine.runner import load_workflow, mock_client, run_workflow
from mocks import chaos


async def failed_run(workflow: dict) -> dict:
    async with mock_client() as client:
        return await run_workflow(workflow, {"payment_id": "pay_001"}, client)


def main():
    scenarios = list(chaos.SCENARIOS) if sys.argv[1:] == ["all"] else sys.argv[1:] or ["schema_drift"]
    for scenario in scenarios:
        chaos.reset()
        chaos.set_switch(scenario, True)
        workflow = load_workflow("customer_onboarding")
        run = asyncio.run(failed_run(workflow))
        print(f"\n=== {scenario}: failed at {run['failed_step']} -> {run['error']}")

        d = diagnose(workflow, run)
        print(f"Diagnosis: {d['summary']}")
        if not d["fixable_by_workflow_change"]:
            print("-> Needs a human. FlowGuard will not attempt a repair.")
            continue

        result = repair(workflow, run, d)
        for a in result["attempts"]:
            print(f"  Attempt {a['number']}: {a['patch']['explanation']}")
            for c in a["changes"]:
                print(f"     change: {c}")
            print(f"     sandbox: {'PASSED' if not a['error'] else a['error']}")
        print(f"-> {result['status'].upper()}")


if __name__ == "__main__":
    main()
