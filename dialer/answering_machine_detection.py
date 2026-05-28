"""Answering Machine Detection (AMD) parsing and VAD evaluation."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AnsweringMachineDetector:
    """Classifies call answers using Telnyx AMD webhooks and LiveKit VAD signals."""

    @staticmethod
    def classify_telnyx_webhook(payload: dict[str, Any]) -> Optional[str]:
        """Parse a Telnyx webhook payload and return the classified outcome.

        Supported event types include:
        - call.machine.detection.ended
        - call.machine.detected
        - call.answered
        - call.hangup / call.failed
        """
        data = payload.get("data", {})
        event_type = data.get("event_type")
        event_payload = data.get("payload", {})

        if not event_type:
            # Fallback to direct payload checking
            event_payload = payload
            event_type = payload.get("event_type") or "call.machine.detection.ended"

        if event_type in ("call.machine.detection.ended", "call.machine.detected"):
            result = event_payload.get("result")
            logger.info("Telnyx AMD webhook received. Event: %s, Result: %s", event_type, result)

            if result == "human":
                return "human_answered"
            elif result == "voicemail":
                return "voicemail"
            elif result == "greeting":
                return "machine_greeting"
            elif result == "machine":
                return "machine_greeting"
            elif result == "silence":
                return "silence"
            elif result == "fax":
                return "machine_greeting"
            elif result == "no_answer":
                return "no_answer"
            elif result == "busy":
                return "busy"

        elif event_type == "call.answered":
            # Just answered, wait for AMD result, but default to human_answered if no AMD is running
            return "human_answered"

        elif event_type == "call.failed":
            failure_reason = event_payload.get("failure_reason")
            logger.warning("Telnyx call failed webhook: %s", failure_reason)
            if failure_reason in ("busy", "user_busy"):
                return "busy"
            elif failure_reason in ("no_answer", "timeout"):
                return "no_answer"
            elif failure_reason in ("destination_unreachable", "invalid_number", "number_unobtainable"):
                return "disconnected"
            else:
                return "carrier_failure"

        return None

    @staticmethod
    def classify_livekit_vad(speech_duration: float, elapsed_seconds: float) -> Optional[str]:
        """Classify call as machine/voicemail using LiveKit VAD metrics.

        Continuous speech > 3.0s in the first 10 seconds of the call usually indicates
        a voicemail greeting or automated machine greeting.
        """
        if elapsed_seconds <= 10.0 and speech_duration > 3.0:
            logger.info(
                "LiveKit VAD classification: speech_duration=%.2fs in initial %.2fs indicates machine/voicemail.",
                speech_duration,
                elapsed_seconds,
            )
            return "voicemail"
        return None
