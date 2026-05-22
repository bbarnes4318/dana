"""Objection handler — address prospect concerns."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState

_MAX_OBJECTION_ATTEMPTS = 3


class ObjectionState(BaseState):
    """Handle objections and attempt a soft rebuttal.

    After ``_MAX_OBJECTION_ATTEMPTS`` the call ends gracefully.
    """

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

        call_state.increment_objections()

        # Too many objections — end gracefully
        if call_state.objection_count >= _MAX_OBJECTION_ATTEMPTS:
            return StateResult(
                next_stage=CallStage.END,
                response_guidance=(
                    "You've addressed their concerns multiple times. "
                    "Thank them for their time and end the call politely."
                ),
            )

        # Check if they came around
        answer = extract_yes_no(utterance)
        if answer is True:
            return StateResult(
                next_stage=CallStage.TRANSFER_READY,
                response_guidance=(
                    "They're open to it now. Confirm and prepare for transfer."
                ),
                extracted_data={"interest_level": "medium"},
            )

        return StateResult(
            next_stage=None,
            response_guidance=(
                "Acknowledge their concern empathetically. Offer one more "
                "value point about how final expense protects their family, "
                "then ask if they'd like to hear more."
            ),
        )
