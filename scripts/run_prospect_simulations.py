#!/usr/bin/env python3
import sys
import argparse
import json
import asyncio
import os

# Ensure the project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from simulations.prospect_simulator import (
    ProspectSimulator,
    SimulationRunner,
    StaticDanaResponseProvider,
    RuntimeDanaResponseProvider,
)


def print_json_error(message: str, exit_code: int = 1):
    print(json.dumps({"error": message}), file=sys.stderr)
    sys.exit(exit_code)


async def main():
    parser = argparse.ArgumentParser(description="Run Dana's prospect simulations.")
    parser.add_argument("--all", action="store_true", help="Run all personas.")
    parser.add_argument(
        "--persona",
        action="append",
        dest="personas",
        default=[],
        help="Specify one or more persona IDs to run.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/simulations",
        help="Output directory for reports (default: data/simulations).",
    )
    parser.add_argument(
        "--mode",
        choices=["static", "runtime"],
        default="static",
        help="Provider mode (static or runtime, default: static).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop running on first failure.",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only output JSON metadata.",
    )
    parser.add_argument(
        "--list-personas",
        action="store_true",
        help="Output list of available personas in JSON format and exit.",
    )

    args = parser.parse_args()

    simulator = ProspectSimulator()

    # 1. Handle --list-personas
    if args.list_personas:
        personas = simulator.get_default_personas()
        personas_json = [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "target_outcome": p.target_outcome,
            }
            for p in personas
        ]
        print(json.dumps(personas_json, indent=2))
        sys.exit(0)

    # 2. Check if either --all or --persona is passed
    if not args.all and not args.personas:
        print_json_error(
            "Must specify either --all, --persona, or --list-personas.", 1
        )

    # 3. Resolve Provider
    if args.mode == "static":
        dana_provider = StaticDanaResponseProvider()
    else:
        try:
            dana_provider = RuntimeDanaResponseProvider()
        except RuntimeError as e:
            print_json_error(str(e), 1)
            return

    runner = SimulationRunner(simulator=simulator, dana_provider=dana_provider)

    # Resolve output directory
    output_dir = args.output_dir if args.output_dir else None

    # 4. Resolve which personas to run
    personas_to_run = []
    if args.all:
        personas_to_run = [p.id for p in simulator.get_default_personas()]
    else:
        # Validate persona IDs
        valid_ids = {p.id for p in simulator.get_default_personas()}
        for pid in args.personas:
            if pid not in valid_ids:
                print_json_error(f"Invalid persona ID: {pid}", 1)
            personas_to_run.append(pid)

    # 5. Run simulations
    import uuid
    from datetime import datetime, timezone
    
    started_at = datetime.now(timezone.utc).isoformat()
    results = []
    passed_scenarios = 0
    failed_scenarios = 0
    total_score = 0.0

    for pid in personas_to_run:
        try:
            # If using runtime mode, make sure we construct a fresh Provider
            # or handle errors cleanly.
            if args.mode == "runtime":
                # Fresh runtime instance per run if required, or reuse
                pass
            res = await runner.run_persona(pid, output_dir=output_dir)
            results.append(res)
            if res.passed:
                passed_scenarios += 1
            else:
                failed_scenarios += 1
                if args.fail_fast:
                    break
            total_score += res.score
        except Exception as e:
            # Handle runtime errors cleanly
            fail_res = {
                "scenario_id": f"scenario_{pid}",
                "persona_id": pid,
                "passed": False,
                "outcome": "error",
                "expected_outcome": "unknown",
                "score": 0.0,
                "compliance_failures": [f"Execution error: {e}"],
                "behavior_failures": [],
                "tool_failures": [],
                "warnings": [f"Execution failed: {e}"]
            }
            results.append(fail_res)
            failed_scenarios += 1
            if args.fail_fast:
                break

    finished_at = datetime.now(timezone.utc).isoformat()
    total_runs = len(results)
    pass_rate = passed_scenarios / total_runs if total_runs > 0 else 0.0
    avg_score = total_score / total_runs if total_runs > 0 else 0.0

    # Format run result to match SimulationRunResult shape
    run_id = str(uuid.uuid4())
    
    # Helper to serialize SimulationResult / dict to JSON dict
    def serialize_result(res):
        if isinstance(res, dict):
            return res
        return {
            "scenario_id": res.scenario_id,
            "persona_id": res.persona_id,
            "passed": res.passed,
            "outcome": res.outcome,
            "expected_outcome": res.expected_outcome,
            "final_stage": res.final_stage,
            "total_turns": res.total_turns,
            "compliance_failures": res.compliance_failures,
            "behavior_failures": res.behavior_failures,
            "tool_failures": res.tool_failures,
            "score": res.score,
            "qa_score": res.qa_score,
            "report_json_path": res.report_json_path,
            "report_markdown_path": res.report_markdown_path,
            "warnings": res.warnings,
        }

    run_result = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_scenarios": total_runs,
        "passed_scenarios": passed_scenarios,
        "failed_scenarios": failed_scenarios,
        "pass_rate": pass_rate,
        "average_score": avg_score,
        "results": [serialize_result(r) for r in results],
        "warnings": [],
    }

    # Write run reports if output_dir is specified and not erroring
    if output_dir and total_runs > 0:
        # Construct dynamic SimulationRunResult objects to reuse write_run_report
        from simulations.prospect_simulator import SimulationRunResult
        obj = SimulationRunResult(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            total_scenarios=total_runs,
            passed_scenarios=passed_scenarios,
            failed_scenarios=failed_scenarios,
            pass_rate=pass_rate,
            average_score=avg_score,
            warnings=[],
        )
        # Populate results with typed SimulationResult objects
        from simulations.prospect_simulator import SimulationResult, SimulatedTurn
        typed_results = []
        for r in results:
            if isinstance(r, dict):
                typed_results.append(SimulationResult(
                    scenario_id=r["scenario_id"],
                    persona_id=r["persona_id"],
                    passed=r["passed"],
                    outcome=r["outcome"],
                    expected_outcome=r["expected_outcome"],
                    score=r["score"],
                    compliance_failures=r["compliance_failures"],
                    warnings=r["warnings"],
                ))
            else:
                typed_results.append(r)
        obj.results = typed_results
        runner.write_run_report(obj, output_dir)
        run_result["report_json_path"] = os.path.join(output_dir, f"simulation_run_{run_id}.json")
        run_result["report_markdown_path"] = os.path.join(output_dir, f"simulation_run_{run_id}.md")

    # Output run_result to stdout
    print(json.dumps(run_result, indent=2))

    # Exit code
    if failed_scenarios > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
