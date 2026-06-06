import os
import yaml
import json
import argparse
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List

from benchmarks.voice_platform_benchmark.metrics_schema import (
    BenchmarkRunResult,
    ProviderBenchmarkReport,
    ScenarioMetrics,
    Slotargets
)
from benchmarks.voice_platform_benchmark.run_synthetic_call import run_synthetic_call
from benchmarks.voice_platform_benchmark.run_transcript_replay import run_transcript_replay

def load_yaml(file_path: str) -> Dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def run_benchmark(
    providers_path: str,
    scenarios_path: str,
    slo_targets: Slotargets = Slotargets()
) -> BenchmarkRunResult:
    """
    Runs the benchmark suite.
    Executes all scenarios across all providers and aggregates results.
    """
    providers = load_yaml(providers_path)
    scenarios = load_yaml(scenarios_path)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    run_id = f"benchmark_run_{int(datetime.now().timestamp())}"
    
    provider_reports: Dict[str, ProviderBenchmarkReport] = {}
    
    for provider_id, provider_config in providers.items():
        scenario_results: Dict[str, ScenarioMetrics] = {}
        
        total_p50_latency = 0.0
        total_p95_latency = 0.0
        total_humanlikeness = 0.0
        total_overall_score = 0.0
        total_cost = 0.0
        compliance_failed_count = 0
        latency_failed_count = 0
        
        for scenario_id, scenario_config in scenarios.items():
            # 1. Run synthetic call simulation to generate transcript & metadata
            transcript = run_synthetic_call(
                provider_id=provider_id,
                scenario_id=scenario_id,
                provider_config=provider_config,
                scenario_config=scenario_config
            )
            
            # 2. Replay and score the generated transcript
            metrics = run_transcript_replay(
                provider_id=provider_id,
                scenario_id=scenario_id,
                transcript=transcript,
                provider_config=provider_config,
                expected_outcome=scenario_config.get("expected_outcome", "ended"),
                slo_targets=slo_targets
            )
            
            # Update counters
            total_p50_latency += metrics.p50_turn_latency_ms
            total_p95_latency += metrics.p95_turn_latency_ms
            total_humanlikeness += metrics.humanlikeness_score
            total_overall_score += metrics.overall_score
            
            # Estimate cost based on turn length
            connected_minutes = (len(transcript) * 15.0) / 60.0
            total_cost += connected_minutes * metrics.cost_per_connected_minute
            if metrics.outcome_passed and scenario_config.get("expected_outcome") == "transferred":
                total_cost += metrics.cost_per_qualified_transfer
                
            has_compliance_fail = (
                metrics.compliance_hard_fail_count > 0 or
                metrics.dnc_failure_count > 0 or
                metrics.wrong_number_failure_count > 0 or
                metrics.transfer_without_consent_count > 0
            )
            if has_compliance_fail:
                compliance_failed_count += 1
            if metrics.latency_failed:
                latency_failed_count += 1
                
            scenario_results[scenario_id] = metrics
            
        # Compute averages
        num_scenarios = len(scenarios)
        avg_p50 = total_p50_latency / num_scenarios
        avg_p95 = total_p95_latency / num_scenarios
        avg_human = total_humanlikeness / num_scenarios
        avg_overall = total_overall_score / num_scenarios
        
        # Overall grade determined by average score & compliance gates
        if compliance_failed_count > 0:
            overall_grade = "F"
        elif avg_overall >= 90.0:
            overall_grade = "A"
        elif avg_overall >= 80.0:
            overall_grade = "B"
        elif avg_overall >= 70.0:
            overall_grade = "C"
        elif avg_overall >= 60.0:
            overall_grade = "D"
        else:
            overall_grade = "F"
            
        report = ProviderBenchmarkReport(
            provider_id=provider_id,
            provider_name=provider_config.get("name", provider_id),
            avg_p50_latency_ms=round(avg_p50, 2),
            avg_p95_latency_ms=round(avg_p95, 2),
            compliance_failed_scenarios=compliance_failed_count,
            latency_failed_scenarios=latency_failed_count,
            avg_humanlikeness_score=round(avg_human, 2),
            avg_overall_score=round(avg_overall, 2),
            overall_grade=overall_grade,
            total_cost=round(total_cost, 4),
            scenario_results=scenario_results
        )
        provider_reports[provider_id] = report
        
    return BenchmarkRunResult(
        run_id=run_id,
        timestamp=timestamp,
        slo_targets=slo_targets,
        provider_reports=provider_reports
    )

