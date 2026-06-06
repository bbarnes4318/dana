#!/usr/bin/env python3
"""Runner script for the Platform Quality Gate.

Automatically locates the latest leaderboard results and executes the quality gate checks.
"""

import glob
import os
import subprocess
import sys


def find_latest_benchmark_file() -> str:
    """Finds the most recent benchmark results file in the default directory."""
    # Try the default first
    default_path = "data/benchmarks/leaderboard.json"
    if os.path.exists(default_path):
        return default_path

    # Look for any JSON file in data/benchmarks
    json_files = glob.glob("data/benchmarks/*.json")
    if not json_files:
        return ""

    # Sort by modification time to find the newest
    json_files.sort(key=os.path.getmtime, reverse=True)
    return json_files[0]


def main() -> None:
    benchmark_file = find_latest_benchmark_file()
    
    if not benchmark_file:
        print("Error: No benchmark results file found in data/benchmarks/", file=sys.stderr)
        print("Please run the benchmark suite first using:", file=sys.stderr)
        print("  python -m benchmarks.voice_platform_benchmark.leaderboard", file=sys.stderr)
        sys.exit(1)

    print(f"Using benchmark results from: {benchmark_file}")
    
    # Run the quality gate command
    cmd = [
        sys.executable,
        "-m",
        "qa.platform_quality_gate",
        "--benchmark-file",
        benchmark_file,
    ]
    
    res = subprocess.run(cmd)
    sys.exit(res.returncode)


if __name__ == "__main__":
    main()
