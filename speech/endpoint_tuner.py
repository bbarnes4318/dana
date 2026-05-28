"""Adaptive Endpoint Tuner.

Configures dynamic turn-taking delays based on call stages and conversational context.
Ensures faster turn-around for yes/no stages and extra patience for objections or confusion.
"""

from __future__ import annotations

import logging
from typing import Tuple, Any
import inspect

logger = logging.getLogger(__name__)

# Base timing delays (min_delay, max_delay) in seconds
STAGE_DELAYS = {
    "OPENING": (0.6, 1.5),             # Patient initial silence tolerance
    "INTEREST_CHECK": (0.2, 0.5),      # Fast yes/no qualification stages
    "AGE_RANGE": (0.2, 0.5),
    "LIVING_SITUATION": (0.2, 0.5),
    "DECISION_MAKER": (0.2, 0.5),
    "TRANSFER_CONSENT": (0.2, 0.5),
    "CALLBACK": (0.4, 0.8),            # Standard pacing
    "DNC": (0.4, 0.8),
    "DISQUALIFIED": (0.4, 0.8),
    "END": (0.4, 0.8),
}

# Objection/confusion delay: extra patient to let user explain fully
OBJECTION_CONFUSION_DELAYS = (0.8, 2.0)

# Silence recovery: patient but finite delay
SILENCE_RECOVERY_DELAYS = (0.8, 2.5)


def get_endpoint_delays(stage: str, is_objection_or_confusion: bool = False, is_silence_recovery: bool = False) -> Tuple[float, float]:
    """Calculate the adaptive min and max endpointing delays in seconds."""
    # Normalize stage names matching short-flow stage names in Prompt 2
    stage_upper = stage.strip().upper()

    if is_objection_or_confusion:
        logger.debug(f"EndpointTuner: Objection/confusion active for stage {stage_upper}. Delay: {OBJECTION_CONFUSION_DELAYS}")
        return OBJECTION_CONFUSION_DELAYS

    if is_silence_recovery:
        logger.debug(f"EndpointTuner: Silence recovery active for stage {stage_upper}. Delay: {SILENCE_RECOVERY_DELAYS}")
        return SILENCE_RECOVERY_DELAYS

    delays = STAGE_DELAYS.get(stage_upper, (0.35, 0.65))  # Safe default if stage not found
    logger.debug(f"EndpointTuner: Delays for stage {stage_upper}: {delays}")
    return delays


def safe_update_endpointing(session: Any, min_delay: float, max_delay: float) -> None:
    """Safely updates endpointing delays on the session using dynamic signature checks."""
    if session is None or not hasattr(session, "update_options"):
        logger.warning("endpoint_update_not_supported: Session does not support updating options.")
        return

    try:
        sig = inspect.signature(session.update_options)
        params = sig.parameters
        
        if "min_endpointing_delay" in params and "max_endpointing_delay" in params:
            session.update_options(
                min_endpointing_delay=min_delay,
                max_endpointing_delay=max_delay
            )
            logger.info(f"Updated session endpointing delays (min_endpointing_delay={min_delay}, max_endpointing_delay={max_delay})")
        elif "endpointing_opts" in params:
            # Try passing as endpointing_opts dict
            session.update_options(
                endpointing_opts={"min_delay": min_delay, "max_delay": max_delay}
            )
            logger.info(f"Updated session endpointing delays via endpointing_opts (min_delay={min_delay}, max_delay={max_delay})")
        else:
            logger.warning("endpoint_update_not_supported: update_options signature does not match known options.")
    except Exception as e:
        logger.warning(f"endpoint_update_not_supported: Failed to update endpointing delays: {e}")
