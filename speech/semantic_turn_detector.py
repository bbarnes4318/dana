"""Semantic Turn Detector.

Orchestrates semantic turn detection, coordinating intent classification and
turn confidence delay adjustments.
"""

from __future__ import annotations

import os
from typing import Optional
from speech.partial_intent_detector import classify_partial_intent
from speech.turn_confidence import calculate_turn_confidence, TurnConfidenceResult
from speech.context_registry import get_current_call_stage


class SemanticTurnDetector:
    """Semantic Turn Detector for the Dana Voice Platform."""

    def __init__(self, enable_kill_switch: Optional[bool] = None) -> None:
        if enable_kill_switch is not None:
            self.enabled = enable_kill_switch
        else:
            val = os.getenv("DANA_ENABLE_SEMANTIC_TURN_DETECTION", "false").strip().lower()
            self.enabled = val in ("true", "1", "yes")

    def process_transcript(
        self,
        text: str,
        stage: Optional[str] = None,
        vad_active: bool = False
    ) -> TurnConfidenceResult:
        """Processes partial user transcript and determines turn confidence and delay options."""
        if not self.enabled:
            # Fallback to standard adaptive delays if semantic turn detection is disabled
            from speech.endpoint_tuner import get_endpoint_delays
            active_stage = stage or get_current_call_stage() or "OPENING"
            min_d, max_d = get_endpoint_delays(active_stage)
            return TurnConfidenceResult(
                confidence=1.0,
                recommended_min_delay=min_d,
                recommended_max_delay=max_d,
                should_emit_early=False
            )

        active_stage = stage or get_current_call_stage() or "OPENING"
        intent = classify_partial_intent(text, active_stage)
        return calculate_turn_confidence(intent, active_stage, text, vad_active)
