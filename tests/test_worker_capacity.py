"""Tests for ops/worker_capacity.py monitoring."""

import os
from unittest.mock import patch, AsyncMock
import pytest
from ops.worker_capacity import WorkerCapacity


@pytest.fixture(autouse=True)
def run_around_tests():
    """Reset capacity counters before and after each test."""
    WorkerCapacity.reset()
    yield
    WorkerCapacity.reset()


def test_capacity_counters_increment_decrement():
    assert WorkerCapacity.get_active_calls() == 0
    WorkerCapacity.increment_calls()
    assert WorkerCapacity.get_active_calls() == 1
    WorkerCapacity.decrement_calls()
    assert WorkerCapacity.get_active_calls() == 0


def test_capacity_task_tracking():
    assert WorkerCapacity.get_active_stt() == 0
    WorkerCapacity.increment_stt()
    assert WorkerCapacity.get_active_stt() == 1
    WorkerCapacity.decrement_stt()
    
    assert WorkerCapacity.get_active_llm() == 0
    WorkerCapacity.increment_llm()
    assert WorkerCapacity.get_active_llm() == 1
    WorkerCapacity.decrement_llm()
    
    assert WorkerCapacity.get_active_tts() == 0
    WorkerCapacity.increment_tts()
    assert WorkerCapacity.get_active_tts() == 1
    WorkerCapacity.decrement_tts()


def test_latency_slo_monitor():
    assert WorkerCapacity.get_p95_latency() == 0.0
    
    # Record some latencies
    latencies = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0]
    for lat in latencies:
        WorkerCapacity.record_turn_latency(lat)

    p95 = WorkerCapacity.get_p95_latency()
    # 95th percentile of 10 items is index 9 (sorted) -> 1000.0
    assert p95 == 1000.0
    assert WorkerCapacity.is_degraded() is True  # 1000ms > 900ms SLO threshold


def test_has_capacity_checks():
    # 1. Under limits -> should have capacity
    assert WorkerCapacity.has_capacity() is True

    # 2. Exceed calls -> block
    for _ in range(WorkerCapacity.max_concurrent_calls):
        WorkerCapacity.increment_calls()
    assert WorkerCapacity.has_capacity() is False
    WorkerCapacity.reset()

    # 3. Exceed STT -> block
    for _ in range(WorkerCapacity.max_stt_tasks):
        WorkerCapacity.increment_stt()
    assert WorkerCapacity.has_capacity() is False
    WorkerCapacity.reset()

    # 4. GPU overload -> block
    with patch.dict(os.environ, {"DANA_MOCK_GPU_UTILIZATION": "0.95"}):
        assert WorkerCapacity.check_gpu_utilization() == 0.95
        assert WorkerCapacity.has_capacity() is False


@pytest.mark.asyncio
async def test_request_fnc_accepts_when_has_capacity():
    from main import request_fnc
    req = AsyncMock()
    req.id = "test-job-1"
    
    # Ensure has_capacity returns True
    assert WorkerCapacity.has_capacity() is True
    
    await request_fnc(req)
    req.accept.assert_called_once()
    req.reject.assert_not_called()


@pytest.mark.asyncio
async def test_request_fnc_rejects_when_no_capacity():
    from main import request_fnc
    req = AsyncMock()
    req.id = "test-job-2"
    
    # Force capacity full by incrementing calls to max
    for _ in range(WorkerCapacity.max_concurrent_calls):
        WorkerCapacity.increment_calls()
    assert WorkerCapacity.has_capacity() is False
    
    await request_fnc(req)
    req.reject.assert_called_once()
    req.accept.assert_not_called()
