#!/usr/bin/env python3
"""
CLI script to place a single manual test call using LiveKit SIP outbound trunk.
Requires explicit confirmation "LIVE CALL" to place a real call.
"""

import os
import sys
import json
import argparse
import asyncio

# Ensure parent directory is on sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telephony.live_call_tester import LiveCallTester, LiveCallTestConfig
from telephony.livekit_adapter import LiveKitOutboundAdapter

async def main():
    parser = argparse.ArgumentParser(description="Place a single live outbound test call.")
    parser.add_argument("--to", required=True, help="Destination phone number (E.164)")
    parser.add_argument("--operator", required=True, help="Operator name/ID placing the test call")
    parser.add_argument("--campaign-id", help="Optional Campaign ID to link with metadata/safety checks")
    parser.add_argument("--provider-config-id", help="Optional Provider Config ID")
    
    # Wait until answered flag
    parser.add_argument("--wait-until-answered", action="store_true", default=True, help="Wait until call is answered")
    parser.add_argument("--no-wait-until-answered", action="store_false", dest="wait_until_answered", help="Do not wait until answered")

    # Krisp flag
    parser.add_argument("--krisp", action="store_true", default=True, help="Enable Krisp AI noise reduction")
    parser.add_argument("--no-krisp", action="store_false", dest="krisp", help="Disable Krisp AI noise reduction")

    # Confirmation
    parser.add_argument("--confirm", required=True, help="Must specify exact confirmation: 'LIVE CALL'")

    args = parser.parse_args()

    # Verify confirmation text
    if args.confirm != "LIVE CALL":
        print("\nERROR: Exact confirmation string '--confirm \"LIVE CALL\"' is required to execute real phone calls.\n", file=sys.stderr)
        sys.exit(1)

    # Check environment live mode flags
    adapter = LiveKitOutboundAdapter()
    if not adapter.live_mode_enabled():
        print("\nERROR: Live mode is disabled in this environment.", file=sys.stderr)
        print("Please configure environment variables first:\n  TELEPHONY_LIVE_MODE=true\n  DANA_ENABLE_OUTBOUND_DIALER=true\n", file=sys.stderr)
        sys.exit(1)

    tester = LiveCallTester()
    config = LiveCallTestConfig(
        phone_number=args.to.strip(),
        campaign_id=args.campaign_id,
        provider_config_id=args.provider_config_id,
        live_mode=True,
        wait_until_answered=args.wait_until_answered,
        krisp_enabled=args.krisp,
        operator=args.operator.strip(),
    )

    print(f"Executing manual live test call to {args.to} under operator {args.operator}...", flush=True)
    res = await tester.place_test_call(config)

    # Output JSON representation
    print(json.dumps(res.model_dump(), indent=2))

    if not res.success:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
