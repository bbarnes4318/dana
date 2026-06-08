import pytest
import os
import numpy as np
from unittest.mock import patch
from tts_service import apply_senior_audio_filters

def test_audio_filters_default_off():
    # Verify that without env vars set, filters do not modify the audio
    dummy_audio = np.ones(100, dtype=np.float32)
    with patch.dict(os.environ, {}):
        filtered = apply_senior_audio_filters(dummy_audio)
        assert np.array_equal(filtered, dummy_audio)

def test_audio_filters_explicitly_disabled():
    dummy_audio = np.ones(100, dtype=np.float32)
    custom_env = {
        "DANA_ENABLE_AUDIO_FILTERS": "false",
        "DANA_AUDIO_FILTER_PROFILE": "pstn_senior"
    }
    with patch.dict(os.environ, custom_env):
        filtered = apply_senior_audio_filters(dummy_audio)
        assert np.array_equal(filtered, dummy_audio)

def test_audio_filters_enabled_but_none_profile():
    dummy_audio = np.ones(100, dtype=np.float32)
    custom_env = {
        "DANA_ENABLE_AUDIO_FILTERS": "true",
        "DANA_AUDIO_FILTER_PROFILE": "none"
    }
    with patch.dict(os.environ, custom_env):
        filtered = apply_senior_audio_filters(dummy_audio)
        assert np.array_equal(filtered, dummy_audio)

def test_audio_filters_enabled_with_profile():
    dummy_audio = np.ones(100, dtype=np.float32) * 0.5
    custom_env = {
        "DANA_ENABLE_AUDIO_FILTERS": "true",
        "DANA_AUDIO_FILTER_PROFILE": "pstn_senior"
    }
    with patch.dict(os.environ, custom_env):
        filtered = apply_senior_audio_filters(dummy_audio)
        # The filter modifies the audio, so it should not be identical
        assert not np.array_equal(filtered, dummy_audio)