def generate_markdown(result: BenchmarkRunResult) -> str:
    """Generates a formatted markdown leaderboard table and summary."""
    lines = []
    lines.append("# Outbound Voice Platform Benchmark Leaderboard")
    lines.append(f"**Run ID:** `{result.run_id}`")
    lines.append(f"**Date:** {result.timestamp}")
    lines.append("")
    
    # SLO configuration table
    lines.append("## Target SLO Targets")
    lines.append("| Metric | Target SLO |")
    lines.append("| :--- | :--- |")
    lines.append(f"| P50 Turn Latency | < {result.slo_targets.p50_turn_latency_max_ms}ms |")
    lines.append(f"| P95 Turn Latency | < {result.slo_targets.p95_turn_latency_max_ms}ms |")
    lines.append(f"| LLM First Token Latency | < {result.slo_targets.llm_first_token_max_ms}ms |")
    lines.append(f"| TTS First Audio Latency | < {result.slo_targets.tts_first_audio_max_ms}ms |")
    lines.append(f"| Barge-in Stop Latency | < {result.slo_targets.barge_in_stop_max_ms}ms |")
    lines.append("")
    
    lines.append("## Leaderboard Summary")
    lines.append("| Rank | Provider | Grade | Overall Score | Avg P50 Latency | Avg P95 Latency | Humanlikeness | Compliance Fails | Latency Fails | Total Cost |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    
    # Sort providers by overall score descending, placing Grade F at the bottom
    sorted_reports = sorted(
        result.provider_reports.values(),
        key=lambda r: (0 if r.overall_grade == "F" else 1, r.avg_overall_score),
        reverse=True
    )
    
    for rank, report in enumerate(sorted_reports, 1):
        lines.append(
            f"| {rank} | {report.provider_name} | **{report.overall_grade}** | {report.avg_overall_score:.2f} | {report.avg_p50_latency_ms:.1f}ms | {report.avg_p95_latency_ms:.1f}ms | {report.avg_humanlikeness_score:.1f}% | {report.compliance_failed_scenarios} | {report.latency_failed_scenarios} | ${report.total_cost:.4f} |"
        )
        
    lines.append("")
    lines.append("## Detailed Scenario Breakdown")
    
    for provider_id, report in result.provider_reports.items():
        lines.append(f"### {report.provider_name}")
        lines.append("| Scenario | Outcome | Grade | Overall Score | P50 Latency | P95 Latency | Humanlikeness | Compliance Fails | Latency SLO Passed |")
        lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        
        for scenario_id, metrics in report.scenario_results.items():
            outcome_str = "PASSED" if metrics.outcome_passed else "FAILED"
            latency_slo_str = "FAILED" if metrics.latency_failed else "PASSED"
            comp_fail_count = (
                metrics.compliance_hard_fail_count +
                metrics.dnc_failure_count +
                metrics.wrong_number_failure_count +
                metrics.transfer_without_consent_count
            )
            
            lines.append(
                f"| {scenario_id} | {outcome_str} | **{metrics.overall_grade}** | {metrics.overall_score:.1f} | {metrics.p50_turn_latency_ms:.1f}ms | {metrics.p95_turn_latency_ms:.1f}ms | {metrics.humanlikeness_score:.1f}% | {comp_fail_count} | {latency_slo_str} |"
            )
        lines.append("")
        
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Voice Platform Benchmark Leaderboard.")
    parser.add_argument("--providers", type=str, default="benchmarks/voice_platform_benchmark/providers.yaml", help="Path to providers config.")
    parser.add_argument("--scenarios", type=str, default="benchmarks/voice_platform_benchmark/scenarios.yaml", help="Path to scenarios config.")
    parser.add_argument("--output-dir", type=str, default="data/benchmarks", help="Output directory for results.")
    
    args = parser.parse_args()
    
    # Setup paths
    providers_path = args.providers
    scenarios_path = args.scenarios
    output_dir = args.output_dir
    
    if not os.path.exists(providers_path) or not os.path.exists(scenarios_path):
        print(f"Error: Config files not found: {providers_path} or {scenarios_path}")
        return
        
    # Run the benchmark
    print("Running Voice Platform Benchmark Suite...")
    result = run_benchmark(providers_path, scenarios_path)
    
    # Create output dir
    os.makedirs(output_dir, exist_ok=True)
    
    # Save JSON report
    json_path = os.path.join(output_dir, "leaderboard.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(mode="json"), f, indent=2)
    print(f"Saved JSON leaderboard to: {json_path}")
    
    # Generate and Save Markdown
    md_content = generate_markdown(result)
    md_path = os.path.join(output_dir, "leaderboard.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Saved Markdown leaderboard to: {md_path}")
    
    # Print Markdown summary table to console
    print("\n--- BENCHMARK LEADERBOARD ---")
    print(md_content.split("## Detailed Scenario Breakdown")[0].strip())
    print("-----------------------------\n")

if __name__ == "__main__":
    main()
