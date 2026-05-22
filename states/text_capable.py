"""Text-capable handler — can the prospect receive texts?"""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState


class TextCapableState(BaseState):
    """Determine whether the prospect can receive text messages."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
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

        answer = extract_yes_no(utterance)

        if answer is None:
            return StateResult(
                next_stage=None,
                response_guidance=(
                    "Couldn't tell. Re-ask: "
                    "'Can you receive text messages on this phone?'"
                ),
            )

        return StateResult(
            next_stage=CallStage.BUDGET,
            response_guidance=(
                "Got it. Now transition to asking about their budget comfort "
                "for a final expense plan."
            ),
            extracted_data={"can_receive_text": answer},
        )
