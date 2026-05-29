"""Provider Health and Circuit Breaker Module.

Tracks failures and cooldown states per component, provider, and call to prevent
error states from cross-contaminating between calls.
"""

from __future__ import annotations
import threading
import time
from typing import Dict, List, Tuple

# Lock for thread safety
_lock = threading.Lock()

# Nested dict: call_id -> (component, provider) -> List[float] (timestamps of errors)
_call_errors: Dict[str, Dict[Tuple[str, str], List[float]]] = {}
# Nested dict: call_id -> (component, provider) -> float (cooldown until timestamp)
_call_cooldowns: Dict[str, Dict[Tuple[str, str], float]] = {}

def record_failure(call_id: str, component: str, provider: str) -> None:
    """Record a model execution failure for a specific call context."""
    if not call_id:
        return
    with _lock:
        if call_id not in _call_errors:
            _call_errors[call_id] = {}
        key = (component.lower(), provider.lower())
        if key not in _call_errors[call_id]:
            _call_errors[call_id][key] = []
        _call_errors[call_id][key].append(time.time())

def get_error_count(call_id: str, component: str, provider: str, window_seconds: int = 300) -> int:
    """Get the number of failures for a provider within the sliding window."""
    if not call_id:
        return 0
    now = time.time()
    cutoff = now - window_seconds
    with _lock:
        if call_id not in _call_errors:
            return 0
        key = (component.lower(), provider.lower())
        errors = _call_errors[call_id].get(key, [])
        # Filter and clean up old errors
        active_errors = [t for t in errors if t >= cutoff]
        _call_errors[call_id][key] = active_errors
        return len(active_errors)

def trigger_cooldown(call_id: str, component: str, provider: str, cooldown_seconds: int = 120) -> None:
    """Put a provider on cooldown for a specific call."""
    if not call_id:
        return
    with _lock:
        if call_id not in _call_cooldowns:
            _call_cooldowns[call_id] = {}
        key = (component.lower(), provider.lower())
        _call_cooldowns[call_id][key] = time.time() + cooldown_seconds

def is_on_cooldown(call_id: str, component: str, provider: str) -> bool:
    """Check if a provider is currently on cooldown for a specific call."""
    if not call_id:
        return False
    with _lock:
        if call_id not in _call_cooldowns:
            return False
        key = (component.lower(), provider.lower())
        cooldown_until = _call_cooldowns[call_id].get(key, 0.0)
        if time.time() < cooldown_until:
            return True
        return False

def check_provider_health(
    call_id: str,
    component: str,
    provider: str,
    window_seconds: int = 300,
    max_errors: int = 3,
    cooldown_seconds: int = 120
) -> bool:
    """Check health of a provider. If error limit exceeded, puts it on cooldown and returns False."""
    if not call_id:
        return True
    
    if is_on_cooldown(call_id, component, provider):
        return False
        
    err_count = get_error_count(call_id, component, provider, window_seconds)
    if err_count >= max_errors:
        trigger_cooldown(call_id, component, provider, cooldown_seconds)
        return False
        
    return True

def cleanup_call(call_id: str) -> None:
    """Clean up health tracking state when a call completes."""
    with _lock:
        _call_errors.pop(call_id, None)
        _call_cooldowns.pop(call_id, None)
