#!/usr/bin/env python
import sys
import json
import argparse
import asyncio
from pathlib import Path

from storage.repository import Repository
from training.fine_tune_gate import FineTuneDatasetGate, FineTuneDatasetGateConfig

async def main():
    parser = argparse.ArgumentParser(description="Gate fine-tuning dataset compliance & quality.")
    parser.add_argument("--manifest", type=str, help="Path to manifest JSON")
    parser.add_argument("--train", type=str, help="Path to train JSONL")
    parser.add_argument("--validation", type=str, help="Path to validation JSONL")
    parser.add_argument("--output-dir", type=str, default="data/fine_tune_approvals", help="Output directory")
    parser.add_argument("--expected-format", type=str, choices=["openai_chat_jsonl", "generic_pairs_jsonl"], help="Expected dataset format")
    parser.add_argument("--min-train-examples", type=int, default=10, help="Min train examples")
    parser.add_argument("--min-validation-examples", type=int, default=1, help="Min validation examples")
    parser.add_argument("--max-duplicate-rate", type=float, default=0.02, help="Max duplicate rate")
    parser.add_argument("--max-redaction-token-rate", type=float, default=0.20, help="Max redaction rate")
    parser.add_argument("--fail-on-medium-warnings", action="store_true", help="Fail on medium warnings")
    parser.add_argument("--require-manifest", action="store_true", help="Require manifest file")
    parser.add_argument("--create-review-item", action="store_true", help="Create pending review item")
    parser.add_argument("--reviewer-request-name", type=str, help="Reviewer request name")
    parser.add_argument("--json-only", action="store_true", help="JSON only output")

    args = parser.parse_args()

    if not args.manifest and (not args.train or not args.validation):
        sys.stderr.write(json.dumps({"status": "error", "message": "Must provide either --manifest or both --train and --validation."}) + "\n")
        sys.exit(1)

    try:
        config = FineTuneDatasetGateConfig(
            manifest_path=args.manifest,
            train_path=args.train,
            validation_path=args.validation,
            output_dir=args.output_dir,
            expected_format=args.expected_format,
            min_train_examples=args.min_train_examples,
            min_validation_examples=args.min_validation_examples,
            max_duplicate_rate=args.max_duplicate_rate,
            max_redaction_token_rate=args.max_redaction_token_rate,
            fail_on_medium_warnings=args.fail_on_medium_warnings,
            require_manifest=args.require_manifest,
            create_review_item=args.create_review_item,
            reviewer_request_name=args.reviewer_request_name
        )

        repo = Repository()
        gate = FineTuneDatasetGate(repository=repo)
        result = await gate.run_gate(config)

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
