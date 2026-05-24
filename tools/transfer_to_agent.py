"""TransferToAgentTool — log a dry-run agent transfer event.

Records transfer intent to ``data/transfers.jsonl``.  The current
implementation is a **dry-run** — it writes the event but does **not**
perform an actual SIP/LiveKit transfer.

.. todo::
    Replace the dry-run implementation with real LiveKit SIP transfer
    logic once the infrastructure is wired up.  The integration point
    is the ``execute`` method below — swap the JSONL append with
    a LiveKit ``TransferParticipant`` RPC or SIP REFER flow.

    NOTE: The strict outbound screening prompt mentions the tool name 'feTransfer'.
    If migrating the transfer tool to 'feTransfer', make sure to register a tool 
    under the name 'feTransfer' in `ToolRegistry` (or register an alias for this tool), 
    and update `ActionPolicy` and `AgentRuntime` to fire 'feTransfer' instead of 
    'transfer_to_agent'.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join("data", "transfers.jsonl")


class TransferToAgentTool(BaseTool):
    """Log a dry-run transfer to a human agent.

    # TODO: Replace dry-run JSONL logging with real LiveKit/SIP transfer.
    #   - Use livekit-api ``RoomServiceClient.transfer_participant()``
    #     or issue a SIP REFER to the target agent queue.
    #   - Add retry / fallback logic and surface transfer status back
    #     to the voice agent so it can inform the lead.
    """

    def __init__(self, output_path: str | Path | None = None) -> None:
        self._output_path = Path(output_path) if output_path else Path(_DEFAULT_PATH)

    @property
    def name(self) -> str:
        return "transfer_to_agent"

    @property
    def description(self) -> str:
        return (
            "Initiate a transfer of the qualified lead to a live agent. "
            "Currently logs a dry-run event to data/transfers.jsonl."
        )

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Record a transfer event.

        Required params:
            call_id (str): Unique call identifier.
            lead_summary (str): Brief summary of the qualified lead.
            transfer_reason (str): Why the transfer is being initiated.

        Returns:
            ToolResult with the recorded transfer event.
        """
        call_id = params.get("call_id")
        lead_summary = params.get("lead_summary", "")
        transfer_reason = params.get("transfer_reason", "")

        if not call_id:
            return ToolResult(
                success=False,
                message="Missing required parameter: call_id",
                error="call_id is required",
            )

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "call_id": call_id,
            "lead_summary": lead_summary,
            "transfer_reason": transfer_reason,
            "status": "dry_run",
        }

        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            with self._output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

            logger.info(
                "Transfer event logged (dry_run) for call %s", call_id
            )
            return ToolResult(
                success=True,
                data=record,
                message=f"Transfer logged (dry_run) for call {call_id}",
            )

        except OSError as exc:
            logger.exception("Failed to log transfer for call %s", call_id)
            return ToolResult(
                success=False,
                message="Failed to log transfer event",
                error=str(exc),
            )
