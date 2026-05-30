#!/usr/bin/env python3
"""CLI script to run Dana's deterministic eval case runner."""

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from storage.repository import Repository
from evals.case_runner import (
    EvalCaseRunner,
    EvalCaseRunConfig,
    StaticResponseProvider,
    RuntimeResponseProvider,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Eval Case Runner CLI.")

    # Case selection
    parser.add_argument("--all", action="store_true", help="Run all approved EvalCase records.")
    parser.add_argument("--case-id", action="append", help="Run specific case ID(s). Can be passed multiple times.")
    parser.add_argument("--stage", type=str, help="Filter cases by stage.")
    parser.add_argument("--severity", type=str, choices=["low", "medium", "high", "critical"], help="Filter cases by severity.")
    parser.add_argument("--limit", type=int, help="Limit the number of cases to execute.")

    # Response input
    parser.add_argument("--response", type=str, help="Candidate response text (single or fallback).")
    parser.add_argument("--responses-json", type=str, help="Path to JSON file containing response mapping.")

    # Mode
    parser.add_argument("--mode", type=str, choices=["static", "runtime"], default="static", help="Execution mode: static or runtime.")

    # Output / config
    parser.add_argument("--output-dir", type=str, default="data/evals", help="Output directory for reports.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop execution after first failure.")
    parser.add_argument("--json-only", action="store_true", help="Only write JSON report, skipping markdown.")
    parser.add_argument("--data-dir", type=str, default="data", help="Data directory for JSONL repository.")

    args = parser.parse_args()

    repo = Repository(data_dir=args.data_dir)

    try:
        # Case selection validation
        if not (args.all or args.case_id or args.stage or args.severity):
            raise ValueError("Must specify at least one case selection option: --all, --case-id, --stage, or --severity.")

        # Response mode validation
        response_map = None
        if args.mode == "static":
            if not args.response and not args.responses_json:
                raise ValueError("Static mode requires either --response or --responses-json.")

            if args.responses_json:
                if not os.path.exists(args.responses_json):
                    raise FileNotFoundError(f"Responses JSON file not found: {args.responses_json}")
                with open(args.responses_json, "r", encoding="utf-8") as f:
                    response_map = json.load(f)

        # Configure response provider
        if args.mode == "runtime":
            try:
                from core.agent_runtime import AgentRuntime
            except ImportError as ie:
                raise ValueError(f"AgentRuntime dependency missing: {ie}")
            if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("TELNYX_API_KEY"):
                raise ValueError("Missing environment API keys for OpenAI/Telnyx runtime execution.")
            response_provider = RuntimeResponseProvider()
        else:
            response_provider = StaticResponseProvider(
                response_map=response_map,
                fallback_response=args.response,
            )

        # Build run config
        config = EvalCaseRunConfig(
            case_ids=args.case_id,
            stage=args.stage,
            severity=args.severity,
            limit=args.limit,
            response_mode=args.mode,
            fail_fast=args.fail_fast,
            output_dir=args.output_dir,
            include_markdown_report=not args.json_only,
            include_json_report=True,
        )

        runner = EvalCaseRunner(repository=repo, response_provider=response_provider)
        
        # Run cases
        result = await runner.run_approved_cases(config)
        
        # Print JSON report to stdout (no non-JSON log messages to stdout!)
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        
        # Determine exit status
        # Exit 1 if any case failed or if we encountered execution errors
        if result.failed_cases > 0:
            sys.exit(1)
            
        sys.exit(0)

    except Exception as e:
        error_output = {
            "error": str(e)
        }
        sys.stderr.write(json.dumps(error_output, indent=2) + "\n")
        sys.exit(1)
    finally:
        await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
