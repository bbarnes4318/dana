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

        lower_utterance = utterance.lower().strip()
        is_silent = not lower_utterance or lower_utterance in ["[silence]", "silence", "hello?", "hello"] or len(lower_utterance.split()) == 0

        # Check for explicit affirmative response
        affirmative_words = ["yes", "yeah", "yep", "yup", "sure", "absolutely", "ok", "okay", "alright", "all right", "sounds good", "go ahead", "that's fine", "put them on", "connect me"]
        has_affirmative = any(word in lower_utterance for word in affirmative_words) or extract_yes_no(utterance) is True

        # Clear asked_silence_followup if affirmative or negative response is processed
        has_negative = extract_yes_no(utterance) is False or "don't" in lower_utterance or "do not" in lower_utterance or "no" in lower_utterance

        asked_followup = "asked_silence_followup" in lead_profile.notes

        if has_affirmative:
            if asked_followup:
                lead_profile.notes.remove("asked_silence_followup")
            return StateResult(
                next_stage=CallStage.TRANSFER_READY,
                response_guidance="Say: 'Perfect. Stay right there for me.'",
                extracted_data={"transfer_consent_confirmed": True, "transfer_ready": True},
            )

        if has_negative:
            if asked_followup:
                lead_profile.notes.remove("asked_silence_followup")
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance="Say: 'No problem. Would it be better if we schedule a callback at a later time?'",
                extracted_data={"transfer_consent_confirmed": False},
            )

        # Silence or ambiguous (treated as silence)
        if is_silent or extract_yes_no(utterance) is None:
            if not asked_followup:
                lead_profile.notes.append("asked_silence_followup")
                return StateResult(
                    next_stage=None,  # Stay in current stage to ask follow-up
                    response_guidance="Say: 'Are you okay holding while I bring the licensed agent on?'"
                )
            else:
                lead_profile.notes.remove("asked_silence_followup")
                return StateResult(
                    next_stage=CallStage.CALLBACK,  # Fall back to callback/end
                    response_guidance="Say: 'Looks like I may have lost you. Would later today or tomorrow work better?'",
                    extracted_data={"callback_requested": True}
                )

        # Ambiguous but non-silent fallback
        return StateResult(
            next_stage=None,
            response_guidance="Say: 'I’m going to bring the licensed agent on so they can go over the options. Hold the line for me, okay?'"
        )
