#!/usr/bin/env python
"""CLI Script to ingest training sources into the repository.

Outputs JSON to stdout containing ingestion metrics.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from storage.repository import Repository
from training.ingestion import TrainingIngestionService


async def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest raw training material into Dana's training system.")
    parser.add_argument("--type", required=True, help="Source type (e.g., call_transcript, youtube, manager_note, etc.)")
    parser.add_argument("--title", required=True, help="Title of the training source")
    parser.add_argument("--file", help="Path to file containing raw text, JSON, or JSONL")
    parser.add_argument("--content", help="Raw inline content string")
    parser.add_argument("--source-uri", help="Optional explicit source URI")

    args = parser.parse_args()

    if not args.file and not args.content:
        print("Error: Either --file or --content must be specified.", file=sys.stderr)
        return 1

    # Instantiate repository (automatically checks DATABASE_URL)
    repo = Repository()
    service = TrainingIngestionService(repository=repo)

    try:
        result = await service.ingest_source(
            source_type=args.type,
            title=args.title,
            content=args.content,
            file_path=args.file,
            source_uri=args.source_uri,
        )

        output = {
            "source_id": result.source_id,
            "duplicate_detected": result.duplicate_detected,
            "normalized_turn_count": result.normalized_turn_count,
            "redaction_count": result.redaction_count,
            "warnings": result.warnings,
        }

        # Print JSON to stdout for callers to parse
        print(json.dumps(output, indent=2))
        return 0

    except Exception as e:
        print(json.dumps({"error": str(e), "warnings": []}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
