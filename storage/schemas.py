"""Pydantic models for storage records.

Each model corresponds to a logical collection in the store.  They are used
by :class:`storage.repository.Repository` for validation before persisting.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
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


class CallCost(BaseModel):
    """Estimated component cost for a specific call."""

    id: Optional[str] = None
    call_id: str
    campaign_id: Optional[str] = None
    component: str
    provider: str = "unknown"
    model: str = "unknown"
    usage_unit: Optional[str] = None
    usage_quantity: Optional[Decimal] = None
    unit_rate: Optional[Decimal] = None
    estimated_cost: Optional[Decimal] = None
    currency: Optional[str] = None
    rate_source: Optional[str] = None
    estimated: Optional[bool] = None
    dry_run: Optional[bool] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class OutcomeMetric(BaseModel):
    """Daily aggregated outcome metrics for a campaign."""

    id: Optional[str] = None
    campaign_id: str
    metric_date: date
    total_dialed: int = 0
    answered: int = 0
    human_answered: int = 0
    voicemail: int = 0
    no_answer: int = 0
    busy: int = 0
    failed: int = 0
    open_to_review: int = 0
    qualified: int = 0
    transferred: int = 0
    callback: int = 0
    dnc: int = 0
    disqualified: int = 0
    cost: Decimal = Decimal("0.0")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TrainingSource(BaseModel):
    """Source material for training lessons."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_type: str
    source_uri: str
    title: str
    imported_at: datetime = Field(default_factory=_utcnow)
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrainingExample(BaseModel):
    """An individual training example for model instruction or fine-tuning."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    call_id: Optional[str] = None
    stage: str
    user_text: str
    ideal_response: str
    bad_response: Optional[str] = None
    labels: dict[str, Any] = Field(default_factory=dict)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    use_for: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class EvalCase(BaseModel):
    """An evaluation test case for the agent."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    stage: str
    prospect_utterance: str
    expected_behavior: str
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    expected_tool: Optional[str] = None
    severity: str
    created_at: datetime = Field(default_factory=_utcnow)


class PromptVersion(BaseModel):
    """A tracked version of an LLM prompt configuration."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str
    sha: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    created_by: str
    change_reason: str
    qa_thresholds: dict[str, Any] = Field(default_factory=dict)
    canary_status: str


class HumanReviewItem(BaseModel):
    """A record queued for human annotation or compliance review."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    item_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    reviewer: Optional[str] = None
    review_notes: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    reviewed_at: Optional[datetime] = None


class DeploymentExperiment(BaseModel):
    """An active experiment comparing prompt versions or model settings."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    experiment_name: str
    prompt_version_id: Optional[str] = None
    traffic_percent: float
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class CallOutcomeLabel(BaseModel):
    """Downstream outcome metadata associated with a call (e.g. sale result)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    call_id: str
    campaign_id: Optional[str] = None
    outcome: str
    sold: Optional[bool] = None
    issued: Optional[bool] = None
    transfer_quality_score: Optional[float] = None
    agent_feedback: Optional[str] = None
    labels: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class TelephonyProviderConfig(BaseModel):
    """Configuration for a telephony provider (Telnyx + LiveKit)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: str = "telnyx_livekit"
    name: str
    status: str = "draft"  # draft|active|disabled
    telnyx_connection_id: Optional[str] = None
    telnyx_sip_trunk_name: Optional[str] = None
    telnyx_phone_numbers: list[str] = Field(default_factory=list)
    livekit_url: Optional[str] = None
    livekit_sip_outbound_trunk_id: Optional[str] = None
    livekit_sip_inbound_trunk_id: Optional[str] = None
    livekit_dispatch_rule_id: Optional[str] = None
    room_name_template: str = "dana-{campaign_id}-{lead_id}-{attempt_id}"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class OutboundCampaign(BaseModel):
    """Configuration for outbound campaign dialing."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    status: str = "draft"  # draft|ready|running|paused|stopped|completed|archived
    campaign_type: str = "final_expense_outbound"
    provider_config_id: Optional[str] = None
    prompt_name: str = "final_expense_alex"
    max_concurrent_calls: int = 1
    daily_call_cap: int = 100
    calls_started_today: int = 0
    timezone: str = "America/New_York"
    calling_window_start: str = "09:30"
    calling_window_end: str = "18:00"
    allowed_days: list[str] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"])
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    transfer_phone_number: Optional[str] = None
    caller_id: Optional[str] = None
    compliance_mode: str = "strict"
    dnc_scrub_required: bool = True
    require_live_mode: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    started_at: Optional[str] = None
    paused_at: Optional[str] = None
    stopped_at: Optional[str] = None


