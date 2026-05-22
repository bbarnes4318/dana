"""ScheduleCallbackTool — persist a callback request to JSONL.

Records that a lead has requested a callback at a specific time so the
dialler (or a human agent) can follow up later.
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

_DEFAULT_PATH = os.path.join("data", "callbacks.jsonl")


class ScheduleCallbackTool(BaseTool):
    """Write a callback request to ``data/callbacks.jsonl``."""

    def __init__(self, output_path: str | Path | None = None) -> None:
        self._output_path = Path(output_path) if output_path else Path(_DEFAULT_PATH)

    @property
    def name(self) -> str:
        return "schedule_callback"

    @property
    def description(self) -> str:
        return (
            "Schedule a callback for a lead. "
            "Writes to data/callbacks.jsonl."
        )

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Record a callback request.

        Required params:
            call_id (str): Unique call identifier.
            lead_name (str): Name of the lead requesting the callback.
            callback_time (str): Requested callback time (ISO-8601 or free text).
            phone_number (str): Phone number to call back.

        Optional params:
            notes (str): Additional notes about the callback.

        Returns:
            ToolResult with the callback record.
        """
        call_id = params.get("call_id")
        lead_name = params.get("lead_name", "")
        callback_time = params.get("callback_time", "")
        phone_number = params.get("phone_number", "")
        notes = params.get("notes", "")

        if not call_id:
            return ToolResult(
                success=False,
                message="Missing required parameter: call_id",
                error="call_id is required",
            )

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "call_id": call_id,
            "lead_name": lead_name,
            "callback_time": callback_time,
            "phone_number": phone_number,
            "notes": notes,
        }

        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            with self._output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

            logger.info("Callback scheduled for call %s", call_id)
            return ToolResult(
                success=True,
                data=record,
                message=f"Callback scheduled for call {call_id}",
            )

        except OSError as exc:
            logger.exception("Failed to schedule callback for call %s", call_id)
            return ToolResult(
                success=False,
                message="Failed to schedule callback",
                error=str(exc),
            )
