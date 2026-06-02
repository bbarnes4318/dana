#!/usr/bin/env python3
"""
CLI script to run a real live multi-lead campaign batch test validation.
Enforces strict 1-3 call limit and requires --live flag.
"""

import os
import sys
import json
import argparse
import asyncio
from pathlib import Path
from typing import List

# Ensure parent directory is on sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import load_environment
load_environment()

from telephony.live_batch_campaign_test import ControlledBatchCampaignTester, LiveBatchTestConfig


def load_numbers_from_file(file_path: str) -> List[str]:
    """Parse phone numbers from JSON, CSV, or plain text files."""
    suffix = Path(file_path).suffix.lower()
    try:
        if suffix == '.json':
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [str(item).strip() for item in data]
                elif isinstance(data, dict):
                    for key in ["phone_numbers", "numbers", "phones"]:
                        if key in data and isinstance(data[key], list):
                            return [str(item).strip() for item in data[key]]
            raise ValueError("JSON file must be a list or a dictionary containing a list under 'phone_numbers'.")
        elif suffix == '.csv':
            import csv
            numbers = []
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    for val in row:
                        if val.strip():
                            numbers.append(val.strip())
            return numbers
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip()]
    except Exception as e:
        sys.stderr.write(f"Error reading numbers file: {e}\n")
        raise e


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Run real live campaign batch test validation.")
    parser.add_argument("--to", help="Comma-separated list of destination phone numbers (E.164)")
    parser.add_argument("--file", help="Path to JSON/CSV/TXT file containing destination phone numbers")
    parser.add_argument("--operator", required=True, help="Operator name/ID placing the test")
    parser.add_argument("--confirm", required=True, help="Must specify exact confirmation: 'LIVE CALL'")
    parser.add_argument("--allow-now", action="store_true", help="Allow outside calling window check override")
    
    # Enforce --live is required
    parser.add_argument("--live", action="store_true", required=True, help="Must specify --live to run real validation")

    args = parser.parse_args()

    sys.stderr.write("Initializing Real Live Batch Campaign Tester CLI...\n")

    # 1. Parse phone numbers
    phone_numbers = []
    if args.to:
        phone_numbers.extend([p.strip() for p in args.to.split(",") if p.strip()])
    if args.file:
        try:
            file_numbers = load_numbers_from_file(args.file)
            phone_numbers.extend(file_numbers)
        except Exception:
            print(json.dumps({
                "success": False,
                "failures": ["Failed to load numbers from file."]
            }, indent=2))
            return 1

    # Exactly 1 to 3 numbers limit check
    if not phone_numbers:
        sys.stderr.write("Error: No phone numbers provided.\n")
        print(json.dumps({
            "success": False,
            "failures": ["No phone numbers provided."]
        }, indent=2))
        return 1

    if len(phone_numbers) > 3:
        sys.stderr.write(f"Error: Real live validation is limited to a maximum of 3 leads (got {len(phone_numbers)}).\n")
        print(json.dumps({
            "success": False,
            "failures": [f"Batch size {len(phone_numbers)} exceeds real live validation limit of 3."]
        }, indent=2))
        return 1

    # Validate confirmation text
    if args.confirm != "LIVE CALL":
        sys.stderr.write("Error: Confirmation 'LIVE CALL' is required for live dialing.\n")
        print(json.dumps({
            "success": False,
            "failures": ["Confirmation 'LIVE CALL' is required to place a live campaign call."]
        }, indent=2))
        return 1

    tester = ControlledBatchCampaignTester()
    config = LiveBatchTestConfig(
        phone_numbers=phone_numbers,
        operator=args.operator.strip(),
        confirm=args.confirm,
        allow_now=args.allow_now,
        max_leads=3,
        hard_max_leads=3,
        require_turns=True,
        require_post_call_export=True,
        run_intake_after_export=True,
        min_agent_turns=1,
        min_prospect_turns=0,
        interactive=True,
        dry_run=False  # Must be False because --live is checked
    )

    try:
        sys.stderr.write(f"Executing real live campaign validation for {len(phone_numbers)} leads...\n")
        res = await tester.run(config)
        # Print clean JSON report to stdout
        print(json.dumps(res.model_dump(mode="json"), indent=2))
        return 0 if res.success else 1
    except Exception as e:
        sys.stderr.write(f"Unexpected CLI error: {e}\n")
        print(json.dumps({"success": False, "failures": [str(e)]}, indent=2))
        return 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
