#!/usr/bin/env python3
"""
CLI script to query and display the live campaign monitoring snapshot.
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

from storage.repository import Repository
from telephony.live_campaign_monitor import get_live_campaign_monitor_snapshot


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Query and display live campaign monitoring snapshot.")
    parser.add_argument("--campaign-id", help="Optional campaign ID to filter monitor results")
    parser.add_argument("--live", action="store_true", help="Continuously poll and print campaign metrics")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds (used with --live)")

    args = parser.parse_args()

    repo = Repository()

    if args.live:
        sys.stderr.write("Starting live campaign monitor polling. Press Ctrl+C to stop.\n")
        try:
            while True:
                snapshot = await get_live_campaign_monitor_snapshot(repo, campaign_id=args.campaign_id)
                # Print clean JSON snapshot to stdout
                print(json.dumps(snapshot, indent=2))
                # Print separator to stderr to keep stdout JSON clean
                sys.stderr.write("---\n")
                await asyncio.sleep(args.interval)
        except KeyboardInterrupt:
            sys.stderr.write("\nPolling stopped by operator.\n")
            return 0
    else:
        try:
            snapshot = await get_live_campaign_monitor_snapshot(repo, campaign_id=args.campaign_id)
            print(json.dumps(snapshot, indent=2))
            return 0
        except Exception as e:
            sys.stderr.write(f"Monitor error: {e}\n")
            print(json.dumps({"error": str(e)}, indent=2))
            return 1


def main() -> None:
    try:
        sys.exit(asyncio.run(main_async()))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
