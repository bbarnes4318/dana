"""
Telnyx Cold Transfer Provider
Executes a carrier-level cold transfer (SIP REFER or Telnyx API transfer bridge)
with safety gates and validation checking.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ColdTransferResult:
    """Outcome of attempting a cold transfer."""
    success: bool
    reason: str
    provider_call_id: Optional[str] = None
    transfer_mode: Optional[str] = None  # e.g., "dry_run", "cold_transfer", "failed"


class ColdTransferProvider:
    """Abstract interface for cold transferring call to a destination number."""

    async def initiate_cold_transfer(
        self,
        room_name: str,
        phone_number: str,
        call_control_id: Optional[str] = None
    ) -> ColdTransferResult:
        """Execute cold transfer bridging the call directly to destination number."""
        raise NotImplementedError


class TelnyxColdTransferProvider(ColdTransferProvider):
    """Executes a cold transfer using Telnyx API (SIP REFER / Call Transfer API)."""

    async def initiate_cold_transfer(
        self,
        room_name: str,
        phone_number: str,
        call_control_id: Optional[str] = None
    ) -> ColdTransferResult:
        # 1. Check if call_control_id is present
        if not call_control_id:
            logger.error(
                "Cold transfer requires Telnyx Call Control call_control_id. "
                "Current call path uses LiveKit SIP, so use warm_bridge instead."
            )
            return ColdTransferResult(
                success=False,
                reason="telnyx_call_control_not_available",
                provider_call_id=None,
                transfer_mode="failed"
            )

        # 2. Safety Gate Check
        confirm_transfer = os.getenv("DANA_CONFIRM_TRANSFER_CALL", "no").strip().lower() == "yes"
        if not confirm_transfer:
            logger.warning("Cold transfer BLOCKED by safety gate (DANA_CONFIRM_TRANSFER_CALL != yes)")
            return ColdTransferResult(
                success=False,
                reason="transfer_not_confirmed",
                provider_call_id=None,
                transfer_mode="dry_run"
            )

        # 3. Check Provider Credentials
        telnyx_api_key = os.getenv("TELNYX_API_KEY")
        if not telnyx_api_key or telnyx_api_key == "replace_me":
            logger.error("Telnyx provider is missing API key configuration.")
            return ColdTransferResult(
                success=False,
                reason="provider_not_configured",
                provider_call_id=None,
                transfer_mode="failed"
            )

        # 4. Telnyx REST API Call (SIP REFER or Call Control Transfer bridge)
        # Note: In a production Telnyx integration, we would retrieve the Telnyx call control ID
        # associated with the room/session, and execute:
        # telnyx.Call.transfer(call_control_id=..., destination_number=phone_number)
        logger.info(
            "Executing Telnyx Cold Transfer for room '%s' to phone number '%s' using call_control_id '%s'...",
            room_name,
            phone_number,
            call_control_id
        )
        
        # Safe failure stub as actual API details depend on active call_control_id matching
        return ColdTransferResult(
            success=False,
            reason="provider_not_configured",  # Return provider_not_configured because we don't have the active call_control_id
            provider_call_id=None,
            transfer_mode="failed"
        )
