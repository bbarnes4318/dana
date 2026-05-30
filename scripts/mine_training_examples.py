#!/usr/bin/env python3
"""CLI script to mine training examples from labeled training sources."""

import argparse
import asyncio
import json
import sys

from storage.repository import Repository
from training.example_miner import TrainingExampleMiner


async def main() -> None:
    parser = argparse.ArgumentParser(description="Mine training candidates from labeled training sources.")
    parser.add_argument("--source-id", type=str, help="ID of a specific training source to mine.")
    parser.add_argument("--recent", type=int, default=None, help="Number of recent training sources to mine.")

    args = parser.parse_args()

    if not args.source_id and args.recent is None:
        print("Error: Either --source-id or --recent must be provided.", file=sys.stderr)
        sys.exit(1)

    repo = Repository()
    miner = TrainingExampleMiner(repository=repo)

    try:
        if args.source_id:
            res = await miner.mine_source(args.source_id)
            summary = {
                "source_id": res.source_id,
                "candidates_created": res.candidates_created,
                "skipped_candidates": res.skipped_candidates,
                "compliance_review_items": res.compliance_review_items,
                "eval_case_candidates": res.eval_case_candidates,
                "training_example_candidates": res.training_example_candidates,
                "failure_candidates": res.failure_candidates,
                "review_item_ids": res.review_item_ids,
                "warnings": res.warnings,
            }
            print(json.dumps(summary, indent=2))
        else:
            results = await miner.mine_recent_sources(args.recent)
            total_created = sum(r.candidates_created for r in results)
            total_skipped = sum(r.skipped_candidates for r in results)
            total_compliance = sum(r.compliance_review_items for r in results)
            total_eval = sum(r.eval_case_candidates for r in results)
            total_training = sum(r.training_example_candidates for r in results)
            total_failure = sum(r.failure_candidates for r in results)

            all_ids = []
            all_warnings = []
            for r in results:
                all_ids.extend(r.review_item_ids)
                all_warnings.extend(r.warnings)

            summary = {
                "recent_count": len(results),
                "candidates_created": total_created,
                "skipped_candidates": total_skipped,
                "compliance_review_items": total_compliance,
                "eval_case_candidates": total_eval,
                "training_example_candidates": total_training,
                "failure_candidates": total_failure,
                "review_item_ids": all_ids,
                "warnings": all_warnings,
            }
            print(json.dumps(summary, indent=2))
    except Exception as e:
        print(f"Error executing mining: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
