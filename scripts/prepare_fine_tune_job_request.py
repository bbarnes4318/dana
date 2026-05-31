#!/usr/bin/env python
import sys
import json
import argparse
import asyncio
from pathlib import Path

from storage.repository import Repository
from training.fine_tune_job_request import FineTuneJobRequestBuilder, FineTuneJobRequestConfig

async def main():
    parser = argparse.ArgumentParser(description="Prepare provider-safe fine-tuning job request package.")
    parser.add_argument("--approval-package", type=str, help="Path to Prompt 19 approval package JSON")
    parser.add_argument("--review-item-id", type=str, help="Dataset approval HumanReviewItem ID")
    parser.add_argument("--manifest", type=str, help="Path to Prompt 18 export manifest JSON")
    parser.add_argument("--train", type=str, help="Path to train JSONL")
    parser.add_argument("--validation", type=str, help="Path to validation JSONL")
    parser.add_argument("--provider", type=str, choices=["openai", "azure_openai", "generic"], default="generic", help="Target fine-tuning provider")
    parser.add_argument("--recommended-base-model", type=str, help="Recommended base model name")
    parser.add_argument("--suffix", type=str, default="dana-final-expense-safe", help="Suffix for fine-tuned model name")
    parser.add_argument("--output-dir", type=str, default="data/fine_tune_job_requests", help="Output directory")
    
    # Gating configs
    parser.add_argument("--no-require-human-approval", action="store_true", help="Disable human approval check requirement")
    parser.add_argument("--no-require-gate-passed", action="store_true", help="Disable dataset gate passed requirement")
    parser.add_argument("--no-require-hash-match", action="store_true", help="Disable file hash matching requirement")
    
    # Execution mode
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run package generation only")
    parser.add_argument("--create-review-item", action="store_true", help="Create pending HumanReviewItem for job request")
    parser.add_argument("--requester", type=str, help="Name of the request creator")
    parser.add_argument("--notes", type=str, help="Custom notes or justification for the request")
    parser.add_argument("--json-only", action="store_true", help="Only output JSON to stdout")

    args = parser.parse_args()

    # Input validation behavior:
    # Require one valid input path:
    # - --review-item-id
    # - OR --approval-package
    # - OR --manifest with --train and --validation
    # - OR --train and --validation for dry-run only
    valid_input = False
    if args.review_item_id:
        valid_input = True
    elif args.approval_package:
        valid_input = True
    elif args.manifest and args.train and args.validation:
        valid_input = True
    elif args.train and args.validation and args.dry_run:
        valid_input = True

    if not valid_input:
        sys.stderr.write(json.dumps({
            "status": "error",
            "message": "Must provide --review-item-id, --approval-package, both --train and --validation with --manifest, or both --train and --validation with --dry-run."
        }) + "\n")
        sys.exit(1)

    try:
        config = FineTuneJobRequestConfig(
            approval_package_path=args.approval_package,
            review_item_id=args.review_item_id,
            manifest_path=args.manifest,
            train_path=args.train,
            validation_path=args.validation,
            provider=args.provider,
            output_dir=args.output_dir,
            require_human_approval=not args.no_require_human_approval,
            require_gate_passed=not args.no_require_gate_passed,
            require_hash_match=not args.no_require_hash_match,
            recommended_base_model=args.recommended_base_model,
            suffix=args.suffix,
            dry_run=args.dry_run,
            create_review_item=args.create_review_item,
            requester=args.requester,
            notes=args.notes
        )

        repo = Repository()
        builder = FineTuneJobRequestBuilder(repository=repo)
        result = await builder.build_request_package(config)

        res_dict = result.model_dump() if hasattr(result, "model_dump") else result.__dict__
        sys.stdout.write(json.dumps(res_dict, indent=2) + "\n")

        if result.passed:
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        sys.stderr.write(json.dumps({"status": "error", "message": str(e)}) + "\n")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
