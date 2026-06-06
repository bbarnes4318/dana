"""Turn Confidence Estimator.

Calculates turn confidence and maps conversational context/intents to stage-aware
endpointing delays.
"""

from __future__ import annotations

import logging
from speech.partial_intent_detector import IntentClass

logger = logging.getLogger(__name__)

# Base timing delays (min_delay, max_delay) in seconds
DEFAULT_MIN_DELAY = 0.35
DEFAULT_MAX_DELAY = 0.65

# Delays for yes/no stages (fast response)
FAST_YES_NO_DELAYS = (0.15, 0.35)

# Patient delays for objections, confusion, still thinking
PATIENT_DELAYS = (0.8, 2.0)

# DNC/Wrong number/Early Emit (immediate)
IMMEDIATE_DELAYS = (0.01, 0.05)

# Backchannel delays (very patient to let them speak more)
BACKCHANNEL_DELAYS = (1.5, 3.0)

# Strict transfer consent delays (make sure they are done speaking)
STRICT_TRANSFER_DELAYS = (0.4, 0.8)


class TurnConfidenceResult:
    """The result of turn confidence calculation."""

    def __init__(
        self,
        confidence: float,
        recommended_min_delay: float,
        recommended_max_delay: float,
        should_emit_early: bool = False
    ) -> None:
        self.confidence = confidence  # 0.0 to 1.0
        self.recommended_min_delay = recommended_min_delay
        self.recommended_max_delay = recommended_max_delay
        self.should_emit_early = should_emit_early


def calculate_turn_confidence(
    intent: str,
    stage: str,
    text: str,
    vad_active: bool = False
) -> TurnConfidenceResult:
    """Calculates turn confidence and endpointing delays based on intent and stage."""
    stage_upper = stage.strip().upper()

    # 1. DNC, Wrong Number, or explicit early emit triggers
    immediate_intents = (
        IntentClass.DNC_STOP,
        IntentClass.WRONG_NUMBER,
        IntentClass.CALLBACK_REQUEST,
    )

    text_clean = text.strip().lower()
    early_emit_phrases = [
        "remove me", "stop calling", "not interested", "who is this", "wrong number", "call me later", "call me back"
    ]
    has_early_emit_phrase = any(phrase in text_clean for phrase in early_emit_phrases)

    if intent in immediate_intents or has_early_emit_phrase:
        logger.info(f"TurnConfidence: Immediate trigger detected. Intent={intent}, text='{text}'")
        return TurnConfidenceResult(
            confidence=1.0,
            recommended_min_delay=IMMEDIATE_DELAYS[0],
            recommended_max_delay=IMMEDIATE_DELAYS[1],
            should_emit_early=True
        )

    # 2. Backchannel Only -> keep patient
    if intent == IntentClass.BACKCHANNEL_ONLY:
        logger.debug(f"TurnConfidence: Backchannel detected ('{text}'). Delaying turn completion.")
        return TurnConfidenceResult(
            confidence=0.1,
            recommended_min_delay=BACKCHANNEL_DELAYS[0],
            recommended_max_delay=BACKCHANNEL_DELAYS[1],
            should_emit_early=False
        )

    # 3. Still Thinking -> keep patient
    if intent == IntentClass.STILL_THINKING:
        logger.debug(f"TurnConfidence: Still thinking detected. Delaying turn completion.")
        return TurnConfidenceResult(
            confidence=0.2,
            recommended_min_delay=PATIENT_DELAYS[0],
            recommended_max_delay=PATIENT_DELAYS[1],
            should_emit_early=False
        )

    # 4. Strict Transfer Consent Stage
    if stage_upper == "TRANSFER_CONSENT":
        if intent in (IntentClass.TRANSFER_CONSENT_YES, IntentClass.TRANSFER_CONSENT_NO):
            return TurnConfidenceResult(
                confidence=0.9,
                recommended_min_delay=FAST_YES_NO_DELAYS[0],
                recommended_max_delay=FAST_YES_NO_DELAYS[1],
                should_emit_early=True
            )
        else:
            return TurnConfidenceResult(
                confidence=0.5,
                recommended_min_delay=STRICT_TRANSFER_DELAYS[0],
                recommended_max_delay=STRICT_TRANSFER_DELAYS[1],
                should_emit_early=False
            )

    # 5. Yes/No qualification stages
    yes_no_stages = ("INTEREST_CHECK", "AGE_RANGE", "LIVING_SITUATION", "DECISION_MAKER")
    if stage_upper in yes_no_stages:
        if intent in (IntentClass.COMPLETE_ANSWER, IntentClass.TRANSFER_CONSENT_YES, IntentClass.TRANSFER_CONSENT_NO):
            return TurnConfidenceResult(
                confidence=0.95,
                recommended_min_delay=FAST_YES_NO_DELAYS[0],
                recommended_max_delay=FAST_YES_NO_DELAYS[1],
                should_emit_early=True
            )
        # Objections/Questions in yes/no stages wait slightly longer
        if intent in (IntentClass.OBJECTION, IntentClass.CONFUSION, IntentClass.PRICE_QUESTION, IntentClass.GOVERNMENT_QUESTION, IntentClass.BOT_OR_AI_QUESTION):
            return TurnConfidenceResult(
                confidence=0.6,
                recommended_min_delay=PATIENT_DELAYS[0],
                recommended_max_delay=PATIENT_DELAYS[1],
                should_emit_early=False
            )

    # 6. General Objections or Questions
    if intent in (IntentClass.OBJECTION, IntentClass.CONFUSION, IntentClass.PRICE_QUESTION, IntentClass.GOVERNMENT_QUESTION, IntentClass.BOT_OR_AI_QUESTION):
        return TurnConfidenceResult(
            confidence=0.7,
            recommended_min_delay=PATIENT_DELAYS[0],
            recommended_max_delay=PATIENT_DELAYS[1],
            should_emit_early=False
        )

    # 7. Fallback Default
    return TurnConfidenceResult(
        confidence=0.8,
        recommended_min_delay=DEFAULT_MIN_DELAY,
        recommended_max_delay=DEFAULT_MAX_DELAY,
        should_emit_early=False
    )
