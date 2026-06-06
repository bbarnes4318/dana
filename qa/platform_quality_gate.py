"""Platform Quality Gate CLI.

Loads benchmark results, runs the scorecard evaluation, and exits with success or failure.
"""

import argparse
import json
import os
import sys
from typing import Dict, Any

from qa.platform_scorecard import PlatformScorecard


def main() -> None:
    parser = argparse.ArgumentParser(description="Dana Platform Quality Gate CLI.")
    parser.add_argument(
        "--benchmark-file",
        type=str,
        required=True,
        help="Path to the JSON benchmark results file.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="dana",
        help="The provider to evaluate (default: 'dana').",
    )
    parser.add_argument(
        "--json-output",
        type=str,
        default="data/benchmarks/platform_scorecard.json",
        help="Path to save the JSON scorecard output.",
    )
    parser.add_argument(
        "--md-output",
        type=str,
        default="data/benchmarks/platform_scorecard.md",
        help="Path to save the Markdown scorecard output.",
    )

    args = parser.parse_args()

    if not os.path.exists(args.benchmark_file):
        print(f"Error: Benchmark file not found at: {args.benchmark_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.benchmark_file, "r", encoding="utf-8") as f:
            benchmark_data = json.load(f)
    except Exception as e:
        print(f"Error: Failed to parse benchmark results JSON: {e}", file=sys.stderr)
        sys.exit(1)

    scorecard = PlatformScorecard(benchmark_data, provider_id=args.provider)
    scorecard.print_summary()

    # Create output directories if needed
    for output_path in (args.json_output, args.md_output):
        if output_path:
            dir_name = os.path.dirname(output_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

    # Save JSON scorecard
    if args.json_output:
        try:
            with open(args.json_output, "w", encoding="utf-8") as f:
                f.write(scorecard.generate_json())
            print(f"Saved JSON scorecard to: {args.json_output}")
        except Exception as e:
            print(f"Warning: Failed to save JSON scorecard: {e}", file=sys.stderr)

    # Save Markdown scorecard
    if args.md_output:
        try:
            with open(args.md_output, "w", encoding="utf-8") as f:
                f.write(scorecard.generate_markdown())
            print(f"Saved Markdown scorecard to: {args.md_output}")
        except Exception as e:
            print(f"Warning: Failed to save Markdown scorecard: {e}", file=sys.stderr)

    # Exit with code depending on quality gate status
    if scorecard.evaluation["passed"]:
        print("\n>>> Platform Quality Gate: PASSED <<<")
        sys.exit(0)
    else:
        print("\n>>> Platform Quality Gate: FAILED <<<", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
