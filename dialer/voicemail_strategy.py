"""Voicemail classification and message drop configuration strategy."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class VoicemailStrategy:
    """Manages voicemail detection classification and subsequent dialer actions."""

    @staticmethod
    def is_voicemail_outcome(outcome: str, amd_result: Optional[str] = None) -> bool:
        """Check if the call outcome or AMD result indicates a voicemail/answering machine."""
        voicemail_labels = {"voicemail", "machine", "machine_greeting", "silence_detected", "fax"}
        if outcome in voicemail_labels:
            return True
        if amd_result in voicemail_labels:
            return True
        return False

    @staticmethod
    def get_voicemail_action(campaign_config: Dict[str, Any]) -> Dict[str, Any]:
        """Determine what action the dialer should take upon detecting a voicemail.
        
        Campaign config parameters:
        - voicemail_strategy: dict
          - leave_message: bool (default: False - hang up immediately)
          - message_type: str ("tts" or "audio")
          - message_text: str (for tts)
          - audio_url: str (for audio)
        
        Returns:
            A dictionary describing the action, e.g.:
            {"action": "hangup"}
            {"action": "tts", "message": "Hi, this is Dana..."}
            {"action": "play_audio", "audio_url": "https://..."}
        """
        strategy_config = campaign_config.get("voicemail_strategy") or {}
        leave_message = strategy_config.get("leave_message", False)

        if not leave_message:
            return {"action": "hangup"}

        message_type = strategy_config.get("message_type", "tts")
        if message_type == "audio":
            audio_url = strategy_config.get("audio_url")
            if audio_url:
                return {"action": "play_audio", "audio_url": audio_url}
            else:
                logger.warning("Voicemail strategy configured to drop audio but no audio_url was provided. Falling back to hangup.")
                return {"action": "hangup"}

        # Default to TTS
        message_text = strategy_config.get("message_text") or "Hello, please call us back when you get a chance."
        return {"action": "tts", "message": message_text}
