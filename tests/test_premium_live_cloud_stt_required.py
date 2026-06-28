import pytest
import os
from unittest.mock import patch, MagicMock
from voice_config import VoiceConfig
from main import SharedComponents
from ops.readiness import check_stt

@pytest.mark.asyncio
async def test_readiness_cloud_stt_requires_key(monkeypatch):
    """Verify check_stt() fails if premium_live and DANA_STT_ROUTING_MODE=cloud but DEEPGRAM_API_KEY is missing/placeholder."""
    monkeypatch.setenv("DANA_VOICE_MODE", "premium_live")
    monkeypatch.setenv("DANA_STT_ROUTING_MODE", "cloud")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "development")
    
    # Test with empty DEEPGRAM_API_KEY
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    passed, msg = await check_stt()
    assert passed is False
    assert "requires DEEPGRAM_API_KEY" in msg
    
    # Test with placeholder DEEPGRAM_API_KEY
    monkeypatch.setenv("DEEPGRAM_API_KEY", "REPLACE_ME")
    passed, msg = await check_stt()
    assert passed is False
    assert "requires DEEPGRAM_API_KEY" in msg
    
    # Test with valid DEEPGRAM_API_KEY
    monkeypatch.setenv("DEEPGRAM_API_KEY", "valid-deepgram-api-key")
    passed, msg = await check_stt()
    assert passed is True
    assert "verified" in msg

@pytest.mark.asyncio
async def test_shared_components_cloud_stt_requires_key(monkeypatch):
    """Verify SharedComponents.initialize() fails if premium_live and DANA_STT_ROUTING_MODE=cloud but DEEPGRAM_API_KEY is missing."""
    monkeypatch.setenv("DANA_VOICE_MODE", "premium_live")
    monkeypatch.delenv("DANA_STT_PROVIDER", raising=False)
    monkeypatch.setenv("DANA_STT_ROUTING_MODE", "cloud")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "development")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "mock-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "mock-voice")
    monkeypatch.setenv("DANA_ALLOW_MOCK_TTS", "false")
    
    config = VoiceConfig()
    # Ensure config has cloud STT settings
    assert config.stt_routing_mode == "cloud"
    assert config.stt_provider == "deepgram"
    
    shared = SharedComponents(config)
    
    # Empty key -> raises RuntimeError
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    with pytest.raises(RuntimeError) as exc_info:
        await shared.initialize()
    assert "requires DEEPGRAM_API_KEY" in str(exc_info.value)
    
    # Placeholder key -> raises RuntimeError
    monkeypatch.setenv("DEEPGRAM_API_KEY", "REPLACE-ME")
    with pytest.raises(RuntimeError) as exc_info:
        await shared.initialize()
    assert "requires DEEPGRAM_API_KEY" in str(exc_info.value)
