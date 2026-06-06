"""Worker capacity tracking and latency SLO monitoring for production servers."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class WorkerCapacity:
    """Manages local concurrency counters, GPU overload monitoring, and latency SLOs."""

    _lock = threading.Lock()
    _active_calls = 0
    _active_stt_tasks = 0
    _active_llm_tasks = 0
    _active_tts_tasks = 0
    
    # Track the last 50 turn latencies
    _recent_turn_latencies: List[float] = []

    # Config limits from environment variables with sensible defaults
    max_concurrent_calls = int(os.getenv("DANA_MAX_CONCURRENT_CALLS", "5"))
    max_stt_tasks = int(os.getenv("DANA_MAX_STT_TASKS", "3"))
    max_llm_tasks = int(os.getenv("DANA_MAX_LLM_TASKS", "3"))
    max_tts_tasks = int(os.getenv("DANA_MAX_TTS_TASKS", "3"))
    gpu_overload_threshold = float(os.getenv("DANA_GPU_OVERLOAD_THRESHOLD", "0.90"))
    latency_slo_threshold_ms = float(os.getenv("DANA_LATENCY_SLO_THRESHOLD_MS", "900.0"))

    @classmethod
    def reset(cls) -> None:
        """Reset all state counters (useful for unit tests)."""
        with cls._lock:
            cls._active_calls = 0
            cls._active_stt_tasks = 0
            cls._active_llm_tasks = 0
            cls._active_tts_tasks = 0
            cls._recent_turn_latencies.clear()

    @classmethod
    def increment_calls(cls) -> int:
        with cls._lock:
            cls._active_calls += 1
            return cls._active_calls

    @classmethod
    def decrement_calls(cls) -> int:
        with cls._lock:
            cls._active_calls = max(0, cls._active_calls - 1)
            return cls._active_calls

    @classmethod
    def get_active_calls(cls) -> int:
        with cls._lock:
            return cls._active_calls

    @classmethod
    def increment_stt(cls) -> int:
        with cls._lock:
            cls._active_stt_tasks += 1
            return cls._active_stt_tasks

    @classmethod
    def decrement_stt(cls) -> int:
        with cls._lock:
            cls._active_stt_tasks = max(0, cls._active_stt_tasks - 1)
            return cls._active_stt_tasks

    @classmethod
    def get_active_stt(cls) -> int:
        try:
            from speech.local_stt_load import get_active_local_stt_tasks
            actual = get_active_local_stt_tasks()
            if actual > 0:
                return actual
        except (ImportError, AttributeError):
            pass
        with cls._lock:
            return cls._active_stt_tasks

    @classmethod
    def increment_llm(cls) -> int:
        with cls._lock:
            cls._active_llm_tasks += 1
            return cls._active_llm_tasks

    @classmethod
    def decrement_llm(cls) -> int:
        with cls._lock:
            cls._active_llm_tasks = max(0, cls._active_llm_tasks - 1)
            return cls._active_llm_tasks

    @classmethod
    def get_active_llm(cls) -> int:
        try:
            from routing.model_router import get_active_local_llm_tasks
            actual = get_active_local_llm_tasks()
            if actual > 0:
                return actual
        except (ImportError, AttributeError):
            pass
        with cls._lock:
            return cls._active_llm_tasks

    @classmethod
    def increment_tts(cls) -> int:
        with cls._lock:
            cls._active_tts_tasks += 1
            return cls._active_tts_tasks

    @classmethod
    def decrement_tts(cls) -> int:
        with cls._lock:
            cls._active_tts_tasks = max(0, cls._active_tts_tasks - 1)
            return cls._active_tts_tasks

    @classmethod
    def get_active_tts(cls) -> int:
        try:
            from routing.model_router import get_active_local_tts_tasks
            actual = get_active_local_tts_tasks()
            if actual > 0:
                return actual
        except (ImportError, AttributeError):
            pass
        with cls._lock:
            return cls._active_tts_tasks

    @classmethod
    def record_turn_latency(cls, latency_ms: float) -> None:
        """Record a turn response latency measurement for SLO monitoring."""
        with cls._lock:
            cls._recent_turn_latencies.append(latency_ms)
            if len(cls._recent_turn_latencies) > 50:
                cls._recent_turn_latencies.pop(0)

    @classmethod
    def get_p95_latency(cls) -> float:
        """Calculate the 95th percentile latency of recent turns."""
        with cls._lock:
            if not cls._recent_turn_latencies:
                return 0.0
            sorted_latencies = sorted(cls._recent_turn_latencies)
            idx = int(len(sorted_latencies) * 0.95)
            # Prevent out of bounds
            idx = min(idx, len(sorted_latencies) - 1)
            return sorted_latencies[idx]

    @classmethod
    def is_degraded(cls) -> bool:
        """Check if P95 latency is exceeding our SLO target threshold."""
        # Require at least 3 samples to avoid temporary boot spikes marking us degraded
        with cls._lock:
            if len(cls._recent_turn_latencies) < 3:
                return False
        p95 = cls.get_p95_latency()
        return p95 > cls.latency_slo_threshold_ms

    @classmethod
    def check_gpu_utilization(cls) -> float:
        """Query GPU memory utilization using nvidia-smi.
        
        Returns:
            Utilization score from 0.0 to 1.0. Returns 0.0 if CUDA is not available or command fails.
        """
        # Allow testing override
        test_val = os.getenv("DANA_MOCK_GPU_UTILIZATION")
        if test_val is not None:
            try:
                return float(test_val)
            except ValueError:
                pass

        try:
            # Query nvidia-smi for memory utilization
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                check=True
            )
            out = res.stdout.strip()
            if out:
                parts = out.split(",")
                mem_used = float(parts[1].strip())
                mem_total = float(parts[2].strip())
                if mem_total > 0:
                    return mem_used / mem_total
        except Exception:
            # Fallback or CPU mode
            pass
        return 0.0

    @classmethod
    def has_capacity(cls) -> bool:
        """Evaluate all metrics to determine if the worker is eligible for new calls."""
        # 1. Active calls limit
        if cls.get_active_calls() >= cls.max_concurrent_calls:
            logger.info("Capacity check blocked: active calls %d >= max %d", cls.get_active_calls(), cls.max_concurrent_calls)
            return False

        # 2. STT task limit
        if cls.get_active_stt() >= cls.max_stt_tasks:
            logger.info("Capacity check blocked: active STT tasks %d >= max %d", cls.get_active_stt(), cls.max_stt_tasks)
            return False

        # 3. LLM task limit
        if cls.get_active_llm() >= cls.max_llm_tasks:
            logger.info("Capacity check blocked: active LLM tasks %d >= max %d", cls.get_active_llm(), cls.max_llm_tasks)
            return False

        # 4. TTS task limit
        if cls.get_active_tts() >= cls.max_tts_tasks:
            logger.info("Capacity check blocked: active TTS tasks %d >= max %d", cls.get_active_tts(), cls.max_tts_tasks)
            return False

        # 5. GPU memory overload check
        gpu_util = cls.check_gpu_utilization()
        if gpu_util >= cls.gpu_overload_threshold:
            logger.info("Capacity check blocked: GPU utilization %.2f >= threshold %.2f", gpu_util, cls.gpu_overload_threshold)
            return False

        # 6. Latency SLO check
        if cls.is_degraded():
            logger.info("Capacity check blocked: P95 latency is degraded (%.1fms > %.1fms)", cls.get_p95_latency(), cls.latency_slo_threshold_ms)
            return False

        return True
