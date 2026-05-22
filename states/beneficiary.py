"""Beneficiary handler — who does the prospect want to protect?"""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request
from core.lead_profile import LeadProfile
from states.base import BaseState


class BeneficiaryState(BaseState):
    """Collect information about who the policy would benefit."""

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

        # Any substantive answer counts — the LLM can refine later
        cleaned = utterance.strip()
        if not cleaned:
            return StateResult(
                next_stage=None,
                response_guidance=(
                    "Ask: 'Who would you want this policy to help — "
                    "a spouse, children, or someone else?'"
                ),
            )

        return StateResult(
            next_stage=CallStage.INTEREST,
            response_guidance=(
                "Great reason. Now gauge their overall interest level in "
                "learning more about a plan today."
            ),
            extracted_data={"beneficiary_or_family_reason": cleaned},
        )
