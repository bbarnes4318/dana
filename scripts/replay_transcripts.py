#!/usr/bin/env python3
"""CLI script to replay prospect-side conversations through Dana's evaluation layer."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from evals.transcript_replay import (
    TranscriptReplayRunner,
    StaticTranscriptResponseProvider,
    RuntimeTranscriptResponseProvider,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Transcript Replay CLI.")

    # Input sources
    parser.add_argument("--fixture", type=str, help="Path to a single JSON replay fixture.")
    parser.add_argument("--dir", type=str, help="Path to directory containing JSON replay fixtures.")

    # Execution config
    parser.add_argument("--output-dir", type=str, default="data/evals", help="Output directory for reports.")
    parser.add_argument("--mode", type=str, choices=["static", "runtime"], default="static", help="Replay mode: static or runtime.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop execution after first failure.")
    parser.add_argument("--json-only", action="store_true", help="Skip Markdown report generation.")

    args = parser.parse_args()

    try:
        # Determine target path/dir
        if args.fixture:
            target_path = Path(args.fixture)
            if not target_path.exists():
                raise FileNotFoundError(f"Fixture file not found: {args.fixture}")
        elif args.dir:
            target_path = Path(args.dir)
            if not target_path.exists():
                raise FileNotFoundError(f"Directory not found: {args.dir}")
        else:
            # Default directory
            target_path = Path("evals/fixtures/transcripts")
            if not target_path.exists():
                raise FileNotFoundError(f"Default transcripts directory not found: {target_path}")

        # Configure response provider
        if args.mode == "runtime":
            try:
                from core.agent_runtime import AgentRuntime
            except ImportError as ie:
                raise ValueError(f"AgentRuntime dependency missing: {ie}")
            if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("TELNYX_API_KEY"):
                raise ValueError("Missing environment API keys for OpenAI/Telnyx runtime execution.")
            provider = RuntimeTranscriptResponseProvider()
        else:
            provider = StaticTranscriptResponseProvider()

        runner = TranscriptReplayRunner(response_provider=provider)

        # Load fixtures
        fixtures = runner.load_fixtures(target_path)
        if not fixtures:
            raise ValueError(f"No json fixtures found in: {target_path}")

        # Run replay
        result = await runner.replay_fixtures(
            fixtures,
            output_dir=args.output_dir,
            fail_fast=args.fail_fast,
            json_only=args.json_only
        )

        # Print JSON report to stdout (no non-JSON log messages to stdout!)
        print(json.dumps(result.model_dump(mode="json"), indent=2))

        # Determine exit status
        # Exit 1 if any fixture failed or if we encountered execution errors
        if result.failed_fixtures > 0:
            sys.exit(1)

        sys.exit(0)

    except Exception as e:
        error_output = {
            "error": str(e)
        }
        sys.stderr.write(json.dumps(error_output, indent=2) + "\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
