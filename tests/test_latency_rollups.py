"""Unit tests for latency metrics percentiles rollups."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from analytics.latency_rollups import get_latency_metrics, calculate_percentile


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


def test_percentile_calculation():
    # Simple list of 1 to 10
    vals = [float(x) for x in range(1, 11)]
    # P50 should be 5.5
    assert calculate_percentile(vals, 50.0) == 5.5
    # P95 should be 9.55
    assert calculate_percentile(vals, 95.0) == pytest.approx(9.55)
    # Empty list
    assert calculate_percentile([], 50.0) == 0.0


@pytest.mark.asyncio
async def test_get_latency_metrics(repo: Repository):
    # Setup test latency metrics
    # Values: 100, 200, 300, 400, 500
    for val in [100.0, 200.0, 300.0, 400.0, 500.0]:
        await repo.save_latency_metric(
            call_id="call-1",
            metric_name="turn_response_latency",
            metric_value_ms=val,
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        )
        await repo.save_latency_metric(
            call_id="call-1",
            metric_name="llm_first_token_latency",
            metric_value_ms=val,
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        )
        await repo.save_latency_metric(
            call_id="call-1",
            metric_name="tts_synthesis_start_latency",
            metric_value_ms=val,
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        )
        await repo.save_latency_metric(
            call_id="call-1",
            metric_name="barge_in_stop_audio_latency",
            metric_value_ms=val,
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        )
        
    # Add metrics outside date range
    await repo.save_latency_metric(
        call_id="call-old",
        metric_name="turn_latency",
        metric_value_ms=1000.0,
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    )

    # Test all-time metrics
    metrics = await get_latency_metrics(repo)
    # The turn_latency values will include 1000.0, so: [100, 200, 300, 400, 500, 1000]
    # P50: 350.0. P95: 875.0.
    assert metrics["p50_turn_latency"] == 350.0
    assert metrics["p95_turn_latency"] == 875.0
    
    # Other metrics (no older entries): [100, 200, 300, 400, 500]
    # P50: 300.0. P95: 480.0.
    assert metrics["p50_llm_first_token"] == 300.0
    assert metrics["p95_llm_first_token"] == 480.0

    # Test date filtered metrics
    metrics_filtered = await get_latency_metrics(
        repo,
        from_date=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        to_date=datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc)
    )
    assert metrics_filtered["p50_turn_latency"] == 300.0
    assert metrics_filtered["p95_turn_latency"] == 480.0
