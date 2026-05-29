"""Transfer-ready handler — prospect has consented and is being connected to an agent."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request
from core.lead_profile import LeadProfile
from states.base import BaseState


class TransferReadyState(BaseState):
    """Final stage handler to manage any final utterance during connection."""

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
                response_guidance="Acknowledge the callback request politely.",
                extracted_data={"callback_requested": True}
            )

        # If they speak while we are connecting, reassure them and keep in TRANSFER_READY or transition to END
        return StateResult(
            next_stage=CallStage.END,
            response_guidance="Acknowledge politely: 'Hold on one moment, connecting you now.'",
            extracted_data={"transfer_ready": True}
        )
