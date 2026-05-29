"""Living situation state handler — parses if prospect lives independently."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState


class LivingSituationState(BaseState):
    """Evaluate prospect's independent living status."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        from states.base import check_global_stops
        stop_result = check_global_stops(utterance)
        if stop_result is not None:
            return stop_result

        if detect_dnc_request(utterance):
            return StateResult(
                next_stage=CallStage.DNC,
                response_guidance="Acknowledge and process the do-not-call request.",
                extracted_data={"do_not_call_requested": True}
            )

        if detect_callback_request(utterance):
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance="Acknowledge the callback request and offer a callback time.",
                extracted_data={"callback_requested": True}
            )

        is_confirming = "confirming_care" in lead_profile.notes

        answer = extract_yes_no(utterance)

        if is_confirming:
            # If they confirm they are in a care facility (yes to "you're currently in a care facility, correct?")
            if answer is True:
                return StateResult(
                    next_stage=CallStage.DISQUALIFIED,
                    response_guidance="Say: 'Understood. These usually require independent living, so I don’t want to point you the wrong direction. Take care.' then end the call.",
                    extracted_data={"disqualified_reason": "In care facility / care home"}
                )
            # If they correct themselves (no, they live independently)
            elif answer is False:
                if "confirming_care" in lead_profile.notes:
                    lead_profile.notes.remove("confirming_care")
                return StateResult(
                    next_stage=CallStage.DECISION_MAKER,
                    response_guidance="Say: 'Okay. And you handle your own financial decisions, correct?'",
                    extracted_data={"living_independently": True}
                )
            
            # If unclear, repeat confirmation
            return StateResult(
                next_stage=None,
                response_guidance="Repeat: 'Just so I make sure I heard you right, you’re currently in a care facility, correct?'"
            )

        # Normal evaluation
        if answer is True:
            return StateResult(
                next_stage=CallStage.DECISION_MAKER,
                response_guidance="Say: 'Okay. And you handle your own financial decisions, correct?'",
                extracted_data={"living_independently": True},
            )

        if answer is False:
            lead_profile.notes.append("confirming_care")
            return StateResult(
                next_stage=None, # Stay here to confirm
                response_guidance="Say: 'Just so I make sure I heard you right, you’re currently in a care facility, correct?'",
                extracted_data={"living_independently": False}
            )

        # Ambiguous
        return StateResult(
            next_stage=None,
            response_guidance=(
                "Clarify and re-ask: 'That’s one of the basic things the licensed agent needs. "
                "You’re living independently, right?'"
            ),
        )
