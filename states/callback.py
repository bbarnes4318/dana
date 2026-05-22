"""Callback handler — prospect wants to be called back later."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.lead_profile import LeadProfile
from states.base import BaseState


class CallbackState(BaseState):
    """Process a callback/reschedule request."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        return StateResult(
            next_stage=CallStage.END,
            response_guidance=(
                "Confirm the callback. Let them know someone will reach "
                "out at a better time. Thank them warmly."
            ),
            extracted_data={"callback_requested": True},
            tool_calls=[{"action": "schedule_callback", "utterance": utterance}],
        )
