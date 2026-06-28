"""Interest check state handler — parses if prospect is open to reviewing final expense options."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState


class InterestCheckState(BaseState):
    """Evaluate prospect's willingness to review options."""

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
                    response_guidance="Acknowledge the callback request and offer a callback time.",
                    extracted_data={"callback_requested": True}
                )
            else:
                # Other objections (no_money, already_have_insurance, etc.) transition to AGE_RANGE/qualifying
                return StateResult(
                    next_stage=CallStage.AGE_RANGE,
                    response_guidance=(
                        "Say: 'Okay. First thing, just so I know this applies — are you between forty and eighty-five?'"
                    ),
                    extracted_data={"open_to_review": True},
                )

        from core.intent.short_response_intent import classify_intent
        intent = classify_intent(utterance)

        if intent == "dnc":
            return StateResult(
                next_stage=CallStage.DNC,
                response_guidance="Acknowledge and process the do-not-call request.",
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
                extracted_data={"open_to_review": False},
            )

        if intent == "agreement":
            return StateResult(
                next_stage=CallStage.AGE_RANGE,
                response_guidance=(
                    "Say: 'Okay. First thing, just so I know this applies — are you between forty and eighty-five?'"
                ),
                extracted_data={"open_to_review": True},
            )

        if intent == "confusion":
            return StateResult(
                next_stage=None,
                response_guidance=(
                    "Clarify: 'It’s Alex with American Beneficiary. I’m getting back with you on the final expense "
                    "burial programs. Is that something you’re still open to looking at?'"
                ),
            )

        if detect_callback_request(utterance) or intent == "repeat":
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance="Acknowledge the callback request and offer a callback time.",
                extracted_data={"callback_requested": True}
            )

        # Ambiguous/Clarification: re-ask
        return StateResult(
            next_stage=None,
            response_guidance=(
                "Clarify: 'It’s Alex with American Beneficiary. I’m getting back with you on the final expense "
                "burial programs. Is that something you’re still open to looking at?'"
            ),
        )
