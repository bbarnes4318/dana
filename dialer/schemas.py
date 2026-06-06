"""Pydantic schemas for the outbound dialer intelligence layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional
from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimezoneWindow(BaseModel):
    """Represents a validated calling timezone and its local time status."""

    timezone_str: str
    local_time: datetime
    is_allowed: bool
    reason: Optional[str] = None


class DialerCampaignConfig(BaseModel):
    """Outbound configuration for a campaign's scheduling and compliance."""

    campaign_id: str
    allowed_days: List[str] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"])
    allowed_calling_hours: tuple[int, int] = (8, 20)  # 8 AM to 8 PM local
    max_attempts: int = 3
    daily_call_cap: int = 100
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    voicemail_strategy: dict[str, Any] = Field(default_factory=dict)
    transfer_queue_config: dict[str, Any] = Field(default_factory=dict)


class CallerIdMetrics(BaseModel):
    """Usage and performance metrics for a specific rotated caller ID."""

    caller_id: str
    campaign_id: str
    status: str = "active"  # active, inactive, cooldown
    total_calls: int = 0
    total_answers: int = 0
    total_dncs: int = 0
    total_complaints: int = 0
    answer_rate: float = 0.0
    dnc_rate: float = 0.0
    complaint_rate: float = 0.0
    cooldown_until: Optional[datetime] = None
    cooldown_reason: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)


class SpamRiskReport(BaseModel):
    """Spam risk assessment for a caller ID."""

    caller_id: str
    score: float = 0.0  # 0.0 to 1.0
    answer_rate_drop_detected: bool = False
    short_call_hangup_rate: float = 0.0
    dnc_complaint_rate: float = 0.0
    status: str = "low_risk"  # low_risk, medium_risk, high_risk
    calculated_at: datetime = Field(default_factory=_utcnow)


class TransferQueueItem(BaseModel):
    """Represents a lead in the live transfer queue waiting for an agent."""

    call_id: str
    lead_id: str
    campaign_id: str
    priority: int = 0
    entered_at: datetime = Field(default_factory=_utcnow)
    status: str = "pending"  # pending, transferring, completed, failed, callback_scheduled
    agent_id: Optional[str] = None
    warm_bridge: bool = False
    attempts: int = 0
