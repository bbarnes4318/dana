"""Age range state handler — parses if prospect is between forty and eighty-five."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no, extract_age
from core.lead_profile import LeadProfile
from states.base import BaseState


class AgeRangeState(BaseState):
    """Evaluate prospect's age eligibility."""

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
                response_guidance="Acknowledge the callback request and offer a callback time.",
            )

        # Check if we are currently in the confirmation sub-state
        is_confirming = "confirming_age" in lead_profile.notes

        answer = extract_yes_no(utterance)
        if answer is None:
            age = extract_age(utterance)
            if age is not None:
                answer = (40 <= age <= 85)

        if is_confirming:
            # If they confirm they are NOT in the range (meaning yes to "you're not between forty and eighty-five, correct?")
            if answer is True:
                return StateResult(
                    next_stage=CallStage.DISQUALIFIED,
                    response_guidance="Say: 'Understood. These usually fit people in that range, so I won’t waste your time. Take care.' then end the call.",
                    extracted_data={"disqualified_reason": "Not between 40 and 85 years old"}
                )
            # If they correct themselves (no, they actually ARE in range)
            elif answer is False:
                # Remove the confirmation note
                if "confirming_age" in lead_profile.notes:
                    lead_profile.notes.remove("confirming_age")
                return StateResult(
                    next_stage=CallStage.LIVING_SITUATION,
                    response_guidance=(
                        "Say: 'Okay. And you’re living independently, right? Not in a nursing home or assisted living?'"
                    ),
                    extracted_data={"age_range_confirmed": True}
                )
            
            # If unclear, repeat confirmation
            return StateResult(
                next_stage=None,
                response_guidance="Repeat: 'Just so I make sure I heard you right, you’re not between forty and eighty-five, correct?'"
            )

        # Normal evaluation
        if answer is True:
            return StateResult(
                next_stage=CallStage.LIVING_SITUATION,
                response_guidance=(
                    "Say: 'Okay. And you’re living independently, right? Not in a nursing home or assisted living?'"
                ),
                extracted_data={"age_range_confirmed": True},
            )

        if answer is False:
            # Transition to confirmation gate
            lead_profile.notes.append("confirming_age")
            return StateResult(
                next_stage=None, # Stay here to confirm
                response_guidance="Say: 'Just so I make sure I heard you right, you’re not between forty and eighty-five, correct?'",
                extracted_data={"age_range_confirmed": False}
            )

        # Ambiguous
        return StateResult(
            next_stage=None,
            response_guidance=(
                "Clarify and re-ask: 'That’s just the basic age range the licensed agent needs. "
                "Are you between forty and eighty-five?'"
            ),
        )
