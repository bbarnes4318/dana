#!/usr/bin/env python3
"""
Readiness checker script for Outbound Telephony.
Outputs JSON readiness state.
"""

import os
import sys
import json
import asyncio

# Ensure parent directory is on sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telephony.live_telephony_readiness import LiveTelephonyReadinessChecker

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Check Outbound Telephony configuration readiness.")
    parser.add_argument("--campaign-id", help="Optional Campaign ID to check")
    parser.add_argument("--provider-config-id", help="Optional Provider Config ID to check")
    args = parser.parse_args()

    checker = LiveTelephonyReadinessChecker()
    res = await checker.run(
        provider_config_id=args.provider_config_id,
        campaign_id=args.campaign_id
    )

    # Output JSON representation
    print(json.dumps(res.model_dump(), indent=2))

    if not res.ready:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
