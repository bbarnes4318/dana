"""Budget handler — confirm the prospect is comfortable with premiums."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState


class BudgetState(BaseState):
    """Ask whether the prospect can handle a monthly premium."""

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
                    "Clarify: 'Would you be comfortable setting aside "
                    "a small amount each month — say between $20 and $50 — "
                    "to make sure your family is taken care of?'"
                ),
            )

        if answer is False:
            # Not an automatic DQ — move on, but record
            return StateResult(
                next_stage=CallStage.BENEFICIARY,
                response_guidance=(
                    "Understood — budget is tight. Let's keep going and see "
                    "if this could still work for you. Ask about who they'd "
                    "want the policy to benefit."
                ),
                extracted_data={"budget_confirmed": False},
            )

        return StateResult(
            next_stage=CallStage.BENEFICIARY,
            response_guidance=(
                "Great, budget works. Now ask who they'd want the policy "
                "to help — a spouse, children, grandchildren?"
            ),
            extracted_data={"budget_confirmed": True},
        )
