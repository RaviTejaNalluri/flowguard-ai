"""Try the diagnosis agent for real (calls Muse Spark, about 1-2 cents per scenario).

  .venv/bin/python try_diagnose.py schema_drift
  .venv/bin/python try_diagnose.py all          # all 4 scenarios
"""

import asyncio
import sys

from agent.diagnose import diagnose
from engine.runner import load_workflow, mock_client, run_workflow
from mocks import chaos


async def failed_run(scenario: str) -> tuple[dict, dict]:
    chaos.reset()
    chaos.set_switch(scenario, True)
    workflow = load_workflow("customer_onboarding")
    async with mock_client() as client:
        return workflow, await run_workflow(workflow, {"payment_id": "pay_001"}, client)


def main():
    scenarios = list(chaos.SCENARIOS) if sys.argv[1:] == ["all"] else sys.argv[1:] or ["schema_drift"]
    for scenario in scenarios:
        workflow, run = asyncio.run(failed_run(scenario))
        print(f"\n=== {scenario}: run failed at {run['failed_step']} -> {run['error']}")

        d = diagnose(workflow, run)
        print(f"Summary:    {d['summary']}")
        print(f"Root cause: {d['root_cause']}")
        print(f"Category:   {d['category']}   Fixable by workflow change: {d['fixable_by_workflow_change']}"
              f"   Confidence: {d['confidence']}")
        for e in d["evidence"]:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
