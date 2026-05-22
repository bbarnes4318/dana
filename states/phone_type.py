"""Phone-type handler — determine cell vs landline."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import (
    detect_callback_request,
    detect_dnc_request,
    extract_phone_type,
)
from core.lead_profile import LeadProfile
from states.base import BaseState


class PhoneTypeState(BaseState):
    """Determine whether the prospect is on a cell phone or landline."""

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

        phone_type = extract_phone_type(utterance)

        if phone_type is None:
            return StateResult(
                next_stage=None,
                response_guidance=(
                    "Couldn't determine phone type. Ask: "
                    "'Are you calling from a cell phone or a landline?'"
                ),
            )

        extracted = {"phone_type": phone_type}

        if phone_type == "cell":
            return StateResult(
                next_stage=CallStage.TEXT_CAPABLE,
                response_guidance=(
                    "Great — cell phone. Now ask if they can receive text messages."
                ),
                extracted_data=extracted,
            )

        # Landline — skip TEXT_CAPABLE, go to BUDGET
        return StateResult(
            next_stage=CallStage.BUDGET,
            response_guidance=(
                "Noted — landline. Move on to asking about their budget comfort."
            ),
            extracted_data=extracted,
        )
