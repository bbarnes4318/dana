import pytest
import os
from unittest.mock import patch
from tts_service import MockKokoro

def test_mock_kokoro_blocked_in_production():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "production", "DANA_ALLOW_MOCK_TTS": "false"}):
        with pytest.raises(RuntimeError) as exc_info:
            MockKokoro()
        assert "MockKokoro is prohibited" in str(exc_info.value)

def test_mock_kokoro_blocked_in_production_even_with_override():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "production", "DANA_ALLOW_MOCK_TTS": "true"}):
        with pytest.raises(RuntimeError) as exc_info:
            MockKokoro()
        assert "MockKokoro is prohibited" in str(exc_info.value)

def test_mock_kokoro_blocked_in_development_without_override():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "development", "DANA_ALLOW_MOCK_TTS": "false"}):
        with pytest.raises(RuntimeError) as exc_info:
            MockKokoro()
        assert "MockKokoro is prohibited" in str(exc_info.value)

def test_mock_kokoro_allowed_in_development_with_override():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "development", "DANA_ALLOW_MOCK_TTS": "true"}):
        mock_tts = MockKokoro()
        assert mock_tts is not None
