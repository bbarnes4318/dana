"""State-location handler — collect the prospect's US state."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_state
from core.lead_profile import LeadProfile
from states.base import BaseState


class StateLocationState(BaseState):
    """Collect the prospect's US state of residence."""

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

        state = extract_state(utterance)

        if state is None:
            return StateResult(
                next_stage=None,
                response_guidance=(
                    "Couldn't determine the state. Politely re-ask: "
                    "'What state do you currently live in?'"
                ),
            )

        return StateResult(
            next_stage=CallStage.PHONE_TYPE,
            response_guidance=(
                f"Got it — {state}. Now ask whether they're on a cell phone "
                "or a landline."
            ),
            extracted_data={"state": state},
        )
