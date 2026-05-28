"""Call Context Registry.

Tracks call stages, campaign IDs, and line quality metrics using task-local contextvars
and a thread-safe registry keyed by call_id. Ensures no data leaks between calls.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Context variables for easy access within the same asyncio Task context
call_id_var: ContextVar[Optional[str]] = ContextVar("call_id", default=None)
campaign_id_var: ContextVar[Optional[str]] = ContextVar("campaign_id", default=None)
call_stage_var: ContextVar[Optional[str]] = ContextVar("call_stage", default=None)
line_quality_var: ContextVar[Optional[float]] = ContextVar("line_quality", default=1.0)

# Global registry keyed by call_id to manage multi-call state
_registry: Dict[str, Dict[str, Any]] = {}


def get_current_call_id() -> Optional[str]:
    """Retrieve the current call_id from task-local storage."""
    return call_id_var.get()


def get_current_campaign_id() -> Optional[str]:
    """Retrieve the campaign_id for the active call."""
    cid = call_id_var.get()
    if cid and cid in _registry:
        return _registry[cid].get("campaign_id")
    return campaign_id_var.get()


def get_current_call_stage() -> Optional[str]:
    """Retrieve the call stage for the active call."""
    cid = call_id_var.get()
    if cid and cid in _registry:
        return _registry[cid].get("call_stage")
    return call_stage_var.get()


def get_current_line_quality() -> float:
    """Retrieve the estimated line quality for the active call (0.0 to 1.0)."""
    cid = call_id_var.get()
    if cid and cid in _registry:
        return _registry[cid].get("line_quality", 1.0)
    val = line_quality_var.get()
    return val if val is not None else 1.0


def register_call(call_id: str, campaign_id: Optional[str] = None) -> None:
    """Register a new call in the global registry and set task-local variables."""
    _registry[call_id] = {
        "campaign_id": campaign_id,
        "call_stage": "INTEREST_CHECK",
        "line_quality": 1.0,
    }
    call_id_var.set(call_id)
    if campaign_id:
        campaign_id_var.set(campaign_id)
    logger.debug(f"Registered call: {call_id} (campaign_id={campaign_id})")


def update_call_stage(call_id: str, stage: str) -> None:
    """Update the call stage for a call."""
    # Enforce stage names upper case matching Prompt 2 short-flow stages
    stage_upper = stage.strip().upper()
    if call_id in _registry:
        _registry[call_id]["call_stage"] = stage_upper
    call_stage_var.set(stage_upper)
    logger.debug(f"Updated call stage for {call_id}: {stage_upper}")


def update_line_quality(call_id: str, quality: float) -> None:
    """Update the line quality score for a call."""
    clamped_quality = max(0.0, min(1.0, quality))
    if call_id in _registry:
        _registry[call_id]["line_quality"] = clamped_quality
    line_quality_var.set(clamped_quality)
    logger.debug(f"Updated line quality for {call_id}: {clamped_quality:.2f}")


def unregister_call(call_id: str) -> None:
    """Unregister a call and clean up registry keys to prevent memory leaks."""
    _registry.pop(call_id, None)
    call_id_var.set(None)
    campaign_id_var.set(None)
    call_stage_var.set(None)
    line_quality_var.set(1.0)
    logger.debug(f"Unregistered call: {call_id}")
