import pytest
import numpy as np
from unittest.mock import patch, AsyncMock
from voice.tts_healthcheck import run_tts_healthcheck
from tts_service import MockKokoro

@pytest.mark.asyncio
async def test_healthcheck_fails_on_empty_audio():
    with patch("os.path.exists", return_value=True):
        mock_tts = MockKokoro()
        mock_tts._synthesize_audio = AsyncMock(return_value=np.array([], dtype=np.float32))
        result = await run_tts_healthcheck(mock_tts)
        assert result.is_healthy is False
        assert "empty" in result.error_message.lower()

@pytest.mark.asyncio
async def test_healthcheck_fails_on_all_zeros():
    with patch("os.path.exists", return_value=True):
        mock_tts = MockKokoro()
        mock_tts._synthesize_audio = AsyncMock(return_value=np.zeros(100, dtype=np.float32))
        result = await run_tts_healthcheck(mock_tts)
        assert result.is_healthy is False
        assert "silent" in result.error_message.lower()

@pytest.mark.asyncio
async def test_healthcheck_fails_on_none_audio():
    with patch("os.path.exists", return_value=True):
        mock_tts = MockKokoro()
        mock_tts._synthesize_audio = AsyncMock(return_value=None)
        result = await run_tts_healthcheck(mock_tts)
        assert result.is_healthy is False
        assert "empty" in result.error_message.lower()
