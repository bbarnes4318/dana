import pytest
from benchmarks.voice_platform_benchmark.metrics_schema import (
    Slotargets,
    ScenarioMetrics,
    ProviderBenchmarkReport,
    BenchmarkRunResult
)

def test_slo_targets_default_values():
    slo = Slotargets()
    assert slo.p50_turn_latency_max_ms == 450.0
    assert slo.p95_turn_latency_max_ms == 850.0
    assert slo.barge_in_stop_max_ms == 200.0
    assert slo.tts_first_audio_max_ms == 200.0
    assert slo.llm_first_token_max_ms == 250.0

def test_scenario_metrics_defaults():
    metrics = ScenarioMetrics(
        scenario_id="test_scen",
        provider_id="dana_local"
    )
    assert metrics.scenario_id == "test_scen"
    assert metrics.provider_id == "dana_local"
    assert metrics.p50_turn_latency_ms == 0.0
    assert metrics.compliance_hard_fail_count == 0
    assert metrics.overall_grade == "A"
    assert metrics.latency_failed is False

def test_provider_benchmark_report_serialization():
    metrics = ScenarioMetrics(
        scenario_id="normal_interested",
        provider_id="dana_local",
        p50_turn_latency_ms=350.0,
        overall_score=95.0,
        overall_grade="A"
    )
    
    report = ProviderBenchmarkReport(
        provider_id="dana_local",
        provider_name="Dana Local",
        avg_p50_latency_ms=350.0,
        avg_overall_score=95.0,
        overall_grade="A",
        scenario_results={"normal_interested": metrics}
    )
    
    dumped = report.model_dump(mode="json")
    assert dumped["provider_id"] == "dana_local"
    assert dumped["scenario_results"]["normal_interested"]["p50_turn_latency_ms"] == 350.0
    assert dumped["scenario_results"]["normal_interested"]["overall_grade"] == "A"
