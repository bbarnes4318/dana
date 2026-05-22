"""EscalateToHumanTool — log an escalation request.

When the agent encounters a situation it cannot handle (e.g. a
regulatory question, a distressed caller, or a complex objection),
this tool records the escalation to ``data/escalations.jsonl``.
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

_DEFAULT_PATH = os.path.join("data", "escalations.jsonl")


class EscalateToHumanTool(BaseTool):
    """Log an escalation event to ``data/escalations.jsonl``."""

    def __init__(self, output_path: str | Path | None = None) -> None:
        self._output_path = Path(output_path) if output_path else Path(_DEFAULT_PATH)

    @property
    def name(self) -> str:
        return "escalate_to_human"

    @property
    def description(self) -> str:
        return (
            "Escalate the current call to a human supervisor. "
            "Logs to data/escalations.jsonl."
        )

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Record an escalation event.

        Required params:
            call_id (str): Unique call identifier.
            reason (str): Why the call is being escalated.

        Optional params:
            urgency (str): Urgency level (e.g. 'low', 'medium', 'high', 'critical').
            lead_summary (str): Brief summary of the lead / call so far.

        Returns:
            ToolResult with the escalation record.
        """
        call_id = params.get("call_id")
        reason = params.get("reason", "")
        urgency = params.get("urgency", "medium")
        lead_summary = params.get("lead_summary", "")

        if not call_id:
            return ToolResult(
                success=False,
                message="Missing required parameter: call_id",
                error="call_id is required",
            )

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "call_id": call_id,
            "reason": reason,
            "urgency": urgency,
            "lead_summary": lead_summary,
        }

        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            with self._output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

            logger.info(
                "Escalation logged for call %s (urgency=%s)", call_id, urgency
            )
            return ToolResult(
                success=True,
                data=record,
                message=f"Escalation logged for call {call_id} (urgency={urgency})",
            )

        except OSError as exc:
            logger.exception("Failed to log escalation for call %s", call_id)
            return ToolResult(
                success=False,
                message="Failed to log escalation",
                error=str(exc),
            )
