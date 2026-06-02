#!/usr/bin/env python3
"""
CLI script to check live production readiness gate.
"""

import os
import sys
import json
import asyncio

# Ensure parent directory is on sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import load_environment
load_environment()

from telephony.live_production_readiness_gate import run_production_readiness_gate


async def main_async() -> int:
    try:
        res = await run_production_readiness_gate()
        print(json.dumps(res.model_dump(mode="json"), indent=2))
        return 0 if res.ready_for_small_canary else 1
    except Exception as e:
        sys.stderr.write(f"Error checking readiness gate: {e}\n")
        print(json.dumps({
            "ready_for_small_canary": False,
            "ready_for_production_scale": False,
            "passed_checks": [],
            "failed_checks": [f"Execution error: {e}"],
            "warnings": [],
            "next_steps": ["Check logs and fix exceptions in telephony/live_production_readiness_gate.py"]
        }, indent=2))
        return 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
