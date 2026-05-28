"""Opening state handler — handles user response when waiting for user to speak first."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request
from core.lead_profile import LeadProfile
from states.base import BaseState


class OpeningState(BaseState):
    """Deliver the opening greeting when prospect speaks first."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        if detect_dnc_request(utterance):
            return StateResult(
                next_stage=CallStage.DNC,
                response_guidance="Acknowledge and process the do-not-call request politely.",
            )

        if detect_callback_request(utterance):
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance="Acknowledge the callback request politely.",
            )

        # Transition to interest check and instruct the LLM to deliver the opening question
        return StateResult(
            next_stage=CallStage.INTEREST_CHECK,
            response_guidance=(
                "Introduce yourself: 'Hey, this is Alex. I’m getting back with you about the final "
                "expense burial options. Are you still open to looking at those?'"
            ),
        )
