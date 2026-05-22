"""MarkDNCTool — record a Do-Not-Call request.

When a lead asks to be placed on the Do-Not-Call list, this tool
appends the request to ``data/dnc.jsonl`` so downstream systems can
suppress future calls.
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

_DEFAULT_PATH = os.path.join("data", "dnc.jsonl")


class MarkDNCTool(BaseTool):
    """Record a Do-Not-Call request to ``data/dnc.jsonl``."""

    def __init__(self, output_path: str | Path | None = None) -> None:
        self._output_path = Path(output_path) if output_path else Path(_DEFAULT_PATH)

    @property
    def name(self) -> str:
        return "mark_dnc"

    @property
    def description(self) -> str:
        return (
            "Mark a phone number as Do-Not-Call. "
            "Writes to data/dnc.jsonl."
        )

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Record a DNC request.

        Required params:
            call_id (str): Unique call identifier.
            phone_number (str): Phone number to suppress.

        Optional params:
            reason (str): Why the lead wants to be on the DNC list.
            requested_by (str): Who requested the DNC (lead name or ID).

        Returns:
            ToolResult with the DNC record.
        """
        call_id = params.get("call_id")
        phone_number = params.get("phone_number", "")
        reason = params.get("reason", "")
        requested_by = params.get("requested_by", "")

        if not call_id:
            return ToolResult(
                success=False,
                message="Missing required parameter: call_id",
                error="call_id is required",
            )

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "call_id": call_id,
            "phone_number": phone_number,
            "reason": reason,
            "requested_by": requested_by,
        }

        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            with self._output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

            logger.info(
                "DNC recorded for phone %s (call %s)", phone_number, call_id
            )
            return ToolResult(
                success=True,
                data=record,
                message=f"Phone {phone_number} marked as DNC for call {call_id}",
            )

        except OSError as exc:
            logger.exception("Failed to record DNC for call %s", call_id)
            return ToolResult(
                success=False,
                message="Failed to record DNC request",
                error=str(exc),
            )
