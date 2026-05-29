"""Financial decision-maker state handler — parses if prospect handles their own financial decisions."""

from __future__ import annotations

import re
from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState


class DecisionMakerState(BaseState):
    """Evaluate prospect's financial decision-maker status."""

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

        lower_utterance = utterance.lower()

        # Check for "talk to spouse first" callback request
        spouse_callback_pattern = r"\b(talk|discuss|ask|check|consult)\b.*\b(spouse|wife|husband|partner|husband|son|daughter|family)\b"
        if re.search(spouse_callback_pattern, lower_utterance) or "talk to my" in lower_utterance:
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance=(
                    "Say: 'That makes sense. Would it be better to have the licensed agent try you "
                    "later today or tomorrow?' then collect callback timing."
                ),
                extracted_data={"callback_requested": True}
            )

        is_confirming = "confirming_decision_maker" in lead_profile.notes

        answer = extract_yes_no(utterance)

        # Check for joint decision with spouse
        is_joint = any(kw in lower_utterance for kw in ["joint", "both", "spouse", "wife", "husband", "partner", "together"])

        if is_confirming:
            # If they confirm someone else handles decisions (yes to "someone else handles those decisions, correct?")
            if answer is True:
                return StateResult(
                    next_stage=CallStage.DISQUALIFIED,
                    response_guidance="Say: 'Understood. I wouldn’t want to set the wrong expectation. Take care.' then end the call.",
                    extracted_data={"disqualified_reason": "Someone else handles financial decisions"}
                )
            # If they correct themselves
            elif answer is False:
                if "confirming_decision_maker" in lead_profile.notes:
                    lead_profile.notes.remove("confirming_decision_maker")
                return StateResult(
                    next_stage=CallStage.TRANSFER_CONSENT,
                    response_guidance=(
                        "Say: 'Okay. That gives the licensed agent enough to take a look. I’m going to "
                        "bring them on so they can go over the actual options with you. Hold the line for me, okay?'"
                    ),
                    extracted_data={"financial_decision_maker": True}
                )
            
            return StateResult(
                next_stage=None,
                response_guidance="Repeat: 'Just so I make sure I heard you right, someone else handles those decisions for you, correct?'"
            )

        # Joint spouse decision maker
        if is_joint and answer is not False:
            return StateResult(
                next_stage=CallStage.TRANSFER_CONSENT,
                response_guidance=(
                    "Say: 'Gotcha. As long as you’re able to make your own decisions, that’s fine.' "
                    "Then say: 'Okay. That gives the licensed agent enough to take a look. I’m going to "
                    "bring them on so they can go over the actual options with you. Hold the line for me, okay?'"
                ),
                extracted_data={"financial_decision_maker": True},
            )

        if answer is True:
            return StateResult(
                next_stage=CallStage.TRANSFER_CONSENT,
                response_guidance=(
                    "Say: 'Okay. That gives the licensed agent enough to take a look. I’m going to "
                    "bring them on so they can go over the actual options with you. Hold the line for me, okay?'"
                ),
                extracted_data={"financial_decision_maker": True},
            )

        if answer is False:
            lead_profile.notes.append("confirming_decision_maker")
            return StateResult(
                next_stage=None, # Stay here to confirm
                response_guidance="Say: 'Just so I make sure I heard you right, someone else handles those decisions for you, correct?'",
                extracted_data={"financial_decision_maker": False}
            )

        # Ambiguous
        return StateResult(
            next_stage=None,
            response_guidance=(
                "Clarify and re-ask: 'That’s one of the basic things the licensed agent needs. "
                "Do you handle your own financial decisions, correct?'"
            ),
        )
