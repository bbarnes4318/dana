import pytest
import os
from unittest.mock import patch
from config.env_schema import validate_env

def test_premium_live_requires_streaming_env_schema():
    # Valid premium_live configuration
    env_dict = {
        "HF_TOKEN": "some_token",
        "LIVEKIT_URL": "wss://test.livekit.cloud",
        "LIVEKIT_API_KEY": "key",
        "LIVEKIT_API_SECRET": "secret",
        "LIVEKIT_SIP_OUTBOUND_TRUNK_ID": "trunk",
        "TELNYX_API_KEY": "t_key",
        "TELNYX_CONNECTION_ID": "conn",
        "TELNYX_OUTBOUND_NUMBER": "+1234567890",
        "POSTGRES_USER": "db_user",
        "POSTGRES_PASSWORD": "db_password",
        "POSTGRES_DB": "db",
        "DATABASE_URL": "postgresql://db_user:db_password@localhost/db",
        "DATABASE_ADMIN_URL": "postgresql://db_user:db_password@localhost/db",
        "REDIS_URL": "redis://localhost",
        "DANA_USE_REDIS_HOT_STATE": "true",
        "VLLM_BASE_URL": "http://localhost",
        "VLLM_MODEL": "model",
        "VLLM_GPU_MEMORY_UTILIZATION": "0.7",
        "VLLM_QUANTIZATION": "fp8",
        "VLLM_MAX_MODEL_LEN": "2048",
        "DANA_RUNTIME_ENV": "production",
        "DANA_ALLOW_MOCK_TTS": "false",
        "DANA_CONTROLLED_LIVE_TEST": "false",
        
        # Premium live specific settings
        "DANA_VOICE_MODE": "premium_live",
        "DANA_TTS_PROVIDER": "elevenlabs",
        "ELEVENLABS_API_KEY": "some_key",
        "ELEVENLABS_VOICE_ID": "some_voice",
        "DANA_ENABLE_STREAMING_RESPONSE": "true",
        "DANA_ENABLE_AUDIO_FILTERS": "false"
    }
    
    # 1. Verification of valid config
    res = validate_env(env_dict)
    assert res["passed"] is True, f"Valid config failed: {res['failures']}"
    
    # 2. Disable streaming response -> must fail
    invalid_env = dict(env_dict)
    invalid_env["DANA_ENABLE_STREAMING_RESPONSE"] = "false"
    res_invalid = validate_env(invalid_env)
    assert res_invalid["passed"] is False
    assert any("streaming" in f.lower() for f in res_invalid["failures"])
