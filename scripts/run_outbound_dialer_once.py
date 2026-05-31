#!/usr/bin/env python
import argparse
import asyncio
import json
import sys
from typing import Any

from ops.training_console import TrainingOperationsConsole


def print_result(res: Any) -> None:
    if hasattr(res, "model_dump"):
        print(json.dumps(res.model_dump(mode="json"), indent=2))
    elif isinstance(res, dict):
        print(json.dumps(res, indent=2))
    else:
        print(json.dumps({"result": str(res)}, indent=2))


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Run a single outbound dialer pacing tick.")
    parser.add_argument("--campaign-id", required=True, help="Campaign ID to run dialer tick for")
    
    # Dry run vs Mock vs Live options
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Simulate eligibility checks, do not write database attempts")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Write database attempts (mock mode by default, live if --live-mode set)")
    parser.set_defaults(dry_run=True)

    parser.add_argument("--live-mode", action="store_true", help="Trigger live LiveKit dialer (requires TELEPHONY_LIVE_MODE env keys)")
    parser.add_argument("--max-calls", type=int, help="Limit number of calls to place in this tick")
    parser.add_argument("--operator", default="system", help="Operator identity running the dialer tick")
    parser.add_argument("--force", action="store_true", help="Force tick, bypassing calling window constraints (for testing only)")

    args = parser.parse_args()
    console = TrainingOperationsConsole()

    try:
        res = await console.run_dialer_once(
            campaign_id=args.campaign_id,
            live_mode=args.live_mode,
            dry_run=args.dry_run,
            max_calls=args.max_calls,
            operator=args.operator,
            force=args.force,
        )
        print_result(res)
        return 0 if res.success else 1
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        print(json.dumps({"success": False, "error": str(e)}))
        return 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
