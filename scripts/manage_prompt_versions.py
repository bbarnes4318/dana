#!/usr/bin/env python3
"""CLI utility for managing Dana prompt versions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from prompts.versioning import PromptVersionManager, PromptVersionSnapshotResult, PromptVersionDiff, PromptValidationResult


def json_serializable(obj: Any) -> Any:
    """Helper to convert dataclasses and datetimes to JSON-serializable formats."""
    if hasattr(obj, "__dataclass_fields__"):
        # Convert dataclass to dict
        return {k: json_serializable(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [json_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: json_serializable(v) for k, v in obj.items()}
    return obj


async def run_cli() -> None:
    parser = argparse.ArgumentParser(description="Manage Dana prompt versions.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # 1. Snapshot mode
    snap_parser = subparsers.add_parser("snapshot", help="Snapshot a prompt file.")
    snap_parser.add_argument("--name", required=True, help="Name of the prompt.")
    snap_parser.add_argument("--file", required=True, help="Path to the prompt file.")
    snap_parser.add_argument("--created-by", required=True, help="Author of the snapshot.")
    snap_parser.add_argument("--version", help="Explicit version string.")
    snap_parser.add_argument("--notes", help="Change notes.")

    # 2. List mode
    list_parser = subparsers.add_parser("list", help="List prompt versions.")
    list_parser.add_argument("--name", help="Filter by prompt name.")
    list_parser.add_argument("--limit", type=int, default=50, help="Max number of results.")

    # 3. Show mode
    show_parser = subparsers.add_parser("show", help="Show details of a prompt version.")
    show_parser.add_argument("--id", required=True, help="Version ID.")

    # 4. Diff mode
    diff_parser = subparsers.add_parser("diff", help="Compare two prompt versions.")
    diff_parser.add_argument("--from", required=True, dest="from_id", help="From version ID.")
    diff_parser.add_argument("--to", required=True, dest="to_id", help="To version ID.")

    # 5. Validate mode
    validate_parser = subparsers.add_parser("validate", help="Validate a prompt file or stored version.")
    validate_group = validate_parser.add_mutually_exclusive_group(required=True)
    validate_group.add_argument("--file", help="Path to the prompt file to validate.")
    validate_group.add_argument("--id", help="Version ID to validate.")

    # 6. Drift mode
    drift_parser = subparsers.add_parser("drift", help="Detect prompt drift on disk.")
    drift_parser.add_argument("--name", required=True, help="Name of the prompt.")
    drift_parser.add_argument("--file", required=True, help="Path to the prompt file.")

    # 7. Export mode
    export_parser = subparsers.add_parser("export", help="Export a prompt version to a safe file.")
    export_parser.add_argument("--id", required=True, help="Version ID to export.")
    export_parser.add_argument("--output", required=True, help="Output destination path.")

    # 8. Report mode
    report_parser = subparsers.add_parser("report", help="Generate prompt audit reports.")
    report_parser.add_argument("--name", required=True, help="Name of the prompt.")
    report_parser.add_argument("--output-dir", default="data/prompt_versions", help="Output directory.")

    args = parser.parse_args()

    manager = PromptVersionManager()

    try:
        if args.mode == "snapshot":
            result = await manager.snapshot_prompt_file(
                prompt_name=args.name,
                file_path=args.file,
                created_by=args.created_by,
                version=args.version,
                notes=args.notes,
            )
            print(json.dumps(json_serializable(result)))

        elif args.mode == "list":
            versions = await manager.list_prompt_versions(prompt_name=args.name, limit=args.limit)
            print(json.dumps(json_serializable(versions)))

        elif args.mode == "show":
            version = await manager.get_prompt_version(args.id)
            print(json.dumps(json_serializable(version)))

        elif args.mode == "diff":
            diff = await manager.diff_prompt_versions(from_version_id=args.from_id, to_version_id=args.to_id)
            print(json.dumps(json_serializable(diff)))

        elif args.mode == "validate":
            if args.file:
                content = manager.load_prompt_file(args.file)
            else:
                version = await manager.get_prompt_version(args.id)
                content = version["content"]
            
            res = await manager.validate_prompt_content(content)
            print(json.dumps(json_serializable(res)))

        elif args.mode == "drift":
            drift = await manager.detect_prompt_drift(prompt_name=args.name, file_path=args.file)
            print(json.dumps(json_serializable(drift)))

        elif args.mode == "export":
            exported_path = await manager.export_prompt_version(version_id=args.id, output_path=args.output)
            print(json.dumps({
                "exported_path": exported_path,
                "status": "success"
            }))

        elif args.mode == "report":
            json_report, md_report = await manager.generate_prompt_report(
                prompt_name=args.name,
                output_dir=args.output_dir
            )
            print(json.dumps({
                "json_report_path": json_report,
                "markdown_report_path": md_report,
                "prompt_name": args.name
            }))

    except Exception as e:
        # Print clean JSON to stderr on error and exit 1
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def main() -> None:
    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
