"""Unit tests for provider metrics rollups."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from analytics.provider_rollups import get_provider_performance


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_provider_performance(repo: Repository):
    # Setup mock provider decisions
    # Deepgram STT - 2 attempts, 1 success (latency 150ms), 1 failure due to timeout (latency 200ms)
    await repo.save_provider_decision(
        call_id="call-1",
        component="stt",
        selected_provider="deepgram",
        decision_reason="normal routing",
        latency_ms=150.0,
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    await repo.save_provider_decision(
        call_id="call-2",
        component="stt",
        selected_provider="deepgram",
        decision_reason="timeout occurred",
        latency_ms=200.0,
        created_at=datetime(2026, 6, 1, 12, 1, tzinfo=timezone.utc)
    )
    
    # ElevenLabs TTS - 1 attempt, success (latency 250ms)
    await repo.save_provider_decision(
        call_id="call-1",
        component="tts",
        selected_provider="elevenlabs",
        decision_reason="high quality routing",
        latency_ms=250.0,
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    
    # Whisper STT - 1 old attempt (outside date range), success
    await repo.save_provider_decision(
        call_id="call-old",
        component="stt",
        selected_provider="whisper",
        decision_reason="fallback to local",
        latency_ms=500.0,
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    )
    
    # Setup mock costs
    # Deepgram cost: 0.05
    await repo.save_call_cost(
        call_id="call-1",
        component="stt",
        provider="deepgram",
        estimated_cost=0.05,
        created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc)
    )
    # ElevenLabs cost: 0.15
    await repo.save_call_cost(
        call_id="call-1",
        component="tts",
        provider="elevenlabs",
        estimated_cost=0.15,
        created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc)
    )

    # Test all-time performance
    perf = await get_provider_performance(repo)
    
    # Deepgram attempts = 2, failures = 1 (timeout). Failure rate = 0.5. Latency = (150+200)/2 = 175. Cost = 0.05 / 1 = 0.05
    assert perf["usage_by_component"]["stt"]["deepgram"] == 2
    assert perf["failure_rates"]["deepgram"] == 0.5
    assert perf["average_latencies"]["deepgram"] == 175.0
    assert perf["average_costs"]["deepgram"] == 0.05
    
    # Elevenlabs attempts = 1, failures = 0. Failure rate = 0.0. Latency = 250. Cost = 0.15
    assert perf["usage_by_component"]["tts"]["elevenlabs"] == 1
    assert perf["failure_rates"]["elevenlabs"] == 0.0
    assert perf["average_latencies"]["elevenlabs"] == 250.0
    assert perf["average_costs"]["elevenlabs"] == 0.15
    
    # Whisper included in all-time
    assert perf["usage_by_component"]["stt"]["whisper"] == 1

    # Test date filtered performance (June 1 only)
    perf_filtered = await get_provider_performance(
        repo,
        from_date=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        to_date=datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc)
    )
    # Whisper excluded
    assert "whisper" not in perf_filtered["failure_rates"]
    assert "whisper" not in perf_filtered["usage_by_component"]["stt"]
    assert perf_filtered["usage_by_component"]["stt"]["deepgram"] == 2
