"""Disqualified handler — prospect does not meet criteria."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.lead_profile import LeadProfile
from states.base import BaseState


class DisqualifiedState(BaseState):
    """Handle a prospect that has been disqualified."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        reason = lead_profile.disqualified_reason or "does not meet criteria"

        return StateResult(
            next_stage=CallStage.END,
            response_guidance=(
                f"The prospect is disqualified ({reason}). "
                "Let them know kindly that you don't have a plan that "
                "fits their situation right now. Thank them and end the call."
            ),
        )
