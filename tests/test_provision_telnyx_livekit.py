"""Unit and integration tests for telephony/provision_telnyx_livekit.py orchestrator."""

import os
import sys
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure standard import capability
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telephony.provision_telnyx_livekit import ProvisioningOrchestrator, mask_str
from telephony.telnyx_config import TelephonyConfig


@pytest.fixture
def clean_env(monkeypatch):
    """Fixture to ensure a clean testing environment."""
    vars_to_clear = [
        "DANA_PROVISION_MODE",
        "DANA_PROVISION_APPLY_CONFIRM",
        "DANA_CONFIRM_TELNYX_READ",
        "DANA_CONFIRM_TELNYX_MUTATION",
        "DANA_CONFIRM_PURCHASE_NUMBER",
        "DANA_CONFIRM_CREATE_LIVEKIT_TRUNK",
        "TELNYX_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "TELNYX_CONNECTION_ID",
        "TELNYX_OUTBOUND_VOICE_PROFILE_ID",
        "TELNYX_PHONE_NUMBER_ID",
        "TELNYX_OUTBOUND_NUMBER",
        "TELNYX_SIP_USERNAME",
        "TELNYX_SIP_PASSWORD",
        "LIVEKIT_SIP_OUTBOUND_TRUNK_ID",
        "TELNYX_PURCHASE_COUNTRY",
        "TELNYX_PURCHASE_AREA_CODE",
        "TELNYX_PURCHASE_LOCALITY"
    ]
    for v in vars_to_clear:
        monkeypatch.delenv(v, raising=False)
    # Set dummy secrets for config construction
    monkeypatch.setenv("TELNYX_API_KEY", "dummy_key")
    monkeypatch.setenv("LIVEKIT_URL", "ws://dummy")
    monkeypatch.setenv("LIVEKIT_API_KEY", "dummy_lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "dummy_lk_secret")


def test_mask_str():
    assert mask_str("replace_me") == "unset"
    assert mask_str("") == "unset"
    assert mask_str("12345678", sensitive=True) == "********"
    assert mask_str("+15551234", sensitive=False) == "******1234"
    assert mask_str("123", sensitive=False) == "****"


@pytest.mark.asyncio
async def test_plan_mode_runs_successfully(clean_env, monkeypatch):
    monkeypatch.setenv("DANA_PROVISION_MODE", "plan")
    
    orchestrator = ProvisioningOrchestrator()
    assert orchestrator.mode == "plan"
    
    with pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    assert excinfo.value.code == 0


@pytest.mark.asyncio
async def test_inspect_mode_requires_read_confirmation(clean_env, monkeypatch):
    monkeypatch.setenv("DANA_PROVISION_MODE", "inspect")
    # Read confirmation missing
    
    orchestrator = ProvisioningOrchestrator()
    with pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    assert excinfo.value.code == 1
    assert orchestrator.report["operator_action"] == "Set DANA_CONFIRM_TELNYX_READ=yes in environment."


@pytest.mark.asyncio
async def test_inspect_mode_success(clean_env, monkeypatch):
    monkeypatch.setenv("DANA_PROVISION_MODE", "inspect")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_READ", "yes")
    
    orchestrator = ProvisioningOrchestrator()
    
    # Mock client read calls
    orchestrator.client.list_outbound_voice_profiles = AsyncMock(return_value=[{"id": "vp-1", "name": "dana-voice-profile"}])
    orchestrator.client.list_credential_connections = AsyncMock(return_value=[{"id": "conn-1", "connection_name": "dana-sip-connection"}])
    orchestrator.client.list_phone_numbers = AsyncMock(return_value=[{"id": "num-1", "phone_number": "+15550001111"}])
    
    with pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    assert excinfo.value.code == 0
    assert orchestrator.report["inspected"] == "yes"


@pytest.mark.asyncio
async def test_apply_mode_requires_apply_confirmation(clean_env, monkeypatch):
    monkeypatch.setenv("DANA_PROVISION_MODE", "apply")
    # DANA_PROVISION_APPLY_CONFIRM missing
    
    orchestrator = ProvisioningOrchestrator()
    with pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    assert excinfo.value.code == 1
    assert "apply_confirm" in orchestrator.report["operator_action"].lower()


@pytest.mark.asyncio
async def test_apply_mode_requires_read_confirmation(clean_env, monkeypatch):
    monkeypatch.setenv("DANA_PROVISION_MODE", "apply")
    monkeypatch.setenv("DANA_PROVISION_APPLY_CONFIRM", "yes")
    # DANA_CONFIRM_TELNYX_READ missing
    
    orchestrator = ProvisioningOrchestrator()
    with pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    assert excinfo.value.code == 1
    assert "confirm_telnyx_read" in orchestrator.report["operator_action"].lower()


@pytest.mark.asyncio
async def test_apply_mode_reuses_existing_resources(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("DANA_PROVISION_MODE", "apply")
    monkeypatch.setenv("DANA_PROVISION_APPLY_CONFIRM", "yes")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_READ", "yes")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_MUTATION", "yes")
    monkeypatch.setenv("DANA_CONFIRM_CREATE_LIVEKIT_TRUNK", "yes")
    
    # Provide username/password since reusing connection requires it
    monkeypatch.setenv("TELNYX_SIP_USERNAME", "test-user")
    monkeypatch.setenv("TELNYX_SIP_PASSWORD", "test-pass")
    
    orchestrator = ProvisioningOrchestrator()
    
    # Mock client calls returning existing matching resources
    orchestrator.client.list_outbound_voice_profiles = AsyncMock(return_value=[{"id": "vp-existing", "name": "dana-voice-profile"}])
    orchestrator.client.list_credential_connections = AsyncMock(return_value=[{
        "id": "conn-existing",
        "connection_name": "dana-sip-connection",
        "username": "test-user"
    }])
    orchestrator.client.list_phone_numbers = AsyncMock(return_value=[{
        "id": "num-existing",
        "phone_number": "+15551234567",
        "connection_id": "conn-existing"
    }])
    
    # Mock update_credential_connection
    orchestrator.client.update_credential_connection = AsyncMock(return_value={"id": "conn-existing"})
    
    # Mock LiveKit API client and trunk checking
    mock_trunk = MagicMock()
    mock_trunk.sip_trunk_id = "lk-trunk-existing"
    mock_trunk.name = "Dana Telnyx Outbound Trunk"
    mock_trunk.address = "sip.telnyx.com"
    mock_trunk.numbers = ["+15551234567"]
    
    mock_list_response = MagicMock()
    mock_list_response.results = [mock_trunk]
    
    mock_lkapi = MagicMock()
    mock_lkapi.sip = MagicMock()
    mock_lkapi.sip.list_sip_outbound_trunk = AsyncMock(return_value=mock_list_response)
    mock_lkapi.aclose = AsyncMock()
    
    # Patch files output location to tmp_path
    monkeypatch.setattr(orchestrator, "_determine_env_file", lambda: str(tmp_path / "provisioned.env"))
    
    with patch("livekit.api.LiveKitAPI", return_value=mock_lkapi), \
         pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    
    assert excinfo.value.code == 0
    
    # Verify reuse states
    assert orchestrator.report["voice_profile"] == "reused"
    assert orchestrator.report["connection"] == "reused"
    assert orchestrator.report["phone_number"] == "reused"
    assert orchestrator.report["livekit_trunk"] == "reused"
    assert orchestrator.report["sip_credentials"] == "env"
    
    # Verify files created
    env_path = tmp_path / "provisioned.env"
    assert env_path.exists()
    
    # Read file contents and verify details
    env_content = env_path.read_text(encoding="utf-8")
    assert "TELNYX_CONNECTION_ID=conn-existing" in env_content
    assert "TELNYX_OUTBOUND_VOICE_PROFILE_ID=vp-existing" in env_content
    assert "TELNYX_OUTBOUND_NUMBER=+15551234567" in env_content
    assert "LIVEKIT_SIP_OUTBOUND_TRUNK_ID=lk-trunk-existing" in env_content
    assert "TELNYX_SIP_USERNAME=test-user" in env_content
    assert "TELNYX_SIP_PASSWORD=test-pass" in env_content
    
    json_path = "telephony/provisioned_resources.json"
    assert os.path.exists(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    assert metadata["connection_id"] == "conn-existing"
    assert "test-pass" not in json.dumps(metadata)
    assert "dummy_key" not in json.dumps(metadata)


@pytest.mark.asyncio
async def test_apply_mode_fails_loudly_when_reused_password_missing(clean_env, monkeypatch):
    monkeypatch.setenv("DANA_PROVISION_MODE", "apply")
    monkeypatch.setenv("DANA_PROVISION_APPLY_CONFIRM", "yes")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_READ", "yes")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_MUTATION", "yes")
    # Password environment variables missing
    
    orchestrator = ProvisioningOrchestrator()
    orchestrator.client.list_outbound_voice_profiles = AsyncMock(return_value=[{"id": "vp-existing", "name": "dana-voice-profile"}])
    orchestrator.client.list_credential_connections = AsyncMock(return_value=[{
        "id": "conn-existing",
        "connection_name": "dana-sip-connection",
        "username": "test-user"
    }])
    
    with pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    assert excinfo.value.code == 1
    assert "Telnyx SIP password could not be retrieved." in orchestrator.report["operator_action"]


@pytest.mark.asyncio
async def test_apply_mode_creates_new_resources(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("DANA_PROVISION_MODE", "apply")
    monkeypatch.setenv("DANA_PROVISION_APPLY_CONFIRM", "yes")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_READ", "yes")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_MUTATION", "yes")
    monkeypatch.setenv("DANA_CONFIRM_CREATE_LIVEKIT_TRUNK", "yes")
    monkeypatch.setenv("DANA_CONFIRM_PURCHASE_NUMBER", "yes")
    monkeypatch.setenv("TELNYX_PURCHASE_COUNTRY", "US")
    
    orchestrator = ProvisioningOrchestrator()
    
    # Return empty lists to force creation
    orchestrator.client.list_outbound_voice_profiles = AsyncMock(return_value=[])
    orchestrator.client.list_credential_connections = AsyncMock(return_value=[])
    orchestrator.client.list_phone_numbers = AsyncMock(return_value=[])
    
    # Mock creations
    orchestrator.client.create_outbound_voice_profile = AsyncMock(return_value={"id": "vp-new"})
    orchestrator.client.create_credential_connection = AsyncMock(return_value={
        "id": "conn-new",
        "username": "new-sip-user",
        "password": "new-sip-password"
    })
    orchestrator.client.update_credential_connection = AsyncMock(return_value={"id": "conn-new"})
    
    # Mock number search and purchase
    orchestrator.client.search_available_phone_numbers = AsyncMock(return_value=[{"phone_number": "+15559998888"}])
    orchestrator.client.purchase_phone_number = AsyncMock(return_value={"id": "order-1"})
    
    # Make sure second search/list call returns the purchased number assigned
    # (or updated numbers search)
    orchestrator.client.list_phone_numbers = AsyncMock(side_effect=[
        [],  # initial search
        [{"id": "num-new", "phone_number": "+15559998888"}]  # verification call
    ])
    orchestrator.client.assign_phone_number_connection = AsyncMock(return_value={"id": "num-new"})
    
    # Mock LiveKit trunk client
    mock_lkapi = MagicMock()
    mock_lkapi.sip = MagicMock()
    mock_lkapi.sip.list_sip_outbound_trunk = AsyncMock(return_value=MagicMock(results=[]))
    
    mock_new_trunk = MagicMock()
    mock_new_trunk.sip_trunk_id = "lk-trunk-new"
    orchestrator.client.create_sip_outbound_trunk = AsyncMock()
    mock_lkapi.sip.create_sip_outbound_trunk = AsyncMock(return_value=mock_new_trunk)
    mock_lkapi.aclose = AsyncMock()
    
    monkeypatch.setattr(orchestrator, "_determine_env_file", lambda: str(tmp_path / "provisioned.env"))
    
    with patch("livekit.api.LiveKitAPI", return_value=mock_lkapi), \
         pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    
    assert excinfo.value.code == 0
    
    assert orchestrator.report["voice_profile"] == "created"
    assert orchestrator.report["connection"] == "created"
    assert orchestrator.report["phone_number"] == "purchased"
    assert orchestrator.report["livekit_trunk"] == "created"
    assert orchestrator.report["sip_credentials"] == "generated"
    
    env_path = tmp_path / "provisioned.env"
    assert env_path.exists()
    env_content = env_path.read_text(encoding="utf-8")
    assert "TELNYX_CONNECTION_ID=conn-new" in env_content
    assert "TELNYX_SIP_USERNAME=new-sip-user" in env_content
    assert "TELNYX_SIP_PASSWORD=new-sip-password" in env_content
    assert "LIVEKIT_SIP_OUTBOUND_TRUNK_ID=lk-trunk-new" in env_content


@pytest.mark.asyncio
async def test_apply_mode_fails_if_purchase_country_missing(clean_env, monkeypatch):
    monkeypatch.setenv("DANA_PROVISION_MODE", "apply")
    monkeypatch.setenv("DANA_PROVISION_APPLY_CONFIRM", "yes")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_READ", "yes")
    monkeypatch.setenv("DANA_CONFIRM_TELNYX_MUTATION", "yes")
    monkeypatch.setenv("DANA_CONFIRM_PURCHASE_NUMBER", "yes")
    # Provide username/password so it passes credential phase
    monkeypatch.setenv("TELNYX_SIP_USERNAME", "test-user")
    monkeypatch.setenv("TELNYX_SIP_PASSWORD", "test-pass")
    # Country missing (TELNYX_PURCHASE_COUNTRY not set)
    
    orchestrator = ProvisioningOrchestrator()
    orchestrator.client.list_outbound_voice_profiles = AsyncMock(return_value=[{"id": "vp-existing", "name": "dana-voice-profile"}])
    orchestrator.client.list_credential_connections = AsyncMock(return_value=[{
        "id": "conn-existing",
        "connection_name": "dana-sip-connection",
        "username": "test-user"
    }])
    orchestrator.client.list_phone_numbers = AsyncMock(return_value=[])  # No owned numbers
    
    with pytest.raises(SystemExit) as excinfo:
        await orchestrator.run()
    assert excinfo.value.code == 1
    assert orchestrator.report["phone_number"] == "missing"
    assert "TELNYX_PURCHASE_COUNTRY" in orchestrator.report["operator_action"]
