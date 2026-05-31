#!/usr/bin/env python3
"""
CLI script to execute the Live Outbound Telephony Smoke Test.
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

from telephony.live_smoke_test import LiveTelephonySmokeTester, LiveSmokeTestConfig

async def main():
    parser = argparse.ArgumentParser(description="Run outbound telephony live smoke test.")
    parser.add_argument("--operator", required=True, help="Operator name/ID placing the test call")
    parser.add_argument("--to", help="Destination phone number (E.164)")
    parser.add_argument("--confirm", help="Must specify exact confirmation: 'LIVE CALL' to make a real call")
    parser.add_argument("--provider-config-id", help="Optional Provider Config ID")
    parser.add_argument("--campaign-id", help="Optional Campaign ID")
    parser.add_argument("--dry-run", action="store_true", help="Dry run check (no calling)")
    parser.add_argument("--no-place-call", dest="place_call", action="store_false", default=True, help="Only run readiness checks, do not place a call")
    
    # Wait until answered flag
    parser.add_argument("--wait-until-answered", action="store_true", default=True, help="Wait until call is answered")
    parser.add_argument("--no-wait-until-answered", action="store_false", dest="wait_until_answered", help="Do not wait until answered")

    # Krisp flag
    parser.add_argument("--krisp", action="store_true", default=True, help="Enable Krisp AI noise reduction")
    parser.add_argument("--no-krisp", action="store_false", dest="krisp", help="Disable Krisp AI noise reduction")
    
    parser.add_argument("--output-dir", default="data/live_smoke_tests", help="Directory where JSON and Markdown reports are saved")

    args = parser.parse_args()

    confirm_val = args.confirm or ""
    if args.place_call and not args.dry_run:
        if confirm_val != "LIVE CALL":
            print("ERROR: Confirmation --confirm \"LIVE CALL\" is required to place a live test call.", file=sys.stderr)
            sys.exit(1)

    print(f"Running outbound telephony smoke test (dry-run={args.dry_run}, place-call={args.place_call})...", file=sys.stderr, flush=True)

    tester = LiveTelephonySmokeTester()
    config = LiveSmokeTestConfig(
        phone_number=args.to,
        operator=args.operator.strip(),
        confirm=confirm_val,
        provider_config_id=args.provider_config_id,
        campaign_id=args.campaign_id,
        place_call=args.place_call,
        wait_until_answered=args.wait_until_answered,
        krisp_enabled=args.krisp,
        dry_run=args.dry_run,
        output_dir=args.output_dir
    )

    res = await tester.run(config)

    # Output clean JSON to stdout
    print(json.dumps(res.model_dump(mode="json"), indent=2))

    if not res.success:
        print(f"Smoke test failed. Failures: {res.failures}", file=sys.stderr)
        sys.exit(1)
    else:
        print("Smoke test completed successfully.", file=sys.stderr)
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
