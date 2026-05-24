"""Unit tests for telephony/telnyx_config.py config loader."""

import os
import pytest
from telephony.telnyx_config import (
    env_str,
    env_bool,
    required,
    TelephonyConfig,
)


def test_env_str_fallback(monkeypatch):
    monkeypatch.delenv("TEST_VAR", raising=False)
    assert env_str("TEST_VAR", "default_val") == "default_val"
    
    monkeypatch.setenv("TEST_VAR", "  hello  ")
    assert env_str("TEST_VAR", "default_val") == "hello"


def test_env_bool_truthy_falsy(monkeypatch):
    monkeypatch.delenv("TEST_VAR", raising=False)
    assert env_bool("TEST_VAR", False) is False
    assert env_bool("TEST_VAR", True) is True

    for truthy in ("true", "1", "yes", "y", "YES", "True"):
        monkeypatch.setenv("TEST_VAR", truthy)
        assert env_bool("TEST_VAR") is True

    for falsy in ("false", "0", "no", "n", "NO", "False"):
        monkeypatch.setenv("TEST_VAR", falsy)
        assert env_bool("TEST_VAR") is False


def test_required_raises(monkeypatch):
    monkeypatch.delenv("TEST_VAR", raising=False)
    with pytest.raises(ValueError, match="Missing required environment variable"):
        required("TEST_VAR")

    monkeypatch.setenv("TEST_VAR", "value")
    assert required("TEST_VAR") == "value"


def test_telephony_config_defaults(monkeypatch):
    # Setup standard required variables
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key123456")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret123456")
    monkeypatch.setenv("TELNYX_API_KEY", "telnyx_key_123456")
    
    config = TelephonyConfig()
    config.validate_api_keys()
    
    assert config.livekit_url == "wss://livekit.test"
    assert config.telnyx_sip_address == "sip.telnyx.com"  # default
    assert config.dana_confirm_telnyx_read is False       # default


def test_telephony_config_validation_errors(monkeypatch):
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)

    config1 = TelephonyConfig()
    with pytest.raises(ValueError, match="TELNYX_API_KEY is required"):
        config1.validate_api_keys()

    monkeypatch.setenv("TELNYX_API_KEY", "some_key")
    config2 = TelephonyConfig()
    with pytest.raises(ValueError, match="LIVEKIT_URL is required"):
        config2.validate_api_keys()


def test_telephony_config_repr_redaction(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key_secret_long")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "very_secret_stuff")
    monkeypatch.setenv("TELNYX_API_KEY", "telnyx_secret_api_key")
    monkeypatch.setenv("TELNYX_SIP_PASSWORD", "sip_password_secret")
    monkeypatch.setenv("TELNYX_SIP_USERNAME", "my_sip_user_name")
    
    config = TelephonyConfig()
    config_repr = repr(config)
    
    # Assert secret contents are redacted or masked
    assert "lk_key_secret_long" not in config_repr
    assert "very_secret_stuff" not in config_repr
    assert "telnyx_secret_api_key" not in config_repr
    assert "sip_password_secret" not in config_repr
    assert "my_sip_user_name" not in config_repr
    
    # Assert masked elements show prefix and suffix
    assert "lk_k...long" in config_repr
    assert "my_s...name" in config_repr
    assert "[REDACTED]" in config_repr
