import pytest
import os
from unittest.mock import patch
from voice_config import VoiceConfig

def test_voice_rescue_mode_overrides():
    custom_env = {
        "DANA_VOICE_MODE": "premium_live",
        "DANA_TTS_PROVIDER": "elevenlabs",
        "DANA_ENABLE_STREAMING_RESPONSE": "true",
        "DANA_ENABLE_AUDIO_FILTERS": "false"
    }
    with patch.dict(os.environ, custom_env):
        config = VoiceConfig()
        assert config.voice_mode == "premium_live"
        assert config.tts_routing_mode == "cloud"
        assert config.allow_cloud_tts_fallback is True
        assert config.tts_provider == "elevenlabs"
        assert config.enable_streaming_response is True
        assert config.enable_audio_filters is False

def test_local_cost_mode_default():
    custom_env = {
        "DANA_VOICE_MODE": "local_cost"
    }
    with patch.dict(os.environ, custom_env):
        config = VoiceConfig()
        assert config.voice_mode == "local_cost"
        # In local_cost mode, they default to local unless explicitly configured
        assert config.tts_provider == "local_kokoro"
        assert config.tts_routing_mode == "hybrid"
