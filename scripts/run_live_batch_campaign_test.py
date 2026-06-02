#!/usr/bin/env python3
"""
CLI script to run a controlled multi-lead live campaign batch test.
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
                    # Check for common keys
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
            # Plain text line-by-line fallback
            with open(file_path, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip()]
    except Exception as e:
        sys.stderr.write(f"Error reading numbers file: {e}\n")
        raise e


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Run controlled multi-lead live campaign batch test.")
    parser.add_argument("--to", help="Comma-separated list of destination phone numbers (E.164)")
    parser.add_argument("--file", help="Path to JSON/CSV/TXT file containing destination phone numbers")
    parser.add_argument("--operator", required=True, help="Operator name/ID placing the test")
    parser.add_argument("--confirm", help="Must specify exact confirmation: 'LIVE CALL' to make a real call")
    parser.add_argument("--allow-now", action="store_true", help="Allow outside calling window check override")
    
    # Batch constraints
    parser.add_argument("--max-leads", type=int, default=3, help="Maximum leads to dial (default 3, hard limit 5)")
    
    # Mode toggles
    parser.add_argument("--interactive", action="store_true", help="Run in interactive mode (human answers)")
    parser.add_argument("--require-turns", action="store_true", help="Require agent/prospect turns to succeed")
    parser.add_argument("--require-post-call-export", action="store_true", help="Require post-call export file to succeed")
    parser.add_argument("--run-intake-after-export", action="store_true", help="Trigger training intake after export")
    
    # Dry-run toggle
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Simulate dialer checks without placing call")
    parser.add_argument("--live", dest="dry_run", action="store_false", help="Place real live telephony calls")
    parser.set_defaults(dry_run=True)

    args = parser.parse_args()

    # Log parameters to stderr
    sys.stderr.write("Initializing Safe Batch Campaign Tester CLI...\n")

    # 1. Parse phone numbers
    phone_numbers = []
    if args.to:
        phone_numbers.extend([p.strip() for p in args.to.split(",") if p.strip()])
    if args.file:
        try:
            file_numbers = load_numbers_from_file(args.file)
            phone_numbers.extend(file_numbers)
        except Exception:
            # Error printed to stderr by helper
            print(json.dumps({
                "success": False,
                "failures": ["Failed to load numbers from file."]
            }, indent=2))
            return 1

    # Apply defaults if flags not passed
    # Prompt requires: require_turns=True, require_post_call_export=True, run_intake_after_export=True by default for batch test config
    # We will honor configuration options but keep defaults standard
    require_turns = args.require_turns
    require_export = args.require_post_call_export
    run_intake = args.run_intake_after_export

    # If --live is specified, default to True for these compliance checks unless explicitly set to false
    # We override them to True by default as per LiveBatchTestConfig specifications
    # Wait, LiveBatchTestConfig lists:
    # - require_turns: bool = true
    # - require_post_call_export: bool = true
    # - run_intake_after_export: bool = true
    # So we'll default them to True in the config if not explicitly set.
    # To allow CLI to control it, let's check if the flags were supplied or we default them to True.
    # We can pass them as True if --live is supplied, or just use config defaults.
    
    tester = ControlledBatchCampaignTester()
    config = LiveBatchTestConfig(
        phone_numbers=phone_numbers,
        operator=args.operator.strip(),
        confirm=args.confirm or "",
        allow_now=args.allow_now,
        max_leads=args.max_leads,
        require_turns=require_turns or True,  # Keep true default
        require_post_call_export=require_export or True, # Keep true default
        run_intake_after_export=run_intake or True, # Keep true default
        interactive=args.interactive,
        dry_run=args.dry_run
    )

    try:
        sys.stderr.write(f"Executing batch campaign test for {len(phone_numbers)} leads...\n")
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