class CampaignLead(BaseModel):
    """A lead loaded into a specific campaign."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    campaign_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: str
    state: Optional[str] = None
    timezone: Optional[str] = None
    status: str = "new"  # new|queued|dialing|in_call|completed|callback|dnc|wrong_number|failed|suppressed|do_not_call
    priority: int = 0
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: Optional[str] = None
    last_attempt_at: Optional[str] = None
    outcome: Optional[str] = None
    suppression_reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CallAttempt(BaseModel):
    """Outcome and detail of an individual outbound call attempt."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    campaign_id: str
    lead_id: str
    provider_config_id: Optional[str] = None
    status: str = "queued"  # queued|dialing|ringing|answered|in_progress|completed|failed|cancelled|blocked
    phone_number_redacted: Optional[str] = None
    phone_number_hash: Optional[str] = None
    livekit_room_name: Optional[str] = None
    livekit_participant_id: Optional[str] = None
    livekit_sip_call_id: Optional[str] = None
    provider_call_id: Optional[str] = None
    sip_call_status: Optional[str] = None
    sip_status_code: Optional[int] = None
    sip_status: Optional[str] = None
    started_at: Optional[str] = None
    answered_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    failure_reason: Optional[str] = None
    transfer_consent: bool = False
    transfer_attempted: bool = False
    transfer_successful: bool = False
    post_call_export_path: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class LiveCallSession(BaseModel):
    """Realtime state session of a call in progress."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    campaign_id: str
    lead_id: str
    attempt_id: str
    call_id: str
    status: str = "starting"  # starting|ringing|active|transferring|ended|failed
    current_stage: Optional[str] = None
    latest_transcript: Optional[str] = None
    compliance_warnings: list[str] = Field(default_factory=list)
    livekit_room_name: Optional[str] = None
    participant_identity: Optional[str] = None
    started_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    ended_at: Optional[str] = None
    outcome: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignControlEvent(BaseModel):
    """Auditable state transition event for a campaign."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    campaign_id: str
    event_type: str  # created|ready|started|paused|resumed|stopped|completed|lead_imported|dialer_tick|call_started|call_ended|blocked|error
    operator: Optional[str] = None
    reason: Optional[str] = None
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class CallerIdNumber(BaseModel):
    """An individual caller ID number record in the pool."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: str  # telnyx|bulkvs|signalwire|twilio
    phone_number: str
    status: str = "active"  # active|paused|cooldown|blocked|retired
    source: str = "manual"  # env|database|api_import|manual
    verified_for_provider: bool = False
    stir_shaken_attestation: Optional[str] = "unknown"  # A|B|C|unknown
    daily_cap: int = 100
    hourly_cap: int = 20
    calls_today: int = 0
    calls_this_hour: int = 0
    last_used_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    spam_label_status: str = "clean"  # clean|suspected|flagged|blocked|unknown
    complaint_count: int = 0
    dnc_count: int = 0
    answer_rate: Optional[float] = 0.0
    transfer_rate: Optional[float] = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CallerIdSelectionConfig(BaseModel):
    """Configuration options for selecting a caller ID from the pool."""

    provider: str
    campaign_id: Optional[str] = None
    strategy: str = "health_weighted"  # round_robin|least_used|health_weighted
    allow_cross_provider: bool = False
    require_verified: bool = True
    max_per_number_per_day: Optional[int] = None
    max_per_number_per_hour: Optional[int] = None


class CallerIdSelectionResult(BaseModel):
    """Result of attempting to select a caller ID."""

    success: bool
    phone_number: Optional[str] = None
    provider: str
    source: str
    reason: str
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    candidate_count: int = 0
    eligible_count: int = 0


