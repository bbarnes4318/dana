"""Tests for ops/healthcheck.py execution."""

from unittest.mock import AsyncMock, patch
import pytest
from ops.healthcheck import run_healthcheck
from ops.worker_capacity import WorkerCapacity


@pytest.fixture(autouse=True)
def run_around_tests():
    WorkerCapacity.reset()
    yield
    WorkerCapacity.reset()


@pytest.mark.asyncio
async def test_healthcheck_all_passed():
    with patch("ops.healthcheck.audit_dependencies", return_value=(True, [])), \
         patch("ops.healthcheck.run_readiness_checks", return_value=(True, {
             "livekit": (True, "ok"),
             "stt": (True, "ok"),
             "llm": (True, "ok"),
             "tts": (True, "ok"),
             "vad": (True, "ok"),
         })):
        
        is_healthy, msg = await run_healthcheck()
        assert is_healthy is True
        assert "passed" in msg


@pytest.mark.asyncio
async def test_healthcheck_dependency_drift():
    with patch("ops.healthcheck.audit_dependencies", return_value=(False, ["livekit mismatch"])):
        is_healthy, msg = await run_healthcheck()
        assert is_healthy is False
        assert "Dependency drift" in msg


@pytest.mark.asyncio
async def test_healthcheck_readiness_failed():
    with patch("ops.healthcheck.audit_dependencies", return_value=(True, [])), \
         patch("ops.healthcheck.run_readiness_checks", return_value=(False, {
             "livekit": (True, "ok"),
             "stt": (False, "STT download failed"),
             "llm": (True, "ok"),
             "tts": (True, "ok"),
             "vad": (True, "ok"),
         })):
        
        is_healthy, msg = await run_healthcheck()
        assert is_healthy is False
        assert "STT download failed" in msg


@pytest.mark.asyncio
async def test_healthcheck_latency_degraded():
    # Make the worker degraded
    latencies = [1000.0, 1000.0, 1000.0]
    for lat in latencies:
        WorkerCapacity.record_turn_latency(lat)
        
    with patch("ops.healthcheck.audit_dependencies", return_value=(True, [])), \
         patch("ops.healthcheck.run_readiness_checks", return_value=(True, {
             "livekit": (True, "ok"),
             "stt": (True, "ok"),
             "llm": (True, "ok"),
             "tts": (True, "ok"),
             "vad": (True, "ok"),
         })):
        
        is_healthy, msg = await run_healthcheck()
        assert is_healthy is False
        assert "Latency SLO degraded" in msg
