#!/usr/bin/env python3
"""CLI utility to import YouTube transcript text/files/manifests.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training.youtube_importer import YouTubeTranscriptImporter, YouTubeTranscriptImportConfig


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Import YouTube transcripts (offline-only).")
    parser.add_argument("--file", help="Transcript text file path")
    parser.add_argument("--content", help="Transcript text content string")
    parser.add_argument("--manifest", help="Manifest JSON listing transcript paths or content")
    parser.add_argument("--title", help="Title for the imported transcript")
    parser.add_argument("--source-url", help="Source YouTube URL metadata")
    parser.add_argument("--output-dir", default="data/imports/youtube_training", help="Output directory")
    parser.add_argument("--run-intake", action="store_true", help="Trigger intake orchestrator after import")
    parser.add_argument("--dry-run", action="store_true", help="Dry run simulation")
    parser.add_argument("--json-only", action="store_true", help="Suppress log messages")

    args = parser.parse_args()

    config = YouTubeTranscriptImportConfig(
        output_dir=args.output_dir,
        source_url=args.source_url,
        title=args.title,
        transcript_file=args.file,
        transcript_text=args.content,
        manifest_path=args.manifest,
        run_intake_after_import=args.run_intake,
        dry_run=args.dry_run,
    )

    importer = YouTubeTranscriptImporter()

    try:
        res = await importer.import_transcripts(config)
        sys.stdout.write(json.dumps(res.model_dump(mode="json"), indent=2) + "\n")
        return 0 if res.failed_count == 0 else 1
    except Exception as import_err:
        sys.stderr.write(json.dumps({
            "error": str(import_err),
            "status": "failed",
            "warnings": [f"CLI import execution crashed: {import_err}"]
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
