#!/usr/bin/env python3
"""CLI utility to generate safe prompt patch candidates for human review."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from prompts.patch_generator import PromptPatchGenerator, PromptPatchGenerationResult


def json_serializable(obj: Any) -> Any:
    """Recursively convert custom objects/dataclasses to JSON-serializable formats."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: json_serializable(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, list):
        return [json_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: json_serializable(v) for k, v in obj.items()}
    return obj


async def run_cli() -> None:
    parser = argparse.ArgumentParser(description="Generate safe prompt patch candidates.")
    parser.add_argument("--prompt-name", required=True, help="Name of the prompt to patch.")
    parser.add_argument("--prompt-file", default="prompts/final_expense_alex.md", help="Path to current live prompt.")
    parser.add_argument("--limit", type=int, default=500, help="Max sources to scan.")
    parser.add_argument("--output-dir", default="data/prompt_patches", help="Directory to save report outputs.")
    parser.add_argument("--dry-run", action="store_true", help="Do not save candidates to HumanReviewItem store.")
    parser.add_argument("--json-only", action="store_true", help="Only produce JSON output and JSON report (skip markdown report).")

    args = parser.parse_args()

    generator = PromptPatchGenerator()

    try:
        # Run patch candidate generation
        result = await generator.generate_for_prompt(
            prompt_name=args.prompt_name,
            prompt_path=args.prompt_file,
            limit=args.limit,
            save_review_items=not args.dry_run,
            output_dir=args.output_dir,
        )

        # If json_only is requested, we delete/skip markdown report if it was written
        if args.json_only and result.report_markdown_path:
            md_path = Path(result.report_markdown_path)
            if md_path.exists():
                md_path.unlink()
            result.report_markdown_path = None

        print(json.dumps(json_serializable(result)))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def main() -> None:
    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
