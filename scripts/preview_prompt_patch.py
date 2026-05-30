#!/usr/bin/env python3
"""CLI utility to preview approved prompt patches and execute safety/regression gates."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from prompts.patch_preview import PromptPatchPreviewer, PromptPatchPreviewResult


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
    parser = argparse.ArgumentParser(description="Preview approved prompt patches and run gates.")
    parser.add_argument("--prompt-name", required=True, help="Name of the prompt to patch.")
    parser.add_argument("--prompt-file", default="prompts/final_expense_alex.md", help="Path to current live prompt.")
    parser.add_argument("--patch-id", action="append", help="Approved patch ID to apply. Can be passed multiple times.")
    parser.add_argument("--all-approved", action="store_true", help="Preview all approved patches.")
    parser.add_argument("--limit", type=int, default=50, help="Max approved patches to load.")
    parser.add_argument("--output-dir", default="data/prompt_patches/previews", help="Directory to save preview outputs.")
    parser.add_argument("--run-gates", action="store_true", default=True, help="Run safety and regression gates.")
    parser.add_argument("--skip-gates", action="store_true", help="Do not run safety and regression gates.")
    parser.add_argument("--create-candidate-version", action="store_true", help="Create a candidate PromptVersion if gates pass.")
    parser.add_argument("--json-only", action="store_true", help="Only produce JSON report and skip markdown report.")
    parser.add_argument("--data-dir", help="Data directory override for Repository.")

    args = parser.parse_args()

    # Input validation
    if not args.patch_id and not args.all_approved:
        print(json.dumps({"error": "Must specify either --patch-id or --all-approved."}), file=sys.stderr)
        sys.exit(1)

    # Instantiate previewer
    from storage.repository import Repository
    repo = Repository(data_dir=args.data_dir) if args.data_dir else Repository()
    previewer = PromptPatchPreviewer(repository=repo)

    try:
        run_gates = not args.skip_gates
        create_candidate_version = args.create_candidate_version

        result = await previewer.build_preview(
            prompt_name=args.prompt_name,
            prompt_path=args.prompt_file,
            patch_ids=args.patch_id,
            output_dir=args.output_dir,
            run_gates=run_gates,
            create_candidate_version=create_candidate_version
        )

        # Handle json-only report deletion
        if args.json_only and result.report_markdown_path:
            md_path = Path(result.report_markdown_path)
            if md_path.exists():
                md_path.unlink()
            result.report_markdown_path = None

        # Print JSON report to stdout
        print(json.dumps(json_serializable(result)))

        # Exit codes: 0 if passed, 1 if gates/preview failed
        if not result.passed:
            sys.exit(1)
        sys.exit(0)

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
    finally:
        await repo.close()


def main() -> None:
    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
