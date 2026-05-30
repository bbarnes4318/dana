#!/usr/bin/env python3
"""CLI script to run Dana's Daily QA Miner."""

import argparse
import asyncio
import json
import sys
import logging
from datetime import datetime

from storage.repository import Repository
from training.daily_qa_miner import DailyQaMiner

# Configure logging to go to stderr so stdout is strictly clean JSON
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run Daily QA Miner for Dana.")
    parser.add_argument("--date", type=str, help="Mine a single date in YYYY-MM-DD format.")
    parser.add_argument("--from", dest="date_from", type=str, help="Start date in YYYY-MM-DD format.")
    parser.add_argument("--to", dest="date_to", type=str, help="End date in YYYY-MM-DD format.")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and write reports, but do not save HumanReviewItems.")
    parser.add_argument("--limit", type=int, help="Optional limit for recent items.")
    parser.add_argument("--output-dir", type=str, default="data/reports", help="Directory where reports are saved.")

    parser.add_argument("--data-dir", type=str, default=None, help="Directory for JSONL backend.")

    args = parser.parse_args()

    # Configure miner's repository
    import os
    data_dir = args.data_dir or os.environ.get("DANA_DATA_DIR", "data")
    repo = Repository(data_dir=data_dir)
    miner = DailyQaMiner(repository=repo)

    # Overload report writer to use output-dir if requested
    if args.output_dir:
        original_write_report = miner.write_daily_report
        def custom_write_report(result, analysis_result):
            return original_write_report(result, analysis_result, output_dir=args.output_dir)
        miner.write_daily_report = custom_write_report

    try:
        if args.date:
            result = await miner.mine_date(args.date, dry_run=args.dry_run)
        elif args.date_from and args.date_to:
            result = await miner.mine_range(args.date_from, args.date_to, dry_run=args.dry_run)
        else:
            err_json = {
                "status": "error",
                "message": "Either --date or both --from and --to must be specified."
            }
            sys.stderr.write(json.dumps(err_json) + "\n")
            return 1

        # Print clean JSON summary to stdout
        sys.stdout.write(json.dumps(result.model_dump(mode="json"), indent=2) + "\n")
        return 0

    except Exception as e:
        err_json = {
            "status": "error",
            "message": str(e)
        }
        sys.stderr.write(json.dumps(err_json) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
