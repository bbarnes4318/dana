"""Call record model for QA analysis.

Stores a complete record of a finished call — every turn, the lead profile
snapshot, tool events, and the final outcome — so the QA scoring pipeline
can evaluate it offline.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class CallTurn(BaseModel):
    """A single conversational turn within a call."""

    speaker: Literal["agent", "prospect"]
    text: str
    stage: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CallRecord(BaseModel):
    """Immutable record of a completed call for QA evaluation.

    Attributes:
        call_id: Unique identifier for the call (UUID string).
        turns: Ordered list of every conversational turn.
        lead_profile: Snapshot of the lead profile at call end.
        final_stage: The stage the call ended in.
        duration_seconds: Total call length in seconds.
        tool_events: Chronological list of tool invocations during the call.
        started_at: UTC timestamp when the call began.
        ended_at: UTC timestamp when the call ended.
        outcome: Disposition of the call.
    """

    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    turns: list[CallTurn] = Field(default_factory=list)
    lead_profile: dict = Field(default_factory=dict)
    final_stage: str = "end"
    duration_seconds: float = 0.0
    tool_events: list[dict] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    outcome: Literal[
        "transferred",
        "callback",
        "dnc",
        "disqualified",
        "abandoned",
        "ended",
    ] = "ended"

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def agent_turns(self) -> list[CallTurn]:
        """Return only the agent's turns."""
        return [t for t in self.turns if t.speaker == "agent"]

    @property
    def prospect_turns(self) -> list[CallTurn]:
        """Return only the prospect's turns."""
        return [t for t in self.turns if t.speaker == "prospect"]

    @property
    def agent_word_count(self) -> int:
        """Total words spoken by the agent."""
        return sum(len(t.text.split()) for t in self.agent_turns)

    @property
    def prospect_word_count(self) -> int:
        """Total words spoken by the prospect."""
        return sum(len(t.text.split()) for t in self.prospect_turns)
