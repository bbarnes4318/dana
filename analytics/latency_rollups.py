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
    """Calculate P50 and P95 percentiles for core latency metrics."""
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
        "barge_in_stop": []
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
    
    for m in filtered:
        name = m.get("metric_name")
        val = m.get("metric_value_ms")
        if name in mapping and val is not None:
            categories[mapping[name]].append(float(val))
            
    # Calculate P50 and P95 for each
    return {
        "p50_turn_latency": round(calculate_percentile(categories["turn_latency"], 50.0), 2),
        "p95_turn_latency": round(calculate_percentile(categories["turn_latency"], 95.0), 2),
        "p50_llm_first_token": round(calculate_percentile(categories["llm_first_token"], 50.0), 2),
        "p95_llm_first_token": round(calculate_percentile(categories["llm_first_token"], 95.0), 2),
        "p50_tts_first_audio": round(calculate_percentile(categories["tts_first_audio"], 50.0), 2),
        "p95_tts_first_audio": round(calculate_percentile(categories["tts_first_audio"], 95.0), 2),
        "p50_barge_in_stop": round(calculate_percentile(categories["barge_in_stop"], 50.0), 2),
        "p95_barge_in_stop": round(calculate_percentile(categories["barge_in_stop"], 95.0), 2)
    }
