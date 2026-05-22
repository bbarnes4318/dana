#!/usr/bin/env python3
"""CLI runner — discover and execute all eval scenarios.

Usage::

    python evals/run_all.py

Discovers every ``.yaml`` file under ``evals/scenarios/``, loads each as
an :class:`EvalScenario`, runs it through the :class:`ScenarioRunner`,
and prints a summary table.

Exit codes:
    0 — all scenarios passed
    1 — one or more scenarios failed
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on sys.path so absolute imports work
# when this script is invoked directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from evals.scenario_runner import ScenarioResult, ScenarioRunner
from evals.scenario_schema import EvalScenario


def discover_scenarios(
    scenarios_dir: str | Path | None = None,
) -> list[Path]:
    """Find all ``.yaml`` files under *scenarios_dir*.

    Parameters
    ----------
    scenarios_dir:
        Directory to search.  Defaults to ``evals/scenarios/`` relative
        to this file.

    Returns
    -------
    list[Path]
        Sorted list of scenario YAML file paths.
    """
    if scenarios_dir is None:
        scenarios_dir = Path(__file__).resolve().parent / "scenarios"
    else:
        scenarios_dir = Path(scenarios_dir)

    if not scenarios_dir.is_dir():
        return []

    return sorted(scenarios_dir.glob("*.yaml"))


def run_all(
    scenarios_dir: str | Path | None = None,
) -> list[ScenarioResult]:
    """Load and run every scenario, returning results.

    Parameters
    ----------
    scenarios_dir:
        Override for the scenarios directory.

    Returns
    -------
    list[ScenarioResult]
        One result per scenario file.
    """
    paths = discover_scenarios(scenarios_dir)
    if not paths:
        print("[WARN] No scenario YAML files found.")
        return []

    runner = ScenarioRunner()
    results: list[ScenarioResult] = []

    for path in paths:
        try:
            scenario = EvalScenario.from_yaml(path)
            result = runner.run_scenario(scenario)
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            results.append(
                ScenarioResult(
                    scenario_name=path.stem,
                    passed=False,
                    errors=[f"Failed to load/run: {exc}"],
                )
            )

    return results


def print_results(results: list[ScenarioResult]) -> None:
    """Print a formatted summary table of results."""
    if not results:
        print("No results to display.")
        return

    # Header
    name_width = max(len(r.scenario_name) for r in results)
    name_width = max(name_width, 20)

    header = (
        f"{'Scenario':<{name_width}}  "
        f"{'Status':<8}  "
        f"{'Stage':<18}  "
        f"{'Turns':<6}  "
        f"{'Assertions'}"
    )
    print()
    print("=" * len(header))
    print("  EVAL SCENARIO RESULTS")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    passed_count = 0
    failed_count = 0

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        status_icon = "\u2705" if r.passed else "\u274c"
        assertion_summary = (
            f"{sum(1 for a in r.assertion_results if a.passed)}"
            f"/{len(r.assertion_results)} passed"
        )

        print(
            f"{r.scenario_name:<{name_width}}  "
            f"{status_icon} {status:<5}  "
            f"{r.final_stage:<18}  "
            f"{r.turn_count:<6}  "
            f"{assertion_summary}"
        )

        if not r.passed:
            failed_count += 1
            # Show failure details
            for ar in r.assertion_results:
                if not ar.passed:
                    print(f"  \u2514\u2500 [{ar.assertion_type}] {ar.message}")
            for err in r.errors:
                print(f"  \u2514\u2500 [ERROR] {err}")
        else:
            passed_count += 1

    print("-" * len(header))
    total = passed_count + failed_count
    print(f"Total: {total} | Passed: {passed_count} | Failed: {failed_count}")
    print("=" * len(header))
    print()


def main() -> int:
    """Entry point. Returns 0 on success, 1 on failure."""
    results = run_all()
    print_results(results)

    if not results:
        print("No scenarios found — exiting with code 1.")
        return 1

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
