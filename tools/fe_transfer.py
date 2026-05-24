"""feTransfer — Gated transfer tool wrapping core fe_transfer logic."""

from __future__ import annotations

import logging
import os
from typing import Any

from tools.base import BaseTool, ToolResult
from telephony.fe_transfer import fe_transfer

logger = logging.getLogger(__name__)


class FeTransferTool(BaseTool):
    """Bridge qualified lead to a licensed final expense insurance agent.
    
    feTransfer is currently a safe failure stub only. Real transfer/bridge is not implemented yet.
    """

    @property
    def name(self) -> str:
        return "feTransfer"

    @property
    def description(self) -> str:
        return (
            "Transfer the qualified lead to a licensed insurance agent. "
            "feTransfer is currently a safe failure stub only. Real transfer/bridge is not implemented yet. "
            "Gated by DANA_CONFIRM_TRANSFER_CALL env var."
        )

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute transfer logic.
        
        Params:
            room_name (str): The LiveKit room name or call ID.
            prospect_identity (str): The name/identity of the prospect.
            licensed_agent_phone_number (str, optional): Target phone number.
            call_summary (str): Summary of lead qualification details.
            transfer_reason (str): Why the transfer is being initiated.
        """
        room_name = params.get("room_name") or params.get("call_id") or "unknown_room"
        prospect_identity = params.get("prospect_identity") or params.get("lead_name") or "Prospect"
        licensed_agent_phone_number = params.get("licensed_agent_phone_number")
        call_summary = str(params.get("call_summary") or params.get("lead_summary") or "Lead qualified")
        transfer_reason = params.get("transfer_reason") or "Lead qualified for final expense"

        logger.info("FeTransferTool: Executing transfer for room %s", room_name)

        try:
            res = await fe_transfer(
                room_name=room_name,
                prospect_identity=prospect_identity,
                licensed_agent_phone_number=licensed_agent_phone_number,
                call_summary=call_summary,
                transfer_reason=transfer_reason
            )

            # Ensure we translate the core FeTransferResult to a ToolResult
            return ToolResult(
                success=res.success,
                data={
                    "success": res.success,
                    "reason": res.reason,
                    "transfer_mode": res.transfer_mode,
                    "call_summary": res.call_summary
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
