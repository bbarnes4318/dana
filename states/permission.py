"""Permission state handler — obtain consent to continue."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState


class PermissionState(BaseState):
    """Ask for and evaluate the prospect's permission to continue."""

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

        if answer is True:
            return StateResult(
                next_stage=CallStage.AGE,
                response_guidance=(
                    "Thank them for their time.  Transition to asking "
                    "their age to see if they qualify for final expense coverage."
                ),
            )

        if answer is False:
            return StateResult(
                next_stage=CallStage.END,
                response_guidance=(
                    "Respect their decision. Thank them for their time "
                    "and end the call politely."
                ),
            )

        # Ambiguous — re-ask
        return StateResult(
            next_stage=None,
            response_guidance=(
                "Clarify: you just need a moment of their time to see if "
                "they qualify.  Re-ask for permission gently."
            ),
        )
