"""Try the engine by hand.

  .venv/bin/python try_engine.py                 # healthy run
  .venv/bin/python try_engine.py schema_drift    # break something first
  .venv/bin/python try_engine.py schema_drift --full   # also print the full run record
  (switches: schema_drift, missing_data, rate_limit, auth_expired)
"""

import asyncio
import json
import sys

from engine.runner import load_workflow, mock_client, run_workflow
from mocks import chaos


async def main():
    full = "--full" in sys.argv
    for name in [a for a in sys.argv[1:] if a != "--full"]:
        chaos.set_switch(name, True)
        print(f"Chaos ON: {name} ({chaos.SCENARIOS[name]})")

    async with mock_client() as client:
        result = await run_workflow(load_workflow("customer_onboarding"), {"payment_id": "pay_001"}, client)

    for s in result["steps"]:
        icon = "OK  " if s["status"] == "success" else "FAIL"
        print(f"  [{icon}] {s['id']:<15} HTTP {s['response']['status']}")
    print(f"\nResult: {result['status'].upper()}" + (f"  ->  {result['error']}" if result["error"] else ""))

    if full:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
