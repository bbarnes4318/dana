#!/usr/bin/env python3
"""CLI utility to export completed call payloads for training ingestion.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training.post_call_exporter import PostCallExporter, PostCallExportConfig


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Export completed call payloads.")
    parser.add_argument("--file", required=True, help="Completed call payload JSON file path")
    parser.add_argument("--output-dir", default="data/imports/post_call_payloads", help="Output payloads directory")
    parser.add_argument("--enabled", action="store_true", default=True, help="Explicitly enable the exporter")
    parser.add_argument("--run-intake", action="store_true", help="Automatically trigger intake orchestrator on output")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without writing payloads")
    parser.add_argument("--json-only", action="store_true", help="Suppress logging logs on stdout")

    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        sys.stderr.write(json.dumps({"error": f"Payload file not found: {args.file}"}, indent=2) + "\n")
        return 1

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as parse_err:
        sys.stderr.write(json.dumps({"error": f"Failed to parse payload JSON: {parse_err}"}, indent=2) + "\n")
        return 1

    config = PostCallExportConfig(
        enabled=args.enabled,
        output_dir=args.output_dir,
        run_intake_after_export=args.run_intake,
        intake_sync=True,  # Sync run inside CLI execution
        dry_run=args.dry_run,
        fail_silently=False,
    )

    exporter = PostCallExporter()

    try:
        res = await exporter.export_completed_call(payload, config)
        sys.stdout.write(json.dumps(res.model_dump(mode="json"), indent=2) + "\n")
        return 0 if res.exported or not args.enabled else 1
    except Exception as export_err:
        sys.stderr.write(json.dumps({
            "error": str(export_err),
            "status": "failed",
            "warnings": [f"CLI export execution crashed: {export_err}"]
        }, indent=2) + "\n")
        return 1


def main() -> None:
    try:
        code = asyncio.run(main_async())
        sys.exit(code)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
