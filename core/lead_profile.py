"""Lead profile model for Dana voice agent.

Stores all data collected during a final-expense qualification call.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class LeadProfile(BaseModel):
    """Pydantic model that accumulates data about the prospect."""

    # Identity
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    # New Short Flow and Registration fields
    lead_id: Optional[str] = None
    lead_phone_e164: Optional[str] = None
    campaign_id: Optional[str] = None
    consent_source: Optional[str] = None
    consent_timestamp: Optional[str] = None
    consent_artifact_id: Optional[str] = None
    open_to_review: Optional[bool] = None
    age_range_confirmed: Optional[bool] = None
    living_independently: Optional[bool] = None
    financial_decision_maker: Optional[bool] = None
    transfer_consent_confirmed: Optional[bool] = None
    callback_time_local: Optional[str] = None
    callback_timezone: Optional[str] = None
    lead_state: Optional[str] = None

    # Qualification fields (Legacy / optional)
    age: Optional[int] = None
    state: Optional[str] = None
    phone_type: Optional[str] = None  # "cell" | "landline"
    can_receive_text: Optional[bool] = None

    # Financial / coverage (Legacy / optional)
    budget_confirmed: Optional[bool] = None
    has_existing_coverage: Optional[bool] = None

    # Intent (Legacy / optional)
    beneficiary_or_family_reason: Optional[str] = None
    interest_level: Optional[str] = None  # "high" | "medium" | "low" | "none"

    # Disposition
    disqualified_reason: Optional[str] = None
    callback_requested: Optional[bool] = None
    do_not_call_requested: bool = False
    transfer_ready: bool = False

    # Misc
    notes: list[str] = Field(default_factory=list)
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def is_qualified(self) -> bool:
        """Return ``True`` when the lead meets all transfer-readiness criteria for short flow."""
        return (
            self.open_to_review is True
            and self.age_range_confirmed is True
            and self.living_independently is True
            and self.financial_decision_maker is True
            and self.transfer_consent_confirmed is True
            and self.do_not_call_requested is False
            and self.disqualified_reason is None
        )

    def completeness_score(self) -> float:
        """Return a 0-1 float indicating how much profile data has been collected.

        Each non-None / non-default field adds to the score.
        """
        tracked_fields: list[tuple[str, object]] = [
            ("lead_id", None),
            ("lead_phone_e164", None),
            ("campaign_id", None),
            ("open_to_review", None),
            ("age_range_confirmed", None),
            ("living_independently", None),
            ("financial_decision_maker", None),
            ("transfer_consent_confirmed", None),
        ]
        filled = sum(
            1 for name, sentinel in tracked_fields
            if getattr(self, name) is not sentinel
        )
        return filled / len(tracked_fields) if tracked_fields else 0.0

    def to_summary_dict(self) -> dict:
        """Return a human-readable summary dict suitable for logging / CRM."""
        return {
            "call_id": self.call_id,
            "lead_id": self.lead_id,
            "lead_phone_e164": self.lead_phone_e164,
            "campaign_id": self.campaign_id,
            "consent_source": self.consent_source,
            "consent_timestamp": self.consent_timestamp,
            "consent_artifact_id": self.consent_artifact_id,
            "open_to_review": self.open_to_review,
            "age_range_confirmed": self.age_range_confirmed,
            "living_independently": self.living_independently,
            "financial_decision_maker": self.financial_decision_maker,
            "transfer_consent_confirmed": self.transfer_consent_confirmed,
            "callback_time_local": self.callback_time_local,
            "callback_timezone": self.callback_timezone,
            "lead_state": self.lead_state,
            "callback_requested": self.callback_requested,
            "do_not_call_requested": self.do_not_call_requested,
            "disqualified_reason": self.disqualified_reason,
            "transfer_ready": self.is_qualified(),
            "is_qualified": self.is_qualified(),
            "completeness": round(self.completeness_score(), 2),
            "notes": self.notes,
            # Legacy properties preserved for compatibility
            "name": f"{self.first_name or ''} {self.last_name or ''}".strip() or None,
            "age": self.age,
            "state": self.state,
            "phone_type": self.phone_type,
            "can_receive_text": self.can_receive_text,
            "budget_confirmed": self.budget_confirmed,
            "has_existing_coverage": self.has_existing_coverage,
            "beneficiary_or_family_reason": self.beneficiary_or_family_reason,
            "interest_level": self.interest_level,
        }
