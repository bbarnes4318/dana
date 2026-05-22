"""Base class for all state handlers."""

from __future__ import annotations

import abc

from core.call_state import CallState, StateResult
from core.lead_profile import LeadProfile


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
