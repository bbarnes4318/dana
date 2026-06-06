import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from benchmarks.voice_platform_benchmark.metrics_schema import ScenarioMetrics, Slotargets
from benchmarks.voice_platform_benchmark.score_latency import score_latency
from benchmarks.voice_platform_benchmark.score_cost import score_cost
from benchmarks.voice_platform_benchmark.score_humanlikeness import score_humanlikeness
from benchmarks.voice_platform_benchmark.score_compliance import score_compliance
from benchmarks.voice_platform_benchmark.score_outcomes import score_outcome

def run_transcript_replay(
    provider_id: str,
    scenario_id: str,
    transcript: List[Dict[str, Any]],  # List of {"speaker": "prospect"|"dana", "text": str, "tool": str|None, "stage": str|None, "metadata": dict|None}
    provider_config: Dict[str, Any],
    expected_outcome: str,
    slo_targets: Slotargets = Slotargets()
) -> ScenarioMetrics:
    """
    Runs a replay-based benchmark for a single scenario/provider.
    Scores compliance, cost, humanlikeness, latency, and outcomes.
    Applies compliance gates and flags SLO failures.
    """
    # 1. Extract latency data if present in metadata, otherwise fall back to provider_config defaults
    turn_response_latencies: List[float] = []
    llm_first_token_latencies: List[float] = []
    tts_first_audio_latencies: List[float] = []
    barge_in_stop_latencies: List[float] = []
    
    for turn in transcript:
        meta = turn.get("metadata") or {}
        # Support both direct keys or nested duration dictionary matching LatencyRecorder to_dict
        durations = meta.get("durations") or meta
        
        # Check for turn_response_latency / turn_response_latency_ms
        turn_lat = durations.get("turn_response_latency") or durations.get("turn_response_latency_ms")
        if turn_lat is not None:
            turn_response_latencies.append(float(turn_lat))
            
        # Check for llm_first_token_latency
        llm_lat = durations.get("llm_first_token_latency") or durations.get("llm_first_token_latency_ms") or durations.get("llm_first_token_ms")
        if llm_lat is not None:
            llm_first_token_latencies.append(float(llm_lat))
            
        # Check for tts_synthesis_start_latency
        tts_lat = durations.get("tts_synthesis_start_latency") or durations.get("tts_synthesis_start_latency_ms") or durations.get("tts_first_audio_ms")
        if tts_lat is not None:
            tts_first_audio_latencies.append(float(tts_lat))
            
        # Check for barge_in_stop_audio_latency
        barge_lat = durations.get("barge_in_stop_audio_latency") or durations.get("barge_in_stop_audio_latency_ms") or durations.get("barge_in_stop_audio_ms")
        if barge_lat is not None:
            barge_in_stop_latencies.append(float(barge_lat))
            
    # Fallback to provider profile configuration if no dynamic latencies are recorded
    if not turn_response_latencies:
        turn_response_latencies = [provider_config.get("p50_turn_latency_ms", 400.0)]
    if not llm_first_token_latencies:
        llm_first_token_latencies = [provider_config.get("llm_first_token_ms", 200.0)]
    if not tts_first_audio_latencies:
        tts_first_audio_latencies = [provider_config.get("tts_first_audio_ms", 150.0)]
    if not barge_in_stop_latencies:
        barge_in_stop_latencies = [provider_config.get("barge_in_stop_audio_ms", 150.0)]
        
    latency_res = score_latency(
        turn_response_latencies=turn_response_latencies,
        llm_first_token_latencies=llm_first_token_latencies,
        tts_first_audio_latencies=tts_first_audio_latencies,
        barge_in_stop_latencies=barge_in_stop_latencies,
        slo_targets=slo_targets
    )
    
    # 2. Score Cost
    # Assume 15 seconds per turn if duration not provided
    total_turns = len(transcript)
    estimated_duration_seconds = total_turns * 15.0
    
    # Determine if transferred
    actual_outcome = "ended"
    for turn in transcript:
        if turn.get("tool") in ("feTransfer", "transfer") or turn.get("stage") in ("transfer_ready", "transfer"):
            actual_outcome = "transferred"
        elif turn.get("stage") == "dnc" or turn.get("tool") == "mark_dnc":
            actual_outcome = "dnc"
        elif turn.get("stage") == "callback" or turn.get("tool") == "schedule_callback":
            actual_outcome = "callback"
        elif turn.get("stage") == "disqualified":
            actual_outcome = "disqualified"
            
    cost_res = score_cost(
        duration_seconds=estimated_duration_seconds,
        was_transferred=(actual_outcome == "transferred"),
        cost_per_connected_minute=provider_config.get("cost_per_connected_minute", 0.05),
        cost_per_qualified_transfer=provider_config.get("cost_per_qualified_transfer", 0.0)
    )
    
    # 3. Score Humanlikeness
    agent_turns = [t.get("text", "") for t in transcript if t.get("speaker") == "dana"]
    human_res = score_humanlikeness(agent_turns=agent_turns)
    
    # 4. Score Compliance
    comp_res = score_compliance(transcript=transcript)
    
    # 5. Score Outcome
    out_res = score_outcome(actual_outcome=actual_outcome, expected_outcome=expected_outcome)
    
    # Apply hard gates:
    # - Any DNC failure = F
    # - Any wrong-number failure = F
    # - Any transfer without consent = F
    # - Any forbidden compliance phrase = F
    has_compliance_fail = (
        comp_res["compliance_hard_fail_count"] > 0 or
        comp_res["dnc_failure_count"] > 0 or
        comp_res["wrong_number_failure_count"] > 0 or
        comp_res["transfer_without_consent_count"] > 0
    )
    
    # Combine scores into overall score
    if has_compliance_fail:
        overall_score = 0.0
        overall_grade = "F"
    else:
        # Weighted sum: 30% Latency, 20% Cost, 30% Humanlikeness, 20% Outcome
        weighted_score = (
            0.30 * latency_res["latency_score"] +
            0.20 * cost_res["cost_score"] +
            0.30 * human_res["humanlikeness_score"] +
            0.20 * out_res["outcome_score"]
        )
        overall_score = round(weighted_score, 2)
        
        # Determine grade
        if overall_score >= 90.0:
            overall_grade = "A"
        elif overall_score >= 80.0:
            overall_grade = "B"
        elif overall_score >= 70.0:
            overall_grade = "C"
        elif overall_score >= 60.0:
            overall_grade = "D"
        else:
            overall_grade = "F"
            
    # Merge outputs into ScenarioMetrics
    return ScenarioMetrics(
        scenario_id=scenario_id,
        provider_id=provider_id,
        
        p50_turn_latency_ms=latency_res["p50_turn_latency_ms"],
        p95_turn_latency_ms=latency_res["p95_turn_latency_ms"],
        transcript_final_to_first_audio_ms=latency_res["transcript_final_to_first_audio_ms"],
        llm_first_token_ms=latency_res["llm_first_token_ms"],
        tts_first_audio_ms=latency_res["tts_first_audio_ms"],
        barge_in_stop_audio_ms=latency_res["barge_in_stop_audio_ms"],
        
        compliance_hard_fail_count=comp_res["compliance_hard_fail_count"],
        dnc_failure_count=comp_res["dnc_failure_count"],
        wrong_number_failure_count=comp_res["wrong_number_failure_count"],
        transfer_without_consent_count=comp_res["transfer_without_consent_count"],
        
        bot_like_phrase_count=human_res["bot_like_phrase_count"],
        repetition_count=human_res["repetition_count"],
        avg_words_per_turn=human_res["avg_words_per_turn"],
        humanlikeness_score=human_res["humanlikeness_score"],
        
        cost_per_connected_minute=provider_config.get("cost_per_connected_minute", 0.0),
        cost_per_qualified_transfer=provider_config.get("cost_per_qualified_transfer", 0.0),
        
        outcome_passed=out_res["outcome_passed"],
        overall_score=overall_score,
        overall_grade=overall_grade,
        latency_failed=latency_res["latency_failed"]
    )
