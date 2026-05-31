#!/usr/bin/env python
import sys
import json
import argparse
from pathlib import Path

from ops.readiness import (
    ContinuousTrainingReadinessAuditor,
    ContinuousTrainingReadinessConfig,
)

def main():
    parser = argparse.ArgumentParser(description="Run continuous training pipeline readiness audit.")
    
    parser.add_argument("--output-dir", type=str, default="data/ops_readiness", help="Output directory for reports.")
    parser.add_argument("--strict", action="store_true", default=True, help="Fail on high severity checks.")
    parser.add_argument("--non-strict", dest="strict", action="store_false", help="Do not fail on high severity checks.")
    parser.add_argument("--fail-on-medium", action="store_true", help="Fail on medium severity checks.")
    
    # Skips
    parser.add_argument("--skip-docs", action="store_true", help="Skip checking runbook files.")
    parser.add_argument("--skip-cli", action="store_true", help="Skip checking CLI scripts.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip checking test files.")
    parser.add_argument("--skip-runtime-safety", action="store_true", help="Skip checking runtime safety controls.")
    parser.add_argument("--skip-fine-tune-safety", action="store_true", help="Skip checking fine-tune safety rules.")
    parser.add_argument("--skip-canary-safety", action="store_true", help="Skip checking canary environment flags.")
    parser.add_argument("--skip-prompt-safety", action="store_true", help="Skip checking prompt file patch markers.")
    parser.add_argument("--skip-storage", action="store_true", help="Skip checking storage schema/migrations.")
    parser.add_argument("--include-slow-checks", action="store_true", help="Include slow verification tasks.")
    parser.add_argument("--json-only", action="store_true", help="Output JSON results to stdout without writing markdown files.")

    args = parser.parse_args()

    config = ContinuousTrainingReadinessConfig(
        output_dir=args.output_dir if not args.json_only else "",
        strict=args.strict,
        include_slow_checks=args.include_slow_checks,
        check_docs=not args.skip_docs,
        check_cli=not args.skip_cli,
        check_tests=not args.skip_tests,
        check_runtime_safety=not args.skip_runtime_safety,
        check_fine_tune_safety=not args.skip_fine_tune_safety,
        check_canary_safety=not args.skip_canary_safety,
        check_prompt_safety=not args.skip_prompt_safety,
        check_storage=not args.skip_storage,
        fail_on_medium=args.fail_on_medium,
        json_only=args.json_only
    )

    try:
        auditor = ContinuousTrainingReadinessAuditor()
        result = auditor.run_all_checks(config)
        
        # Output clean JSON result to stdout
        sys.stdout.write(json.dumps(result.model_dump(mode="json"), indent=2) + "\n")
        
        if result.passed:
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        # Clean JSON error to stderr
        sys.stderr.write(json.dumps({
            "status": "error",
            "message": str(e)
        }, indent=2) + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
