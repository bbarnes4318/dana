#!/usr/bin/env python3
"""CLI utility to execute the training intake scheduler.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training.intake_scheduler import TrainingIntakeScheduler, TrainingIntakeScheduleConfig


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Run Dana Training Intake Scheduler.")
    parser.add_argument("--mode", choices=["once", "daily", "hourly"], default="once", help="Schedule execution loop mode")
    parser.add_argument("--output-dir", default="data/intake_reports", help="Reports directory")
    parser.add_argument("--state-path", default="data/intake_reports/intake_state.json", help="Orchestration state JSON")
    
    # QA flags
    parser.add_argument("--daily-qa", action="store_true", default=True, help="Enable Daily QA Miner (default)")
    parser.add_argument("--no-daily-qa", action="store_false", dest="daily_qa", help="Disable Daily QA Miner")
    
    # Process steps
    parser.add_argument("--no-label", action="store_true", help="Skip downstream labeling")
    parser.add_argument("--no-mine", action="store_true", help="Skip downstream example mining")
    
    parser.add_argument("--limit", type=int, help="Limit number of files scanned per folder")
    parser.add_argument("--dry-run", action="store_true", help="Dry run simulation")
    parser.add_argument("--max-runs", type=int, default=1, help="Maximum number of scheduler iterations")
    parser.add_argument("--sleep-seconds", type=int, help="Seconds to sleep between loop runs")
    parser.add_argument("--lock-path", default="data/intake_reports/intake_scheduler.lock", help="Scheduler locking path")
    parser.add_argument("--json-only", action="store_true", help="Suppress logs")

    args = parser.parse_args()

    config = TrainingIntakeScheduleConfig(
        mode=args.mode,
        output_dir=args.output_dir,
        state_path=args.state_path,
        run_daily_qa=args.daily_qa,
        label_after_ingest=not args.no_label,
        mine_after_label=not args.no_mine,
        limit=args.limit,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep_seconds,
        max_runs=args.max_runs,
        lock_path=args.lock_path,
    )

    scheduler = TrainingIntakeScheduler()

    try:
        res = await scheduler.run(config)
        
        # Check exit code (exit 1 if lock acquisition failed or general scheduler failure occurred)
        exit_code = 0
        if res.error:
            exit_code = 1
        elif res.failed_runs > 0:
            exit_code = 1
            
        sys.stdout.write(json.dumps(res.model_dump(mode="json"), indent=2) + "\n")
        return exit_code
    except Exception as sched_err:
        sys.stderr.write(json.dumps({
            "error": str(sched_err),
            "status": "failed",
            "warnings": [f"CLI scheduler execution crashed: {sched_err}"]
        }, indent=2) + "\n")
        return 1


def main() -> None:
    try:
        code = asyncio.run(main_async())
        sys.exit(code)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
