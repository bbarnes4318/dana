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


class Call(BaseModel):
    """Details about a call."""

    call_id: str
    lead_id: Optional[str] = None
    campaign_id: Optional[str] = None
    phone_e164: Optional[str] = None
    caller_id: Optional[str] = None
    started_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    outcome: Optional[str] = None
    recording_url: Optional[str] = None
    transcript: Optional[list[dict[str, Any]]] = None
    qualification: Optional[dict[str, Any]] = None
    compliance_flags: Optional[dict[str, Any]] = None
    latency_summary: Optional[dict[str, Any]] = None
    qa_score: Optional[float] = None
    amd_result: Optional[str] = None
    retry_after: Optional[datetime] = None
    dry_run: Optional[bool] = False
    created_at: datetime = Field(default_factory=_utcnow)


class Transfer(BaseModel):
    """Details about a call transfer."""

    call_id: str
    lead_id: Optional[str] = None
    transfer_mode: str
    agent_id: Optional[str] = None
    target_phone: Optional[str] = None
    success: bool
    failure_reason: Optional[str] = None
    provider_call_id: Optional[str] = None
    summary: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=_utcnow)


class Callback(BaseModel):
    """Details about a scheduled callback."""

    call_id: Optional[str] = None
    lead_id: Optional[str] = None
    phone_e164: str
    callback_time_local: str
    callback_timezone: str
    status: str
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class DncRequest(BaseModel):
    """A Do Not Call (DNC) request."""

    call_id: Optional[str] = None
    lead_id: Optional[str] = None
    phone_e164: str
    campaign_id: Optional[str] = None
    reason: Optional[str] = None
    requested_at: datetime = Field(default_factory=_utcnow)


class ConsentRecord(BaseModel):
    """A record of marketing/TCPA consent."""

    consent_artifact_id: str
    lead_id: Optional[str] = None
    phone_e164: str
    source_vendor: str
    consent_text: str
    consent_timestamp: datetime
    landing_page_url: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    tcpa_consent_version: Optional[str] = None
    campaign_id: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=_utcnow)


class LatencyMetric(BaseModel):
    """Record of a latency metric during a call."""

    call_id: str
    metric_name: str
    metric_value_ms: float
    created_at: datetime = Field(default_factory=_utcnow)


class Campaign(BaseModel):
    """Campaign metadata and configuration."""

    campaign_id: str
    name: str
    status: str
    config: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

