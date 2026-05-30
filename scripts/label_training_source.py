#!/usr/bin/env python
"""CLI Script to deterministically label a training source.

Outputs clean JSON to stdout.
"""

import argparse
import asyncio
import json
import sys

from storage.repository import Repository
from training.labeler import TranscriptLabeler


async def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministically label turns in a training source.")
    parser.add_argument("--source-id", required=True, help="ID of the training source to label")

    args = parser.parse_args()

    repo = Repository()
    labeler = TranscriptLabeler(repository=repo)

    try:
        result = await labeler.label_training_source(args.source_id)

        output = {
            "source_id": result.source_id,
            "total_turns": result.total_turns,
            "objection_counts": result.objection_counts,
            "stage_counts": result.stage_counts,
            "compliance_risk_counts": result.compliance_risk_counts,
            "good_example_candidates": result.good_example_candidates,
            "failure_candidates": result.failure_candidates,
        }

        print(json.dumps(output, indent=2))
        return 0

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
