import pytest
import os
from unittest.mock import patch, AsyncMock
from voice.tts_healthcheck import run_tts_healthcheck
from tts_service import MockKokoro

@pytest.mark.asyncio
async def test_healthcheck_success():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "development", "DANA_ALLOW_MOCK_TTS": "true"}):
        with patch("os.path.exists", return_value=True):
            mock_tts = MockKokoro()
            result = await run_tts_healthcheck(mock_tts)
            assert result.is_healthy is True
            assert result.error_message is None

@pytest.mark.asyncio
async def test_healthcheck_model_missing():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "development", "DANA_ALLOW_MOCK_TTS": "true"}):
        with patch("config.runtime_env.is_production", return_value=True):
            def side_effect(path):
                if "onnx" in path:
                    return False
                return True
    
            with patch("os.path.exists", side_effect=side_effect):
                mock_tts = MockKokoro()
                result = await run_tts_healthcheck(mock_tts)
                assert result.is_healthy is False
                assert "model file not found" in result.error_message.lower()

@pytest.mark.asyncio
async def test_healthcheck_voices_missing():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "development", "DANA_ALLOW_MOCK_TTS": "true"}):
        with patch("config.runtime_env.is_production", return_value=True):
            def side_effect(path):
                if "voices" in path or "bin" in path:
                    return False
                return True
    
            with patch("os.path.exists", side_effect=side_effect):
                mock_tts = MockKokoro()
                result = await run_tts_healthcheck(mock_tts)
                assert result.is_healthy is False
                assert "voices file not found" in result.error_message.lower()

@pytest.mark.asyncio
async def test_healthcheck_synthesis_exception():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "development", "DANA_ALLOW_MOCK_TTS": "true"}):
        with patch("os.path.exists", return_value=True):
            mock_tts = MockKokoro()
            mock_tts._synthesize_audio = AsyncMock(side_effect=RuntimeError("Synthesis error"))
            result = await run_tts_healthcheck(mock_tts)
            assert result.is_healthy is False
            assert "synthesis failed" in result.error_message.lower()
