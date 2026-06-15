"""feTransfer — Gated transfer tool wrapping core fe_transfer logic."""

from __future__ import annotations

import logging
import os
from typing import Any

from tools.base import BaseTool, ToolResult
from telephony.fe_transfer import fe_transfer

logger = logging.getLogger(__name__)


class FeTransferTool(BaseTool):
    """Bridge qualified lead to a licensed final expense insurance agent using LiveKit warm bridge."""

    @property
    def name(self) -> str:
        return "feTransfer"

    @property
    def description(self) -> str:
        return (
            "Transfer the qualified lead to a licensed insurance agent. "
            "Uses LiveKit warm bridge as the production path. Gated by DANA_CONFIRM_TRANSFER_CALL env var."
        )

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute transfer logic.
        
        Params:
            room_name (str): The LiveKit room name.
            prospect_identity (str): The name/identity of the prospect.
            licensed_agent_phone_number (str, optional): Target phone number.
            call_summary (str): Prose summary of lead qualification details.
            transfer_reason (str): Why the transfer is being initiated.
            lead_profile (dict): Structured LeadProfile snapshot.
            lead_state (str, optional): Prospect's state.
            call_id (str): Unique call ID.
            call_control_id (str, optional): Telnyx call control ID.
        """
        room_name = params.get("room_name") or params.get("call_id") or "unknown_room"
        prospect_identity = params.get("prospect_identity") or params.get("lead_name") or "Prospect"
        licensed_agent_phone_number = params.get("licensed_agent_phone_number")
        
        call_summary_param = params.get("call_summary")
        if isinstance(call_summary_param, dict):
            call_summary = "Lead qualified for final expense options"
        else:
            call_summary = str(call_summary_param) if call_summary_param else "Lead qualified for final expense options"

        transfer_reason = params.get("transfer_reason") or "Lead qualified for final expense"
        
        lead_profile = params.get("lead_profile") or params.get("lead_summary")
        if not isinstance(lead_profile, dict):
            lead_profile = {}

        lead_state = params.get("lead_state") or lead_profile.get("lead_state")
        call_id = params.get("call_id") or lead_profile.get("call_id") or room_name
        call_control_id = params.get("call_control_id")

        logger.info("FeTransferTool: Executing transfer for room %s, call_id %s, lead_state %s", room_name, call_id, lead_state)

        try:
            res = await fe_transfer(
                room_name=room_name,
                prospect_identity=prospect_identity,
                licensed_agent_phone_number=licensed_agent_phone_number,
                call_summary=call_summary,
                transfer_reason=transfer_reason,
                lead_profile=lead_profile,
                lead_state=lead_state,
                call_id=call_id,
                call_control_id=call_control_id
            )

            # Ensure we translate the core FeTransferResult to a ToolResult
            return ToolResult(
                success=res.success,
                data={
                    "success": res.success,
                    "reason": res.reason,
                    "transfer_mode": res.transfer_mode,
                    "agent_id": res.agent_id,
                    "call_summary": res.call_summary,
                    "provider_call_id": res.provider_call_id
                },
                message=f"Transfer execution: success={res.success}, reason={res.reason}",
                error=None if res.success else res.reason
            )

        except Exception as exc:
            logger.exception("Unexpected error inside FeTransferTool execution")
            return ToolResult(
                success=False,
                message="Unexpected error during transfer execution",
                error=str(exc)
            )
