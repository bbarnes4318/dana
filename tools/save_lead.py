"""SaveLeadTool — persist qualified lead data to a JSONL file.

Each invocation appends one JSON line to ``data/leads.jsonl`` containing
a timestamp, the originating ``call_id``, and the full lead profile.
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

# Default output path relative to project root
_DEFAULT_PATH = os.path.join("data", "leads.jsonl")


class SaveLeadTool(BaseTool):
    """Write lead profile data to ``data/leads.jsonl``."""

    def __init__(self, output_path: str | Path | None = None) -> None:
        self._output_path = Path(output_path) if output_path else Path(_DEFAULT_PATH)

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "save_lead"

    @property
    def description(self) -> str:
        return (
            "Persist a qualified lead to data/leads.jsonl. "
            "Expects 'call_id' and 'lead_profile' in params."
        )

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Append a lead record to the JSONL file.

        Required params:
            call_id (str): Unique identifier for the call.
            lead_profile (dict): Collected lead information.

        Returns:
            ToolResult with the written record in ``data``.
        """
        call_id: str | None = params.get("call_id")
        lead_profile: dict[str, Any] | None = params.get("lead_profile")

        if not call_id:
            return ToolResult(
                success=False,
                message="Missing required parameter: call_id",
                error="call_id is required",
            )
        if not lead_profile:
            return ToolResult(
                success=False,
                message="Missing required parameter: lead_profile",
                error="lead_profile is required",
            )

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "call_id": call_id,
            "lead_profile": lead_profile,
        }

        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            with self._output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

            logger.info("Lead saved for call %s", call_id)
            return ToolResult(
                success=True,
                data=record,
                message=f"Lead saved for call {call_id}",
            )

        except OSError as exc:
            logger.exception("Failed to save lead for call %s", call_id)
            return ToolResult(
                success=False,
                message="Failed to write lead data",
                error=str(exc),
            )
