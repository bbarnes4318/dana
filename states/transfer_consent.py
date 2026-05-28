"""Transfer consent state handler — parses if prospect explicitly consents to being connected."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState


class TransferConsentState(BaseState):
    """Evaluate prospect's consent to transfer."""

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

        lower_utterance = utterance.lower().strip()

        # Check for explicit affirmative response
        affirmative_words = ["yes", "yeah", "yep", "yup", "sure", "absolutely", "ok", "okay", "alright", "all right", "sounds good", "go ahead"]
        has_affirmative = any(word in lower_utterance for word in affirmative_words) or extract_yes_no(utterance) is True

        if has_affirmative:
            return StateResult(
                next_stage=CallStage.TRANSFER_READY,
                response_guidance="Say: 'Perfect. Stay right there for me.'",
                extracted_data={"transfer_consent_confirmed": True, "transfer_ready": True},
            )

        # Explicit negative response
        if extract_yes_no(utterance) is False or "don't" in lower_utterance or "do not" in lower_utterance or "no" in lower_utterance:
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance="Say: 'No problem. Would it be better if we schedule a callback at a later time?'",
                extracted_data={"transfer_consent_confirmed": False},
            )

        # Silence or ambiguous: do NOT treat as consent. Stay here and re-ask.
        return StateResult(
            next_stage=None,
            response_guidance=(
                "You need explicit verbal consent before transferring. Re-ask: "
                "'I'm going to bring the licensed agent on so they can go over the options. "
                "Is it okay if I connect you now?'"
            ),
        )
