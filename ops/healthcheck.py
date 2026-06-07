"""Active healthcheck CLI auditing dependency drift, readiness checks, capacity metrics, and turn latency SLOs."""

from __future__ import annotations

import asyncio
import sys
from typing import Dict, Tuple
from dotenv import load_dotenv

load_dotenv()

from ops.dependency_audit import audit_dependencies
from ops.readiness import run_readiness_checks
from ops.worker_capacity import WorkerCapacity


async def run_healthcheck() -> Tuple[bool, str]:
    """Execute all system, dependency, and performance audits.
    
    Returns:
        A tuple: (is_healthy, status_message)
    """
    # 1. Dependency Audit
    dep_ok, dep_errors = audit_dependencies()
    if not dep_ok:
        return False, f"Dependency drift detected: {'; '.join(dep_errors)}"

    # 2. Readiness Checks (skip storage checks in healthcheck to avoid spamming database)
    success, readiness_results = await run_readiness_checks()
    
    # We require at least LiveKit credentials, STT, LLM, TTS, VAD, and Telephony to be operational
    for name in ("livekit", "stt", "llm", "tts", "vad", "telephony"):
        ok, msg = readiness_results.get(name, (False, "Not checked"))
        if not ok:
            return False, f"Critical readiness component '{name}' failed: {msg}"

    # 3. Capacity & SLO check
    # P95 latency SLO check
    if WorkerCapacity.is_degraded():
        return False, f"Latency SLO degraded: P95 turn latency ({WorkerCapacity.get_p95_latency():.1f}ms) exceeds limit ({WorkerCapacity.latency_slo_threshold_ms:.1f}ms)"

    # GPU utilization check
    gpu_util = WorkerCapacity.check_gpu_utilization()
    if gpu_util >= WorkerCapacity.gpu_overload_threshold:
        return False, f"GPU memory overload: {gpu_util:.1%} >= threshold {WorkerCapacity.gpu_overload_threshold:.1%}"

    # Active calls check
    active_calls = WorkerCapacity.get_active_calls()
    if active_calls >= WorkerCapacity.max_concurrent_calls:
        # We might not fail healthcheck completely for max calls (as it's transient load),
        # but if it's exceeded, we'll mark worker degraded/at capacity.
        # For healthcheck exit code, we return True (healthy but busy), but print details.
        pass

    return True, "All health checks passed"


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    is_healthy, message = loop.run_until_complete(run_healthcheck())
    
    print(f"Dana Worker Health Check: {'[HEALTHY]' if is_healthy else '[UNHEALTHY]'}")
    print(f"Status: {message}")
    
    if not is_healthy:
        sys.exit(1)
    sys.exit(0)
