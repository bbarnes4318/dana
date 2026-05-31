#!/usr/bin/env python
"""CLI script to export safe fine-tuning datasets from human-approved examples."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from storage.repository import Repository
from training.fine_tune_export import FineTuneExportBuilder, FineTuneExportConfig

async def main() -> int:
    parser = argparse.ArgumentParser(description="Export compliant fine-tuning datasets.")
    parser.add_argument("--export-name", required=True, help="Name of the export.")
    parser.add_argument("--format", default="openai_chat_jsonl", choices=["openai_chat_jsonl", "generic_pairs_jsonl"], help="Format of the export.")
    parser.add_argument("--output-dir", default="data/fine_tune_exports", help="Output directory.")
    parser.add_argument("--train-ratio", type=float, default=0.90, help="Train ratio.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of exported examples.")
    parser.add_argument("--min-examples", type=int, default=10, help="Minimum number of examples required to export.")
    parser.add_argument("--max-examples", type=int, default=None, help="Maximum number of exported examples.")
    parser.add_argument("--stage", action="append", dest="include_stages", help="Include stage (can be repeated).")
    parser.add_argument("--exclude-stage", action="append", dest="exclude_stages", help="Exclude stage (can be repeated).")
    parser.add_argument("--objection-type", action="append", dest="include_objection_types", help="Include objection type (can be repeated).")
    parser.add_argument("--exclude-objection-type", action="append", dest="exclude_objection_types", help="Exclude objection type (can be repeated).")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode (no files written).")
    parser.add_argument("--json-only", action="store_true", help="Only output JSON to stdout.")

    args = parser.parse_args()

    config = FineTuneExportConfig(
        export_name=args.export_name,
        format=args.format,
        output_dir=args.output_dir,
        train_ratio=args.train_ratio,
        limit=args.limit,
        min_examples=args.min_examples,
        max_examples=args.max_examples,
        require_fine_tune_eligible=True,
        include_stages=args.include_stages,
        exclude_stages=args.exclude_stages,
        include_objection_types=args.include_objection_types,
        exclude_objection_types=args.exclude_objection_types,
        dry_run=args.dry_run
    )

    try:
        repo = Repository()
        builder = FineTuneExportBuilder(repository=repo)
        result = await builder.build_export(config)
        
        res_dict = result.model_dump() if hasattr(result, "model_dump") else result.__dict__
        
        if result.exported_examples < config.min_examples and not config.dry_run:
            sys.stderr.write(json.dumps({
                "status": "error",
                "message": f"Not enough exported examples: found {result.exported_examples}, required {config.min_examples}.",
                "result": res_dict
            }) + "\n")
            return 1
            
        sys.stdout.write(json.dumps(res_dict) + "\n")
        return 0
        
    except Exception as e:
        sys.stderr.write(json.dumps({
            "status": "error",
            "message": str(e)
        }) + "\n")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
