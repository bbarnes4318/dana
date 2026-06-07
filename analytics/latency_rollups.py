"""Latency analytics and percentile rollup functions."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from storage.repository import Repository
from analytics.platform_metrics import is_within_range


def calculate_percentile(values: list[float], percentile: float) -> float:
    """Calculate the percentile of a list of values using linear interpolation."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * (percentile / 100.0)
    idx = int(math.floor(k))
    frac = k - idx
    if idx + 1 < len(sorted_values):
        return sorted_values[idx] + frac * (sorted_values[idx + 1] - sorted_values[idx])
    else:
        return sorted_values[idx]


async def get_latency_metrics(
    repository: Optional[Repository] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None
) -> dict:
    """Calculate P50 and P95 percentiles for core latency and interruption metrics."""
    repo = repository or Repository()
    
    # Load all latency metrics
    metrics = await repo.store.query("latency_metrics", {})
    
    # Filter by date range
    filtered = [
        m for m in metrics
        if is_within_range(m.get("created_at") or m.get("timestamp"), from_date, to_date)
    ]
    
    # Group values by metric categories
    categories = {
        "turn_latency": [],
        "llm_first_token": [],
        "tts_first_audio": [],
        "barge_in_stop": [],
        "total_barge_in_stop": [],
        "tts_cancel_duration": [],
        "audio_flush_duration": []
    }
    
    # Mapping table for metric names
    mapping = {
        "turn_response_latency": "turn_latency",
        "turn_latency": "turn_latency",
        "llm_first_token_latency": "llm_first_token",
        "llm_first_token": "llm_first_token",
        "tts_synthesis_start_latency": "tts_first_audio",
        "tts_first_audio": "tts_first_audio",
        "barge_in_stop_audio_latency": "barge_in_stop",
        "barge_in_stop": "barge_in_stop"
    }
    
    # Call-level stats for false interruptions to prevent turn double-counting
    call_false_counts = {}
    call_false_rates = {}
    
    # Stage-level list of total_barge_in_stop_ms
    stage_interruption_latencies = {}

    for m in filtered:
        name = m.get("metric_name")
        val = m.get("metric_value_ms")
        call_id = m.get("call_id") or "unknown"
        if val is None:
            continue
            
        val_f = float(val)
        
        if name in mapping:
            categories[mapping[name]].append(val_f)
        elif name == "total_barge_in_stop_ms":
            categories["total_barge_in_stop"].append(val_f)
        elif name == "tts_cancel_duration_ms":
            categories["tts_cancel_duration"].append(val_f)
        elif name == "audio_flush_duration_ms":
            categories["audio_flush_duration"].append(val_f)
        elif name == "false_interruption_count":
            call_false_counts[call_id] = max(call_false_counts.get(call_id, 0.0), val_f)
        elif name == "false_interruption_rate":
            call_false_rates[call_id] = max(call_false_rates.get(call_id, 0.0), val_f)
            
        # Group stage specific metrics (formatted as metric_name_stage_STAGE)
        if "_stage_" in name:
            parts = name.split("_stage_")
            base_name = parts[0]
            stage_name = parts[1].upper()
            if base_name in ("total_barge_in_stop_ms", "barge_in_stop_audio_latency", "barge_in_stop"):
                stage_interruption_latencies.setdefault(stage_name, []).append(val_f)

    # Roll up percentiles by stage
    interruption_latency_by_stage = {}
    for stage_name, vals in stage_interruption_latencies.items():
        if vals:
            interruption_latency_by_stage[stage_name] = {
                "p50": round(calculate_percentile(vals, 50.0), 2),
                "p95": round(calculate_percentile(vals, 95.0), 2),
                "count": len(vals)
            }

    # Calculate P50 and P95 for each
    return {
        "p50_turn_latency": round(calculate_percentile(categories["turn_latency"], 50.0), 2),
        "p95_turn_latency": round(calculate_percentile(categories["turn_latency"], 95.0), 2),
        "p50_llm_first_token": round(calculate_percentile(categories["llm_first_token"], 50.0), 2),
        "p95_llm_first_token": round(calculate_percentile(categories["llm_first_token"], 95.0), 2),
        "p50_tts_first_audio": round(calculate_percentile(categories["tts_first_audio"], 50.0), 2),
        "p95_tts_first_audio": round(calculate_percentile(categories["tts_first_audio"], 95.0), 2),
        "p50_barge_in_stop": round(calculate_percentile(categories["barge_in_stop"], 50.0), 2),
        "p95_barge_in_stop": round(calculate_percentile(categories["barge_in_stop"], 95.0), 2),
        
        # Interruption Telemetry metrics
        "p50_total_barge_in_stop_ms": round(calculate_percentile(categories["total_barge_in_stop"], 50.0), 2),
        "p95_total_barge_in_stop_ms": round(calculate_percentile(categories["total_barge_in_stop"], 95.0), 2),
        "p50_tts_cancel_duration_ms": round(calculate_percentile(categories["tts_cancel_duration"], 50.0), 2),
        "p95_tts_cancel_duration_ms": round(calculate_percentile(categories["tts_cancel_duration"], 95.0), 2),
        "p50_audio_flush_duration_ms": round(calculate_percentile(categories["audio_flush_duration"], 50.0), 2),
        "p95_audio_flush_duration_ms": round(calculate_percentile(categories["audio_flush_duration"], 95.0), 2),
        "false_interruption_count": int(sum(call_false_counts.values())),
        "false_interruption_rate": round(sum(call_false_rates.values()) / len(call_false_rates), 4) if call_false_rates else 0.0,
        "interruption_latency_by_stage": interruption_latency_by_stage
    }
