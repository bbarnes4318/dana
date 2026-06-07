"""Call state management for Dana voice agent.

Defines the CallStage enum representing all possible stages in a
final-expense qualification call, the CallState dataclass that tracks
progression through those stages, and the StateResult returned by
individual state handlers.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class CallStage(enum.Enum):
    """Every stage the qualification call can be in."""

    ANSWERED = "answered"
    OPENING = "opening"
    INTEREST_CHECK = "interest_check"
    AGE_RANGE = "age_range"
    LIVING_SITUATION = "living_situation"
    DECISION_MAKER = "decision_maker"
    TRANSFER_CONSENT = "transfer_consent"
    TRANSFER_READY = "transfer_ready"
    CALLBACK = "callback"
    DNC = "dnc"
    DISQUALIFIED = "disqualified"
    END = "end"


@dataclass
class CallState:
    """Mutable call-level state that tracks where we are in the conversation.

    Attributes:
        current_stage: The stage the call is currently in.
        previous_stage: The stage the call was in before the last transition.
        stage_history: Ordered list of every stage visited (including repeats).
        turn_count: Number of conversational turns so far.
        objection_count: Number of objections encountered.
        started_at: UTC timestamp when the call began.
        last_transition_at: UTC timestamp of the most recent stage transition.
    """

    current_stage: CallStage = CallStage.OPENING
    previous_stage: Optional[CallStage] = None
    stage_history: list[CallStage] = field(default_factory=lambda: [CallStage.OPENING])
    turn_count: int = 0
    objection_count: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_transition_at: Optional[datetime] = None
    _transition_callbacks: list = field(default_factory=list, repr=False, compare=False)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def transition_to(self, stage: CallStage) -> None:
        """Move to *stage*, recording history and timestamps."""
        self.previous_stage = self.current_stage
        self.current_stage = stage
        self.stage_history.append(stage)
        self.last_transition_at = datetime.now(timezone.utc)
        for cb in getattr(self, "_transition_callbacks", []):
            try:
                cb(stage)
            except Exception:
                pass

    def increment_turn(self) -> None:
        self.turn_count += 1

    def increment_objections(self) -> None:
        self.objection_count += 1


@dataclass
class StateResult:
    """Value object returned by every state handler's ``handle()`` method.

    Attributes:
        next_stage: The stage to transition to (``None`` means stay).
        response_guidance: Natural-language guidance for the LLM to use
            when composing the agent's spoken reply.
        extracted_data: Key/value pairs extracted from the user utterance
            that should be written to the :class:`LeadProfile`.
        tool_calls: Any tool invocations the handler wants to trigger
            (e.g. CRM writes, transfer requests).
    """

    next_stage: Optional[CallStage] = None
    response_guidance: str = ""
    extracted_data: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
