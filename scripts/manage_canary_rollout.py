#!/usr/bin/env python
"""CLI tool for managing prompt canary rollouts."""

from __future__ import annotations

import sys
import os
import json
import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any

# Configure logging to go only to stderr to preserve stdout for JSON outputs
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

from storage.repository import Repository
from deployment.canary import CanaryManager


def print_json_stdout(data: Any) -> None:
    """Print clean JSON to stdout and flush."""
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


def print_json_stderr(error_msg: str, **kwargs: Any) -> None:
    """Print clean JSON error details to stderr."""
    err_data = {"error": error_msg}
    err_data.update(kwargs)
    sys.stderr.write(json.dumps(err_data) + "\n")
    sys.stderr.flush()


async def run_cli() -> int:
    parser = argparse.ArgumentParser(description="Manage prompt canary rollouts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. check
    p_check = subparsers.add_parser("check", help="Check candidate eligibility")
    p_check.add_argument("--candidate-id", required=True, help="PromptVersion ID")

    # 2. create
    p_create = subparsers.add_parser("create", help="Create planned canary plan")
    p_create.add_argument("--candidate-id", required=True, help="PromptVersion ID")
    p_create.add_argument("--name", required=True, help="Experiment name")
    p_create.add_argument("--created-by", required=True, help="Author name")
    p_create.add_argument("--traffic", type=float, default=1.0, help="Initial traffic percentage")
    p_create.add_argument("--max-traffic", type=float, default=10.0, help="Max traffic percentage")

    # 3. approve
    p_approve = subparsers.add_parser("approve", help="Approve planned canary")
    p_approve.add_argument("--experiment-id", required=True, help="Experiment ID")
    p_approve.add_argument("--approved-by", required=True, help="Approver name")
    p_approve.add_argument("--notes", required=True, help="Approval notes")

    # 4. start
    p_start = subparsers.add_parser("start", help="Start approved canary")
    p_start.add_argument("--experiment-id", required=True, help="Experiment ID")
    p_start.add_argument("--started-by", required=True, help="Manager starting the canary")

    # 5. pause
    p_pause = subparsers.add_parser("pause", help="Pause running canary")
    p_pause.add_argument("--experiment-id", required=True, help="Experiment ID")
    p_pause.add_argument("--paused-by", required=True, help="Manager pausing the canary")
    p_pause.add_argument("--reason", required=True, help="Reason for pausing")

    # 6. rollback
    p_rollback = subparsers.add_parser("rollback", help="Roll back running/paused canary")
    p_rollback.add_argument("--experiment-id", required=True, help="Experiment ID")
    p_rollback.add_argument("--rolled-back-by", required=True, help="Manager rolling back")
    p_rollback.add_argument("--reason", required=True, help="Rollback reason")

    # 7. complete
    p_complete = subparsers.add_parser("complete", help="Complete running canary")
    p_complete.add_argument("--experiment-id", required=True, help="Experiment ID")
    p_complete.add_argument("--completed-by", required=True, help="Manager completing")
    p_complete.add_argument("--reason", required=True, help="Completion reason")

    # 8. cancel
    p_cancel = subparsers.add_parser("cancel", help="Cancel canary experiment")
    p_cancel.add_argument("--experiment-id", required=True, help="Experiment ID")
    p_cancel.add_argument("--cancelled-by", required=True, help="Manager cancelling")
    p_cancel.add_argument("--reason", required=True, help="Cancellation reason")

    # 9. list
    p_list = subparsers.add_parser("list", help="List experiments")
    p_list.add_argument("--status", help="Filter by status")
    p_list.add_argument("--limit", type=int, default=50, help="Max experiments to return")

    # 10. show
    p_show = subparsers.add_parser("show", help="Show details of an experiment")
    p_show.add_argument("--experiment-id", required=True, help="Experiment ID")

    # 11. decide
    p_decide = subparsers.add_parser("decide", help="Deterministic prompt routing decision")
    p_decide.add_argument("--prompt-name", required=True, help="Prompt name stem")
    p_decide.add_argument("--call-id", required=True, help="Call ID")
    p_decide.add_argument("--experiment-id", help="Experiment ID (optional)")
    p_decide.add_argument("--force-control", action="store_true", help="Force control version")
    p_decide.add_argument("--force-candidate", action="store_true", help="Force candidate version")

    # 12. report
    p_report = subparsers.add_parser("report", help="Generate reports")
    p_report.add_argument("--experiment-id", required=True, help="Experiment ID")
    p_report.add_argument("--output-dir", default="data/canary", help="Output directory")

    args = parser.parse_args()

    # Initialize Repo & Manager
    data_dir = os.environ.get("DANA_DATA_DIR", "data")
    repo = Repository(data_dir=data_dir)
    manager = CanaryManager(repository=repo)

    try:
        if args.command == "check":
            res = await manager.check_candidate_eligibility(args.candidate_id)
            print_json_stdout(res.to_dict())
            return 0

        elif args.command == "create":
            plan = await manager.create_canary_plan(
                candidate_prompt_version_id=args.candidate_id,
                experiment_name=args.name,
                created_by=args.created_by,
                traffic_percentage=args.traffic,
                max_traffic_percentage=args.max_traffic,
            )
            print_json_stdout(plan.to_dict())
            return 0

        elif args.command == "approve":
            res = await manager.approve_canary(
                experiment_id=args.experiment_id,
                approved_by=args.approved_by,
                approval_notes=args.notes,
            )
            print_json_stdout(res.to_dict())
            return 0

        elif args.command == "start":
            res = await manager.start_canary(
                experiment_id=args.experiment_id,
                started_by=args.started_by,
            )
            print_json_stdout(res.to_dict())
            return 0

        elif args.command == "pause":
            res = await manager.pause_canary(
                experiment_id=args.experiment_id,
                paused_by=args.paused_by,
                reason=args.reason,
            )
            print_json_stdout(res.to_dict())
            return 0

        elif args.command == "rollback":
            res = await manager.rollback_canary(
                experiment_id=args.experiment_id,
                rolled_back_by=args.rolled_back_by,
                reason=args.reason,
            )
            print_json_stdout(res.to_dict())
            return 0

        elif args.command == "complete":
            res = await manager.complete_canary(
                experiment_id=args.experiment_id,
                completed_by=args.completed_by,
                reason=args.reason,
            )
            print_json_stdout(res.to_dict())
            return 0

        elif args.command == "cancel":
            res = await manager.cancel_canary(
                experiment_id=args.experiment_id,
                cancelled_by=args.cancelled_by,
                reason=args.reason,
            )
            print_json_stdout(res.to_dict())
            return 0

        elif args.command == "list":
            experiments = await manager.list_canaries(status=args.status, limit=args.limit)
            print_json_stdout(experiments)
            return 0

        elif args.command == "show":
            exp = await manager.get_canary(args.experiment_id)
            print_json_stdout(exp)
            return 0

        elif args.command == "decide":
            decision = await manager.choose_prompt_for_call(
                prompt_name=args.prompt_name,
                call_id=args.call_id,
                experiment_id=args.experiment_id,
                force_control=args.force_control,
                force_candidate=args.force_candidate,
            )
            print_json_stdout(decision.to_dict())
            return 0

        elif args.command == "report":
            json_path, md_path = await manager.generate_canary_report(
                experiment_id=args.experiment_id,
                output_dir=args.output_dir,
            )
            print_json_stdout({
                "experiment_id": args.experiment_id,
                "report_json_path": json_path,
                "report_markdown_path": md_path,
            })
            return 0

    except Exception as exc:
        print_json_stderr(str(exc))
        return 1

    finally:
        await repo.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_cli()))
