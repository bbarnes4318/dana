#!/usr/bin/env python3
"""CLI script to review pending training items (approve, reject, request changes)."""

import argparse
import asyncio
import json
import sys
from typing import Any

from storage.repository import Repository
from training.review_service import HumanReviewService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Human review CLI tool.")
    
    # Action modes (mutually exclusive group is good, or check manually)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List pending review items.")
    group.add_argument("--show", type=str, metavar="ITEM_ID", help="Show full detail of a review item.")
    group.add_argument("--approve", type=str, metavar="ITEM_ID", help="Approve a review item.")
    group.add_argument("--reject", type=str, metavar="ITEM_ID", help="Reject a review item.")
    group.add_argument("--needs-changes", type=str, metavar="ITEM_ID", help="Request changes on a review item.")

    # Action parameters
    parser.add_argument("--type", type=str, help="Filter by item type (only used with --list).")
    parser.add_argument("--limit", type=int, default=50, help="Limit number of items returned (only used with --list).")
    parser.add_argument("--reviewer", type=str, help="Reviewer name (required for approve, reject, needs-changes).")
    parser.add_argument("--notes", type=str, help="Review notes (optional for approve, required for reject and needs-changes).")

    args = parser.parse_args()

    repo = Repository()
    service = HumanReviewService(repository=repo)

    try:
        if args.list:
            items = await service.list_pending_review_items(item_type=args.type, limit=args.limit)
            formatted_items = []
            for item in items:
                short_summary = item.get("payload", {}).get("why_this_matters", "")
                formatted_items.append({
                    "id": item.get("id"),
                    "item_type": item.get("item_type"),
                    "status": item.get("status"),
                    "created_at": item.get("created_at"),
                    "short summary": short_summary,
                    "short_summary": short_summary  # Include both to be safe
                })
            
            output = {
                "count": len(formatted_items),
                "items": formatted_items
            }
            print(json.dumps(output, indent=2))

        elif args.show:
            item = await service.get_review_item(args.show)
            # Use model_dump or serialize correctly
            # Since repository returns it as a dict (via model_dump in save/retrieve), it's a dict.
            print(json.dumps(item, indent=2, default=str))

        elif args.approve:
            if not args.reviewer:
                raise ValueError("Reviewer is required.")
            res = await service.approve_review_item(args.approve, args.reviewer, args.notes)
            # ReviewActionResult is a Pydantic model
            print(json.dumps(json.loads(res.model_dump_json()), indent=2))

        elif args.reject:
            if not args.reviewer:
                raise ValueError("Reviewer is required.")
            if not args.notes:
                raise ValueError("Review notes are required for rejection or changes requested.")
            res = await service.reject_review_item(args.reject, args.reviewer, args.notes)
            print(json.dumps(json.loads(res.model_dump_json()), indent=2))

        elif args.needs_changes:
            if not args.reviewer:
                raise ValueError("Reviewer is required.")
            if not args.notes:
                raise ValueError("Review notes are required for rejection or changes requested.")
            res = await service.request_changes(args.needs_changes, args.reviewer, args.notes)
            print(json.dumps(json.loads(res.model_dump_json()), indent=2))

    except Exception as e:
        error_output = {
            "error": str(e)
        }
        print(json.dumps(error_output, indent=2), file=sys.stderr)
        sys.exit(1)
    finally:
        await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
