from pydantic import BaseModel, Field
from typing import Dict, List, Optional

class Slotargets(BaseModel):
    p50_turn_latency_max_ms: float = 450.0
    p95_turn_latency_max_ms: float = 850.0
    barge_in_stop_max_ms: float = 200.0
    tts_first_audio_max_ms: float = 200.0
    llm_first_token_max_ms: float = 250.0

class ScenarioMetrics(BaseModel):
    scenario_id: str
    provider_id: str
    
    # Latency (in ms)
    p50_turn_latency_ms: float = 0.0
    p95_turn_latency_ms: float = 0.0
    transcript_final_to_first_audio_ms: float = 0.0
    llm_first_token_ms: float = 0.0
    tts_first_audio_ms: float = 0.0
    barge_in_stop_audio_ms: float = 0.0
    
    # Compliance & Safety
    compliance_hard_fail_count: int = 0
    dnc_failure_count: int = 0
    wrong_number_failure_count: int = 0
    transfer_without_consent_count: int = 0
    
    # Conversational Quality / Humanlikeness
    bot_like_phrase_count: int = 0
    repetition_count: int = 0
    avg_words_per_turn: float = 0.0
    humanlikeness_score: float = 100.0
    
    # Financials
    cost_per_connected_minute: float = 0.0
    cost_per_qualified_transfer: float = 0.0
    
    # Outcomes & overall
    outcome_passed: bool = True
    overall_score: float = 100.0
    overall_grade: str = "A"  # A, B, C, D, F
    latency_failed: bool = False

class ProviderBenchmarkReport(BaseModel):
    provider_id: str
    provider_name: str
    avg_p50_latency_ms: float = 0.0
    avg_p95_latency_ms: float = 0.0
    compliance_failed_scenarios: int = 0
    latency_failed_scenarios: int = 0
    avg_humanlikeness_score: float = 0.0
    avg_overall_score: float = 0.0
    overall_grade: str = "A"
    total_cost: float = 0.0
    scenario_results: Dict[str, ScenarioMetrics] = Field(default_factory=dict)

class BenchmarkRunResult(BaseModel):
    run_id: str
    timestamp: str
    slo_targets: Slotargets = Field(default_factory=Slotargets)
    provider_reports: Dict[str, ProviderBenchmarkReport] = Field(default_factory=dict)
