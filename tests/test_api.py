from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from unittest import mock
from unittest.mock import AsyncMock, patch
from dana.runtime.api import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_providers_endpoint():
    response = client.get("/providers")
    assert response.status_code == 200
    data = response.json()
    assert "llm" in data
    assert "tts" in data
    assert "stt" in data
    assert "telephony" in data
    assert "google_gemini" in data["llm"]
    assert "elevenlabs" in data["tts"]

@patch("dana.providers.provider_registry.registry.get_telephony")
@patch("dana.runtime.api.emit_crm_event_async", new_callable=AsyncMock)
@patch("dana.runtime.api.repo.save_live_call_session", new_callable=AsyncMock)
def test_start_call(mock_save, mock_emit, mock_get_telephony):
    mock_telephony = mock.MagicMock()
    mock_telephony.originate_call = AsyncMock(return_value="SIP-Call-Initiated")
    mock_get_telephony.return_value = mock_telephony

    payload = {
        "phone_number": "+1234567890",
        "campaign_id": "test-campaign",
        "contact_id": "test-contact",
        "metadata": {"test": "val"}
    }
    response = client.post("/calls/start", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "call_id" in data
    assert data["dial_result"] == "SIP-Call-Initiated"
    
    mock_emit.assert_called_with(
        "call.started",
        repository=mock.ANY,
        call_id=data["call_id"],
        campaign_id="test-campaign",
        phone_e164="+1234567890",
        lead_profile={"test": "val"}
    )
    mock_save.assert_called_once()

@patch("dana.providers.provider_registry.registry.get_telephony")
@patch("dana.runtime.api.emit_crm_event_async", new_callable=AsyncMock)
def test_end_call(mock_emit, mock_get_telephony):
    mock_telephony = mock.MagicMock()
    mock_telephony.end_call = AsyncMock(return_value=True)
    mock_get_telephony.return_value = mock_telephony

    call_id = "test-call-id"
    response = client.post(f"/calls/{call_id}/end")
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    mock_emit.assert_called_with(
        "call.ended",
        repository=mock.ANY,
        call_id=call_id
    )

@patch("dana.providers.provider_registry.registry.get_telephony")
@patch("dana.runtime.api.emit_crm_event_async", new_callable=AsyncMock)
def test_transfer_call(mock_emit, mock_get_telephony):
    mock_telephony = mock.MagicMock()
    mock_telephony.transfer_call = AsyncMock(return_value=True)
    mock_get_telephony.return_value = mock_telephony

    call_id = "test-call-id"
    payload = {
        "destination": "+1999999999",
        "warm": True
    }
    response = client.post(f"/calls/{call_id}/transfer", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    # Check that transfer event was emitted
    mock_emit.assert_any_call(
        "transfer.requested",
        repository=mock.ANY,
        call_id=call_id,
        transfer={"destination": "+1999999999", "warm": True}
    )
    mock_emit.assert_any_call(
        "transfer.succeeded",
        repository=mock.ANY,
        call_id=call_id
    )

@patch("dana.runtime.api.repo.get_live_call_session", new_callable=AsyncMock)
def test_get_call_status(mock_get):
    mock_get.return_value = {
        "call_id": "test-call-id",
        "status": "connected",
        "campaign_id": "test-campaign"
    }
    response = client.get("/calls/test-call-id/status")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "connected"
