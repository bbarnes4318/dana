import pytest
import os
from unittest.mock import patch
from tts_service import MockKokoro, MockKokoroModel
from config.runtime_env import is_mock_tts_allowed

def test_mock_tts_blocked_in_production():
    custom_env = {
        "DANA_RUNTIME_ENV": "production",
        "DANA_ALLOW_MOCK_TTS": "true"  # Even if true, production blocks it
    }
    with patch.dict(os.environ, custom_env):
        assert is_mock_tts_allowed() is False
        
        with pytest.raises(RuntimeError):
            MockKokoro()
            
        with pytest.raises(RuntimeError):
            MockKokoroModel("dummy_path", "dummy_voices")

def test_mock_tts_blocked_in_premium_live():
    custom_env = {
        "DANA_VOICE_MODE": "premium_live",
        "DANA_RUNTIME_ENV": "production"
    }
    with patch.dict(os.environ, custom_env):
        assert is_mock_tts_allowed() is False
        
        with pytest.raises(RuntimeError):
            MockKokoro()
