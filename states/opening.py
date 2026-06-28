"""Opening state handler — handles user response when waiting for user to speak first."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request
from core.lead_profile import LeadProfile
from states.base import BaseState


class OpeningState(BaseState):
    """Deliver the opening greeting when prospect speaks first."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        # Check for registered objections first to allow rebuttals
        from core.objection_classifier import ObjectionClassifier
        classifier = ObjectionClassifier()
        objection_intent = classifier.classify(utterance)
        
        if objection_intent:
            if objection_intent == "not_interested":
                return StateResult(
                    next_stage=CallStage.END,
                    response_guidance="Say: 'Understood. I won’t keep you. Take care.' then end the call.",
                    extracted_data={"open_to_review": False},
                )
            elif objection_intent == "busy":
                return StateResult(
                    next_stage=CallStage.CALLBACK,
                    response_guidance="Acknowledge the callback request politely.",
                    extracted_data={"callback_requested": True}
                )
            else:
                # If they raise an objection immediately (e.g. "I already have insurance"), transition to INTEREST_CHECK to rebuttal first
                return StateResult(
                    next_stage=CallStage.INTEREST_CHECK,
                    response_guidance="Acknowledge the concern and ask if they are open to reviewing options.",
                )

        from core.intent.short_response_intent import classify_intent
        intent = classify_intent(utterance)

        if intent == "dnc":
            return StateResult(
                next_stage=CallStage.DNC,
                response_guidance="Acknowledge and process the do-not-call request politely.",
                extracted_data={"do_not_call_requested": True}
            )

        if intent == "wrong_number":
            return StateResult(
                next_stage=CallStage.END,
                response_guidance="Acknowledge politely that this is the wrong number, apologize, and end the call.",
                extracted_data={"open_to_review": False}
            )

        if intent == "refusal":
            return StateResult(
                next_stage=CallStage.END,
                response_guidance="Say: 'Understood. I won’t keep you. Take care.' then end the call.",
                extracted_data={"open_to_review": False}
            )

        if intent == "agreement":
            return StateResult(
                next_stage=CallStage.AGE_RANGE,
                response_guidance=(
                    "The caller already agreed/expressed interest. Say: 'Okay. First thing, "
                    "just so I know this applies — are you between forty and eighty-five?'"
                ),
                extracted_data={"open_to_review": True}
            )

        if intent == "confusion":
            return StateResult(
                next_stage=CallStage.INTEREST_CHECK,
                response_guidance=(
                    "The caller is asking who is this / what is this about. Acknowledge and briefly explain "
                    "in one short sentence, then check interest. Say: 'This is Alex with American Beneficiary. "
                    "I'm calling about the final expense burial options you asked about. Are you still open to looking at those?'"
                )
            )

        if detect_callback_request(utterance) or intent == "repeat":
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance="Acknowledge the callback request politely.",
                extracted_data={"callback_requested": True}
            )

        # Transition to interest check and instruct the LLM to deliver the opening question
        return StateResult(
            next_stage=CallStage.INTEREST_CHECK,
            response_guidance=(
                "Introduce yourself: 'Hey, this is Alex. I’m getting back with you about the final "
                "expense burial options. Are you still open to looking at those?'"
            ),
        )
