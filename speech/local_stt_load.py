"""Local STT Load Tracker.

Thread-safe, exception-safe global tracking of active Whisper transcription tasks.
"""

from __future__ import annotations
import threading
from typing import Any

# Global counter for active local STT transcription tasks
_active_local_tasks: int = 0
_lock = threading.Lock()

def increment_active_local_stt_tasks() -> None:
    global _active_local_tasks
    with _lock:
        _active_local_tasks += 1

def decrement_active_local_stt_tasks() -> None:
    global _active_local_tasks
    with _lock:
        _active_local_tasks = max(0, _active_local_tasks - 1)

def get_active_local_stt_tasks() -> int:
    global _active_local_tasks
    with _lock:
        return _active_local_tasks

class TrackLocalSTTTask:
    """Context manager to track active local Whisper tasks safely."""
    def __enter__(self) -> TrackLocalSTTTask:
        increment_active_local_stt_tasks()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        decrement_active_local_stt_tasks()

class AsyncTrackLocalSTTTask:
    """Async context manager to track active local Whisper tasks safely."""
    async def __aenter__(self) -> AsyncTrackLocalSTTTask:
        increment_active_local_stt_tasks()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        decrement_active_local_stt_tasks()
