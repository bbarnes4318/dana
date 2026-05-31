#!/usr/bin/env python
"""Command line interface for Dana's continuous training console.

Outputs clean JSON to stdout for console commands.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from storage.repository import Repository
from ops.training_console import TrainingOperationsConsole, TrainingConsoleConfig


async def main() -> int:
    parser = argparse.ArgumentParser(description="Dana Continuous Training Console CLI")
    parser.add_argument("--json-only", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--output-dir", default="data/training_console")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Summary
    subparsers.add_parser("summary", help="Show training console summary metrics.")

    # Review
    review_parser = subparsers.add_parser("review", help="Coaching queue review actions.")
    review_sub = review_parser.add_subparsers(dest="review_command", required=True)

    list_parser = review_sub.add_parser("list", help="List review items.")
    list_parser.add_argument("--status", default="pending")
    list_parser.add_argument("--type", dest="item_type", default=None)
    list_parser.add_argument("--limit", type=int, default=50)

    show_parser = review_sub.add_parser("show", help="Show review item details.")
    show_parser.add_argument("--item-id", required=True)

    approve_parser = review_sub.add_parser("approve", help="Approve review item.")
    approve_parser.add_argument("--item-id", required=True)
    approve_parser.add_argument("--reviewer", required=True)
    approve_parser.add_argument("--notes", default=None)

    reject_parser = review_sub.add_parser("reject", help="Reject review item.")
    reject_parser.add_argument("--item-id", required=True)
    reject_parser.add_argument("--reviewer", required=True)
    reject_parser.add_argument("--notes", required=True)

    needs_changes_parser = review_sub.add_parser("needs-changes", help="Request review changes.")
    needs_changes_parser.add_argument("--item-id", required=True)
    needs_changes_parser.add_argument("--reviewer", required=True)
    needs_changes_parser.add_argument("--notes", required=True)

    # Intake
    intake_parser = subparsers.add_parser("intake", help="Intake pipeline actions.")
    intake_sub = intake_parser.add_subparsers(dest="intake_command", required=True)

    folder_parser = intake_sub.add_parser("folder", help="Run folder intake.")
    folder_parser.add_argument("--path", required=True)
    folder_parser.add_argument("--type", dest="source_type", default=None)

    manifest_parser = intake_sub.add_parser("manifest", help="Run manifest intake.")
    manifest_parser.add_argument("--file", dest="manifest_path", required=True)

    daily_parser = intake_sub.add_parser("daily", help="Run daily intake.")
    daily_parser.add_argument("--daily-qa", action="store_true", default=False)

    youtube_parser = intake_sub.add_parser("youtube", help="Import YouTube transcript.")
    youtube_parser.add_argument("--file")
    youtube_parser.add_argument("--content")
    youtube_parser.add_argument("--manifest")
    youtube_parser.add_argument("--title")
    youtube_parser.add_argument("--url", dest="source_url")
    youtube_parser.add_argument("--run-intake", action="store_true", default=False)

    # Scheduler
    scheduler_parser = subparsers.add_parser("scheduler", help="Intake scheduler actions.")
    scheduler_sub = scheduler_parser.add_subparsers(dest="scheduler_command", required=True)
    once_parser = scheduler_sub.add_parser("once", help="Run scheduler once.")
    once_parser.add_argument("--daily-qa", action="store_true", default=True)
    once_parser.add_argument("--limit", type=int, default=100)

    # Readiness
    readiness_parser = subparsers.add_parser("readiness", help="Continuous training readiness audit.")
    readiness_parser.add_argument("--strict", action="store_true", default=True)
    readiness_parser.add_argument("--fail-on-medium", action="store_true", default=False)

    # Reports
    reports_parser = subparsers.add_parser("reports", help="Reports management.")
    reports_sub = reports_parser.add_subparsers(dest="reports_command", required=True)

    rep_list_parser = reports_sub.add_parser("list", help="List reports.")
    rep_list_parser.add_argument("--type", dest="report_type", default=None)
    rep_list_parser.add_argument("--limit", type=int, default=50)

    rep_show_parser = reports_sub.add_parser("show", help="Show report contents.")
    rep_show_parser.add_argument("--path", required=True)

    args = parser.parse_args()

    # Console config mapping
    config = TrainingConsoleConfig(
        output_dir=args.output_dir,
        json_only=args.json_only,
        dry_run=args.dry_run,
    )

    repo = Repository()
    console = TrainingOperationsConsole(repository=repo)

    try:
        if args.command == "summary":
            summary = await console.get_summary(limit=config.default_limit)
            sys.stdout.write(json.dumps(summary.model_dump(mode="json"), indent=2) + "\n")
            return 0

        elif args.command == "review":
            if args.review_command == "list":
                res = await console.list_review_items(
                    status=args.status, item_type=args.item_type, limit=args.limit
                )
            elif args.review_command == "show":
                res = await console.show_review_item(item_id=args.item_id)
            elif args.review_command == "approve":
                res = await console.approve_review_item(
                    item_id=args.item_id, reviewer=args.reviewer, notes=args.notes
                )
            elif args.review_command == "reject":
                res = await console.reject_review_item(
                    item_id=args.item_id, reviewer=args.reviewer, notes=args.notes
                )
            elif args.review_command == "needs-changes":
                res = await console.request_review_changes(
                    item_id=args.item_id, reviewer=args.reviewer, notes=args.notes
                )
            else:
                raise ValueError(f"Unknown review subcommand: {args.review_command}")

        elif args.command == "intake":
            if args.intake_command == "folder":
                res = await console.run_intake(
                    mode="folder",
                    path=args.path,
                    source_type=args.source_type,
                    dry_run=config.dry_run,
                )
            elif args.intake_command == "manifest":
                res = await console.run_intake(
                    mode="manifest",
                    manifest_path=args.manifest_path,
                    dry_run=config.dry_run,
                )
            elif args.intake_command == "daily":
                res = await console.run_intake(
                    mode="daily",
                    daily_qa=args.daily_qa,
                    dry_run=config.dry_run,
                )
            elif args.intake_command == "youtube":
                res = await console.import_youtube(
                    file=args.file,
                    content=args.content,
                    manifest=args.manifest,
                    title=args.title,
                    source_url=args.source_url,
                    run_intake=args.run_intake,
                    dry_run=config.dry_run,
                )
            else:
                raise ValueError(f"Unknown intake subcommand: {args.intake_command}")

        elif args.command == "scheduler":
            if args.scheduler_command == "once":
                res = await console.run_scheduler_once(
                    daily_qa=args.daily_qa,
                    dry_run=config.dry_run,
                    limit=args.limit,
                )
            else:
                raise ValueError(f"Unknown scheduler subcommand: {args.scheduler_command}")

        elif args.command == "readiness":
            res = console.run_readiness(
                strict=args.strict,
                fail_on_medium=args.fail_on_medium,
            )

        elif args.command == "reports":
            if args.reports_command == "list":
                res = console.list_reports(
                    report_type=args.report_type,
                    limit=args.limit,
                )
            elif args.reports_command == "show":
                res = console.read_report(path=args.path)
            else:
                raise ValueError(f"Unknown reports subcommand: {args.reports_command}")

        else:
            raise ValueError(f"Unknown command: {args.command}")

        # Final output
        sys.stdout.write(json.dumps(res.model_dump(mode="json"), indent=2) + "\n")
        return 0 if res.success else 1

    except Exception as e:
        err_json = {
            "action": args.command,
            "success": False,
            "message": "CLI command execution crashed.",
            "error": str(e),
            "warnings": [],
        }
        sys.stderr.write(json.dumps(err_json, indent=2) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
