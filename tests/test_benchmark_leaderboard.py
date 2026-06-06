import os
import pytest
from benchmarks.voice_platform_benchmark.leaderboard import run_benchmark, generate_markdown
from benchmarks.voice_platform_benchmark.metrics_schema import Slotargets

def test_run_benchmark_and_markdown_generation():
    providers_path = "benchmarks/voice_platform_benchmark/providers.yaml"
    scenarios_path = "benchmarks/voice_platform_benchmark/scenarios.yaml"
    
    assert os.path.exists(providers_path)
    assert os.path.exists(scenarios_path)
    
    result = run_benchmark(providers_path, scenarios_path)
    
    assert result.run_id is not None
    assert "dana_local" in result.provider_reports
    assert "retell_reference" in result.provider_reports
    
    report = result.provider_reports["dana_local"]
    assert report.avg_p50_latency_ms > 0
    assert len(report.scenario_results) == 14
    
    # Generate markdown
    md = generate_markdown(result)
    assert "# Outbound Voice Platform Benchmark Leaderboard" in md
    assert "Dana Local" in md
    assert "Retell AI Reference Profile" in md
