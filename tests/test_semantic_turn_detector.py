import pytest
import os
from unittest.mock import patch
from speech.semantic_turn_detector import SemanticTurnDetector
from speech.turn_confidence import IMMEDIATE_DELAYS

def test_semantic_turn_detector_disabled_by_default():
    # Gated behavior when kill switch is default (False)
    with patch.dict(os.environ, {}, clear=True):
        detector = SemanticTurnDetector()
        assert detector.enabled is False
        
        # Should return endpoint_tuner defaults for the stage
        res = detector.process_transcript("remove me", stage="INTEREST_CHECK")
        assert res.should_emit_early is False
        assert res.recommended_min_delay == 0.2
        assert res.recommended_max_delay == 0.5

def test_semantic_turn_detector_enabled_by_env():
    with patch.dict(os.environ, {"DANA_ENABLE_SEMANTIC_TURN_DETECTION": "true"}):
        detector = SemanticTurnDetector()
        assert detector.enabled is True
        
        # When enabled, "remove me" should trigger immediate delays
        res = detector.process_transcript("remove me", stage="INTEREST_CHECK")
        assert res.should_emit_early is True
        assert res.recommended_min_delay == IMMEDIATE_DELAYS[0]

def test_explicit_override_in_constructor():
    detector = SemanticTurnDetector(enable_kill_switch=True)
    assert detector.enabled is True
    res = detector.process_transcript("remove me", stage="INTEREST_CHECK")
    assert res.should_emit_early is True
