"""Pre-dial compliance gate checker."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from compliance.consent_record import ConsentRecord
from compliance.dnc_registry import InternalDNCRegistry
from compliance.calling_window import resolve_lead_timezone, is_calling_window_allowed

logger = logging.getLogger(__name__)


@dataclass
class PreDialDecision:
    """Outcome of running a lead through the pre-dial compliance check."""

    allowed: bool
    reason: str
    blocked_by: List[str] = field(default_factory=list)
    lead_id: Optional[str] = None
    phone_e164: Optional[str] = None
    campaign_id: Optional[str] = None


class PreDialGate:
    """Orchestrates compliance checks before an outbound call is placed."""

    async def check(
        self,
        lead: Union[dict, Any],
        campaign: Dict[str, Any],
        consent_record: Optional[ConsentRecord],
        dnc_registry: InternalDNCRegistry,
        now: Optional[datetime] = None
    ) -> PreDialDecision:
        """Check all rules and return a PreDialDecision."""
        if now is None:
            now = datetime.now(timezone.utc)

        blocked_by: List[str] = []

        # Helper to get lead attributes/keys safely
        def get_lead_val(key: str) -> Optional[Any]:
            if isinstance(lead, dict):
                return lead.get(key)
            return getattr(lead, key, None)

        lead_id = get_lead_val("lead_id")
        phone_e164 = get_lead_val("lead_phone_e164") or get_lead_val("phone_e164") or get_lead_val("phone_number")
        campaign_id = get_lead_val("campaign_id") or campaign.get("campaign_id")

        # 1. Check phone number existence
        if not phone_e164:
            blocked_by.append("missing_phone_e164")

        # 2. Check campaign status
        is_paused = campaign.get("is_paused", False)
        if is_paused:
            blocked_by.append("campaign_paused")

        # 3. Check consent record
        if not consent_record:
            blocked_by.append("missing_consent_record")
        else:
            approved_vendors = campaign.get("approved_consent_sources", [])
            if consent_record.source_vendor not in approved_vendors:
                blocked_by.append("consent_source_not_approved")

        # 4. Check DNC Registry
        if phone_e164:
            on_dnc = await dnc_registry.contains(phone_e164, campaign_id=campaign_id)
            if on_dnc:
                blocked_by.append("phone_on_dnc")

        # 5. Check attempts limit
        max_attempts = campaign.get("max_attempts", 3)
        attempts = get_lead_val("attempts") or 0
        if attempts >= max_attempts:
            blocked_by.append("exceeded_max_attempts")

        # 6. Check timezone and calling window
        tz_str, tz_source, tz_confidence = resolve_lead_timezone(lead)
        if not tz_str:
            blocked_by.append("missing_timezone_no_fallback")
        else:
            allowed_hours = campaign.get("allowed_calling_hours") or (8, 20)  # default: 8 AM to 8 PM local
            if not is_calling_window_allowed(tz_str, allowed_hours, now):
                blocked_by.append("outside_calling_window")

        # 7. Check caller ID validation
        caller_id = campaign.get("caller_id")
        active_caller_ids = campaign.get("active_caller_ids")
        if not caller_id:
            blocked_by.append("caller_id_missing")
        elif active_caller_ids is not None and caller_id not in active_caller_ids:
            blocked_by.append("caller_id_inactive")

        if blocked_by:
            reason = f"Blocked by compliance rules: {', '.join(blocked_by)}"
            return PreDialDecision(
                allowed=False,
                reason=reason,
                blocked_by=blocked_by,
                lead_id=lead_id,
                phone_e164=phone_e164,
                campaign_id=campaign_id
            )

        return PreDialDecision(
            allowed=True,
            reason="Approved for dialing",
            blocked_by=[],
            lead_id=lead_id,
            phone_e164=phone_e164,
            campaign_id=campaign_id
        )
