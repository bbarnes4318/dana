"""Unit tests for the environment variable schema validation layer."""

from __future__ import annotations

import pytest
from config.env_schema import validate_env, mask_value

@pytest.fixture
def base_valid_env() -> dict[str, str]:
    """Return a minimal valid production environment dictionary."""
    return {
        # Hugging Face
        "HF_TOKEN": "hf_some_valid_token_12345",
        # LiveKit Cloud
        "LIVEKIT_URL": "wss://myproject.livekit.cloud",
        "LIVEKIT_API_KEY": "lk_api_key_abc",
        "LIVEKIT_API_SECRET": "lk_api_secret_xyz",
        # LiveKit SIP
        "LIVEKIT_SIP_OUTBOUND_TRUNK_ID": "sip_trunk_id_123",
        # Telnyx
        "TELNYX_API_KEY": "telnyx_key_999",
        "TELNYX_CONNECTION_ID": "conn_id_xyz",
        "TELNYX_OUTBOUND_NUMBER": "+1234567890",
        # Database
        "POSTGRES_USER": "dana_user",
        "POSTGRES_PASSWORD": "my_strong_password",
        "POSTGRES_DB": "dana",
        "DATABASE_URL": "postgresql://dana_user:my_strong_password@pgbouncer:6432/dana",
        "DATABASE_ADMIN_URL": "postgresql://dana_user:my_strong_password@postgres:5432/dana",
        # Redis
        "REDIS_URL": "redis://redis:6379/0",
        "DANA_USE_REDIS_HOT_STATE": "true",
        # vLLM
        "VLLM_BASE_URL": "http://vllm-server:8000/v1",
        "VLLM_MODEL": "meta-llama/Llama-3.1-8B-Instruct",
        "VLLM_GPU_MEMORY_UTILIZATION": "0.70",
        "VLLM_QUANTIZATION": "fp8",
        "VLLM_MAX_MODEL_LEN": "4096",
        # Runtime Safety
        "DANA_RUNTIME_ENV": "production",
        "DANA_ALLOW_MOCK_TTS": "false",
        "DANA_CONTROLLED_LIVE_TEST": "false",
    }


def test_valid_env_passes(base_valid_env):
    res = validate_env(base_valid_env)
    assert res["passed"] is True
    assert len(res["failures"]) == 0
    assert res["READY_TO_START_SERVICES"] is True
    assert res["READY_FOR_LIVE_CALL_TEST"] is True
    assert res["PRODUCTION_ENV_VALID"] is True


def test_missing_required_var_fails(base_valid_env):
    # Test missing HF_TOKEN
    env = dict(base_valid_env)
    del env["HF_TOKEN"]
    res = validate_env(env)
    assert res["passed"] is False
    assert any("HF_TOKEN" in f for f in res["failures"])
    assert res["READY_TO_START_SERVICES"] is False
    assert res["READY_FOR_LIVE_CALL_TEST"] is False


def test_placeholder_required_var_fails(base_valid_env):
    # Test placeholder HF_TOKEN
    env = dict(base_valid_env)
    env["HF_TOKEN"] = "replace_me"
    res = validate_env(env)
    assert res["passed"] is False
    assert any("HF_TOKEN" in f for f in res["failures"])


def test_missing_livekit_values_fail(base_valid_env):
    for key in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]:
        env = dict(base_valid_env)
        del env[key]
        res = validate_env(env)
        assert res["passed"] is False
        assert any(key in f for f in res["failures"])


def test_missing_telnyx_values_fail(base_valid_env):
    for key in ["TELNYX_API_KEY", "TELNYX_CONNECTION_ID"]:
        env = dict(base_valid_env)
        del env[key]
        res = validate_env(env)
        assert res["passed"] is False
        assert any(key in f for f in res["failures"])


def test_missing_livekit_sip_trunk_fails(base_valid_env):
    env = dict(base_valid_env)
    del env["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]
    res = validate_env(env)
    assert res["passed"] is False
    assert any("LIVEKIT_SIP_OUTBOUND_TRUNK_ID" in f for f in res["failures"])


def test_missing_database_urls_fail(base_valid_env):
    for key in ["DATABASE_URL", "DATABASE_ADMIN_URL"]:
        env = dict(base_valid_env)
        del env[key]
        res = validate_env(env)
        assert res["passed"] is False
        assert any(key in f for f in res["failures"])


def test_runtime_env_not_production_fails(base_valid_env):
    env = dict(base_valid_env)
    env["DANA_RUNTIME_ENV"] = "development"
    res = validate_env(env)
    assert res["passed"] is False
    assert any("DANA_RUNTIME_ENV" in f for f in res["failures"])


def test_allow_mock_tts_true_fails(base_valid_env):
    env = dict(base_valid_env)
    env["DANA_ALLOW_MOCK_TTS"] = "true"
    res = validate_env(env)
    assert res["passed"] is False
    assert any("DANA_ALLOW_MOCK_TTS" in f for f in res["failures"])


def test_controlled_live_test_true_warns(base_valid_env):
    env = dict(base_valid_env)
    env["DANA_CONTROLLED_LIVE_TEST"] = "true"
    res = validate_env(env)
    # Controlled live test is a warning, not a blocker failure
    assert res["passed"] is True
    assert any("DANA_CONTROLLED_LIVE_TEST" in w for w in res["warnings"])
    assert res["READY_FOR_LIVE_CALL_TEST"] is True  # We can still test calls (it's the point of this mode)


def test_optional_cloud_fallbacks_not_required(base_valid_env):
    env = dict(base_valid_env)
    # Ensure they are absent
    if "OPENAI_API_KEY" in env:
        del env["OPENAI_API_KEY"]
    res = validate_env(env)
    assert res["passed"] is True


def test_deprecated_aliases_produce_warnings(base_valid_env):
    env = dict(base_valid_env)
    # Use deprecated alias instead of canonical
    del env["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]
    env["DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk_alias_123"
    res = validate_env(env)
    # The validation passes because the alias acts as a fallback, but a warning is logged
    assert res["passed"] is True
    assert any("DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID" in w for w in res["warnings"])


def test_secrets_masking():
    # Masking simple credentials
    assert mask_value("HF_TOKEN", "hf_secret_123") == "present"
    assert mask_value("LIVEKIT_API_SECRET", "lk_secret_123") == "present"
    
    # Masking connection strings
    db_url = "postgresql://myuser:mypassword@pgbouncer:6432/mydb"
    masked_db = mask_value("DATABASE_URL", db_url)
    assert "mypassword" not in masked_db
    assert "postgresql://myuser:******@pgbouncer:6432/mydb" in masked_db

    # Empty or placeholders should show status
    assert mask_value("HF_TOKEN", "") == "empty"
    assert mask_value("HF_TOKEN", None) == "missing"
    assert mask_value("HF_TOKEN", "replace_me") == "placeholder"
