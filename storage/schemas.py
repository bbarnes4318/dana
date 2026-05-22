"""Pydantic models for storage records.

Each model corresponds to a logical collection in the store.  They are used
by :class:`storage.repository.Repository` for validation before persisting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class LeadSnapshot(BaseModel):
    """Point-in-time snapshot of lead data captured during a call."""

    call_id: str
    lead_profile: dict[str, Any]
    timestamp: datetime = Field(default_factory=_utcnow)
    stage: str


class CallTurn(BaseModel):
    """A single conversational turn in a call."""

    call_id: str
    turn_number: int
    speaker: str  # "user" or "agent"
    text: str
    stage: str
    timestamp: datetime = Field(default_factory=_utcnow)


class ToolEvent(BaseModel):
    """Record of a tool invocation during a call."""

    call_id: str
    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    timestamp: datetime = Field(default_factory=_utcnow)


class QAReport(BaseModel):
    """Quality-assurance report for a completed call."""

    call_id: str
    scores: dict[str, Any] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utcnow)


class TrainingNote(BaseModel):
    """A reusable training observation mined from calls or coaching."""

    source: str
    topic: str
    sales_lesson: str
    good_example: Optional[str] = None
    bad_example: Optional[str] = None
    call_stage: Optional[str] = None
    timestamp: datetime = Field(default_factory=_utcnow)
