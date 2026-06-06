from typing import List, Dict, Any
from benchmarks.voice_platform_benchmark.metrics_schema import Slotargets

def percentile(data: List[float], q: float) -> float:
    """Calculate the q-th percentile of a list of data points (0.0 <= q <= 1.0)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    idx = (n - 1) * q
    floor_idx = int(idx)
    ceil_idx = min(floor_idx + 1, n - 1)
    if floor_idx == ceil_idx:
        return float(sorted_data[floor_idx])
    return float(sorted_data[floor_idx] + (sorted_data[ceil_idx] - sorted_data[floor_idx]) * (idx - floor_idx))

def score_latency(
    turn_response_latencies: List[float],
    llm_first_token_latencies: List[float],
    tts_first_audio_latencies: List[float],
    barge_in_stop_latencies: List[float],
    slo_targets: Slotargets = Slotargets()
) -> Dict[str, Any]:
    """
    Computes latency metrics and flags SLO failures.
    Returns:
        Dict containing:
            - p50_turn_latency_ms
            - p95_turn_latency_ms
            - transcript_final_to_first_audio_ms (average turn latency)
            - llm_first_token_ms (average LLM first token latency)
            - tts_first_audio_ms (average TTS first audio latency)
            - barge_in_stop_audio_ms (average barge-in stop latency)
            - latency_failed (bool)
            - score_penalty (float)
    """
    p50_turn = percentile(turn_response_latencies, 0.50)
    p95_turn = percentile(turn_response_latencies, 0.95)
    
    avg_turn = sum(turn_response_latencies) / len(turn_response_latencies) if turn_response_latencies else 0.0
    avg_llm = sum(llm_first_token_latencies) / len(llm_first_token_latencies) if llm_first_token_latencies else 0.0
    avg_tts = sum(tts_first_audio_latencies) / len(tts_first_audio_latencies) if tts_first_audio_latencies else 0.0
    avg_barge = sum(barge_in_stop_latencies) / len(barge_in_stop_latencies) if barge_in_stop_latencies else 0.0
    
    # Check SLO targets
    failed = False
    if p50_turn > slo_targets.p50_turn_latency_max_ms:
        failed = True
    if p95_turn > slo_targets.p95_turn_latency_max_ms:
        failed = True
    if avg_llm > slo_targets.llm_first_token_max_ms:
        failed = True
    if avg_tts > slo_targets.tts_first_audio_max_ms:
        failed = True
    if avg_barge > slo_targets.barge_in_stop_max_ms:
        failed = True
        
    # Calculate latency score (100 is perfect, subtract points for SLO breaches)
    latency_score = 100.0
    if p50_turn > slo_targets.p50_turn_latency_max_ms:
        latency_score -= 15.0
    if p95_turn > slo_targets.p95_turn_latency_max_ms:
        # Subtract more for P95 violations as it directly degrades UX
        latency_score -= 25.0
    if avg_llm > slo_targets.llm_first_token_max_ms:
        latency_score -= 10.0
    if avg_tts > slo_targets.tts_first_audio_max_ms:
        latency_score -= 10.0
    if avg_barge > slo_targets.barge_in_stop_max_ms:
        latency_score -= 10.0
        
    latency_score = max(0.0, latency_score)
    
    return {
        "p50_turn_latency_ms": round(p50_turn, 2),
        "p95_turn_latency_ms": round(p95_turn, 2),
        "transcript_final_to_first_audio_ms": round(avg_turn, 2),
        "llm_first_token_ms": round(avg_llm, 2),
        "tts_first_audio_ms": round(avg_tts, 2),
        "barge_in_stop_audio_ms": round(avg_barge, 2),
        "latency_failed": failed,
        "latency_score": latency_score
    }
