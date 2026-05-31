#!/usr/bin/env python
import sys
import json
import argparse
import asyncio
from pathlib import Path

from storage.repository import Repository
from training.fine_tune_job_tracker import (
    FineTuneJobTracker,
    FineTuneJobTrackerConfig,
)

async def main():
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--dry-run", action="store_true", help="Perform dry run without persisting records.")
    parent_parser.add_argument("--notes", type=str, help="Custom notes or details.")
    parent_parser.add_argument("--output-dir", type=str, default="data/fine_tune_job_tracking", help="Output directory for reports.")
    parent_parser.add_argument("--json-only", action="store_true", help="Output clean JSON only.")

    parser = argparse.ArgumentParser(
        description="Manual tracking and approval system for fine-tuning jobs.",
        parents=[parent_parser]
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # check
    check_parser = subparsers.add_parser("check", parents=[parent_parser], help="Check start eligibility of fine-tuning job request.")
    check_parser.add_argument("--job-request-package", type=str)
    check_parser.add_argument("--job-request-review-item-id", type=str)
    check_parser.add_argument("--job-start-review-item-id", type=str)
    check_parser.add_argument("--provider", type=str, choices=["openai", "azure_openai", "generic"])
    
    # request-start
    req_parser = subparsers.add_parser("request-start", parents=[parent_parser], help="Submit a job start approval request.")
    req_parser.add_argument("--job-request-package", type=str)
    req_parser.add_argument("--job-request-review-item-id", type=str)
    req_parser.add_argument("--actor", type=str, required=True)
    req_parser.add_argument("--reason", type=str, required=True)
    
    # record-upload
    upload_parser = subparsers.add_parser("record-upload", parents=[parent_parser], help="Record a manual file upload reference.")
    upload_parser.add_argument("--job-start-review-item-id", type=str, required=True)
    upload_parser.add_argument("--provider-file-id", type=str, required=True)
    upload_parser.add_argument("--provider-validation-file-id", type=str)
    upload_parser.add_argument("--actor", type=str, required=True)
    upload_parser.add_argument("--reason", type=str, required=True)
    
    # record-job
    job_parser = subparsers.add_parser("record-job", parents=[parent_parser], help="Record a manual fine-tuning job start reference.")
    job_parser.add_argument("--job-start-review-item-id", type=str, required=True)
    job_parser.add_argument("--provider-job-id", type=str, required=True)
    job_parser.add_argument("--actor", type=str, required=True)
    job_parser.add_argument("--reason", type=str, required=True)
    
    # update-status
    status_parser = subparsers.add_parser("update-status", parents=[parent_parser], help="Manually update job status.")
    status_parser.add_argument("--tracking-id", type=str, required=True)
    status_parser.add_argument("--status", type=str, required=True)
    status_parser.add_argument("--actor", type=str, required=True)
    status_parser.add_argument("--reason", type=str, required=True)
    status_parser.add_argument("--provider-model-id", type=str)
    
    # show
    show_parser = subparsers.add_parser("show", parents=[parent_parser], help="Show details of a tracking record.")
    show_parser.add_argument("--tracking-id", type=str, required=True)
    
    # list
    list_parser = subparsers.add_parser("list", parents=[parent_parser], help="List tracking records.")
    list_parser.add_argument("--status", type=str)
    list_parser.add_argument("--limit", type=int, default=50)
    
    # report
    report_parser = subparsers.add_parser("report", parents=[parent_parser], help="Generate tracking reports.")
    report_parser.add_argument("--tracking-id", type=str, required=True)
    
    args = parser.parse_args()

    repo = Repository()
    tracker = FineTuneJobTracker(repository=repo)

    try:
        if args.command == "check":
            config = FineTuneJobTrackerConfig(
                job_request_package_path=args.job_request_package,
                job_request_review_item_id=args.job_request_review_item_id,
                job_start_review_item_id=args.job_start_review_item_id,
                provider=args.provider,
                output_dir=args.output_dir,
                notes=args.notes,
                dry_run=args.dry_run
            )
            result = await tracker.check_start_eligibility(config)
            res_dict = result.model_dump(mode="json")
            sys.stdout.write(json.dumps(res_dict, indent=2) + "\n")
            if result.eligible:
                sys.exit(0)
            else:
                sys.exit(1)
                
        elif args.command == "request-start":
            config = FineTuneJobTrackerConfig(
                job_request_package_path=args.job_request_package,
                job_request_review_item_id=args.job_request_review_item_id,
                actor=args.actor,
                reason=args.reason,
                notes=args.notes,
                output_dir=args.output_dir,
                dry_run=args.dry_run
            )
            result = await tracker.create_start_approval_request(config)
            res_dict = result.model_dump(mode="json")
            sys.stdout.write(json.dumps(res_dict, indent=2) + "\n")
            if result.success:
                sys.exit(0)
            else:
                sys.exit(1)
                
        elif args.command == "record-upload":
            config = FineTuneJobTrackerConfig(
                job_start_review_item_id=args.job_start_review_item_id,
                provider_file_id=args.provider_file_id,
                provider_validation_file_id=args.provider_validation_file_id,
                actor=args.actor,
                reason=args.reason,
                notes=args.notes,
                output_dir=args.output_dir,
                dry_run=args.dry_run
            )
            result = await tracker.record_manual_upload(config)
            res_dict = result.model_dump(mode="json")
            sys.stdout.write(json.dumps(res_dict, indent=2) + "\n")
            if result.success:
                sys.exit(0)
            else:
                sys.exit(1)
                
        elif args.command == "record-job":
            config = FineTuneJobTrackerConfig(
                job_start_review_item_id=args.job_start_review_item_id,
                provider_job_id=args.provider_job_id,
                actor=args.actor,
                reason=args.reason,
                notes=args.notes,
                output_dir=args.output_dir,
                dry_run=args.dry_run
            )
            result = await tracker.record_manual_job_start(config)
            res_dict = result.model_dump(mode="json")
            sys.stdout.write(json.dumps(res_dict, indent=2) + "\n")
            if result.success:
                sys.exit(0)
            else:
                sys.exit(1)
                
        elif args.command == "update-status":
            result = await tracker.update_manual_status(
                tracking_id=args.tracking_id,
                new_status=args.status,
                actor=args.actor,
                reason=args.reason,
                provider_model_id=args.provider_model_id
            )
            res_dict = result.model_dump(mode="json")
            sys.stdout.write(json.dumps(res_dict, indent=2) + "\n")
            if result.success:
                sys.exit(0)
            else:
                sys.exit(1)
                
        elif args.command == "show":
            result = await tracker.get_tracking_record(args.tracking_id)
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
            sys.exit(0)
            
        elif args.command == "list":
            result = await tracker.list_tracking_records(status=args.status, limit=args.limit)
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
            sys.exit(0)
            
        elif args.command == "report":
            json_path, md_path = await tracker.generate_tracking_report(args.tracking_id, output_dir=args.output_dir or parser.get_default("output_dir"))
            sys.stdout.write(json.dumps({
                "status": "success",
                "report_json_path": json_path,
                "report_markdown_path": md_path
            }, indent=2) + "\n")
            sys.exit(0)

    except Exception as e:
        sys.stderr.write(json.dumps({
            "status": "error",
            "message": str(e)
        }, indent=2) + "\n")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
