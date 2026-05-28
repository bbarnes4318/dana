"""Backchannel and Perfect usage policy for Dana.

Enforces deterministic rules for choosing backchannels and restricting the usage
of the word 'Perfect' according to conversational context and compliance requirements.
"""

from __future__ import annotations

import re
from typing import Optional
from core.call_state import CallStage

BACKCHANNELS = ["Okay.", "Gotcha.", "Fair enough.", "That makes sense.", "Understood.", "Right."]


def check_confusion_or_hostility(user_text: str) -> tuple[bool, bool]:
    """Analyze user text for signs of confusion or hostility."""
    text = user_text.lower().strip()
    if not text or text == "silence":
        return False, False

    confusion_keywords = ["what", "who is this", "repeat", "huh", "pardon", "dont understand", "don't understand", "explain"]
    hostile_keywords = ["stop calling", "remove", "dnc", "fucking", "shit", "piss", "scam", "spam", "get lost", "f*ck", "go away"]

    is_confused = any(k in text for k in confusion_keywords)
    is_hostile = any(k in text for k in hostile_keywords)
    return is_confused, is_hostile


def is_terminal_response(text: str, stage: str) -> bool:
    """Check if the agent response is a close, handoff, or compliance-sensitive phrase."""
    stage_lower = stage.lower()
    if stage_lower in ("dnc", "disqualified", "end"):
        return True

    text_lower = text.lower()
    terminal_indicators = [
        "take care",
        "connect you",
        "hold the line",
        "licensed coordinator",
        "licensed benefits coordinator",
        "licensed agent",
        "make a note",
        "do not call",
        "stay right there",
        "american beneficiary",
    ]
    return any(indicator in text_lower for indicator in terminal_indicators)


class BackchannelPolicy:
    """Tracks and decides conversational backchannel prefixing deterministically."""

    def __init__(self) -> None:
        self.last_backchannel: Optional[str] = None
        self.used_last_turn: bool = False

    def select_backchannel(
        self,
        current_stage: str,
        user_text: str,
        turn_count: int,
        objection_handled: bool,
    ) -> Optional[str]:
        """Choose a backchannel prefix based on strict deterministic rules."""
        stage_lower = current_stage.lower()

        # 1. No backchannels two turns in a row
        if self.used_last_turn:
            self.used_last_turn = False
            return None

        # 2. No backchannel on opening, answered, DNC, wrong number, disqualification, or end
        if stage_lower in ("answered", "opening", "dnc", "disqualified", "end"):
            self.used_last_turn = False
            return None

        # 3. No backchannel after silence
        user_clean = user_text.strip().lower()
        if not user_clean or user_clean == "silence":
            self.used_last_turn = False
            return None

        # 4. Use only when the previous utterance was substantive (length > 2 words)
        if len(user_text.split()) <= 2:
            self.used_last_turn = False
            return None

        # 5. Deterministic round-robin rotation excluding last_backchannel
        candidates = [b for b in BACKCHANNELS if b != self.last_backchannel]
        # Use turn_count to determine the backchannel index stably
        selected = candidates[turn_count % len(candidates)]

        self.last_backchannel = selected
        self.used_last_turn = True
        return selected

    def clean_perfect_usage(
        self,
        text: str,
        current_stage: str,
        user_text: str,
        objection_handled: bool,
        is_confused: bool = False,
        is_hostile: bool = False,
    ) -> str:
        """Enforces 'Perfect' usage restrictions on final output.

        - Do not use 'Perfect' after confusion, objections, silence, hostility, disqualification, or DNC.
        - Use 'Perfect' only after transfer consent or a clear callback time.
        """
        text_lower = text.lower()
        if "perfect" not in text_lower:
            return text

        strip_perfect = False
        stage_lower = current_stage.lower()

        # Rule 1: Silence
        user_clean = user_text.strip().lower()
        if not user_clean or user_clean == "silence":
            strip_perfect = True

        # Rule 2: Confusion, objections, hostility, DNC or disqualified stages
        if is_confused or is_hostile or objection_handled or stage_lower in ("dnc", "disqualified"):
            strip_perfect = True

        # Rule 3: Use "Perfect" ONLY after transfer consent (TRANSFER_READY) or callback time (CALLBACK)
        if stage_lower not in ("transfer_ready", "callback"):
            strip_perfect = True

        if strip_perfect:
            # Cleanly remove "Perfect" case-insensitive, followed by optional punctuation and spaces
            cleaned = re.sub(r'\bperfect[\s.,!?]*', '', text, flags=re.IGNORECASE)
            # Normalize whitespace
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            # Ensure proper capitalization if stripped from the beginning
            if cleaned and cleaned[0].islower():
                cleaned = cleaned[0].upper() + cleaned[1:]
            return cleaned

        return text
