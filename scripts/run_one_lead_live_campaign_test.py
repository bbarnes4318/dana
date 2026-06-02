#!/usr/bin/env python3
"""
CLI script to run a controlled one-lead live campaign dial test.
"""

import os
import sys
import json
import argparse
import asyncio

# Ensure parent directory is on sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import load_environment
load_environment()

from telephony.one_lead_live_campaign_test import ControlledCampaignTester, ControlledCampaignTestConfig


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Run controlled one-lead campaign test dial.")
    parser.add_argument("--to", required=True, help="Destination phone number (E.164)")
    parser.add_argument("--operator", required=True, help="Operator name/ID placing the test")
    parser.add_argument("--confirm", help="Must specify exact confirmation: 'LIVE CALL' to make a real call")
    parser.add_argument("--allow-now", action="store_true", help="Allow outside calling window check override")
    
    # Dry run option
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Simulate dialer checks without placing call")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Place a real live telephony call")
    parser.set_defaults(dry_run=False)

    parser.add_argument("--output-dir", default="data/telephony_reports", help="Directory for reports")

    # Prompt 38 parameters
    parser.add_argument("--require-turns", action="store_true", help="Require agent/prospect turns to succeed")
    parser.add_argument("--require-post-call-export", action="store_true", help="Require post-call export file to succeed")
    parser.add_argument("--run-intake-after-export", action="store_true", help="Trigger training intake after export")
    parser.add_argument("--min-agent-turns", type=int, default=1, help="Minimum agent turns required")
    parser.add_argument("--min-prospect-turns", type=int, help="Minimum prospect turns required (default 0 or 1 for interactive)")
    parser.add_argument("--interactive", action="store_true", help="Run in interactive mode (human answers)")

    args = parser.parse_args()

    # Required parameters check
    if not args.dry_run and args.confirm != "LIVE CALL":
        print(json.dumps({
            "success": False,
            "blocker_reason": "Confirmation 'LIVE CALL' is required for live dialing.",
            "errors": ["Confirmation 'LIVE CALL' is required for live dialing."]
        }, indent=2))
        return 1

    min_prospect = args.min_prospect_turns
    if min_prospect is None:
        min_prospect = 1 if args.interactive else 0

    tester = ControlledCampaignTester()
    config = ControlledCampaignTestConfig(
        to=args.to.strip(),
        operator=args.operator.strip(),
        confirm=args.confirm or "",
        allow_now=args.allow_now,
        dry_run=args.dry_run,
        output_dir=args.output_dir,
        require_turns=args.require_turns,
        require_post_call_export=args.require_post_call_export,
        run_intake_after_export=args.run_intake_after_export,
        min_agent_turns=args.min_agent_turns,
        min_prospect_turns=min_prospect,
        interactive=args.interactive
    )


    try:
        res = await tester.run(config)
        # Output clean JSON to stdout
        print(json.dumps(res.model_dump(mode="json"), indent=2))
        return 0 if res.success else 1
    except Exception as e:
        sys.stderr.write(f"Unexpected error: {e}\n")
        print(json.dumps({"success": False, "error": str(e)}, indent=2))
        return 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
