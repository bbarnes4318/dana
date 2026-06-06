"""Conversational timing controls for Dana.

Determines pre-speech pauses and timing adjustments to simulate natural human pacing.
"""

from __future__ import annotations


class ConversationalTiming:
    """Manages pre-speech delays and timing properties for conversational turns."""

    def get_pre_speech_delay(self, stage: str) -> float:
        """Determines the required pre-speech pause in seconds before responding.

        Sensitive questions/stages get a small thinking/hesitation pause (0.5s to 1.0s)
        to avoid immediate robotic responses to sensitive disclosures.
        """
        stage_lower = stage.lower().strip()

        # Sensitive qualification questions warrant a small pause to feel human-like
        sensitive_stages = {
            "age_range": 0.8,
            "living_situation": 0.8,
            "decision_maker": 0.8,
            "transfer_consent": 0.8,
        }

        return sensitive_stages.get(stage_lower, 0.0)
