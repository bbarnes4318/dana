#!/usr/bin/env python
"""CLI Script to rebuild Dana's RAG retrieval documents from approved TrainingExamples."""

import argparse
import asyncio
import json
import sys

from storage.repository import Repository
from training.rag_builder import TrainingRagDocumentBuilder


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert approved training examples into RAG documents."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all", action="store_true", help="Build RAG documents for all approved examples."
    )
    group.add_argument(
        "--recent", type=int, help="Build RAG documents for the N most recent approved examples."
    )
    group.add_argument(
        "--example-id", type=str, help="Build RAG document for a specific training example ID."
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="Evaluate eligibility without writing to vector store."
    )
    parser.add_argument(
        "--approved-only",
        type=str,
        default="true",
        help="Only convert approved examples (default: true).",
    )

    args = parser.parse_args()

    # Parse approved-only flag
    approved_only = args.approved_only.lower() in ("true", "yes", "1")

    # Instantiate repository and builder
    repo = Repository()
    builder = TrainingRagDocumentBuilder(repository=repo)

    try:
        if args.example_id:
            result = await builder.build_from_training_example(
                example_id=args.example_id, dry_run=args.dry_run
            )
        else:
            limit = args.recent if args.recent is not None else None
            result = await builder.build_from_approved_examples(
                limit=limit, dry_run=args.dry_run, approved_only=approved_only
            )

        # Output ONLY clean JSON to stdout
        output_data = result.model_dump(mode="json")
        print(json.dumps(output_data, indent=2))
        return 0

    except Exception as e:
        # Return error format
        err_output = {
            "total_training_examples_scanned": 0,
            "eligible_examples": 0,
            "skipped_examples": 0,
            "documents_created": 0,
            "documents_upserted": 0,
            "vector_store_count": 0,
            "skipped_reasons": {},
            "document_ids": [],
            "warnings": [f"Error occurred: {e}"],
        }
        print(json.dumps(err_output, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
