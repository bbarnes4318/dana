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

    # Qualification fields
    age: Optional[int] = None
    state: Optional[str] = None
    phone_type: Optional[str] = None  # "cell" | "landline"
    can_receive_text: Optional[bool] = None

    # Financial / coverage
    budget_confirmed: Optional[bool] = None
    has_existing_coverage: Optional[bool] = None

    # Intent
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
        """Return ``True`` when the lead meets all transfer-readiness criteria.

        Transfer ready ONLY if:
        - age present
        - state present
        - phone_type known
        - budget confirmed **or** interest_level is 'high'
        - no DNC
        - no disqualifier
        - prospect willing (transfer_ready flag)
        """
        if self.do_not_call_requested:
            return False
        if self.disqualified_reason is not None:
            return False
        if self.age is None or self.state is None or self.phone_type is None:
            return False
        budget_or_interest = (
            self.budget_confirmed is True
            or self.interest_level == "high"
        )
        if not budget_or_interest:
            return False
        if not self.transfer_ready:
            return False
        return True

    def completeness_score(self) -> float:
        """Return a 0-1 float indicating how much profile data has been collected.

        Each non-None / non-default field adds to the score.
        """
        tracked_fields: list[tuple[str, object]] = [
            ("first_name", None),
            ("last_name", None),
            ("age", None),
            ("state", None),
            ("phone_type", None),
            ("can_receive_text", None),
            ("budget_confirmed", None),
            ("has_existing_coverage", None),
            ("beneficiary_or_family_reason", None),
            ("interest_level", None),
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
            "name": f"{self.first_name or ''} {self.last_name or ''}".strip() or None,
            "age": self.age,
            "state": self.state,
            "phone_type": self.phone_type,
            "can_receive_text": self.can_receive_text,
            "budget_confirmed": self.budget_confirmed,
            "has_existing_coverage": self.has_existing_coverage,
            "beneficiary_or_family_reason": self.beneficiary_or_family_reason,
            "interest_level": self.interest_level,
            "disqualified_reason": self.disqualified_reason,
            "callback_requested": self.callback_requested,
            "do_not_call_requested": self.do_not_call_requested,
            "transfer_ready": self.transfer_ready,
            "is_qualified": self.is_qualified(),
            "completeness": round(self.completeness_score(), 2),
            "notes": self.notes,
        }
