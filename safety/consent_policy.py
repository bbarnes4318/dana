"""Consent policy management for Dana voice agent.

Loads consent configuration from ``config/consent_policy.yaml`` and
provides helpers for checking recording-notice requirements,
two-party consent states, and consent scripts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.call_state import CallState

# Canonical list — kept as a module-level constant for easy reference.
TWO_PARTY_CONSENT_STATES: list[str] = [
    "CA", "CT", "FL", "IL", "MD", "MA", "MI", "MT", "NV", "NH", "PA", "WA",
]

# Default path relative to project root.
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "consent_policy.yaml"


@dataclass
class _ConsentConfig:
    """Internal representation of the consent-policy YAML."""

    recording_notice_required: bool = True
    recording_notice_text: str = ""
    two_party_consent_states: list[str] = field(default_factory=list)
    one_party_consent_states: list[str] = field(default_factory=list)
    consent_timeout_seconds: int = 10


class ConsentPolicy:
    """Manages recording-consent rules and scripts.

    Usage::

        policy = ConsentPolicy()
        if policy.requires_recording_notice("CA"):
            print(policy.get_consent_script())
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self._config = self._load_config(path)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def has_consent(self, call_state: CallState) -> bool:
        """Return ``True`` if the call has already obtained consent.

        Currently this checks whether the call has progressed past the
        ``PERMISSION`` stage, which implies consent was granted.
        """
        from core.call_state import CallStage

        # If we've moved beyond PERMISSION, consent was captured.
        if call_state.current_stage.value not in ("opening", "permission"):
            return True

        # Check history for PERMISSION followed by another stage.
        stages = call_state.stage_history
        for i, stage in enumerate(stages):
            if stage == CallStage.PERMISSION and i < len(stages) - 1:
                return True

        return False

    def requires_recording_notice(self, state: str) -> bool:
        """Return ``True`` if *state* is a two-party-consent state.

        Args:
            state: Two-letter US state abbreviation (e.g. ``"CA"``).
        """
        return state.upper() in self._config.two_party_consent_states

    def get_consent_script(self) -> str:
        """Return the recording-notice script text."""
        return self._config.recording_notice_text

    @property
    def consent_timeout_seconds(self) -> int:
        """Maximum seconds to wait for consent acknowledgement."""
        return self._config.consent_timeout_seconds

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _load_config(path: Path) -> _ConsentConfig:
        """Load and parse the consent-policy YAML file."""
        if not path.is_file():
            # Fall back to sensible defaults when config is missing.
            return _ConsentConfig(
                two_party_consent_states=list(TWO_PARTY_CONSENT_STATES),
            )

        with open(path, "r", encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}

        return _ConsentConfig(
            recording_notice_required=raw.get("recording_notice_required", True),
            recording_notice_text=raw.get("recording_notice_text", ""),
            two_party_consent_states=[
                s.upper() for s in raw.get("two_party_consent_states", TWO_PARTY_CONSENT_STATES)
            ],
            one_party_consent_states=[
                s.upper() for s in raw.get("one_party_consent_states", [])
            ],
            consent_timeout_seconds=raw.get("consent_timeout_seconds", 10),
        )
