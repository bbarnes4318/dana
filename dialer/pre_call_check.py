"""Pre-call verification checkpoint before placing outbound calls."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from compliance.consent_record import ConsentRecord
from compliance.dnc_registry import InternalDNCRegistry
from compliance.pre_dial_gate import PreDialGate, PreDialDecision

logger = logging.getLogger(__name__)


async def verify_pre_call(
    lead: Union[dict, Any],
    campaign: Dict[str, Any],
    consent_record: Optional[ConsentRecord],
    dnc_registry: InternalDNCRegistry,
    now: Optional[datetime] = None
) -> PreDialDecision:
    """Execute pre-dial gate checks and log the outcome.
    
    Returns the PreDialDecision representing compliance allowed/blocked state.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    gate = PreDialGate()
    decision = await gate.check(
        lead=lead,
        campaign=campaign,
        consent_record=consent_record,
        dnc_registry=dnc_registry,
        now=now
    )

    if not decision.allowed:
        logger.warning(
            "Outbound call BLOCKED: phone=%s, lead_id=%s, campaign_id=%s. Reason: %s",
            decision.phone_e164,
            decision.lead_id,
            decision.campaign_id,
            decision.reason
        )
    else:
        logger.info(
            "Outbound call ALLOWED: phone=%s, lead_id=%s, campaign_id=%s",
            decision.phone_e164,
            decision.lead_id,
            decision.campaign_id
        )

    return decision
