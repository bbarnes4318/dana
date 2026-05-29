"""Base class for all state handlers."""

from __future__ import annotations

import abc
from typing import Optional

from core.call_state import CallStage, CallState, StateResult
from core.lead_profile import LeadProfile


from core.canonical_responses import WRONG_NUMBER_CLOSE

def check_global_stops(utterance: str) -> Optional[StateResult]:
    lower = utterance.lower()
    if any(w in lower for w in ["wrong number", "not this person", "no such person", "don't know who that is", "wrong person", "not me"]):
        return StateResult(
            next_stage=CallStage.END,
            response_guidance=f"Say: '{WRONG_NUMBER_CLOSE}' then end the call.",
        )
    if any(w in lower for w in ["passed away", "loss", "grief", "grieving", "funeral", "death", "passed recently"]):
        return StateResult(
            next_stage=CallStage.END,
            response_guidance="Say: 'I am so sorry to hear that. I won’t keep you. Please take care.' then end the call.",
        )
    if any(w in lower for w in ["leave a message", "voice message", "after the tone", "record your message", "not available", "leave your message", "voicemail", "message system"]):
        return StateResult(
            next_stage=CallStage.END,
            response_guidance="Voicemail detected. End the call.",
        )
    return None


class BaseState(abc.ABC):
    """Abstract base every stage handler must subclass."""

    @abc.abstractmethod
    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        """Process a user *utterance* and return a :class:`StateResult`."""
        ...
