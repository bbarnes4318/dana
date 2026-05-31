#!/usr/bin/env python3
"""CLI utility for running the automatic training intake orchestrator.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training.intake_orchestrator import (
    TrainingIntakeOrchestrator,
    TrainingIntakeConfig,
)


async def main_async() -> int:
    # Parent parser for shared arguments
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--type", dest="source_type", help="Source type override")
    parent_parser.add_argument("--path", help="Folder or directory path (used in folder mode)")
    parent_parser.add_argument("--file", help="File, manifest, or payload path")
    parent_parser.add_argument("--output-dir", default="data/intake_reports", help="Reports output directory")
    parent_parser.add_argument("--state-path", default="data/intake_reports/intake_state.json", help="Ingestion state JSON file")
    
    parent_parser.add_argument("--no-label", action="store_true", help="Skip downstream labeling process")
    parent_parser.add_argument("--no-mine", action="store_true", help="Skip downstream example mining process")
    parent_parser.add_argument("--daily-qa", action="store_true", help="Trigger daily QA miner run (daily mode only)")
    parent_parser.add_argument("--move-processed", action="store_true", help="Move successfully ingested files to processed directory")
    parent_parser.add_argument("--continue-on-error", action="store_true", default=True, help="Continue processing list on errors")
    parent_parser.add_argument("--fail-fast", action="store_true", help="Exit 1 immediately on any individual processing failure")
    parent_parser.add_argument("--limit", type=int, help="Limit number of files to process")
    parent_parser.add_argument("--dry-run", action="store_true", help="Simulate intake process without saving records")
    parent_parser.add_argument("--since", help="Since filter for Daily QA or file queries")
    parent_parser.add_argument("--json-only", action="store_true", help="Suppress all stdout logs except final JSON")

    parser = argparse.ArgumentParser(description="Dana Training Intake Orchestrator CLI.")
    
    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Intake command mode")
    
    # Post-call mode
    subparsers.add_parser("post-call", parents=[parent_parser], help="Process a single completed call payload file.")
    # Folder mode
    subparsers.add_parser("folder", parents=[parent_parser], help="Scan a directory for files to ingest.")
    # Manifest mode
    subparsers.add_parser("manifest", parents=[parent_parser], help="Ingest training materials from a manifest JSON file.")
    # Daily mode
    subparsers.add_parser("daily", parents=[parent_parser], help="Run daily automated scanning of all standard folders.")
    
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.stderr.write(json.dumps({"error": "No command provided. Choose from: post-call, folder, manifest, daily."}, indent=2) + "\n")
        return 1

    # Map command to mode
    mode = args.command.replace("-", "_")

    # Set up input_path
    input_path = None
    if mode == "post_call":
        input_path = args.file
    elif mode == "folder":
        input_path = args.path
    elif mode == "manifest":
        input_path = args.file

    config = TrainingIntakeConfig(
        mode=mode,
        source_type=args.source_type,
        input_path=input_path,
        manifest_path=args.file if mode == "manifest" else None,
        output_dir=args.output_dir,
        state_path=args.state_path,
        label_after_ingest=not args.no_label,
        mine_after_label=not args.no_mine,
        run_daily_qa=args.daily_qa,
        move_processed=args.move_processed,
        continue_on_error=not args.fail_fast,
        limit=args.limit,
        dry_run=args.dry_run,
        since=args.since,
        report_markdown=True,
    )

    orchestrator = TrainingIntakeOrchestrator()

    try:
        res = await orchestrator.run(config)
        
        # Check fail-fast exit code
        exit_code = 0
        if args.fail_fast and res.failed_count > 0:
            exit_code = 1
            
        sys.stdout.write(json.dumps(res.model_dump(mode="json"), indent=2) + "\n")
        return exit_code

    except Exception as e:
        sys.stderr.write(json.dumps({
            "error": str(e),
            "status": "failed",
            "warnings": [f"Intake pipeline execution crashed: {e}"]
        }, indent=2) + "\n")
        return 1


def main() -> None:
    try:
        # Standard loop run
        code = asyncio.run(main_async())
        sys.exit(code)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
