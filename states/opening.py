"""Opening state handler — first words of the call."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_name
from core.lead_profile import LeadProfile
from states.base import BaseState


class OpeningState(BaseState):
    """Deliver the opening greeting and transition to permission."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        # Check for immediate DNC
        if detect_dnc_request(utterance):
            return StateResult(
                next_stage=CallStage.DNC,
                response_guidance="Acknowledge and process the do-not-call request.",
            )

        if detect_callback_request(utterance):
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance="Acknowledge the callback request politely.",
            )

        # Try to pick up a name
        extracted: dict = {}
        name = extract_name(utterance)
        if name:
            extracted["first_name"] = name

        return StateResult(
            next_stage=CallStage.PERMISSION,
            response_guidance=(
                "Greet the prospect warmly, introduce yourself as Dana, "
                "and ask for permission to continue the conversation."
            ),
            extracted_data=extracted,
        )
