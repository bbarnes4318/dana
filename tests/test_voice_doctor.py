import pytest
import os
from unittest.mock import patch
from ops.voice_doctor import get_voice_doctor_report, mask_credential

def test_credential_masking():
    assert mask_credential(None) == "MISSING"
    assert mask_credential("replace_me") == "PLACEHOLDER (INVALID)"
    assert mask_credential("short") == "PRESENT (TOO SHORT/INSECURE)"
    assert mask_credential("my-super-secret-key-12345") == "my-s...2345"

def test_voice_doctor_report_premium_live_configured():
    custom_env = {
        "DANA_VOICE_MODE": "premium_live",
        "DANA_TTS_PROVIDER": "elevenlabs",
        "ELEVENLABS_API_KEY": "my-super-secret-key-12345",
        "ELEVENLABS_VOICE_ID": "voice-id-abc",
        "DANA_ENABLE_STREAMING_RESPONSE": "true",
        "DANA_ENABLE_AUDIO_FILTERS": "false",
        "DANA_ALLOW_MOCK_TTS": "false"
    }
    with patch.dict(os.environ, custom_env):
        report = get_voice_doctor_report()
        assert report["DANA_VOICE_MODE"] == "premium_live"
        assert report["elevenlabs_credentials_present"] is True
        assert report["premium_live_correctly_configured"] is True
        assert report["masked_keys"]["ELEVENLABS_API_KEY"] == "my-s...2345"

def test_voice_doctor_report_premium_live_misconfigured():
    custom_env = {
        "DANA_VOICE_MODE": "premium_live",
        "DANA_TTS_PROVIDER": "elevenlabs",
        "ELEVENLABS_API_KEY": "replace_me",  # invalid placeholder
        "ELEVENLABS_VOICE_ID": "",
        "DANA_ENABLE_STREAMING_RESPONSE": "true"
    }
    with patch.dict(os.environ, custom_env):
        report = get_voice_doctor_report()
        assert report["premium_live_correctly_configured"] is False
        assert len(report["premium_live_issues"]) > 0
