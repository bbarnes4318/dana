"""Unit tests for telephony/telnyx_api.py client."""

import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx
from telephony.telnyx_config import TelephonyConfig
from telephony.telnyx_api import TelnyxAPIClient


@pytest.mark.asyncio
async def test_client_header_generation(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk_secret")
    monkeypatch.setenv("TELNYX_API_KEY", "my_test_api_key_123")

    config = TelephonyConfig()
    client = TelnyxAPIClient(config)
    
    headers = client._get_headers()
    assert headers["Authorization"] == "Bearer my_test_api_key_123"
    assert headers["Content-Type"] == "application/json"


def test_sanitize_error_cleans_key(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk_secret")
    monkeypatch.setenv("TELNYX_API_KEY", "secretkey123")

    config = TelephonyConfig()
    client = TelnyxAPIClient(config)

    raw_exception = Exception("Failed with key: secretkey123 inside message")
    sanitized = client._sanitize_error(raw_exception)
    assert "secretkey123" not in sanitized
    assert "TELNYX_API_KEY_REDACTED" in sanitized


@pytest.mark.asyncio
async def test_read_gates_dry_run(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk_secret")
    monkeypatch.setenv("TELNYX_API_KEY", "test_key")
    # Gate set to no
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_READ", "no")

    config = TelephonyConfig()
    client = TelnyxAPIClient(config)

    # Should return None due to safety gate
    result = await client.list_phone_numbers()
    assert result is None


@pytest.mark.asyncio
async def test_mutation_gates_dry_run(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk_secret")
    monkeypatch.setenv("TELNYX_API_KEY", "test_key")
    # Gate set to no
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_MUTATION", "no")

    config = TelephonyConfig()
    client = TelnyxAPIClient(config)

    result = await client.create_credential_connection("my-connection")
    assert result["status"] == "dry_run"
    assert result["connection_name"] == "my-connection"
    assert result["id"] is None
    assert result["real_resource_created"] is False
    assert result["would_create"] is True


@pytest.mark.asyncio
async def test_purchase_gates_dry_run(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk_secret")
    monkeypatch.setenv("TELNYX_API_KEY", "test_key")
    # Gate set to no
    monkeypatch.setenv("DANA_CONFIRM_PURCHASE_NUMBER", "no")

    config = TelephonyConfig()
    client = TelnyxAPIClient(config)

    result = await client.purchase_phone_number("+15551234567")
    assert result["status"] == "dry_run"
    assert result["phone_numbers"][0]["phone_number"] == "+15551234567"
    assert result["phone_numbers"][0]["status"] == "dry_run"
    assert result["id"] is None
    assert result["real_resource_created"] is False
    assert result["would_create"] is True


@pytest.mark.asyncio
async def test_client_makes_httpx_calls_when_gated_yes(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk_secret")
    monkeypatch.setenv("TELNYX_API_KEY", "test_key")
    # Gates set to yes
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_READ", "yes")

    config = TelephonyConfig()
    client = TelnyxAPIClient(config)

    # Mock httpx response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": [{"id": "num-1", "phone_number": "+15551234567"}]}

    # Mock the HTTPX async client methods
    async_mock_get = AsyncMock(return_value=mock_response)

    class MockAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        get = async_mock_get

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    numbers = await client.list_phone_numbers()
    assert numbers is not None
    assert len(numbers) == 1
    assert numbers[0]["id"] == "num-1"
    async_mock_get.assert_called_once()
